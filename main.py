"""pygbag entrypoint for the web build.

This file lives at the project root so pygbag bundles the whole repo
(`mystery_world/`, `agents/`, `examples/`) into the WASM build. For desktop
play, use `scripts/play_game.py` instead.

Build:    uv run pygbag --build main.py
Preview:  uv run pygbag main.py
Deploy:   upload `build/web/` to GitHub Pages / Vercel / itch.io
"""
# /// script
# dependencies = ["openai"]
# ///
from __future__ import annotations

import asyncio
import random
import sys


async def _install_runtime_deps() -> None:
    """In Pyodide (browser), install pure-Python deps via micropip.

    On desktop these are already available from `uv sync`, so this is a no-op.
    """
    if sys.platform != "emscripten":
        return
    try:
        import micropip  # type: ignore[import-not-found]
        await micropip.install("openai")
    except Exception as e:
        print(f"[main] could not install openai in browser: {e}")


async def main() -> None:
    await _install_runtime_deps()

    # Imports happen *after* the runtime install so openai is resolvable on web.
    from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
    from mystery_world.generator import generate_mystery
    from mystery_world.renderer.game import MysteryGame
    from mystery_world.world import MysteryEnvironment

    seed = random.randint(0, 999_999)
    config = COMPLEXITY_PRESETS[ComplexityLevel.EASY]
    world = generate_mystery(config, seed)
    env = MysteryEnvironment(world)

    game = MysteryGame(env, window_title=f"MysteryArena — seed {seed}")
    game.prompt_for_openai_key(default_model="gpt-4o-mini")
    await game.run_async()


asyncio.run(main())
