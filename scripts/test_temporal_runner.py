"""
M6 verification: 2x2 temporal-reasoning eval driver.

Properties checked:
  1. All four conditions run without error across multiple seeds.
  2. Determinism: same (state, agent) re-run yields the same per-condition
     accusation_correct flag and the same accuse_kwargs.
  3. OracleAccuseAgent sanity: A == B == C == D == 1.0 on every episode.
     Proves the eval pipeline plumbs ground truth through correctly under
     all four conditions.
  4. HashAgent order-sensitivity: for at least one episode, the agent's
     ACCUSE kwargs under oracle_correct differ from those under
     oracle_shuffled. That is the property the eval matrix exists to
     detect; without it the matrix is dead weight.
  5. Aggregation: aggregate_2x2_results returns per-condition accuracies
     in [0, 1] and the two gap fields (oracle_temporal_reasoning_gap,
     free_temporal_reasoning_gap).
  6. Smoke check with visual_mode=True: images flow through to the agent
     history without breaking determinism.

Usage:
    .venv/bin/python scripts/test_temporal_runner.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.generator import generate_mystery
from evaluation.stub_agents import HashAgent, OracleAccuseAgent
from evaluation.temporal_runner import (
    CONDITIONS,
    aggregate_2x2_results,
    run_2x2,
    run_condition,
)


def _gen(seed: int) -> "WorldState":  # type: ignore[name-defined]
    return generate_mystery(
        seed=seed,
        config=COMPLEXITY_PRESETS[ComplexityLevel.EASY],
        audit_temporal_necessity=True,
    )


def _check_all_conditions_run(seeds: list[int]) -> bool:
    """Every condition runs without raising and returns a ConditionResult."""
    for seed in seeds:
        state = _gen(seed)
        agent = OracleAccuseAgent(state)
        for cond in CONDITIONS:
            try:
                result = run_condition(state, agent, cond, visual_mode=False)
            except Exception as exc:
                print(f"FAIL [run seed={seed} cond={cond}]: {type(exc).__name__}: {exc}")
                return False
            if result.condition != cond:
                print(f"FAIL [run seed={seed}]: returned condition {result.condition} != {cond}")
                return False
    print(f"  all_conditions_run: OK across {len(seeds)} seeds")
    return True


def _check_determinism(seeds: list[int]) -> bool:
    """Re-running the same condition with a fresh agent must give the same
    accusation_correct and accuse_kwargs."""
    for seed in seeds:
        state = _gen(seed)
        for cond in CONDITIONS:
            a1 = HashAgent(state)
            r1 = run_condition(state, a1, cond, visual_mode=False)
            a2 = HashAgent(state)
            r2 = run_condition(state, a2, cond, visual_mode=False)
            if r1.accusation_correct != r2.accusation_correct:
                print(
                    f"FAIL [determinism seed={seed} cond={cond}]: "
                    f"correct differs {r1.accusation_correct} vs {r2.accusation_correct}"
                )
                return False
            if r1.accusation_kwargs != r2.accusation_kwargs:
                print(
                    f"FAIL [determinism seed={seed} cond={cond}]: "
                    f"kwargs differ {r1.accusation_kwargs} vs {r2.accusation_kwargs}"
                )
                return False
    print(f"  determinism: OK across {len(seeds)} seeds x {len(CONDITIONS)} conditions")
    return True


def _check_oracle_sanity(seeds: list[int]) -> bool:
    """OracleAccuseAgent should score 1.0 in every condition on every
    episode. Failure here means the eval driver itself is broken."""
    matrices = [run_2x2(_gen(s), OracleAccuseAgent, visual_mode=False) for s in seeds]
    agg = aggregate_2x2_results(matrices)
    bad = [c for c in CONDITIONS if agg[c] != 1.0]
    if bad:
        print(
            f"FAIL [oracle_sanity]: conditions {bad} did not score 1.0; "
            f"agg={agg}"
        )
        return False
    if agg["oracle_temporal_reasoning_gap"] != 0.0 or agg["free_temporal_reasoning_gap"] != 0.0:
        print(
            f"FAIL [oracle_sanity]: gaps non-zero under oracle agent; agg={agg}"
        )
        return False
    print(f"  oracle_sanity: 1.0 across all 4 conditions and {len(seeds)} episodes")
    return True


def _check_hash_order_sensitivity(seeds: list[int]) -> bool:
    """HashAgent's ACCUSE must differ between oracle_correct and
    oracle_shuffled on at least one episode in the sample. Without this,
    the eval matrix cannot distinguish ordered from shuffled."""
    n_order_sensitive = 0
    n_total = 0
    for seed in seeds:
        state = _gen(seed)
        matrix = run_2x2(state, HashAgent, visual_mode=False)
        n_total += 1
        kw_correct  = matrix.per_condition["oracle_correct"].accusation_kwargs
        kw_shuffled = matrix.per_condition["oracle_shuffled"].accusation_kwargs
        if kw_correct != kw_shuffled:
            n_order_sensitive += 1
    if n_order_sensitive == 0:
        print(
            f"FAIL [hash_order_sensitivity]: HashAgent produced identical "
            f"accusations under oracle_correct and oracle_shuffled across "
            f"all {n_total} seeds — the eval matrix is not discriminating."
        )
        return False
    print(
        f"  hash_order_sensitivity: {n_order_sensitive}/{n_total} episodes "
        f"have different ACCUSE under shuffled history"
    )
    return True


def _check_aggregation(seeds: list[int]) -> bool:
    matrices = [run_2x2(_gen(s), HashAgent, visual_mode=False) for s in seeds]
    agg = aggregate_2x2_results(matrices)
    for cond in CONDITIONS:
        if not (0.0 <= agg[cond] <= 1.0):
            print(f"FAIL [aggregation]: {cond} accuracy out of range: {agg[cond]}")
            return False
    if "oracle_temporal_reasoning_gap" not in agg or "free_temporal_reasoning_gap" not in agg:
        print(f"FAIL [aggregation]: gap fields missing; agg={agg}")
        return False
    if agg["n_episodes"] != float(len(seeds)):
        print(f"FAIL [aggregation]: n_episodes={agg['n_episodes']} != {len(seeds)}")
        return False
    print(f"  aggregation: ranges + gap fields OK; agg={agg}")
    return True


def _check_visual_smoke() -> bool:
    """Run one episode with visual_mode=True for HashAgent in all four
    conditions. The driver must accept images in history without
    error and still be deterministic."""
    state = _gen(0)
    m1 = run_2x2(state, HashAgent, visual_mode=True)
    m2 = run_2x2(state, HashAgent, visual_mode=True)
    for cond in CONDITIONS:
        if m1.per_condition[cond].accusation_kwargs != m2.per_condition[cond].accusation_kwargs:
            print(f"FAIL [visual_smoke seed=0 cond={cond}]: non-deterministic accuse")
            return False
    print("  visual_smoke: visual_mode=True deterministic across all conditions")
    return True


def main() -> int:
    seeds = list(range(15))
    ok1 = _check_all_conditions_run(seeds)
    ok2 = _check_determinism(seeds)
    ok3 = _check_oracle_sanity(seeds)
    ok4 = _check_hash_order_sensitivity(seeds)
    ok5 = _check_aggregation(seeds)
    ok6 = _check_visual_smoke()
    all_ok = all([ok1, ok2, ok3, ok4, ok5, ok6])
    print(f"\nM6 overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
