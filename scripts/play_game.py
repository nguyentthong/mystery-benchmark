"""Graphical (2D top-down) human-player mode for MysteryArena.

Same case selection options as scripts/play.py — but instead of a CLI REPL,
launches a pygame window where the player drives an avatar with WASD, presses
E to interact, and ESC to open the menu.

Usage:
    uv run scripts/play_game.py                          # random MEDIUM case
    uv run scripts/play_game.py --level EASY --seed 7
    uv run scripts/play_game.py --example trivial_seed_0
    uv run scripts/play_game.py --load path/to/world.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.generator import generate_mystery
from mystery_world.renderer.game import MysteryGame
from mystery_world.world import MysteryEnvironment, WorldState

LEVEL_NAMES = {lvl.name: lvl for lvl in ComplexityLevel}
EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _load_world(args: argparse.Namespace) -> WorldState:
    if args.load:
        ws = WorldState.load(args.load)
        print(f"Loaded world from {args.load}  (seed={ws.seed})")
        return ws

    if args.example:
        path = EXAMPLES_DIR / f"{args.example}.json"
        if not path.exists():
            available = sorted(p.stem for p in EXAMPLES_DIR.glob("*.json"))
            print(f"Example '{args.example}' not found. Available:")
            for eid in available:
                print(f"  {eid}")
            sys.exit(1)
        data = json.loads(path.read_text())
        seed = data["seed"]
        level = LEVEL_NAMES[data["complexity"].upper()]
        config = COMPLEXITY_PRESETS[level]
        print(f"Loading example '{args.example}' — {data['complexity']} case, seed={seed}")
        return generate_mystery(config, seed)

    seed = args.seed if args.seed is not None else random.randint(0, 999999)
    level = LEVEL_NAMES[args.level.upper()]
    config = COMPLEXITY_PRESETS[level]
    print(f"Generating a {args.level} mystery (seed={seed}) ...")
    return generate_mystery(config, seed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Play a mystery case in a 2D top-down window")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--load", metavar="FILE", help="Load a saved world JSON")
    group.add_argument("--example", metavar="ID", help="Play a benchmark example by ID")
    group.add_argument(
        "--level", default="MEDIUM", choices=list(LEVEL_NAMES),
        help="Complexity level for a fresh case (default: MEDIUM)",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed (random if omitted)")
    parser.add_argument(
        "--npc-url", default=None,
        help="OpenAI-compatible base URL for LLM-powered NPC interviews",
    )
    parser.add_argument("--npc-model", default="Qwen/Qwen3.5-27B")
    parser.add_argument("--npc-seed", type=int, default=42)
    args = parser.parse_args()

    world = _load_world(args)
    env = MysteryEnvironment(world)

    if args.npc_url:
        from mystery_world.npc_responder import NPCResponder
        env.set_npc_responder(NPCResponder(
            base_url=args.npc_url, model=args.npc_model, seed=args.npc_seed
        ))
        print(f"NPC interviews: {args.npc_model} @ {args.npc_url}")
    else:
        print("NPC interviews: deterministic fallback (pass --npc-url to use an LLM)")

    MysteryGame(env, window_title=f"MysteryArena — seed {world.seed}").run()


if __name__ == "__main__":
    main()
