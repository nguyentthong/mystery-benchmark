# MysteryArena 3D port — Milestone 1 implementation notes

## Targeted versions

- **Godot 4.3 stable** (uses `WebSocketPeer`, `CharacterBody3D`, GL Compatibility renderer). 4.4+ should also work; 4.2 untested.
- **Python 3.13** (matches `pyproject.toml` `requires-python`).
- **`websockets >= 13`** (tested with 16.0).

## How to run (developer mode)

Two terminals.

Terminal A — start the Python server:

```
cd /mnt/ssd2/thong/mystery-benchmark
uv pip install 'websockets>=13.0'           # one-time
uv run python -m server.godot_server --seed 42
# stdout: LISTEN ws://127.0.0.1:7777
# stderr: structured logs
```

Flags:
- `--seed N`              world seed (default 42)
- `--port N`              port; `0` for an OS-assigned free port (M5 sidecar mode)
- `--host H`              default `127.0.0.1`
- `--complexity LEVEL`    `TRIVIAL|EASY|MEDIUM|HARD|EXPERT` (default `EASY`)
- `--log-level LEVEL`     default `INFO`

Terminal B — verify with the Python smoke client:

```
uv run python -m mystery_world.godot_client
# connected: ws://127.0.0.1:7777
# room:      Wine Pantry (loc_727630)
# size:      18 x 13
# doors:     1
#   -  north  ->  Billiard Room
# spawn:     (9.5, 6.5) facing 0 deg
```

Or open the Godot project at `game/project.godot` and press **F5**:

- Window opens at 1280×720, mouse captured.
- Connects to `ws://127.0.0.1:7777` (override with env `MYSTERY_SERVER_URL=...` or CLI `-- --server ws://...`).
- Renders the spawn room: floor + perimeter walls + lighting.
- WASD to move, mouse to look. ESC releases mouse, click to recapture.
- HUD shows FPS and the current room name (ASCII only).

## Acceptance check (manually verified)

- [x] `python -m server.godot_server --seed 42` runs and stays up.
- [x] Stdout contains exactly `LISTEN ws://127.0.0.1:7777` — no pygame banner, no log lines (banner suppressed via `PYGAME_HIDE_SUPPORT_PROMPT=1` set before any imports). Logs go to stderr only.
- [x] `--port 0` binds a free port and the LISTEN line reflects the chosen one (e.g. `LISTEN ws://127.0.0.1:44107`).
- [x] **Determinism:** seed 42 → "Wine Pantry (loc_727630), 18×13, 1 door north → Billiard Room" twice. Seed 7 → "Attic Storage (loc_302686), 2 doors". Different seed produces a visibly different room.
- [x] Server exits cleanly on SIGINT and SIGTERM.
- [ ] **F5-in-Godot smoke test:** I have not run the Godot project on this host (Godot 4.3 binary not installed in the dev sandbox). All other acceptance criteria for the Godot side were validated by code review only — see "Things to verify on first F5 launch" below.

## Architecture

```
┌─────────────────────────────────────┐    WebSocket / JSON    ┌────────────────────────────────────────┐
│  Python                             │ ◄────────────────────► │  Godot 4 client                        │
│                                     │                        │                                        │
│  server/godot_server.py             │                        │  game/project.godot                    │
│   ├ generate_mystery(seed)          │                        │  game/scenes/main.tscn                 │
│   ├ build_room_layout (existing)    │                        │  game/scripts/main.gd                  │
│   ├ compute_door_pairings (exist.)  │                        │  game/scripts/ws_client.gd             │
│   └ JSON protocol                   │                        │  game/scripts/room_builder.gd          │
│                                     │                        │  game/scripts/player.gd                │
│  mystery_world/godot_client.py      │ (smoke test, M3+ uses) │  game/scripts/hud.gd                   │
└─────────────────────────────────────┘                        └────────────────────────────────────────┘
```

- **Python is authoritative** (`CLAUDE.md` rule 1). Godot does no procedural layout, no door pairing — it consumes whatever the server sends.
- **Tile→world conversion** lives in one place: `TILE_M = 2.0` in `main.gd`, passed into the room builder. No magic `* 2.0` elsewhere.
- **Door pairing** uses the existing `compute_door_pairings(world_state)` in `mystery_world/renderer/layout.py`, then `build_room_layout(location, sides)` for the player's spawn room. Doors come back with `wall ∈ {north, south, east, west}` already inferred so Godot doesn't need to recompute it.

## Files added

- `server/__init__.py`
- `server/godot_server.py`
- `mystery_world/godot_client.py`
- `game/project.godot`
- `game/scenes/main.tscn`
- `game/scripts/main.gd`
- `game/scripts/ws_client.gd`
- `game/scripts/room_builder.gd`
- `game/scripts/player.gd`
- `game/scripts/hud.gd`
- `prompts/godot-port-milestone-1-NOTES.md` (this file)

## Files modified

- `pyproject.toml` — added `websockets>=13.0`. No other deps.

## Files **not** modified (per `CLAUDE.md` rule 2)

- `mystery_world/world.py`
- `mystery_world/events.py`
- `mystery_world/generator.py`
- `mystery_world/narrator.py`
- `mystery_world/npc_responder.py`
- `mystery_world/entities.py`
- `mystery_world/__init__.py`
- everything in `mystery_world/renderer/`, `agents/`, `benchmark/`, `evaluation/`, `scripts/`, `examples/`, `web/`, `mystery_world.world` etc.

## Deviations from the prompt

