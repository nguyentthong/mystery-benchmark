"""
M4 verification: ensure env.step() returns a PNG image alongside the
textual / visible_evidence channels when visual_mode is enabled, and that
the image is reproducible from (seed, action_history).

Properties checked:
  1. visual_mode=False -> result.image is None on every step;
     get_observation_image() also returns None.
  2. visual_mode=True  -> result.image is non-empty PNG bytes on every step;
     get_observation_image() returns the same after the same action history.
  3. Reproducibility: two envs at the same seed driven through the same
     action sequence produce byte-identical images at every observation.
  4. Image evolution: across a multi-step sequence the image bytes change at
     least once (otherwise the visual channel is static and useless).

Usage:
    .venv/bin/python scripts/test_step_image.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.generator import generate_mystery
from mystery_world.world import AgentAction, MysteryEnvironment


def _is_png(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(b"\x89PNG\r\n\x1a\n")


def _drive(env: MysteryEnvironment, n_waits: int = 5) -> list[bytes | None]:
    """Drive the env through one initial observation + n_waits WAIT actions,
    collecting the rendered image at each observation point."""
    images: list[bytes | None] = [env.get_observation_image()]
    for _ in range(n_waits):
        result = env.step(AgentAction.WAIT)
        images.append(result.image)
    return images


def _check_visual_mode_off(seed: int, level: ComplexityLevel) -> None:
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state, visual_mode=False)

    if env.get_observation_image() is not None:
        raise AssertionError(
            f"[seed={seed}, {level.name}] visual_mode=False but "
            f"get_observation_image() returned non-None"
        )

    for _ in range(3):
        result = env.step(AgentAction.WAIT)
        if result.image is not None:
            raise AssertionError(
                f"[seed={seed}, {level.name}] visual_mode=False but step() "
                f"populated result.image"
            )


def _check_visual_mode_on(seed: int, level: ComplexityLevel) -> None:
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state, visual_mode=True)

    initial = env.get_observation_image()
    if not _is_png(initial):
        raise AssertionError(
            f"[seed={seed}, {level.name}] initial observation image is not PNG: "
            f"{type(initial)} len={len(initial) if initial else 0}"
        )

    for _ in range(3):
        result = env.step(AgentAction.WAIT)
        if not _is_png(result.image):
            raise AssertionError(
                f"[seed={seed}, {level.name}] step().image is not PNG: "
                f"{type(result.image)} len={len(result.image) if result.image else 0}"
            )


def _check_reproducibility(seed: int, level: ComplexityLevel) -> None:
    """Two envs from the same seed driven through identical actions must
    produce byte-identical image bytes at every step."""
    state_a = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env_a = MysteryEnvironment(state_a, visual_mode=True)
    images_a = _drive(env_a, n_waits=5)

    state_b = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env_b = MysteryEnvironment(state_b, visual_mode=True)
    images_b = _drive(env_b, n_waits=5)

    if len(images_a) != len(images_b):
        raise AssertionError(
            f"[seed={seed}, {level.name}] image counts differ: "
            f"{len(images_a)} vs {len(images_b)}"
        )
    for i, (a, b) in enumerate(zip(images_a, images_b)):
        if a != b:
            ha = hashlib.md5(a).hexdigest() if a else "None"
            hb = hashlib.md5(b).hexdigest() if b else "None"
            raise AssertionError(
                f"[seed={seed}, {level.name}] image at step {i} differs across "
                f"reruns: md5(a)={ha} md5(b)={hb}"
            )


def _check_image_evolution(seed: int, level: ComplexityLevel) -> None:
    """Across a multi-step sequence, the image must change at least once
    (otherwise the visual channel is static and carries no temporal info)."""
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state, visual_mode=True)
    images = _drive(env, n_waits=12)   # enough WAITs to cross aging bands
    digests = {hashlib.md5(img).hexdigest() for img in images if img is not None}
    if len(digests) < 2:
        raise AssertionError(
            f"[seed={seed}, {level.name}] over 13 observations the image never "
            f"changed (saw only 1 unique digest) -- visual channel is static"
        )


_CHECKS = (
    ("visual_mode_off",  _check_visual_mode_off),
    ("visual_mode_on",   _check_visual_mode_on),
    ("reproducibility",  _check_reproducibility),
    ("image_evolution",  _check_image_evolution),
)


def main() -> int:
    seeds = [0, 1, 2, 7, 42, 100, 999]
    levels = [ComplexityLevel.EASY, ComplexityLevel.MEDIUM, ComplexityLevel.HARD]
    n_total = len(seeds) * len(levels) * len(_CHECKS)
    n_passed = 0
    for seed in seeds:
        for level in levels:
            for name, fn in _CHECKS:
                try:
                    fn(seed, level)
                    n_passed += 1
                except AssertionError as exc:
                    print(f"FAIL [{name}]: {exc}")
    print(f"{n_passed}/{n_total} step-image checks passed.")
    return 0 if n_passed == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
