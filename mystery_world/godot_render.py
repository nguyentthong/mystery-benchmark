"""Python <-> Godot bridge for the 3D visual channel (M3/M4/M9, 3D variant).

Launches a long-lived Godot subprocess running ``game/scenes/render.tscn``
(see ``game/scripts/render.gd``) and ferries one render request per
``env.step()`` call. Returns PNG bytes back to the caller.

Lifecycle
---------
1. First call to ``get_default_renderer()`` (or first ``GodotRenderer()``
   construction) spawns ``godot --path game/ scenes/render.tscn`` once.
2. The Godot process writes a single ``READY\\n`` line to stdout. We wait
   for that, then enter a request/response loop.
3. Each render request is one line of JSON on stdin. The Godot side replies
   with ``RENDER <base64-png>\\n`` (or ``ERR <message>\\n``).
4. ``close()`` writes ``{"cmd": "shutdown"}`` and waits for the process to
   exit. Atexit handlers do this automatically.

Headless fallback
-----------------
For CI / sandbox environments without a Godot binary on PATH (or where the
SDL/Vulkan display cannot be opened), set ``MYSTERYARENA_GODOT=mock`` to
swap in ``MockGodotRenderer``: a pure-Python stub that returns deterministic
1x1 PNGs whose pixel value is a hash of the (room_id, game_time,
visible_evidence) tuple. The mock satisfies the same protocol so the env-
side determinism / reproducibility tests still exercise the M3/M4/M9 wiring.

Real visual verification of the Godot path is via
``scripts/smoke_godot_render.py`` on a machine with Godot installed.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from mystery_world.entities import Evidence, Location, WorldObject
    from mystery_world.world import MysteryEnvironment, WorldState


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Room dict assembly (mirrors server/godot_server.py:serialize_room, inlined
# here to avoid pulling in pygame via mystery_world.renderer.layout).
# ---------------------------------------------------------------------------

_TILE_FLOOR = "F"
_TILE_WALL  = "W"
_TILE_DOOR  = "D"


def _serialize_room(
    state: "WorldState",
    location_id: str,
    visible_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the per-room JSON payload that render.gd / room_builder.gd
    consumes. Layout: a simple rectangle of FLOOR tiles bordered by WALL
    tiles, with door tiles punched in for adjacent rooms. Objects and
    characters are placed at hash-derived positions so the same room always
    looks the same.

    visible_evidence (optional): the M2 list of {id, visual_state, ...} for
    the agent's current room. Used to populate the evidence_overlays in
    the render command. None means no overlays.
    """
    loc = state.locations[location_id]

    # Room dimensions: pick a size that comfortably fits the objects.
    n_objects = len(loc.objects_here)
    n_chars = len(loc.characters_here)
    interior = max(6, 3 + n_objects + n_chars)
    width = interior
    height = interior

    # Tile grid: FLOOR everywhere, WALL on the border.
    tiles: list[list[str]] = []
    for x in range(width):
        col: list[str] = []
        for y in range(height):
            if x == 0 or y == 0 or x == width - 1 or y == height - 1:
                col.append(_TILE_WALL)
            else:
                col.append(_TILE_FLOOR)
        tiles.append(col)

    # Punch one door per adjacent room into the wall closest to that room.
    # Order them by adjacency id so the layout is deterministic.
    doors: list[dict[str, Any]] = []
    adj_ids = sorted(loc.adjacent_ids)
    door_walls = ["north", "east", "south", "west"]
    for i, adj_id in enumerate(adj_ids):
        wall = door_walls[i % 4]
        if wall == "north":
            dx, dy = width // 2, 0
        elif wall == "south":
            dx, dy = width // 2, height - 1
        elif wall == "east":
            dx, dy = width - 1, height // 2
        else:  # west
            dx, dy = 0, height // 2
        tiles[dx][dy] = _TILE_DOOR
        adj = state.locations.get(adj_id)
        doors.append({
            "x": dx, "y": dy, "wall": wall,
            "leads_to": adj_id,
            "leads_to_name": adj.name if adj else adj_id,
        })

    # Deterministic object placements: hash the object id to (x, y) inside
    # the interior, retry if the slot is already taken.
    used: set[tuple[int, int]] = {(d["x"], d["y"]) for d in doors}
    objects: list[dict[str, Any]] = []
    for oid in sorted(loc.objects_here):
        obj = state.objects.get(oid)
        if obj is None:
            continue
        x, y = _hash_to_interior(oid, width, height, used)
        used.add((x, y))
        objects.append({
            "id": oid,
            "name": obj.name,
            "x": x,
            "y": y,
            "kind": _object_kind(state, obj),
        })

    characters: list[dict[str, Any]] = []
    for cid in sorted(loc.characters_here):
        char = state.characters.get(cid)
        if char is None:
            continue
        x, y = _hash_to_interior(cid, width, height, used)
        used.add((x, y))
        characters.append({
            "id": cid,
            "name": char.full_name,
            "x": x,
            "y": y,
            "role": _character_role(char),
            "alive": bool(char.is_alive),
        })

    spawn_x, spawn_y = width // 2, height // 2

    payload: dict[str, Any] = {
        "room_id": location_id,
        "name": loc.name,
        "description": loc.description,
        "width": width,
        "height": height,
        "tiles": tiles,
        "doors": doors,
        "objects": objects,
        "characters": characters,
        "spawn": {"x": float(spawn_x) + 0.5, "y": float(spawn_y) + 0.5, "facing_deg": 0.0},
    }

    # Evidence overlays: attach the aging trace at each visible evidence's
    # host object position. We look up the host object in this room's
    # ``objects`` list (the room.objects[k].x/y just placed above).
    obj_by_id = {o["id"]: o for o in objects}
    overlays: list[dict[str, Any]] = []
    for ev in (visible_evidence or []):
        # Each visible_evidence entry was produced by env._compute_visible_evidence,
        # which iterates obj.evidence_id in loc.objects_here. Find that host obj.
        ev_id = ev["id"]
        host_oid: str | None = None
        for oid in loc.objects_here:
            obj = state.objects.get(oid)
            if obj is not None and obj.evidence_id == ev_id:
                host_oid = oid
                break
        if host_oid is None or host_oid not in obj_by_id:
            continue
        host_pos = obj_by_id[host_oid]
        overlays.append({
            "id": ev_id,
            "x": host_pos["x"],
            "y": host_pos["y"],
            "evidence_family": _evidence_family_from_name(ev.get("name", "")),
            "visual_state": ev.get("visual_state") or "BRIGHT",
        })
    payload["evidence_overlays"] = overlays

    return payload


