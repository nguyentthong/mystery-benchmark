# Implementation prompt — MysteryArena 3D port (Godot 4), Milestone 1

You are implementing the first milestone of a 3D first-person port of an
existing Python benchmark called **MysteryArena**. Read this entire prompt
before writing any code, then read `CLAUDE.md` at the repo root and obey it.
The rules in `CLAUDE.md` are non-negotiable; if anything below contradicts
them, `CLAUDE.md` wins — flag the contradiction and stop.

## Context

MysteryArena is a procedural murder-mystery benchmark for evaluating
**vision-language model (VLM) agents** as embodied detectives in a partially
observed environment. It currently ships with a 2D pygame renderer. We want to
upgrade to a **3D first-person view** to test viewpoint-dependent reasoning,
first-person partial observability (FOV cone, occlusion), and visual grounding
under realistic clutter. Both a human and a VLM agent must be able to play the
same game.

We picked **Godot 4** as the engine because it exports natively to Mac,
Windows, and Linux (downloadable game), supports headless mode for the agent
benchmark, is open source (no licensing friction), and is small enough to
distribute.

The Python world-simulation logic is mature and battle-tested. **Do not port
it to GDScript.** Godot is a thin client.

## Architecture

```
┌─────────────────────────────┐    WebSocket   ┌────────────────────────────┐
│  Python (existing, reused)  │     JSON       │  Godot 4 client (new)      │
│  - mystery_world/world.py   │ ◄────────────► │  - 3D scene + FPS controls │
│  - mystery_world/events.py  │                │  - Kenney 3D asset lookup  │
│  - mystery_world/generator  │                │  - Viewport → PNG capture  │
│  - mystery_world/narrator   │                │  - human + agent run modes │
│  - mystery_world/npc_resp.. │                └────────────────────────────┘
│  - server.py (NEW: WS srv)  │
└─────────────────────────────┘
```

**Reused, do not modify** (read `CLAUDE.md` rule 2):
- `mystery_world/world.py`
- `mystery_world/events.py`
- `mystery_world/generator.py`
- `mystery_world/narrator.py`
- `mystery_world/npc_responder.py`
- `mystery_world/entities.py`
- `mystery_world/__init__.py`
- everything in `agents/`, `benchmark/`, `evaluation/`

**Replaced** (will be deleted in a later milestone, leave alone for now):
- `mystery_world/renderer/`

**New code you will write in this milestone**:
- `server/godot_server.py` — Python WebSocket server wrapping `MysteryEnvironment`.
- `game/` — Godot 4 project (Godot 4.3+).
- `mystery_world/godot_client.py` — thin Python client for benchmark/agent code
  to talk to a running Godot instance (or to the embedded server).

## Milestone 1 scope: "Hello-room"

Deliverable: **a human can launch the Godot game, the Python side generates a
mystery world, and the human walks around exactly one room of that world in
first person.** No NPCs, no clues, no interaction, no dialogue, no agent mode
yet. This milestone exists to nail the plumbing before we add gameplay.

### Acceptance criteria

A reviewer running on a fresh checkout, with Godot 4.3+ installed and Python
deps installed, must be able to:

1. Run `python -m server.godot_server --seed 42` — server starts on
   `ws://127.0.0.1:7777` and waits for a client.
2. Open the Godot project at `game/project.godot` and press F5 — game window
   opens, connects to the server, fetches the layout for the player's spawn
   room, and renders it in 3D.
3. Walk around with WASD + mouse look. Bumping into walls stops the player
   (AABB collision, see `CLAUDE.md` rule 11).
4. The room has the correct dimensions, walls in the correct positions, doors
   visible at the correct walls (non-functional in this milestone — they're
   just visible openings), and floor + ceiling materials.
5. Ctrl-C on the server cleanly disconnects the client.
6. Re-running with `--seed 7` produces a visibly different room (different
   shape and door layout). Determinism: same seed → identical room.

### Non-goals for this milestone (do NOT build these yet)

- NPCs, characters, dialogue, body
- Clue / weapon / object placement and pickup
- Multiple rooms / room transitions through doors
- Headless / agent mode (no `--headless` flag yet, no frame capture, no PNG)
- Fog-of-war, FOV cone, partial observability rendering
- Distribution / native exports / CI
- HF Space hosting

These come in milestones 2–5 and are listed at the end for context only.

## Protocol (WebSocket, JSON)

Keep it minimal for this milestone. One request type, one response type.

