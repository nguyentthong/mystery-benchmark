# Project rules for AI coding agents

This file captures durable engineering rules learned while building the 2D pygame
prototype of MysteryArena. They are not specific to the 2D renderer — they
describe invariants of the *benchmark*. Any port (e.g. the Godot 3D version) must
preserve them.

## Architecture invariants

1. **Python is authoritative for world state.**
   `mystery_world/world.py`, `events.py`, `generator.py`, `narrator.py`, and
   `npc_responder.py` own the truth. Renderers visualize state — they never
   invent or mutate it. A new renderer is a *client*; it gets state and emits
   actions, nothing more.

2. **Renderer-agnostic logic stays put.** Do not touch `world.py`, `events.py`,
   `generator.py`, `narrator.py`, `npc_responder.py`, `agents/`, `benchmark/`,
   or `evaluation/` when porting. If you think you need to, ask first — the
   right answer is usually a thin adapter layer instead.

3. **One render path, two surfaces.** The same code that draws the human window
   must also produce offscreen frames for benchmark recording. Do not branch on
   "headless vs. windowed" anywhere except the topmost surface/IO layer.
   Diverging code paths will silently rot one of the two.

4. **The agent and the human play the same game.** Identical action space,
   identical observation contract, identical world. If the human can do it, the
   agent can; if the agent can do it, the human can.

## Rendering / display rules

5. **Every object/clue is always visibly drawn, regardless of evidence state.**
   Do not hide HIDDEN, DESTROYED, or unexamined items. The reasoning task is to
   *interpret* what's seen, not to spot what's missing. (Multiple bugs came
   from "smart" early-returns that hid icons.)

6. **Each object type has a recognizable, distinct icon/model.** No generic
   "?" or fallback sprite for entities that exist in the world. Cover the full
   weapon/clue pool, not just the common ones.

7. **Minimum spacing between placed entities.** Don't let objects, NPCs, and
   the player spawn cluster on top of each other. In 2D the rule is 3 tiles
   (Chebyshev). In 3D pick an analogous metric distance and enforce it on
   placement, including against the spawn point.

8. **Status/UI text is compact and non-redundant.** Don't restate what the
   frame already shows. (e.g. don't print "You moved to the Library" when the
   library is now visibly rendered.) One-line status is the default; multi-line
   only when truly new information.

9. **ASCII-safe user-visible text.** No em-dashes, smart quotes, or exotic
   punctuation in HUD/status/dialogue. Cross-platform fonts and encoders
   misrender them. Prefer `--`, `"..."`, `[...]`.

10. **Readability over density.** Fonts large enough to read on a recorded
    frame at default resolution. Dedicated sidebar for persistent status; do
    not pack it into the play area.

## Physics / interaction rules

11. **Collision uses AABB, not center-tile/center-point.** A player whose
    bounding box clips a wall is colliding, even if their center is in a free
    cell. Same for objects and NPCs.

12. **Triggers (doors, interactables) fire on AABB overlap, not exact
    center-on-tile.** Brushing a doorway counts. Requiring pixel-perfect
    alignment is a bug.

13. **Doors are paired across rooms.** A door on the north wall of room A
    connects to a door on the south wall of room B. Use the world graph to
    decide which wall, then place the partner deterministically. Don't pick
    door positions independently per room.

14. **Bodies don't move after death.** A character with `time_of_death` set is
    static at its murder location for the rest of the episode. The renderer
    must not interpolate, animate-walk, or relocate them.

15. **The murder location is fixed by world state, not by the renderer.** Read
    it; don't recompute it.

## Recording / performance rules

16. **Headless recording is the primary product.** The benchmark exists to
    record agent rollouts. Anything that breaks frame capture is a P0.

17. **Adaptive frame rate.** High FPS during action, low FPS during idle/wait
    phases. Don't burn disk on duplicate frames.

18. **Dedup identical consecutive frames before encoding.** Threaded encode is
    fine; blocking the sim loop on encode is not.

19. **Determinism: same seed → same world, same placements, same NPC
    responses.** The renderer's job is to be a deterministic function of world
    state plus seed. Animation can be wall-clock; logic cannot.

## NPC / dialogue rules

20. **NPC responders must not contradict known world state.** Witness claims
    can be false (that's the game), but they must be derived from the
    generator's alibi/witness fields, not improvised by the renderer.

21. **Don't put renderer hacks in NPC prompts.** If the dialogue is wrong, fix
    it in `npc_responder.py` / `generator.py`, not by post-processing strings
    in the renderer.

## When extending or porting

22. **Reuse the Python core; replace only the renderer.** A new renderer
    (3D, web, VR, whatever) is a client of `MysteryEnvironment`, talking over
    a thin protocol (function call, WebSocket, IPC). It is *not* a fork of the
    benchmark logic.

23. **Two run modes in one build:**
    - *Human mode*: GUI window, keyboard/mouse input, real-time.
    - *Agent mode*: headless, accepts actions over RPC, emits rendered frames
      + structured observation. Same binary, flag-selected.

24. **Distribution targets matter.** Builds must run on Mac, Windows, Linux
    natively (downloadable game) AND headless in a container (HF Space /
    benchmark CI). Don't pick tooling that breaks either.

25. **Don't add dependencies casually.** Each new package is a build-matrix
    multiplier and a security-review item. Justify it.
