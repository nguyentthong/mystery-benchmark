# MysteryArena 3D port — implementation notes

This document covers what's been built across what was originally split as
Milestones 1, 2, 3, 4, and partial 5. Scope grew because the user wanted a
playable detective game, not just a walking-simulator stub.

## Targeted versions

- **Godot 4.3 stable** (uses `WebSocketPeer`, `CharacterBody3D`, GL
  Compatibility renderer). 4.4+ should also work; 4.2 untested.
- **Python 3.13** (matches `pyproject.toml`).
- **`websockets >= 13`** (tested with 16.0).
- macOS (Apple Silicon or Intel) verified as a dev target. Linux/Windows
  exports not yet built.

## What works

### Game flow
- Procedurally generated mystery world driven by the existing
  `mystery_world/generator.py` (unchanged). Seed-deterministic.
- First-person 3D exploration of the spawn room and any room reachable
  through doors. Room transitions are seamless: walk into a door's floor
  patch, the world rebuilds, you respawn just inside the connected room.
- Visited rooms are tracked server-side and shown on a HUD minimap
  (top-right): `[*]` current, `[x]` visited, `[ ]` unvisited.
- Object props rendered as Kenney `.glb` meshes when assets are installed,
  otherwise tinted boxes (red = weapon, dark red = murder weapon, tan =
  generic object). Murder weapon hue is just for debug feedback — the agent
  doesn't get told which weapon is the murder weapon, only that something
  is "a weapon."
- Characters rendered as Kenney humanoids (or capsules as fallback). Alive
  NPCs stand; the victim's body lies flat on the floor (CLAUDE.md rule 14).
  Color-coded labels: blue (suspect), green (innocent), tan (witness),
  red (victim/body).
- Crosshair + camera-forward raycast picks out the focused entity, with a
  hover prompt at the bottom of the screen (`[E] Examine X`, `[E] Talk to X`).
- E key examines an object or opens a chat with an NPC.
- TAB toggles the inventory panel (lists collected evidence).
- F opens the accusation modal (suspect / weapon / location text inputs;
  ENTER on Location to submit).
- ESC releases mouse capture / closes the open modal.
- Accusation result panel shows CORRECT or INCORRECT with the partial
  score breakdown.

### Server (Python)
- `server/godot_server.py` is a WebSocket server wrapping `MysteryEnvironment`.
  All actions are translated into `env.step(AgentAction.*)` calls, so the
  benchmark scoring stays identical between the Godot client and the LLM
  agents in `agents/`.
- LISTEN line on stdout for the M5 sidecar contract; everything else on
  stderr (CLAUDE.md rules 8 + 9 about log discipline).
- `OPENAI_API_KEY` env var → an NPCResponder is attached automatically and
  TALK_TO returns LLM-generated NPC dialogue. Without the key, the
  deterministic template fallback in the env is used.
- Visited rooms tracked in the server wrapper. World graph + visited bits
  are bundled into every `room` payload so the minimap stays fresh
  without a separate request.

### Asset pipeline (Kenney)
- `scripts/download_kenney_assets.sh /path/to/*.zip` extracts already-
  downloaded Kenney CC0 packs (Furniture Kit, Mini Characters, Weapon Pack)
  into `game/assets/kenney/` organised by kit. With no args it prints the
  manual-download instructions and reports how many assets are present.
- `game/config/asset_map.json` maps logical entity kinds + name patterns
  to relative `.glb` paths under the assets dir. Substring matching on
  object names ("kitchen cleaver" matches `cleaver` → `weapons/knife.glb`).
  Edit freely — no code change needed.
- `room_builder.gd` tries the mapped path; if the file isn't present
  (`ResourceLoader.exists` → false), falls back to the colored cube/capsule.
  This means the game works end-to-end before any assets are downloaded.

## How to run (developer mode)

Two terminals.

Terminal A — start the Python server:

