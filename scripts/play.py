"""
Interactive human-player mode for MysteryArena.

Usage:
    uv run scripts/play.py                          # random MEDIUM case
    uv run scripts/play.py --level EASY --seed 7    # specific difficulty + seed
    uv run scripts/play.py --load path/to/world.json
    uv run scripts/play.py --visual --audit         # visual-temporal benchmark
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.generator import generate_mystery
from mystery_world.narrator import render_initial_briefing, render_step_observation
from mystery_world.world import AgentAction, MysteryEnvironment, WorldState
from agents.maximum_score_oracle_agent import OracleAgent


# ---------------------------------------------------------------------------
# Visual channel display (M1-M9 visual-temporal benchmark)
# ---------------------------------------------------------------------------

class VisualDisplay:
    """Live pygame window that shows the rendered post-action observation.

    Decode the PNG bytes returned by env.step().image, upscale to fill the
    window, blit, flip. Pumps events between updates so the window stays
    responsive; the player still drives the game from the terminal REPL.
    """

    def __init__(self, window_size: tuple[int, int] = (720, 720)) -> None:
        # Force a real SDL driver -- the renderer's headless _ensure_pygame
        # sets the dummy driver as a default, but we need a visible window
        # here. Drop both env vars first so SDL picks a real driver.
        os.environ.pop("SDL_VIDEODRIVER", None)
        os.environ.pop("SDL_AUDIODRIVER", None)
        import pygame
        self._pygame = pygame
        pygame.display.init()
        pygame.font.init()
        self.window_size = window_size
        self.screen = pygame.display.set_mode(window_size)
        pygame.display.set_caption("MysteryArena -- visual channel")
        self.font = pygame.font.SysFont(None, 22)

    def update(self, png_bytes: bytes | None, status: str = "") -> None:
        if png_bytes is None:
            return
        pygame = self._pygame
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        surf = pygame.image.frombuffer(img.tobytes(), img.size, "RGB")
        win_w, win_h = self.window_size
        text_h = 30
        max_h = win_h - text_h
        ratio = min(win_w / img.size[0], max_h / img.size[1])
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        surf = pygame.transform.smoothscale(surf, new_size)
        self.screen.fill((18, 16, 22))
        x = (win_w - new_size[0]) // 2
        y = (max_h - new_size[1]) // 2
        self.screen.blit(surf, (x, y))
        if status:
            text = self.font.render(status, True, (220, 220, 220))
            self.screen.blit(text, (8, win_h - text_h + 5))
        pygame.display.flip()
        # Drain events so the OS doesn't mark the window as unresponsive.
        for _ in pygame.event.get():
            pass

    def update_clip(
        self,
        frames: list[bytes],
        status: str = "",
        per_frame_ms: int = 200,
    ) -> None:
        """Play N frames sequentially, settling on the last."""
        pygame = self._pygame
        if not frames:
            return
        for f in frames[:-1]:
            self.update(f, status + " [clip]")
            pygame.time.wait(per_frame_ms)
        self.update(frames[-1], status)

    def close(self) -> None:
        self._pygame.display.quit()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

DIVIDER = "─" * 70
THICK   = "═" * 70

def _print_box(text: str) -> None:
    print(f"\n{THICK}")
    print(text)
    print(THICK)

def _print_result(text: str) -> None:
    print(f"\n{DIVIDER}")
    print(text)
    print(DIVIDER)

HELP_TEXT = """
COMMANDS
  look                          — examine your surroundings
  go <location>                 — move to an adjacent room
  examine <object>              — inspect an object closely
  talk <name>                   — start / continue an interview (you will be prompted for a question)
  take <object>                 — pick up a portable object
  inventory                     — review evidence you have collected
  wait                          — let time pass (costs one action)
  accuse                        — make your final accusation (you will be prompted)
  map                           — show the estate map and your current position
  suspects                      — list all suspects
  hint                          — ask the oracle agent for its recommended next action
  save                          — save current session to --save-dir
  help                          — show this message
  quit                          — save and exit without finishing
