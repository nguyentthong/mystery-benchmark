"""
M7 verification: per-skill probes.

Properties checked:
  1. Probe extraction is deterministic per state.
  2. extract_probes() produces all four probes for a typical audited
     episode (event_ordering present, change_detection non-empty,
     time_of_death present, persistent_identity non-empty with both
     positive and negative pairs).
  3. OracleAccuseAgent's probe responses score well: tau >= 0.5
     (the content-heuristic isn't perfect), change_detection_acc == 1.0,
     time_of_death_mae == 0.0, persistent_identity_acc == 1.0.
  4. HashAgent's probe responses score around chance: change_detection
     and persistent_identity accuracies are well below the Oracle's.
  5. run_2x2(with_probes=True) attaches probe_scores to every
     ConditionResult; the probe keys are present.
  6. kendall_tau correctness on small fixed inputs.

Usage:
    .venv/bin/python scripts/test_probes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.generator import generate_mystery
from evaluation.probes import (
    aggregate_probe_scores,
    extract_probes,
    kendall_tau,
    score_probes,
)
from evaluation.stub_agents import HashAgent, OracleAccuseAgent
from evaluation.temporal_runner import CONDITIONS, run_2x2


def _gen(seed: int):
    return generate_mystery(
        seed=seed,
        config=COMPLEXITY_PRESETS[ComplexityLevel.EASY],
        audit_temporal_necessity=True,
    )


def _check_kendall_tau_unit() -> bool:
    cases = [
        ([0, 1, 2, 3], [0, 1, 2, 3], 1.0),     # identical
        ([3, 2, 1, 0], [0, 1, 2, 3], -1.0),    # reverse
        ([0, 2, 1, 3], [0, 1, 2, 3], 4 / 6),   # one swap
        ([0], [0], 1.0),                         # singleton
    ]
    for pred, truth, expected in cases:
        got = kendall_tau(pred, truth)
        if abs(got - expected) > 1e-6:
            print(f"FAIL [kendall_tau_unit]: kendall_tau({pred},{truth}) = {got}, expected {expected}")
            return False
    print("  kendall_tau_unit: OK")
    return True


def _check_extraction_determinism(seeds: list[int]) -> bool:
    for seed in seeds:
        state = _gen(seed)
        p1 = extract_probes(state)
        p2 = extract_probes(state)
        # Compare key fields
        if (p1.event_ordering is None) != (p2.event_ordering is None):
            print(f"FAIL [extraction_determinism]: seed={seed} event_ordering presence differs")
            return False
        if p1.event_ordering is not None:
            if p1.event_ordering.true_chronological_order != p2.event_ordering.true_chronological_order:
                print(f"FAIL [extraction_determinism]: seed={seed} true order differs")
                return False
        if len(p1.change_detection) != len(p2.change_detection):
            print(f"FAIL [extraction_determinism]: seed={seed} change pair counts differ")
            return False
        if p1.time_of_death is None or p2.time_of_death is None:
            print(f"FAIL [extraction_determinism]: seed={seed} time_of_death missing")
            return False
        if p1.time_of_death.true_step != p2.time_of_death.true_step:
            print(f"FAIL [extraction_determinism]: seed={seed} true_step differs")
            return False
        if len(p1.persistent_identity) != len(p2.persistent_identity):
            print(f"FAIL [extraction_determinism]: seed={seed} identity pair counts differ")
            return False
    print(f"  extraction_determinism: OK across {len(seeds)} seeds")
    return True


def _check_extraction_completeness(seeds: list[int]) -> bool:
    """All four probe types should be populated on a typical audited episode."""
    for seed in seeds:
        state = _gen(seed)
        p = extract_probes(state)
        if p.event_ordering is None:
            print(f"FAIL [extraction_completeness]: seed={seed} no event_ordering")
            return False
        if not p.change_detection:
            print(f"FAIL [extraction_completeness]: seed={seed} no change_detection pairs")
            return False
        if p.time_of_death is None:
            print(f"FAIL [extraction_completeness]: seed={seed} no time_of_death")
            return False
        if not p.persistent_identity:
            print(f"FAIL [extraction_completeness]: seed={seed} no persistent_identity pairs")
            return False
        # Should have at least one positive and one negative identity pair.
        has_pos = any(pp.are_same_object for pp in p.persistent_identity)
        has_neg = any(not pp.are_same_object for pp in p.persistent_identity)
        if not (has_pos and has_neg):
            print(
                f"FAIL [extraction_completeness]: seed={seed} identity probe "
                f"missing positives or negatives (pos={has_pos}, neg={has_neg})"
            )
            return False
    print(f"  extraction_completeness: OK across {len(seeds)} seeds")
    return True


def _check_oracle_probe_scores(seeds: list[int]) -> bool:
    """Oracle should score well on every probe."""
    per_episode: list[dict[str, float]] = []
    for seed in seeds:
        state = _gen(seed)
        probes = extract_probes(state)
        agent = OracleAccuseAgent(state)
        per_episode.append(score_probes(agent, probes))
    agg = aggregate_probe_scores(per_episode)
    failed = []
    if agg.get("event_ordering_tau", -2) < 0.5:
        failed.append(f"event_ordering_tau={agg.get('event_ordering_tau')}")
    if agg.get("change_detection_acc", 0) < 1.0:
        failed.append(f"change_detection_acc={agg.get('change_detection_acc')}")
    if agg.get("time_of_death_mae", 1) != 0.0:
        failed.append(f"time_of_death_mae={agg.get('time_of_death_mae')}")
    if agg.get("persistent_identity_acc", 0) < 1.0:
        failed.append(f"persistent_identity_acc={agg.get('persistent_identity_acc')}")
    if failed:
        print(f"FAIL [oracle_probe_scores]: {failed}; agg={agg}")
        return False
    print(f"  oracle_probe_scores: OK; agg={agg}")
    return True


def _check_hash_below_oracle(seeds: list[int]) -> bool:
    """HashAgent should score worse than OracleAccuseAgent on the
    capability-bearing probes (change_detection and persistent_identity)."""
    oracle_eps: list[dict[str, float]] = []
    hash_eps: list[dict[str, float]] = []
    for seed in seeds:
        state = _gen(seed)
        probes = extract_probes(state)
        oracle_eps.append(score_probes(OracleAccuseAgent(state), probes))
        hash_eps.append(score_probes(HashAgent(state), probes))
    oracle_agg = aggregate_probe_scores(oracle_eps)
    hash_agg = aggregate_probe_scores(hash_eps)
    fails = []
    if hash_agg.get("change_detection_acc", 1) >= oracle_agg.get("change_detection_acc", 0):
        fails.append(
            f"change_detection: hash={hash_agg.get('change_detection_acc')} >= "
            f"oracle={oracle_agg.get('change_detection_acc')}"
        )
    if hash_agg.get("persistent_identity_acc", 1) >= oracle_agg.get("persistent_identity_acc", 0):
        fails.append(
            f"persistent_identity: hash={hash_agg.get('persistent_identity_acc')} >= "
            f"oracle={oracle_agg.get('persistent_identity_acc')}"
        )
    if hash_agg.get("time_of_death_mae", 0) <= oracle_agg.get("time_of_death_mae", 1):
        fails.append(
            f"time_of_death_mae: hash={hash_agg.get('time_of_death_mae')} <= "
            f"oracle={oracle_agg.get('time_of_death_mae')}"
        )
    if fails:
        print(f"FAIL [hash_below_oracle]: {fails}")
        return False
    print(
        f"  hash_below_oracle: OK; hash={hash_agg}, oracle={oracle_agg}"
    )
    return True


def _check_run_2x2_attaches_probe_scores(seeds: list[int]) -> bool:
    expected_keys = {
        "event_ordering_tau",
        "change_detection_acc",
        "time_of_death_mae",
        "persistent_identity_acc",
    }
    for seed in seeds:
        state = _gen(seed)
        matrix = run_2x2(state, OracleAccuseAgent, visual_mode=False, with_probes=True)
        for cond in CONDITIONS:
            cr = matrix.per_condition[cond]
            if not isinstance(cr.probe_scores, dict):
                print(f"FAIL [run_2x2_probes]: seed={seed} cond={cond} probe_scores not dict")
                return False
            missing = expected_keys - cr.probe_scores.keys()
            if missing:
                print(
                    f"FAIL [run_2x2_probes]: seed={seed} cond={cond} missing keys "
                    f"{missing}; got {cr.probe_scores.keys()}"
                )
                return False
    print(f"  run_2x2_attaches_probe_scores: OK across {len(seeds)} seeds")
    return True


def main() -> int:
    seeds = list(range(10))
    ok1 = _check_kendall_tau_unit()
    ok2 = _check_extraction_determinism(seeds)
    ok3 = _check_extraction_completeness(seeds)
    ok4 = _check_oracle_probe_scores(seeds)
    ok5 = _check_hash_below_oracle(seeds)
    ok6 = _check_run_2x2_attaches_probe_scores(seeds)
    all_ok = all([ok1, ok2, ok3, ok4, ok5, ok6])
    print(f"\nM7 overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
