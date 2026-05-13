"""
M5 verification: canonical itinerary emission + temporal-necessity audit.

Properties checked:
  1. Canonical itinerary is always emitted on generated WorldState
     (regardless of the audit flag) and is a non-empty list of dicts.
  2. With audit_temporal_necessity=True, 100/100 generated EASY episodes
     pass programmatic re-audit (audit_episode returns True).
  3. End-to-end smoke run: for a sample of audited episodes, replaying the
     canonical itinerary against a fresh visual_mode=True env produces a
     valid image observation at each non-ACCUSE step, and the final ACCUSE
     marks accusation_correct=True.
  4. The audited replay surfaces at least one piece of evidence at two or
     more distinct VisualState values (the core cross-observation
     change-relation that the temporal-reasoning track depends on).

Usage:
    .venv/bin/python scripts/test_canonical_itinerary.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.canonical import audit_episode
from mystery_world.generator import generate_mystery
from mystery_world.world import AgentAction, MysteryEnvironment


def _replay(env: MysteryEnvironment, itinerary: list[dict]) -> dict:
    """Replay an itinerary, collecting per-step diagnostics.

    Returns {n_steps, n_with_image, n_with_visible_evidence,
             multi_state_evidence_ids, accusation_correct}.
    """
    states_seen: dict[str, set[str]] = {}
    n_steps = 0
    n_with_image = 0
    n_with_visible_evidence = 0

    # Pre-step snapshot
    for entry in env.get_visible_evidence():
        vs = entry.get("visual_state")
        if vs is not None:
            states_seen.setdefault(entry["id"], set()).add(vs)

    for action_entry in itinerary:
        action = AgentAction[action_entry["action"]]
        result = env.step(action, **action_entry.get("kwargs", {}))
        n_steps += 1
        if result.image is not None:
            n_with_image += 1
        if result.visible_evidence:
            n_with_visible_evidence += 1
        for entry in result.visible_evidence:
            vs = entry.get("visual_state")
            if vs is not None:
                states_seen.setdefault(entry["id"], set()).add(vs)

    multi_state = [eid for eid, vs in states_seen.items() if len(vs) >= 2]
    return {
        "n_steps": n_steps,
        "n_with_image": n_with_image,
        "n_with_visible_evidence": n_with_visible_evidence,
        "multi_state_evidence_ids": multi_state,
        "accusation_correct": env.accusation_correct,
    }


def _check_unaudited_emits_itinerary() -> int:
    """Without audit_temporal_necessity, the canonical itinerary is still
    populated. Verify across several seeds and levels."""
    n_passed = 0
    n_total = 0
    for seed in [0, 1, 2, 7, 42]:
        for level in [ComplexityLevel.EASY, ComplexityLevel.MEDIUM, ComplexityLevel.HARD]:
            n_total += 1
            state = generate_mystery(
                seed=seed, config=COMPLEXITY_PRESETS[level],
                audit_temporal_necessity=False,
            )
            if state.canonical_itinerary and isinstance(state.canonical_itinerary, list):
                if all(isinstance(e, dict) and "action" in e for e in state.canonical_itinerary):
                    n_passed += 1
                    continue
            print(
                f"FAIL [unaudited_emits_itinerary]: seed={seed} {level.name} "
                f"canonical_itinerary malformed or empty"
            )
    print(f"  unaudited_emits_itinerary: {n_passed}/{n_total}")
    return n_passed == n_total


def _check_audited_100() -> int:
    """100 EASY seeds with audit_temporal_necessity=True should all generate
    successfully and pass programmatic re-audit."""
    n_attempts = 100
    n_audit_passed = 0
    n_re_audit_passed = 0
    for seed in range(n_attempts):
        state = generate_mystery(
            seed=seed, config=COMPLEXITY_PRESETS[ComplexityLevel.EASY],
            audit_temporal_necessity=True,
        )
        # The generator should not have returned an unaudited state when the
        # flag is on; if max_retries is exhausted it returns the last attempt
        # (which may or may not pass). Re-audit programmatically.
        n_audit_passed += 1   # generator returned (didn't raise)
        passed, reason = audit_episode(state)
        if passed:
            n_re_audit_passed += 1
        else:
            print(f"FAIL [audited_100]: seed={seed} reason={reason}")
    print(f"  audited_100 generated:   {n_audit_passed}/{n_attempts}")
    print(f"  audited_100 re-audited:  {n_re_audit_passed}/{n_attempts}")
    return n_re_audit_passed == n_attempts


def _check_end_to_end_smoke(n_sample: int = 10) -> int:
    """For a sample of audited episodes, replay the canonical itinerary in
    a fresh visual_mode=True env and verify:
      - every non-ACCUSE step produces a non-None image
      - the final ACCUSE marks accusation_correct=True
      - at least one piece of evidence is observed in >=2 VisualStates."""
    n_passed = 0
    for seed in range(n_sample):
        state = generate_mystery(
            seed=seed, config=COMPLEXITY_PRESETS[ComplexityLevel.EASY],
            audit_temporal_necessity=True,
        )
        env = MysteryEnvironment(state, visual_mode=True)
        result = _replay(env, state.canonical_itinerary)
        # All non-ACCUSE steps should have rendered an image.
        n_accuse = sum(1 for e in state.canonical_itinerary if e["action"] == "ACCUSE")
        expected_with_image = result["n_steps"]   # ACCUSE also renders post-state
        if result["n_with_image"] != expected_with_image:
            print(
                f"FAIL [smoke seed={seed}]: only {result['n_with_image']}/"
                f"{expected_with_image} steps produced an image"
            )
            continue
        if result["accusation_correct"] is not True:
            print(
                f"FAIL [smoke seed={seed}]: accusation_correct={result['accusation_correct']}"
            )
            continue
        if not result["multi_state_evidence_ids"]:
            print(
                f"FAIL [smoke seed={seed}]: no cross-observation VisualState change "
                f"during replay"
            )
            continue
        n_passed += 1
    print(f"  end_to_end_smoke: {n_passed}/{n_sample}")
    return n_passed == n_sample


def main() -> int:
    ok1 = _check_unaudited_emits_itinerary()
    ok2 = _check_audited_100()
    ok3 = _check_end_to_end_smoke(n_sample=10)
    all_ok = ok1 and ok2 and ok3
    print(f"\nM5 overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
