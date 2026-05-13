"""Headless image-observation rendering for agents.

`render_observation_image(env)` returns the current room as a numpy array of
shape (H, W, 3), uint8. No display window is opened. Critically, the rendered
image contains **no in-scene text labels** — object/character names are not
drawn on top of sprites, so a VLM cannot read the answer off the pixels. The
text channel (room name, last observation, exits, who-is-here) is supplied
separately to the agent as a normal textual observation.

Usage:

    img = render_observation_image(env)              # (H, W, 3) uint8 numpy
    # ... pass to a multimodal API as base64 PNG / PIL Image ...

The renderer is fully deterministic given (env.state.seed, location_id), so
two episodes with the same seed produce identical pixel observations.
"""
from __future__ import annotations

import io
import os
import weakref
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from PIL import Image as PILImage

import pygame

from mystery_world.entities import EvidenceState, VisualState
from mystery_world.renderer.layout import (
    RoomLayout,
    Tile,
    build_room_layout,
    compute_door_pairings,
)
from mystery_world.renderer.sprites import ProceduralSprites, SpriteLoader
from mystery_world.world import MysteryEnvironment

# Tile size for agent observations. Smaller than the human-play view so the
# image stays under typical multimodal API size limits.
OBS_TILE_PX = 32
OBS_BG = (18, 16, 22)

# Per-band aging colours for the evidence trace overlay. Calibrated so that
# the three bands are visually distinct (a VLM has to be able to tell BRIGHT
# from DULL from FADED). Mapped from VisualState.name.
_AGING_TRACE_COLORS: dict[str, tuple[int, int, int]] = {
    VisualState.BRIGHT.name: (215,  35,  40),   # vivid red — wet, recently deposited
    VisualState.DULL.name:   (135,  45,  35),   # dark dried red
    VisualState.FADED.name:  ( 80,  55,  45),   # brown crust — long elapsed
}
# Dark outline drawn around every trace so it remains visible regardless of
# the floor tile it sits on.
_AGING_TRACE_OUTLINE: tuple[int, int, int] = (15, 10, 10)


# ---------------------------------------------------------------------------
# Headless pygame init
# ---------------------------------------------------------------------------

_pygame_inited = False


def _ensure_pygame() -> None:
    """Initialise pygame in headless (dummy SDL) mode if not already inited."""
    global _pygame_inited
    if _pygame_inited:
        return
    # Only override SDL driver if no display is configured.
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.display.init()
    pygame.font.init()
    _pygame_inited = True


# ---------------------------------------------------------------------------
# Per-env layout cache
# ---------------------------------------------------------------------------
#
# The layout is mutated in place across renders (positions accumulate as
# NPCs enter/leave the room over time), so the cache must be tied to the
# specific env that owns the state. Keying a plain dict by id(env) suffers
# from CPython id-reuse (a recycled memory address can produce a stale
# hit); WeakKeyDictionary entries are removed automatically when their
# env is GC'd, so id-reuse cannot resurrect a stale layout.
# (WorldState is a dataclass and not hashable by default, so we key by
# the env wrapper rather than the state itself.)

_env_layout_caches: "weakref.WeakKeyDictionary[MysteryEnvironment, dict[str, RoomLayout]]" = weakref.WeakKeyDictionary()
_env_pairings_caches: "weakref.WeakKeyDictionary[MysteryEnvironment, dict[str, dict[str, str]]]" = weakref.WeakKeyDictionary()


def _pairings_for(env: MysteryEnvironment) -> dict[str, dict[str, str]]:
    """Cache door pairings per env."""
    cached = _env_pairings_caches.get(env)
    if cached is None:
        cached = compute_door_pairings(env.state)
        _env_pairings_caches[env] = cached
    return cached