def _hash_to_interior(
    seed_str: str, width: int, height: int, used: set[tuple[int, int]]
) -> tuple[int, int]:
    """Deterministic interior-tile placement for a string id, avoiding ``used``."""
    h = hashlib.md5(seed_str.encode()).digest()
    for attempt in range(64):
        x = 1 + (h[(attempt * 2) % len(h)] % (width - 2))
        y = 1 + (h[(attempt * 2 + 1) % len(h)] % (height - 2))
        if (x, y) not in used:
            return x, y
    # Fallback (should be unreachable in practice for sane room sizes).
    for x in range(1, width - 1):
        for y in range(1, height - 1):
            if (x, y) not in used:
                return x, y
    return 1, 1


def _object_kind(state: "WorldState", obj: "WorldObject") -> str:
    if obj.id == state.murder_weapon_id:
        return "murder_weapon"
    if getattr(obj, "is_weapon", False):
        return "weapon"
    if obj.evidence_id:
        return "evidence_host"
    return "prop"


def _character_role(char: Any) -> str:
    from mystery_world.entities import CharacterRole
    if CharacterRole.VICTIM in char.roles:
        return "victim"
    if CharacterRole.SUSPECT in char.roles:
        return "suspect"
    if CharacterRole.WITNESS in char.roles:
        return "witness"
    return "innocent"


def _evidence_family_from_name(name: str) -> str:
    """Map an evidence's human-readable name to a render family."""
    n = (name or "").lower()
    if "blood" in n or "stain" in n:
        return "bloodstain"
    if "foot" in n or "tread" in n or "print" in n:
        return "footprint"
    if "candle" in n or "wax" in n:
        return "candle"
    if "trace" in n:
        return "trace"
    return "default"


# ---------------------------------------------------------------------------
# GodotRenderer: subprocess wrapper
# ---------------------------------------------------------------------------

class GodotRendererError(RuntimeError):
    pass