**Client → Server**:
```json
{"type": "get_current_room", "request_id": "abc123"}
```

**Server → Client**:
```json
{
  "type": "room",
  "request_id": "abc123",
  "room_id": "library",
  "name": "Library",
  "width": 12,
  "height": 9,
  "tiles": [["F","F","W",...], ...],
  "doors": [
    {"x": 5, "y": 0, "wall": "north", "leads_to": "hall"}
  ],
  "spawn": {"x": 6.5, "y": 4.5, "facing_deg": 90.0}
}
```

`tiles` is a 2D array of `"F"` (floor), `"W"` (wall), `"D"` (door cell). Read
the existing `mystery_world/renderer/layout.py` *for reference only* to
understand the tile schema — do not import it. The server should call
`build_room_layout` (or its equivalent in the current code) and serialize the
result. Coordinates are in tile units; the Godot client converts to world
units (1 tile = 2.0m is a sensible default).

## Engineering rules specific to this port (in addition to `CLAUDE.md`)

- **Python ≥ 3.11.** Use `uv` for env management; this repo already does.
- **Godot 4.3+, GL Compatibility renderer.** Targets older laptops cleanly and
  exports to web later if we want.
- **No GDScript classes named the same as Godot built-ins.**
- **Tile→world conversion is a single helper.** Don't sprinkle `* 2.0` magic
  numbers across the scene.
- **First-person camera at eye height (~1.7m).** Mouse look pitch clamped to
  ±85°. WASD moves on the XZ plane only; no flying.
- **Player collider is a CapsuleShape3D, radius ~0.35m.** This is the 3D analog
  of the 2D AABB radius — see `CLAUDE.md` rule 11.
- **Walls are static `StaticBody3D` with a `BoxShape3D` collider** matching the
  visual mesh. Build them procedurally from the tile grid; don't hand-author a
  scene per room.
- **Determinism:** the server seeds the world; the Godot side adds no
  randomness. Lighting/cosmetics may animate on wall-clock, but layout cannot.
- **No assets in this milestone beyond procedural meshes + 2 solid-color
  materials** (one for floor, one for walls). Kenney asset import is
  milestone 2. Keep this PR small.

## What to deliver

1. `server/godot_server.py` — `python -m server.godot_server` runnable, with
   `--seed` and `--port` flags. Uses the `websockets` library (add to
   `pyproject.toml`).
2. `game/project.godot` + `game/scenes/main.tscn` + `game/scripts/*.gd` —
   Godot project that connects on launch, builds the room procedurally from
   the server's response, and gives FPS controls.
3. `mystery_world/godot_client.py` — minimal async client; an `__main__` block
   that prints the current room's name as a smoke test.
4. `pyproject.toml` updated with `websockets` (and nothing else).
5. A short `prompts/godot-port-milestone-1-NOTES.md` written by you,
   documenting: (a) Godot version you targeted, (b) any deviations from this
   prompt and why, (c) how to run, (d) known limitations, (e) what milestone 2
   should pick up first.

**Do not** create a top-level README, modify the existing README, or write
any other markdown. **Do not** add CI, Dockerfiles, or export presets in this
milestone.

## Lessons from the 2D prototype (carry these forward)

These are the bug categories that ate the most time during the 2D build.
`CLAUDE.md` codifies them; flag any code you write that risks repeating them.

- **Collision was wrong** when it used the player's center tile instead of
  AABB corners. Use AABB. (`CLAUDE.md` 11)
- **Door triggers were wrong** when they required pixel-perfect alignment.
  Brushing the doorway must count. (`CLAUDE.md` 12). *Not in scope this
  milestone, but design the door geometry so triggers will fit naturally
  later.*
- **Doors must be paired across rooms.** A door on room A's north wall lines
  up with a door on room B's south wall. (`CLAUDE.md` 13). *In milestone 1 we
  only render one room, but the door positions you render must come from the
  paired layout the server returns — do not pick door cells client-side.*
- **Min-spacing between placed entities** (3 tiles in 2D). Not relevant in
  milestone 1 (no entities yet) but the 3D analog will be enforced later;
  don't bake assumptions that violate it.
- **Status text** must be compact and not duplicate visible info. (`CLAUDE.md`
  8). The HUD in this milestone shows only FPS + current room name. Nothing
  else.
- **ASCII-only HUD text.** (`CLAUDE.md` 9)
- **Determinism.** Same seed → same room shape, same door positions, every
  time. Verify before submitting.