def _layout_for(env: MysteryEnvironment, location_id: str) -> RoomLayout:
    layout_cache = _env_layout_caches.get(env)
    if layout_cache is None:
        layout_cache = {}
        _env_layout_caches[env] = layout_cache
    if location_id not in layout_cache:
        loc = env.state.locations[location_id]
        sides = _pairings_for(env).get(location_id, {})
        layout_cache[location_id] = build_room_layout(loc, neighbor_sides=sides)
    layout = layout_cache[location_id]
    # Refresh dynamic content (NPCs may have moved into / out of this room)
    loc = env.state.locations[location_id]
    used: set[tuple[int, int]] = set(layout.doors.keys())
    layout.objects = {oid: pos for oid, pos in layout.objects.items() if oid in loc.objects_here}
    layout.characters = {cid: pos for cid, pos in layout.characters.items() if cid in loc.characters_here}
    used.update(layout.objects.values())
    used.update(layout.characters.values())
    free = [
        (x, y)
        for x in range(2, layout.width - 2)
        for y in range(2, layout.height - 2)
        if layout.tiles[x][y] == Tile.FLOOR and (x, y) not in used
    ]
    import random
    rng = random.Random(f"obs::{location_id}::{env.state.current_step}")
    rng.shuffle(free)
    MIN_SPACING = 3
    placed = list(used)

    def _take_one():
        if not free:
            return None
        for i, t in enumerate(free):
            if all(max(abs(t[0]-p[0]), abs(t[1]-p[1])) >= MIN_SPACING for p in placed):
                free.pop(i); placed.append(t); return t
        best = max(range(len(free)),
                   key=lambda i: min((max(abs(free[i][0]-p[0]), abs(free[i][1]-p[1]))
                                      for p in placed), default=99))
        t = free.pop(best); placed.append(t); return t

    for oid in loc.objects_here:
        if oid not in layout.objects:
            t = _take_one()
            if t is None:
                break
            layout.objects[oid] = t
    for cid in loc.characters_here:
        if cid not in layout.characters:
            t = _take_one()
            if t is None:
                break
            layout.characters[cid] = t
    return layout


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_observation_image(
    env: MysteryEnvironment,
    sprites: SpriteLoader | None = None,
    tile_px: int = OBS_TILE_PX,
    at_game_time: float | None = None,
) -> np.ndarray:
    """Render the agent's current room as an (H, W, 3) uint8 numpy array.

    No text labels are drawn on the image — vision must be discriminative on
    its own. No detective body or player avatar is drawn either: the action
    space is semantic, so the agent is not on-camera. Visible evidence with a
    non-None VisualState is overlaid as a coloured trace whose hue reflects
    the band (BRIGHT/DULL/FADED), so the same evidence renders differently
    across observations at different game-times.
    """
    _ensure_pygame()
    sprites = sprites or ProceduralSprites()
    layout = _layout_for(env, env.agent_location_id)

    w_px = layout.width * tile_px
    h_px = layout.height * tile_px
    surface = pygame.Surface((w_px, h_px))
    surface.fill(OBS_BG)

    # Tiles (floor / wall / door)
    for x in range(layout.width):
        for y in range(layout.height):
            rect = pygame.Rect(x * tile_px, y * tile_px, tile_px, tile_px)
            tile = layout.tiles[x][y]
            if tile == Tile.WALL:
                sprites.draw_wall(surface, rect)
            elif tile == Tile.DOOR:
                sprites.draw_door(surface, rect)
            else:
                sprites.draw_floor(surface, rect)

    # Objects
    for oid, (x, y) in layout.objects.items():
        obj = env.state.objects.get(oid)
        if obj is None:
            continue
        ev_state = None
        discovered = False
        if obj.evidence_id:
            ev = env.state.evidence.get(obj.evidence_id)
            if ev:
                ev_state = ev.state
                discovered = ev.id in env._discovered_evidence
        rect = pygame.Rect(x * tile_px, y * tile_px, tile_px, tile_px)
        sprites.draw_object(surface, rect, obj, discovered, ev_state)

    # Characters
    for cid, (x, y) in layout.characters.items():
        char = env.state.characters.get(cid)
        if char is None:
            continue
        rect = pygame.Rect(x * tile_px, y * tile_px, tile_px, tile_px)
        sprites.draw_character(surface, rect, char)

    # Aging-trace overlays for visible evidence (M3). One coloured circle per
    # visible piece of evidence, placed on its host object, hue determined by
    # the evidence's VisualState. Same persistent evidence at a different
    # current_step renders a different colour, which is the visual carrier of
    # change across observations. ``at_game_time`` (M9) lets a clip render
    # sub-step instants by overriding the aging-computation game-time.
    _draw_aging_overlays(env, layout, surface, tile_px, at_game_time=at_game_time)

    # Convert to numpy (H, W, 3) uint8 RGB
    raw = pygame.surfarray.array3d(surface)
    # pygame returns (W, H, 3); transpose to (H, W, 3)
    return raw.transpose(1, 0, 2).copy()