```
cd /path/to/mystery-benchmark
uv sync                                       # installs websockets etc.
export OPENAI_API_KEY=sk-...                  # optional but recommended
uv run python -m server.godot_server --seed 42
# stdout: LISTEN ws://127.0.0.1:7777
```

Server flags:
- `--seed N`              world seed (default 42)
- `--port N`              port; `0` for an OS-assigned free port
- `--host H`              default `127.0.0.1`
- `--complexity LEVEL`    `TRIVIAL|EASY|MEDIUM|HARD|EXPERT` (default `EASY`)
- `--npc-model MODEL`     OpenAI-compatible model id (default `gpt-4o-mini`)
- `--log-level LEVEL`     default `INFO`

Terminal B — Godot:

```
/Applications/Godot.app/Contents/MacOS/Godot --path /path/to/mystery-benchmark/game
```

Or open `Godot.app`, **Import** → `mystery-benchmark/game/project.godot`,
then F5.

## Controls

- WASD — move
- Mouse — look (captured by default)
- E — interact (examine object / talk to NPC)
- TAB — toggle inventory
- F — make accusation
- ESC — release mouse capture / close current modal

## Optional: install Kenney 3D assets

The game ships playable with colored cubes/capsules. For richer visuals:

```
# 1. Visit https://kenney.nl/assets and download (free / CC0):
#      - Furniture Kit
#      - Mini Characters Kit (or Character Kit)
#      - Blaster Kit (or Weapon Pack)
# 2. Extract them into the assets directory:
./scripts/download_kenney_assets.sh ~/Downloads/kenney_*.zip
# 3. Restart Godot.
```

If the actual `.glb` filenames inside the Kenney packs differ from the
defaults in `game/config/asset_map.json`, edit that JSON to point at the
real names. Substring matching is forgiving.

## WebSocket protocol summary

Client → Server:

| Message            | Fields                                              |
|--------------------|-----------------------------------------------------|
| ping               | request_id                                          |
| get_current_room   | request_id                                          |
| move_to_room       | request_id, target_location_id                      |
| examine_object     | request_id, object_name                             |
| talk_to            | request_id, character_name, question                |
| take_object        | request_id, object_name                             |
| inventory          | request_id                                          |
| accuse             | request_id, suspect_name, weapon_name, location_name|
| world_graph        | request_id                                          |

Server → Client:

| Message            | Fields                                              |
|--------------------|-----------------------------------------------------|
| pong               | request_id                                          |
| room               | request_id, name, room_id, w/h, tiles, doors[],     |
|                    | objects[], characters[], spawn{}, world_graph[]     |
| action_result      | request_id, success, observation, evidence_found[]  |
| inventory          | request_id, items[]                                 |
| accusation_result  | request_id, correct, observation, details{}         |
| world_graph        | request_id, locations[]                             |
| error              | request_id, error                                   |

Coordinate convention: tile (x, y) becomes world (x * 2, *, y * 2). 1 tile
= 2 metres. tile X = world X (east), tile Y = world Z (south), Y is up.

## Files added

- `CLAUDE.md` (root)                                      engineering rules
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
- `game/config/asset_map.json`
- `game/assets/kenney/.gitkeep`
- `scripts/download_kenney_assets.sh`
- `prompts/godot-port-milestone-1.md`
- `prompts/godot-port-milestone-1-NOTES.md` (this file)

## Files modified

- `pyproject.toml` — added `websockets>=13.0`. No other deps.

## Files **not** modified (per `CLAUDE.md` rule 2)

- `mystery_world/world.py`, `events.py`, `generator.py`, `narrator.py`,
  `npc_responder.py`, `entities.py`, `__init__.py`
- `mystery_world/renderer/*` (still ships for the 2D web build)
- `agents/`, `benchmark/`, `evaluation/`, `scripts/play.py`, `examples/`,
  `web/`, `main.py`

## What's deliberately not built yet