class GodotRenderer:
    """Long-lived Godot subprocess that renders one frame per ``render()``.

    Boot the process once per Python session (or per env-instance) and reuse
    it across many step() calls. The Godot binary is located via the
    ``GODOT_BIN`` env var, otherwise ``shutil.which("godot")``.
    """

    DEFAULT_WIDTH: int = 720
    DEFAULT_HEIGHT: int = 720
    READY_TOKEN: str = "READY"
    RENDER_TOKEN: str = "RENDER "
    ERR_TOKEN: str = "ERR "

    def __init__(
        self,
        project_path: str | Path | None = None,
        godot_bin: str | None = None,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        ready_timeout_sec: float = 30.0,
    ) -> None:
        self.project_path = Path(project_path) if project_path else _default_project_path()
        self.godot_bin = (
            godot_bin
            or os.environ.get("GODOT_BIN")
            or shutil.which("godot")
            or shutil.which("godot4")
            or _detect_godot_app()
            or "godot"
        )
        self.width = width
        self.height = height
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._launch(ready_timeout_sec)

    def _launch(self, ready_timeout_sec: float) -> None:
        cmd = [
            self.godot_bin,
            "--path", str(self.project_path),
            "res://scenes/render.tscn",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,                # line-buffered
                text=True,
            )
        except FileNotFoundError as exc:
            raise GodotRendererError(
                f"Could not launch Godot from {self.godot_bin!r}. Set GODOT_BIN "
                f"to the path of your Godot binary, or set MYSTERYARENA_GODOT=mock "
                f"to use the deterministic stub renderer for CI."
            ) from exc
        atexit.register(self.close)

        # Start a daemon thread that drains stderr so Godot doesn't eventually
        # block on a full stderr buffer mid-run. Lines come back through the
        # Python logger at INFO so push_error / bad-JSON diagnostics are
        # visible without dumping to the terminal directly.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()

        # Wait for READY.
        import time
        deadline = time.monotonic() + ready_timeout_sec
        while time.monotonic() < deadline:
            line = self._proc.stdout.readline() if self._proc.stdout else ""
            if not line:
                if self._proc.poll() is not None:
                    raise GodotRendererError(
                        "Godot subprocess exited before READY (see godot stderr above)."
                    )
                continue
            if line.strip() == self.READY_TOKEN:
                return
            # Anything else is diagnostic noise; relay to logger.
            logger.debug("godot[startup]: %s", line.rstrip())
        raise GodotRendererError(
            f"Godot did not emit READY within {ready_timeout_sec}s"
        )

    def _drain_stderr(self) -> None:
        """Background thread: read Godot's stderr line by line and forward
        each line to the Python logger at WARNING. Prevents the stderr
        buffer from filling up and blocking the Godot process."""
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            for line in iter(self._proc.stderr.readline, ""):
                if not line:
                    break
                logger.warning("godot[stderr]: %s", line.rstrip())
        except Exception:   # pragma: no cover -- thread teardown
            pass

    def render(
        self,
        room_payload: dict[str, Any],
        width: int | None = None,
        height: int | None = None,
    ) -> bytes:
        """Send one render command and return PNG bytes.

        ``room_payload`` is the dict from ``_serialize_room``; it must include
        ``evidence_overlays``. The render command is one line of JSON on
        stdin; the response is ``RENDER <base64-png>`` on stdout.
        """
        if self._proc is None or self._proc.poll() is not None:
            raise GodotRendererError("Godot subprocess is not running.")
        overlays = room_payload.pop("evidence_overlays", [])
        cmd_dict = {
            "cmd": "render",
            "room": room_payload,
            "evidence_overlays": overlays,
            "width": width or self.width,
            "height": height or self.height,
        }
        # Put overlays back so the caller's payload is unmodified.
        room_payload["evidence_overlays"] = overlays

        line = json.dumps(cmd_dict) + "\n"
        with self._lock:
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None
            self._proc.stdin.write(line)
            self._proc.stdin.flush()

            # Read response lines until we see a RENDER or ERR.
            while True:
                resp = self._proc.stdout.readline()
                if not resp:
                    raise GodotRendererError("Godot subprocess closed stdout.")
                resp = resp.rstrip("\n")
                if resp.startswith(self.RENDER_TOKEN):
                    b64 = resp[len(self.RENDER_TOKEN):]
                    return base64.b64decode(b64)
                if resp.startswith(self.ERR_TOKEN):
                    raise GodotRendererError(f"Godot render error: {resp[len(self.ERR_TOKEN):]}")
                logger.debug("godot: %s", resp)

    def close(self) -> None:
        if self._proc is None:
            return
        with self._lock:
            try:
                if self._proc.stdin and not self._proc.stdin.closed:
                    self._proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                    self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                self._proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None