1. **CLI argument convention for the Godot binary.** The prompt suggested
   `--server ws://...`. Godot reserves engine-level CLI flags before `--`,
   so user args are read from `OS.get_cmdline_user_args()`. The user must
   pass them after a `--` separator at run time, e.g.
   `mystery.exe -- --server ws://10.0.0.5:7777`. Both `--server URL` and
   `--server=URL` forms are accepted. `MYSTERY_SERVER_URL` env var also
   honored. Default URL is unchanged: `ws://127.0.0.1:7777`.

2. **`spawn.facing_deg` is `0.0`** (north / +X look direction). The 2D
   layout uses `default_spawn = (W//2, H//2)` and doesn't pick a facing
   direction; rather than invent one the server emits `0.0` and Godot
   yaws there. M2 will likely set facing = "look toward the door you
   entered through" once we add room transitions.

3. **No ceiling rendered.** Floor + walls + a directional light + ambient
   environment is enough for a recognisable room and keeps the visual
   simple. Adding a ceiling is a one-line change in `room_builder.gd` if
   it's wanted — left out to honour `CLAUDE.md` rule 25 (no extras).

4. **Walls are individual `BoxMesh` + `BoxShape3D` per WALL tile.** This
   produces ~50 static bodies for an 18×13 room — perfectly fine for M1.
   M3+ may want to merge runs of walls into single boxes for cleaner
   physics scenes, but the AABB collision contract (`CLAUDE.md` rule 11)
   is already exact this way.

## Known limitations / explicit M1 non-goals

- **Doors are visible openings only.** A "D" tile produces a gap in the
  wall; there is no trigger, no transition. Walking through it just
  steps into the next gridless space. M3 wires up the `MOVE` action.
- **No NPCs, clues, weapons, or interactables.** Even though the
  generator places them, the M1 server intentionally does not serialize
  `objects` or `characters` lists. The protocol field will be added in
  M2 alongside Kenney asset loading.
- **No headless / agent mode.** Godot M1 always opens a window. M3 adds
  `--headless` + frame capture + RPC actions.
- **No fog-of-war / FOV cone.** The whole spawn room is visible from
  the moment it loads. M4 layers partial-observability rendering on top.
- **No NPC-dialogue runtime dependency.** `npc_responder.py` is not
  imported by the M1 server. The OpenAI BYOK plan documented in the
  prompt's Distribution UX section applies starting M3 (when `talk`
  becomes an action).
- **No native exports.** `game/export_presets.cfg` is not committed.
  M5 will set up the GitHub Actions matrix.

## Things to verify on first F5 launch in Godot

These were not exercised in the dev sandbox because no Godot binary was
available there. A reviewer with Godot 4.3 should confirm:

1. The project opens without script errors (Godot's import pass should
   pick up the .tscn and .gd files; a `.godot/` cache will be created
   on first open — that's expected and `.gitignore`able later).
2. F5 launches the main scene, the window connects, and the HUD shows
   `Room: Wine Pantry` (with seed 42).
3. WASD moves the player on the XZ plane; mouse rotates view; ESC
   releases capture; click recaptures.
4. The player cannot clip through walls (AABB / capsule collision).
5. The player can stand inside the door cell (it's walkable) — but
   nothing happens when they do, and there's no wall blocking the
   visible opening.

If the project has script errors that I can't predict from static
review, the most likely culprit is a Godot 4.x API rename — fixes are
usually 1-line. Don't change the architecture to work around them; just
adjust the offending call.

## Where M2 should pick up first

1. **Extend the protocol.** `room` should also include `objects: [{id,
   name, x, y, kind}, ...]` and `characters: [{id, name, x, y, role}, ...]`.
   The data is already in `state.objects` / `state.characters` and
   `layout.objects` / `layout.characters`; serialize it the same way
   doors are serialized today.
2. **Mesh lookup table.** Map `kind` (e.g. "weapon:revolver",
   "object:cigar_stub", "character:suspect") to a Kenney `.glb` filename
   in `game/assets/`. Keep the table data-driven (`res://config/
   mesh_map.json`) so the implementer doesn't have to recompile to add
   one model.
3. **Min-spacing in 3D.** The 2D rule is 3 tiles Chebyshev. With
   `TILE_M = 2.0`, that's 6.0 m. Confirm the layout's existing 2D
   spacing translates correctly; the server already enforces it before
   serialization, so M2's job is just to render.
4. **Object visibility invariant** (`CLAUDE.md` rule 5). When a clue is
   `HIDDEN` or `DESTROYED`, still render its mesh in 3D. Don't filter
   client-side based on evidence state. Don't even let the server filter
   — the server should send all placed objects, with their evidence
   metadata as a side field, and let the renderer decide visual styling
   (e.g. dim colour for destroyed). This is the regression that ate the
   most time in 2D.

## Test artefacts (for reference)

```
$ uv run python -m server.godot_server --seed 42 --port 0 1>stdout.log 2>stderr.log &
$ cat stdout.log
LISTEN ws://127.0.0.1:44107

$ uv run python -m mystery_world.godot_client --url ws://127.0.0.1:44107
connected: ws://127.0.0.1:44107
room:      Wine Pantry (loc_727630)
size:      18 x 13
doors:     1
  -  north  ->  Billiard Room
spawn:     (9.5, 6.5) facing 0 deg

$ uv run python -m server.godot_server --seed 7 --port 0 1>stdout.log 2>stderr.log &
$ uv run python -m mystery_world.godot_client --url <chosen-url>
room:      Attic Storage (loc_302686)
doors:     2
  -  north  ->  Boat House
  -  south  ->  Observatory Deck
```
