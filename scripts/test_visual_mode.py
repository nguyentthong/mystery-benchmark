"""
M1 verification: ensure ``visual_mode=True`` strips evidence-freshness and
time-of-death vocabulary from the textual observation channel.

This is the upstream guard for the visual-temporal benchmark variant. Under
visual mode, freshness is meant to be carried by rendered observations (added
in later milestones), so the text channel must not leak it. Under the default
mode (``visual_mode=False``), the textual benchmark behaves exactly as on
master and the same vocabulary is still present.

Usage:
    python scripts/test_visual_mode.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.generator import generate_mystery
from mystery_world.narrator import render_initial_briefing
from mystery_world.world import AgentAction, MysteryEnvironment


# Phrases that must NOT appear under visual_mode=True. These are the
# evidence-freshness and time-of-death signals that the visual channel will
# carry instead.
_FORBIDDEN_IN_VISUAL = (
    "time of death",
    "time-of-death",
    "very recent",
    "predates the murder",
    "relatively recent",
    "older than it first appears",
    "how fresh",
)

# Under visual_mode=False, the initial briefing should reveal WHEN the
# killing happened. _TIME_DESCRIPTIONS in narrator.py has 10 stylistic
# variants; the sanity check passes if any one of these phrasings appears,
# so we know we haven't accidentally stripped death-time disclosure from
# the textual benchmark.
_REQUIRED_IN_TEXTUAL_ANY = (
    "time of death",
    "time-of-death",
    "death occurred",
    "victim died",
    "killing happen",       # matches "happening", "happened"
    "signs of life",
    "that is when it happened",
)


def _gather_observations(env: MysteryEnvironment) -> str:
    """Return briefing + every ANALYZE result for every discoverable evidence.

    This is enough to surface all freshness/time-of-death leak sites that M1
    targets. We force-discover every evidence so that ANALYZE works for all of
    them; this is purely for the audit and bypasses the discovery mechanic.
    """
    parts = [render_initial_briefing(env)]

    # Bypass discovery for the audit: pretend the agent has seen every
    # evidence so we can ANALYZE all of them.
    env._discovered_evidence.update(env.state.evidence.keys())

    for eid in env.state.evidence:
        result = env._handle_analyze(evidence_id=eid)
        parts.append(result.observation)

    return "\n".join(parts)


def _check_seed(seed: int, level: ComplexityLevel) -> None:
    config = COMPLEXITY_PRESETS[level]
    state = generate_mystery(seed=seed, config=config)

    # visual_mode = True: no leakage
    env_visual = MysteryEnvironment(state, visual_mode=True)
    text_visual = _gather_observations(env_visual).lower()
    leaks = [p for p in _FORBIDDEN_IN_VISUAL if p in text_visual]
    if leaks:
        raise AssertionError(
            f"[seed={seed}, {level.name}] visual_mode=True leaked: {leaks}"
        )

    # visual_mode = False: vocabulary preserved (sanity check)
    state2 = generate_mystery(seed=seed, config=config)
    env_textual = MysteryEnvironment(state2, visual_mode=False)
    text_textual = _gather_observations(env_textual).lower()
    if not any(p in text_textual for p in _REQUIRED_IN_TEXTUAL_ANY):
        raise AssertionError(
            f"[seed={seed}, {level.name}] visual_mode=False briefing has none of "
            f"{_REQUIRED_IN_TEXTUAL_ANY} — death-time disclosure may have been "
            f"accidentally stripped."
        )

    # ANALYZE results in textual mode must contain at least one freshness phrase
    freshness_phrases = (
        "very recent",
        "predates the murder",
        "relatively recent",
        "older than it first appears",
    )
    if not any(p in text_textual for p in freshness_phrases):
        raise AssertionError(
            f"[seed={seed}, {level.name}] visual_mode=False did not produce any freshness phrase "
            f"(checked: {freshness_phrases}). Sanity check failed."
        )


def main() -> int:
    seeds = [0, 1, 2, 7, 42, 100, 999]
    levels = [ComplexityLevel.EASY, ComplexityLevel.MEDIUM, ComplexityLevel.HARD]
    n_total = len(seeds) * len(levels)
    n_passed = 0
    for seed in seeds:
        for level in levels:
            try:
                _check_seed(seed, level)
                n_passed += 1
            except AssertionError as exc:
                print(f"FAIL: {exc}")
    print(f"{n_passed}/{n_total} cases passed.")
    return 0 if n_passed == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