def _draw_aging_overlays(
    env: MysteryEnvironment,
    layout: RoomLayout,
    surface: pygame.Surface,
    tile_px: int,
    at_game_time: float | None = None,
) -> None:
    """Draw the VisualState-coloured trace overlay on top of each visible
    evidence's host object. Reads env.get_visible_evidence() so the env stays
    the single source of truth about what's visible and at what band.

    ``at_game_time`` overrides the aging-computation game-time, used by M9's
    clip renderer to render frames at sub-step instants."""
    # Build a quick lookup from evidence_id to host object's tile position.
    host_pos: dict[str, tuple[int, int]] = {}
    for oid, pos in layout.objects.items():
        obj = env.state.objects.get(oid)
        if obj is not None and obj.evidence_id:
            host_pos[obj.evidence_id] = pos

    radius = max(4, tile_px // 4)
    for entry in env.get_visible_evidence(at_game_time=at_game_time):
        vs_name = entry.get("visual_state")
        if vs_name is None:
            continue   # non-temporal evidence (no contact_timestamp); not aged
        color = _AGING_TRACE_COLORS.get(vs_name)
        if color is None:
            continue
        pos = host_pos.get(entry["id"])
        if pos is None:
            continue   # host object not in the rendered room (off-camera evidence)
        tx, ty = pos
        cx = tx * tile_px + tile_px // 2
        cy = ty * tile_px + tile_px // 2
        pygame.draw.circle(surface, color, (cx, cy), radius)
        pygame.draw.circle(surface, _AGING_TRACE_OUTLINE, (cx, cy), radius, 1)


def render_observation_png(env: MysteryEnvironment, **kwargs) -> bytes:
    """Same as `render_observation_image` but returns PNG-encoded bytes,
    suitable for direct upload to multimodal APIs.

    Accepts ``at_game_time`` to render at a sub-step instant (M9 clips)."""
    arr = render_observation_image(env, **kwargs)
    try:
        from PIL import Image
    except ImportError as e:
        raise ImportError(
            "PIL/Pillow required for PNG encoding. `uv add pillow`"
        ) from e
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def render_observation_pil(env: MysteryEnvironment, **kwargs) -> "PILImage.Image":
    """Same but returns a PIL Image object."""
    arr = render_observation_image(env, **kwargs)
    try:
        from PIL import Image
    except ImportError as e:
        raise ImportError("PIL/Pillow required. `uv add pillow`") from e
    return Image.fromarray(arr)


def render_observation_clip(
    env: MysteryEnvironment,
    n_frames: int,
    span_start_game_time: float | None = None,
    span_end_game_time: float | None = None,
    **kwargs,
) -> list[bytes]:
    """Render N frames of PNG bytes at evenly-spaced game-time offsets
    within ``[span_start, span_end]`` (exclusive of start, inclusive of end).

    Defaults: ``span_start = current_step - 1`` and ``span_end = current_step``
    so the clip covers the duration of the step that just completed. The
    last frame matches ``render_observation_png(env)``.

    ``n_frames=1`` returns a single-element list equivalent to the
    single-image observation. Used by M9's per-action clip dispatch.
    """
    if n_frames <= 0:
        return []
    end = float(env.state.current_step) if span_end_game_time is None else float(span_end_game_time)
    start = (end - 1.0) if span_start_game_time is None else float(span_start_game_time)
    span = end - start
    frames: list[bytes] = []
    for i in range(n_frames):
        # (i+1)/N puts the last frame exactly at `end`; frame 1 sits at
        # start + span/N (excludes start so we always advance from the
        # action's pre-state).
        offset = (i + 1) / n_frames
        gt = start + offset * span
        frames.append(render_observation_png(env, at_game_time=gt, **kwargs))
    return frames