def _default_project_path() -> Path:
    """Return the path to the bundled game/ project directory."""
    return Path(__file__).resolve().parent.parent / "game"


def _detect_godot_app() -> str | None:
    """Best-effort search for a Godot binary in OS-typical install locations.

    macOS: Godot ships as ``Godot.app`` with the binary at
    ``Contents/MacOS/Godot``; users often double-click to install rather than
    putting it on PATH. We look in /Applications and ~/Applications, and
    accept any version-suffixed app bundle like ``Godot_v4.3-stable.app``.

    Linux: many users keep Godot under ~/ as a downloaded binary. We probe
    ~/Godot, ~/godot, and ~/bin/godot.

    Returns the first existing path or None.
    """
    candidates: list[Path] = []
    if sys.platform == "darwin":
        app_dirs = [
            Path("/Applications"),
            Path.home() / "Applications",
            Path.home() / "Downloads",   # many users leave the .app here
            Path.home() / "Desktop",
        ]
        for app_dir in app_dirs:
            if not app_dir.exists():
                continue
            # Plain Godot.app
            plain = app_dir / "Godot.app" / "Contents" / "MacOS" / "Godot"
            candidates.append(plain)
            # Version-suffixed bundles, e.g. Godot_v4.3-stable_macos.universal.app
            try:
                for entry in app_dir.iterdir():
                    if entry.is_dir() and entry.suffix == ".app" and entry.name.lower().startswith("godot"):
                        candidates.append(entry / "Contents" / "MacOS" / "Godot")
            except (PermissionError, OSError):
                continue
    elif sys.platform.startswith("linux"):
        home = Path.home()
        candidates.extend([
            home / "Godot", home / "godot",
            home / "bin" / "godot", home / "bin" / "Godot",
            Path("/opt/godot/godot"), Path("/opt/godot/Godot"),
        ])
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    return None


# ---------------------------------------------------------------------------
# MockGodotRenderer: deterministic stub for CI / sandbox use
# ---------------------------------------------------------------------------

class MockGodotRenderer:
    """Pure-Python stand-in for GodotRenderer that returns deterministic
    PNGs without launching a subprocess. Sufficient to exercise the env
    contract (PNG bytes returned, deterministic, sub-step game_time
    threaded through) when no Godot binary is available."""

    def __init__(self, **_: Any) -> None:
        pass

    def render(
        self,
        room_payload: dict[str, Any],
        width: int | None = None,
        height: int | None = None,
    ) -> bytes:
        # Hash the (room_id, all visible-evidence states, optional game_time
        # carried in the payload) into a deterministic seed, then synthesise
        # a small PIL image whose pixels are a function of that hash.
        try:
            from PIL import Image
        except ImportError as exc:
            raise GodotRendererError(
                "MockGodotRenderer requires Pillow."
            ) from exc
        seed_str = json.dumps({
            "room_id": room_payload.get("room_id"),
            "width": room_payload.get("width"),
            "height": room_payload.get("height"),
            "overlays": [
                (o.get("id"), o.get("visual_state"), o.get("x"), o.get("y"))
                for o in room_payload.get("evidence_overlays", [])
            ],
            "game_time": room_payload.get("game_time"),
        }, sort_keys=True)
        h = hashlib.sha256(seed_str.encode()).digest()
        w = width or 64
        h_px = height or 64
        # Map the hash into 4-pixel-band RGB stripes so different inputs yield
        # visually-different (and dhash-distinguishable) PNGs.
        img = Image.new("RGB", (w, h_px), (0, 0, 0))
        pixels = img.load()
        for y in range(h_px):
            r = h[(y * 3) % len(h)]
            g = h[(y * 3 + 1) % len(h)]
            b = h[(y * 3 + 2) % len(h)]
            for x in range(w):
                pixels[x, y] = (r, g, b)
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def close(self) -> None:
        return


# ---------------------------------------------------------------------------
# Process-wide singleton dispatch
# ---------------------------------------------------------------------------

_default_renderer: GodotRenderer | MockGodotRenderer | None = None
_default_lock = threading.Lock()