- **In-room FOV cone / occlusion**. Cross-room visibility is already
  filtered by `MysteryEnvironment.observe_location()`. Within a room, our
  small props don't occlude meaningfully; adding ray-cast occlusion is
  trivial later if we add wardrobes / shelves / large furniture that
  warrants it.
- **TAKE_OBJECT** is wired in the protocol but no key is bound for it in
  the UI. Easy to add (e.g., extend the focus prompt to `[E] Examine | [G]
  Take`).
- **CHECK_ROUTE / TRAVEL_TIME / ANALYZE** actions exist in the env but
  aren't surfaced in the UI yet. The protocol can be extended without any
  Python-side change.
- **VLM agent mode** (`--headless` Godot, frame capture, RPC actions for
  benchmark recording). The protocol is already shaped to support it; what
  remains is wiring Godot's headless `--render` flag and a frame-capture
  loop that emits PNGs alongside `room`/`action_result` messages.
- **Native exports** (`.app` / `.exe` / Linux binaries) and the M5 sidecar
  pattern (Godot binary auto-launches PyInstaller-built server). Server
  already honours `--port 0` + LISTEN sentinel, so the sidecar pattern is
  unblocked.
- **First-launch OpenAI key prompt** in Godot, persisting to user config
  dir. Currently the user sets `OPENAI_API_KEY` in the server's environment
  before launch.

## Acceptance check (manually verified)

- [x] `python -m server.godot_server --seed 42` runs and stays up.
- [x] Stdout contains exactly one `LISTEN ws://...` line; no banner, no
      log spam (`PYGAME_HIDE_SUPPORT_PROMPT=1` is set before any imports).
- [x] `--port 0` binds a free port and the LISTEN line reflects it.
- [x] Determinism: seed 42 → "Wine Pantry, kitchen cleaver, lipstick-stained
      glass, wicker basket, wall mirror; Gareth Greystone alive, Linnea Juno
      dead." Same on every run.
- [x] Different seed yields a different room.
- [x] examine_object returns a description.
- [x] talk_to with a question triggers the env's interview path; multi-turn
      history is preserved per character.
- [x] accuse with the correct triple returns `correct=True`; with a wrong
      triple returns `correct=False` and reveals the actual culprit.
- [x] world_graph payload reflects visited rooms growing as the agent moves.
- [x] inventory returns `[]` when empty.
- [x] Server exits cleanly on SIGINT/SIGTERM.
- [ ] **Godot end-to-end:** all UI panels (chat, inventory, accuse,
      result) build and show correctly. Not validated on the dev sandbox
      (no Godot binary). Tested by code review only — see "Godot
      first-launch verification" below.

## Godot first-launch verification (please run)

On macOS, after starting the server in Terminal A:

1. `Godot --path .../game` — window opens, HUD shows "Connecting...".
2. After ~1 sec the room renders. Wine Pantry. Crosshair visible. Hover
   prompts appear when looking at objects/NPCs.
3. Press E on the suspect (Gareth Greystone): chat panel opens, mouse
   visible. Type a question, ENTER. Reply appears. Multiple turns work.
   ESC closes the chat, mouse re-captures.
4. Press TAB: inventory panel says `(0 items)`. TAB or ESC to close.
5. Walk into the door floor-patch (north wall): minimap shows Wine Pantry
   becoming `[x]` visited, Billiard Room becomes `[*]`. New room renders.
6. Explore further. Find the marble bookend (the murder weapon).
7. Press F: accuse modal opens. Type:
     Suspect:  `Gareth Greystone`
     Weapon:   `marble bookend`
     Location: `Wine Pantry`
   ENTER on Location. Result panel: `CORRECT accusation`, partial_score
   `1.00 / 1.00`.
8. ESC dismisses the result panel. Game-over state — you can keep walking
   but the env's `is_solved` is True.

If any of this fails, paste the Godot Output panel contents here.