"""

LEVEL_NAMES = {lvl.name: lvl for lvl in ComplexityLevel}


# ---------------------------------------------------------------------------
# Command parser
# ---------------------------------------------------------------------------

def _parse_command(raw: str) -> tuple[AgentAction, dict] | None:
    """
    Convert a natural-language command into (AgentAction, kwargs).
    Returns None for special commands (help, map, quit, suspects) handled
    outside the env.step() loop.
    """
    tokens = raw.strip().split()
    if not tokens:
        return None
    verb = tokens[0].lower()
    rest = " ".join(tokens[1:])

    if verb in ("look", "l", "examine") and (not rest or rest in ("room", "around", "location")):
        return AgentAction.EXAMINE_LOCATION, {}

    if verb in ("go", "move", "walk", "run"):
        return AgentAction.MOVE, {"target_location": rest}

    if verb in ("examine", "inspect", "x") and rest:
        return AgentAction.EXAMINE_OBJECT, {"object_name": rest}

    if verb in ("wait", "w"):
        return AgentAction.WAIT, {}

    if verb in ("inventory", "inv", "i"):
        return AgentAction.CHECK_INVENTORY, {}

    if verb in ("take", "grab", "pick"):
        obj = rest.removeprefix("up ").strip()
        return AgentAction.TAKE_OBJECT, {"object_name": obj}

    # "talk <name>" or "ask <name>"
    if verb in ("talk", "ask", "interview", "question"):
        return AgentAction.TALK_TO, {"character_name": rest, "_needs_question": True}

    # "accuse" — handled interactively in the main loop
    if verb in ("accuse", "arrest", "charge"):
        return AgentAction.ACCUSE, {"_interactive": True}

    return None   # unknown / handled by caller


# ---------------------------------------------------------------------------
# Main game loop
# ---------------------------------------------------------------------------

def play(
    env: MysteryEnvironment,
    save_dir: Path | None = None,
    display: VisualDisplay | None = None,
) -> None:
    state = env.state

    # Build quick-reference data
    suspect_list = [
        c for c in state.characters.values()
        if any(r.name == "SUSPECT" for r in c.roles) and c.is_alive
    ]
    location_map: dict[str, list[str]] = {
        loc.name: [state.locations[a].name for a in loc.adjacent_ids if a in state.locations]
        for loc in state.locations.values()
    }

    def _show_map() -> None:
        print(f"\n{'=== ESTATE MAP ==='}")
        for name, exits in location_map.items():
            marker = " ◄ YOU" if name == state.locations.get(env.agent_location_id, type("", (), {"name": ""})()).name else ""
            print(f"  {name}{marker}")
            if exits:
                print(f"    └─ exits: {', '.join(exits)}")

    def _show_suspects() -> None:
        print(f"\n{'=== SUSPECTS ==='}")
        for s in suspect_list:
            loc = state.locations.get(s.location_id)
            loc_name = loc.name if loc else "unknown"
            print(f"  • {s.full_name}  (last seen: {loc_name})")

    def _show_hint() -> None:
        """Run the oracle against the current env state and print its next action."""
        print("\n[Oracle] Analysing the case ...", flush=True)
        oracle = OracleAgent()
        oracle.initialize(env, "")
        action, kwargs = oracle.decide_action("")
        # Human-readable rendering of the recommended action
        msgs = {
            AgentAction.MOVE:            lambda k: f"go {k.get('target_location', '?')}",
            AgentAction.EXAMINE_OBJECT:  lambda k: f"examine {k.get('object_name', '?')}",
            AgentAction.EXAMINE_LOCATION:lambda k: "look",
            AgentAction.TALK_TO:         lambda k: f"talk {k.get('character_name', '?')}",
            AgentAction.ACCUSE:          lambda k: (
                f"accuse {k.get('suspect_name','?')} "
                f"with {k.get('weapon_name','?')} "
                f"in {k.get('location_name','?')}"
            ),
        }
        render = msgs.get(action, lambda k: f"{action.name} {k}")
        print(f"[Oracle] Best next action: {render(kwargs)}")

    def _interactive_accuse() -> dict:
        print("\nYou are about to make your final accusation. This ends the game.")

        # Show inventory so the player has evidence IDs at hand
        if env._discovered_evidence:
            print("\n--- Your evidence ---")
            for eid in env._discovered_evidence:
                ev = state.evidence.get(eid)
                if ev:
                    print(f"  [{eid}] {ev.name}: {ev.description}")
        else:
            print("(No evidence collected yet.)")

        print()
        print("Suspects :", ", ".join(s.full_name for s in suspect_list))
        weapons = [o.name for o in state.objects.values() if o.is_weapon]
        print("Weapons  :", ", ".join(weapons))
        print("Locations:", ", ".join(l.name for l in state.locations.values()))

        def _ask(prompt: str) -> str:
            return input(prompt).strip().strip(",.;")

        def _ask_ids(prompt: str) -> list[str]:
            raw = input(prompt).strip()
            return [x.strip() for x in raw.split(",") if x.strip()] if raw else []

        suspect  = _ask("\nWho did it?          > ")
        weapon   = _ask("What weapon?         > ")
        location = _ask("Where did it happen? > ")

        kwargs: dict = {
            "suspect_name":  suspect,
            "weapon_name":   weapon,
            "location_name": location,
        }

        # Triangle evidence
        print("\n--- Locard triangle evidence (enter IDs from inventory, comma-separated, or blank) ---")
        sw = _ask_ids("Suspect ↔ weapon evidence (e.g. fingerprints on weapon)? > ")
        wv = _ask_ids("Weapon  ↔ victim evidence (e.g. blood on weapon)?        > ")
        sr = _ask_ids("Suspect ↔ murder room evidence (e.g. footprints)?        > ")
        if sw:
            kwargs["suspect_weapon_evidence"] = sw
        if wv:
            kwargs["weapon_victim_evidence"] = wv
        if sr:
            kwargs["suspect_room_evidence"] = sr

        # Alibi contradiction
        print("\n--- Alibi contradiction ---")
        # Always show culprit's actual alibi claims so the player can copy exact text
        culprit_char = state.get_culprit()
        if culprit_char and culprit_char.alibi_claims:
            for claim in culprit_char.alibi_claims:
                print(f"  Culprit claimed: {claim.location_name} at {claim.clock_time_str}")
        claimed_loc  = _ask("Claimed location?                        > ")
        claimed_time = _ask("Claimed time?                            > ")
        contra_ids   = _ask_ids("Which evidence contradicts this? (IDs)   > ")
        if claimed_loc or claimed_time or contra_ids:
            kwargs["alibi_contradiction"] = {
                "claimed_location":       claimed_loc,
                "claimed_time":           claimed_time,
                "contradiction_evidence": contra_ids,
            }

        # Eliminations — show available SE evidence so the player has the right IDs
        print("\n--- Innocent-suspect eliminations (blank suspect name to finish) ---")
        from mystery_world.entities import EdgeType as _ET2
        for ev in state.evidence.values():
            if (
                ev.relevance is not None
                and ev.relevance.edge_type == _ET2.SUSPECT_ELSEWHERE
                and not ev.is_red_herring
                and ev.linked_character_id
            ):
                char = state.characters.get(ev.linked_character_id)
                corr = state.characters.get(ev.corroborator_id) if ev.corroborator_id else None
                if char and not char.is_culprit:
                    print(
                        f"  [{ev.id}] {char.full_name} was elsewhere"
                        + (f" — witnessed by {corr.full_name}" if corr else "")
                    )
        eliminations: dict = {}
        while True:
            name = _ask("  Suspect name (blank to finish)?           > ")
            if not name:
                break
            eid  = _ask("  Evidence placing them elsewhere (ID)?     > ")
            corr = _ask("  Who witnessed / corroborates?             > ")
            eliminations[name] = {"evidence_id": eid, "corroborator": corr}
        if eliminations:
            kwargs["eliminations"] = eliminations

        return kwargs

    # ── Initial briefing ────────────────────────────────────────────────
    briefing = render_initial_briefing(env)
    _print_box(briefing)

    # Initial visual observation
    if display is not None:
        loc = env.get_current_location()
        loc_name = loc.name if loc else "?"
        display.update(
            env.get_observation_image(),
            status=f"{loc_name}  |  step {env.state.current_step}  |  budget {env.budget_remaining}",
        )

    # ── REPL ────────────────────────────────────────────────────────────
    while not env.is_solved:
        budget = env.budget_remaining
        loc = env.get_current_location()
        loc_name = loc.name if loc else "?"

        prompt = f"\n[{loc_name} | budget: {budget}] > "
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGame aborted.")
            return

        if not raw:
            continue

        low = raw.lower()

        # Special non-action commands
        if low in ("help", "h", "?"):
            print(HELP_TEXT)
            continue

        if low == "map":
            _show_map()
            continue

        if low in ("suspects", "suspect list"):
            _show_suspects()
            continue

        if low in ("hint", "oracle", "best"):
            _show_hint()
            continue

        if low in ("save", "s"):
            if save_dir:
                saved = env.save_session(save_dir)
                print(f"Session saved to {saved}")
            else:
                print("No save directory configured. Restart with --save-dir <path>.")
            continue

        if low in ("quit", "exit", "q"):
            if save_dir:
                saved = env.save_session(save_dir)
                print(f"Session saved to {saved}")
            print("You leave the case unsolved.")
            return

        # Parse into action
        parsed = _parse_command(raw)

        if parsed is None:
            print("Unknown command. Type 'help' for a list of commands.")
            continue

        action, kwargs = parsed

        # --- TALK_TO: prompt for question ---
        if action == AgentAction.TALK_TO and kwargs.pop("_needs_question", False):
            char_name = kwargs["character_name"]
            if not char_name:
                print("Who do you want to talk to?")
                continue
            question = input(f'What do you ask {char_name}? > ').strip()
            if not question:
                question = "Where were you at the time of the murder?"
            kwargs["question"] = question

        # --- ACCUSE: interactive prompt ---
        if action == AgentAction.ACCUSE and kwargs.pop("_interactive", False):
            kwargs = _interactive_accuse()
            confirm = input(
                f"\nAccuse {kwargs['suspect_name']!r} with {kwargs['weapon_name']!r}"
                f" in {kwargs['location_name']!r}? [y/N] > "
            ).strip().lower()
            if confirm != "y":
                print("Accusation cancelled.")
                continue

            # Bypass discovery/interview requirements for human players.
            # Rule: if the player cites at least one *valid* ID for a triangle
            # edge, inject all valid IDs for that edge so recall = 1.0 and the
            # edge earns full F1 credit. Evidence validity mirrors the oracle's
            # filter (non-red-herring, correct edge, fresh, matches ground truth).
            from mystery_world.world import _relevance_matches_truth
            from mystery_world.entities import EdgeType as _ET

            murder_ts  = state.murder_timestamp
            threshold  = state.freshness_threshold

            def _valid_ids_for_edge(edge: _ET) -> set[str]:
                return {
                    ev.id for ev in state.evidence.values()
                    if not ev.is_red_herring
                    and ev.relevance is not None
                    and ev.relevance.edge_type == edge
                    and abs(ev.relevance.contact_timestamp - murder_ts) < threshold
                    and _relevance_matches_truth(ev.relevance, edge, state)
                }

            for kwarg_key, edge in (
                ("suspect_weapon_evidence", _ET.SUSPECT_WEAPON),
                ("weapon_victim_evidence",  _ET.WEAPON_VICTIM),
                ("suspect_room_evidence",   _ET.SUSPECT_ROOM),
            ):
                cited = set(kwargs.get(kwarg_key) or [])
                if not cited:
                    continue
                valid = _valid_ids_for_edge(edge)
                if cited & valid:               # at least one correct ID cited
                    all_ids = list(valid)
                    kwargs[kwarg_key] = all_ids
                    env._discovered_evidence.update(all_ids)
                else:
                    env._discovered_evidence.update(cited)  # let scoring penalise wrong IDs

            # Alibi contra evidence
            env._discovered_evidence.update(
                (kwargs.get("alibi_contradiction") or {}).get("contradiction_evidence") or []
            )
            # Elimination evidence + auto-interview corroborators
            for elim_claim in (kwargs.get("eliminations") or {}).values():
                eid = elim_claim.get("evidence_id", "")
                if eid:
                    env._discovered_evidence.add(eid)
                corr_name = elim_claim.get("corroborator", "")
                if corr_name:
                    corr_char = next(
                        (c for c in state.characters.values()
                         if c.full_name.lower() == corr_name.lower()),
                        None,
                    )
                    if corr_char:
                        env._interviewed_characters.add(corr_char.id)
            # Populate alibi claims if the player never interviewed the culprit
            if kwargs.get("alibi_contradiction") and not env._revealed_alibi_claims:
                culprit = state.get_culprit()
                if culprit:
                    for claim in culprit.alibi_claims:
                        env._revealed_alibi_claims.append({
                            "character": culprit.full_name,
                            "location":  claim.location_name,
                            "time":      claim.clock_time_str,
                        })

        # --- Execute ---
        result = env.step(action, **kwargs)
        obs = render_step_observation(env, result.observation)
        _print_result(obs)

        # Refresh the visual channel after every action so the agent's image
        # observation tracks the env's current_step.
        if display is not None:
            loc = env.get_current_location()
            loc_name = loc.name if loc else "?"
            status = (
                f"{loc_name}  |  step {env.state.current_step}  |  "
                f"budget {env.budget_remaining}  |  {action.name}"
            )
            if len(result.frames) > 1:
                display.update_clip(result.frames, status=status)
            else:
                display.update(result.image, status=status)

    # ── Auto-save on episode end ─────────────────────────────────────────
    if save_dir:
        saved = env.save_session(save_dir)
        print(f"\nSession auto-saved to {saved}")

    # ── End screen ──────────────────────────────────────────────────────
    summary = env.get_episode_summary()
    culprit  = state.get_culprit()
    weapon   = state.objects.get(state.murder_weapon_id)
    murder_loc = state.locations.get(state.murder_location_id)

    _print_box(
        f"{'CASE CLOSED' if summary['accusation_correct'] else 'CASE FAILED'}\n"
        f"\n"
        f"  True answer : {culprit.full_name if culprit else '?'}"
        f"  with the {weapon.name if weapon else '?'}"
        f"  in the {murder_loc.name if murder_loc else '?'}\n"
        f"\n"
        f"  Actions used : {summary['actions_taken']} / {summary['budget']}\n"
        f"  Evidence found : {len(summary['evidence_discovered'])} / {summary['total_evidence']}\n"
        f"  Interviews : {len(summary['characters_interviewed'])} character(s) questioned\n"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Play a mystery case interactively")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--load", metavar="FILE", help="Load a saved world JSON")
    group.add_argument(
        "--example",
        metavar="ID",
        help="Play a benchmark example by ID, e.g. trivial_seed_0. "
             "Run 'python scripts/list_examples.py' to see all available IDs.",
    )
    group.add_argument(
        "--level",
        default="MEDIUM",
        choices=list(LEVEL_NAMES),
        help="Complexity level for a fresh case (default: MEDIUM)",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed (random if omitted)")
    parser.add_argument(
        "--npc-provider",
        default="fallback",
        choices=["fallback", "openai", "openrouter", "vllm"],
        help="NPC backend: fallback (deterministic, no LLM), openai (api.openai.com, "
             "needs OPENAI_API_KEY), openrouter (openrouter.ai, needs OPENROUTER_API_KEY), "
             "vllm (custom OpenAI-compatible URL via --npc-url).",
    )
    parser.add_argument(
        "--npc-url",
        default=None,
        help="OpenAI-compatible base URL (only used when --npc-provider vllm). "
             "Example: http://localhost:8123/v1.",
    )
    parser.add_argument(
        "--npc-model",
        default="gpt-4o-mini",
        help="NPC model name (default: gpt-4o-mini for openai; pass any model for other providers).",
    )
    parser.add_argument(
        "--npc-seed",
        type=int,
        default=42,
        help="Fixed seed for NPC responses (default: 42)",
    )
    parser.add_argument(
        "--save-dir",
        default=None,
        metavar="DIR",
        help="Directory to save session (world + transcript). Auto-saved on quit/game-over.",
    )
    parser.add_argument(
        "--visual",
        action="store_true",
        help="Enable the visual-temporal benchmark variant: visual_mode=True "
             "(text channel strips freshness / death-time vocabulary) plus a "
             "live pygame window showing the rendered post-action observation.",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Use audit_temporal_necessity=True when generating, so the chosen "
             "episode is guaranteed to surface a cross-observation VisualState "
             "change (M5). Implied recommended with --visual.",
    )
    parser.add_argument(
        "--clip-frames",
        type=int,
        default=1,
        metavar="N",
        help="Clip frame count for time-elapsed actions (WAIT, MOVE, TALK_TO) "
             "under --visual. N=1 (default) returns a single image per action; "
             "N>=2 plays a short clip in the window.",
    )
    args = parser.parse_args()

    import datetime
    import json

    EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

    if args.load:
        world_state = WorldState.load(args.load)
        print(f"Loaded world from {args.load}  (seed={world_state.seed})")
    elif args.example:
        example_path = EXAMPLES_DIR / f"{args.example}.json"
        if not example_path.exists():
            available = sorted(p.stem for p in EXAMPLES_DIR.glob("*.json"))
            print(f"Example '{args.example}' not found. Available examples:")
            for eid in available:
                print(f"  {eid}")
            sys.exit(1)
        ex_data = json.loads(example_path.read_text())
        seed  = ex_data["seed"]
        level = LEVEL_NAMES[ex_data["complexity"].upper()]
        config = COMPLEXITY_PRESETS[level]
        multi = "  (multi-evidence)" if ex_data.get("multi_evidence") else ""
        print(f"Loading example '{args.example}' — {ex_data['complexity']} case, seed={seed}{multi} ...")
        world_state = generate_mystery(
            config, seed, audit_temporal_necessity=args.audit
        )
    else:
        import random
        seed = args.seed if args.seed is not None else random.randint(0, 999999)
        level = LEVEL_NAMES[args.level.upper()]
        config = COMPLEXITY_PRESETS[level]
        if args.audit:
            print(f"Generating an AUDITED {args.level} mystery (seed={seed}) ...")
        else:
            print(f"Generating a {args.level} mystery (seed={seed}) ...")
        world_state = generate_mystery(
            config, seed, audit_temporal_necessity=args.audit
        )

    # Optional clip dispatch (M9) for time-elapsed actions under --visual.
    clip_dispatch: dict[AgentAction, int] | None = None
    if args.visual and args.clip_frames > 1:
        clip_dispatch = {
            AgentAction.WAIT:    args.clip_frames,
            AgentAction.MOVE:    args.clip_frames,
            AgentAction.TALK_TO: args.clip_frames,
        }

    env = MysteryEnvironment(
        world_state,
        visual_mode=args.visual,
        clip_dispatch=clip_dispatch,
    )

    # Determine save directory
    save_dir: Path | None = None
    if args.save_dir:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        save_dir = Path(args.save_dir) / f"seed{world_state.seed}_{ts}"
        print(f"Session will be saved to: {save_dir}")

    if args.npc_provider != "fallback":
        from mystery_world.npc_responder import NPCResponder
        if args.npc_provider == "openai":
            responder = NPCResponder(base_url=None, model=args.npc_model,
                                     seed=args.npc_seed, api_key_env="OPENAI_API_KEY")
            print(f"NPC interviews: {args.npc_model} via OpenAI")
        elif args.npc_provider == "openrouter":
            responder = NPCResponder(base_url="https://openrouter.ai/api/v1",
                                     model=args.npc_model, seed=args.npc_seed,
                                     api_key_env="OPENROUTER_API_KEY")
            print(f"NPC interviews: {args.npc_model} via OpenRouter")
        else:  # vllm
            if not args.npc_url:
                print("--npc-provider vllm requires --npc-url")
                sys.exit(1)
            responder = NPCResponder(base_url=args.npc_url, model=args.npc_model, seed=args.npc_seed)
            print(f"NPC interviews: {args.npc_model} @ {args.npc_url}")
        env.set_npc_responder(responder)
    else:
        print("NPC interviews: deterministic fallback (pass --npc-provider to use an LLM)")

    # Open the visual window if --visual was passed. If pygame can't reach
    # a display (headless server, no $DISPLAY), fall back to text-only and
    # warn instead of crashing.
    display: VisualDisplay | None = None
    if args.visual:
        try:
            display = VisualDisplay()
            print("Visual channel: pygame window open.")
        except Exception as exc:
            print(
                f"WARNING: could not open pygame display ({exc}). Falling back "
                f"to text-only. (Try unsetting SDL_VIDEODRIVER, or use "
                f"`python scripts/save_visual_frames.py` for headless setups.)"
            )

    try:
        play(env, save_dir=save_dir, display=display)
    finally:
        if display is not None:
            display.close()


if __name__ == "__main__":
    main()
