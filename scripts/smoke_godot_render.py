"""
Local Godot smoke test: spawn the real Godot subprocess, request one render,
write the PNG to disk. Use this on your own machine after pulling the
visual-temporal branch to verify the 3D path actually works.

The CI / sandbox tests use the MockGodotRenderer (no Godot binary needed).
This script forces the real backend (MYSTERYARENA_GODOT=real).

Pre-requisites:
  - Godot 4.3+ installed and reachable as `godot` on PATH, OR
    set GODOT_BIN=/path/to/godot before running.
  - A display (Mac/Linux with X / Wayland / native window server). For
    headless servers, you'll need `xvfb-run python scripts/smoke_godot_render.py`.
  - uv-managed venv with pillow installed (already a dependency).

Usage:
    .venv/bin/python scripts/smoke_godot_render.py --seed 0 --out /tmp/render.png
    GODOT_BIN=/Applications/Godot.app/Contents/MacOS/Godot \\
        .venv/bin/python scripts/smoke_godot_render.py --seed 0
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force the real Godot backend (override any MOCK setting).
os.environ["MYSTERYARENA_GODOT"] = "real"

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.generator import generate_mystery
from mystery_world.godot_render import (
    GodotRenderer,
    GodotRendererError,
    _serialize_room,
    render_observation_png_3d,
)
from mystery_world.world import AgentAction, MysteryEnvironment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--level",
        default="EASY",
        choices=[lvl.name for lvl in ComplexityLevel],
    )
    parser.add_argument(
        "--out",
        default="/tmp/mysteryarena_render.png",
        help="Path where the rendered PNG is written.",
    )
    parser.add_argument(
        "--width",  type=int, default=720,
        help="Render output width in pixels.",
    )
    parser.add_argument(
        "--height", type=int, default=720,
        help="Render output height in pixels.",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Use audit_temporal_necessity=True so the chosen episode is "
             "guaranteed to surface cross-observation VisualState change.",
    )
    args = parser.parse_args()

    level = ComplexityLevel[args.level.upper()]
    config = COMPLEXITY_PRESETS[level]
    print(f"Generating {args.level} mystery (seed={args.seed}, audit={args.audit}) ...")
    state = generate_mystery(
        seed=args.seed, config=config,
        audit_temporal_necessity=args.audit,
    )
    env = MysteryEnvironment(state, visual_mode=True)
    print(f"  state.seed={state.seed}  agent_location={env.agent_location_id}")

    print("Launching Godot subprocess (this may take a few seconds) ...")
    try:
        png = render_observation_png_3d(env, width=args.width, height=args.height)
    except GodotRendererError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "Hints:\n"
            "  - Set GODOT_BIN to the path of your Godot 4.3+ binary.\n"
            "  - On macOS: GODOT_BIN=/Applications/Godot.app/Contents/MacOS/Godot\n"
            "  - On headless Linux: `sudo apt install xvfb` and prefix the\n"
            "    command with `xvfb-run`.\n",
            file=sys.stderr,
        )
        return 1

    if not png or len(png) < 100:
        print(f"ERROR: render returned suspiciously small payload ({len(png) if png else 0} bytes).", file=sys.stderr)
        return 1

    Path(args.out).write_bytes(png)
    print(f"OK -- wrote {len(png)} bytes to {args.out}")
    print(f"      open this file to verify the rendered 3D scene looks sensible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