def get_default_renderer() -> GodotRenderer | MockGodotRenderer:
    """Return (and lazily construct) the process-wide singleton renderer.

    Selection rules:
      - MYSTERYARENA_GODOT=mock                -> MockGodotRenderer
      - MYSTERYARENA_GODOT=real                -> GodotRenderer (raise if no binary)
      - default                                -> try GodotRenderer, fall back
                                                  to MockGodotRenderer on launch error
    """
    global _default_renderer
    if _default_renderer is not None:
        return _default_renderer
    with _default_lock:
        if _default_renderer is not None:
            return _default_renderer
        choice = os.environ.get("MYSTERYARENA_GODOT", "").lower()
        if choice == "mock":
            print(
                "[godot_render] MYSTERYARENA_GODOT=mock -- using MockGodotRenderer "
                "(deterministic striped PNGs, not real 3D).",
                file=sys.stderr,
            )
            _default_renderer = MockGodotRenderer()
            return _default_renderer
        if choice == "real":
            _default_renderer = GodotRenderer()
            return _default_renderer
        try:
            _default_renderer = GodotRenderer()
            print(
                "[godot_render] Using real Godot subprocess.",
                file=sys.stderr,
            )
        except GodotRendererError as exc:
            # Loud fallback. Without this, users see striped MockGodotRenderer
            # output in play.py and assume the 3D pipeline is broken, when
            # really Godot just wasn't reachable.
            print(
                f"\n[godot_render] WARNING: Godot subprocess could not start "
                f"({exc})\n"
                f"[godot_render] Falling back to MockGodotRenderer -- the image "
                f"channel will show deterministic striped PNGs, NOT a 3D scene.\n"
                f"[godot_render] To use the real 3D renderer:\n"
                f"[godot_render]   - install Godot 4.3+ and put it on PATH, OR\n"
                f"[godot_render]   - set GODOT_BIN to the binary path. e.g.:\n"
                f"[godot_render]     export GODOT_BIN=/Applications/Godot.app/Contents/MacOS/Godot\n"
                f"[godot_render]   then re-run your command.\n",
                file=sys.stderr,
            )
            _default_renderer = MockGodotRenderer()
        return _default_renderer


def reset_default_renderer() -> None:
    """Close the process-wide singleton (used in tests + reproducibility runs)."""
    global _default_renderer
    with _default_lock:
        if _default_renderer is not None:
            _default_renderer.close()
            _default_renderer = None


# ---------------------------------------------------------------------------
# Top-level render entry points (mirror the pygame renderer API)
# ---------------------------------------------------------------------------

def render_observation_png_3d(
    env: "MysteryEnvironment",
    at_game_time: float | None = None,
    width: int | None = None,
    height: int | None = None,
) -> bytes:
    """Render the agent's current room as PNG bytes using the Godot backend
    (or the mock, depending on environment configuration).

    ``at_game_time`` overrides current_step for the VisualState computation
    (M9 sub-step rendering). The Godot payload includes ``game_time`` so the
    mock's deterministic hash distinguishes frames at different sub-step
    instants -- exactly what M9's clip test exercises.
    """
    if not env.visual_mode:
        return None  # type: ignore[return-value]
    renderer = get_default_renderer()
    visible = env.get_visible_evidence(at_game_time=at_game_time)
    room_payload = _serialize_room(env.state, env.agent_location_id, visible)
    # Pass game_time via the payload so the mock varies its output too.
    room_payload["game_time"] = (
        float(env.state.current_step) if at_game_time is None else float(at_game_time)
    )
    return renderer.render(room_payload, width=width, height=height)


def render_observation_clip_3d(
    env: "MysteryEnvironment",
    n_frames: int,
    width: int | None = None,
    height: int | None = None,
) -> list[bytes]:
    """N PNG frames at evenly-spaced sub-step game_time offsets within
    ``[current_step - 1, current_step]``. Matches the pygame M9 contract:
    frames[-1] is at exactly ``current_step``."""
    if not env.visual_mode or n_frames < 1:
        return []
    if n_frames == 1:
        png = render_observation_png_3d(env, width=width, height=height)
        return [png] if png is not None else []
    end = float(env.state.current_step)
    start = end - 1.0
    span = end - start
    frames: list[bytes] = []
    for i in range(n_frames):
        offset = (i + 1) / n_frames
        gt = start + offset * span
        frames.append(
            render_observation_png_3d(env, at_game_time=gt, width=width, height=height)
        )
    return frames