## Follow-on milestones (context only — DO NOT implement now)

So you understand the trajectory and don't paint us into a corner:

- **M2** — Static dressing: place clue and NPC dummy meshes from
  Kenney's free CC0 3D Furniture + Character kits, driven by the world state
  the server emits.
- **M3** — Action protocol: extend WS protocol with `move`, `turn`,
  `interact`, `talk`, plus an observation message containing a rendered PNG.
  Add `--headless` mode to the Godot build for the VLM agent.
- **M4** — Partial observability: server filters observation to FOV cone +
  visited rooms; client renders fog-of-war minimap.
- **M5** — Distribution: GitHub Actions matrix building Mac/Windows/Linux
  exports + an HF Space variant. **Single-binary UX** (see below).

If a design choice in M1 would make M3 or M4 painful, say so in your NOTES
file and propose the alternative — don't silently lock us in.

## Distribution UX constraint (read before designing M1)

The end goal is that a non-technical friend downloads one zip, double-clicks
one binary, and plays. They will NEVER manually launch the Python server.
M5 will achieve this with the **auto-launched sidecar pattern**:

- The shipped Godot binary launches a PyInstaller-built `mystery_server`
  executable as a child process on startup, on a random free port.
- Godot reads the chosen port from the child's stdout, connects, and
  proceeds.
- Godot kills the child on exit (clean shutdown over WS, then SIGTERM).
- For the VLM benchmark, the auto-launch is skipped via a flag
  (`--external-server ws://...`) and the server is run independently.

You do **not** implement the sidecar in M1. But your M1 design must NOT
foreclose it. Concretely that means:

- Server reads `--port 0` and prints the chosen port to stdout in a parseable
  form (e.g. `LISTEN ws://127.0.0.1:54321`) on a single line, before any
  other output. Godot M5 will parse this.
- Server logs go to stderr, not stdout.
- Server does not require a config file or working directory; everything
  via flags / env vars.
- Server exits cleanly on SIGTERM and on WS client disconnect (if you add
  a `--single-client` flag, fine; default can stay long-lived).
- Godot's connection logic reads server URL from a CLI arg / env var, not a
  hardcoded `ws://127.0.0.1:7777`. M1 default can be that URL, but it must
  be overridable.

Separately: NPC dialogue (`mystery_world/npc_responder.py`) calls an
OpenAI-compatible endpoint. That's a *second* runtime dependency, orthogonal
to this WS server. M1 has no NPCs so it doesn't matter yet, but be aware of
the eventual plan:

- **The shipped game uses the OpenAI API with the user's own key (BYOK).**
  No hosted endpoint, no bundled local model.
- On first launch, Godot prompts the user for an OpenAI API key, stores it
  in the OS-appropriate user config dir (e.g. `~/.config/mystery-detective/
  config.json` on Linux, `~/Library/Application Support/...` on macOS,
  `%APPDATA%\...` on Windows — use a small cross-platform helper).
- When Godot spawns the Python sidecar (M5), it passes the key as the
  `OPENAI_API_KEY` env var. `npc_responder.py` already reads that env var,
  so no Python-side change is required.
- For the VLM benchmark and dev workflows, the env var is set normally; no
  config file needed.
- Server **must not** read or persist the API key itself. Treat it as
  a value supplied by the caller's environment. Don't log it.

M1 itself adds no key handling (no NPCs, no LLM calls). But do not bake any
assumption that conflicts with the above (e.g. don't read a config file in
the server, don't hardcode an endpoint URL anywhere).

## Final checklist before you submit

- [ ] Read `CLAUDE.md` and confirm every rule it lists is honored or
      explicitly N/A for this milestone.
- [ ] No edits to `mystery_world/world.py`, `events.py`, `generator.py`,
      `narrator.py`, `npc_responder.py`, `entities.py`, `__init__.py`, or
      anything in `agents/`, `benchmark/`, `evaluation/`.
- [ ] `python -m server.godot_server --seed 42` runs and stays up.
- [ ] Godot project loads, connects, renders a room, allows FPS movement.
- [ ] Same seed → same room (manually verified twice).
- [ ] AABB collision against walls works; player cannot clip through.
- [ ] HUD shows only FPS + room name, ASCII only.
- [ ] No new Python deps beyond `websockets`.
- [ ] `prompts/godot-port-milestone-1-NOTES.md` exists and is honest about
      deviations and limitations.

If you get stuck, stop and ask — do not invent requirements or modify the
Python core to make Godot's life easier.
