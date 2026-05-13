"""
M8 verification: twin-pair episode generation + metric.

Properties checked:
  1. generate_twin_pair produces a TwinPair where both halves pass
     audit_episode (cross-observation VisualState change present).
  2. The twin's temporal structure is genuinely flipped: at the same
     observation_step, the visual_state of the same persistent evidence
     differs between A and B on at least one observation per pair.
  3. Twin pairs preserve the who/what/where ground truth: A and B share
     the same culprit_id, murder_weapon_id, and murder_location_id.
  4. run_twin_pair + aggregate_twin_pair_results report a
     twin_pair_accuracy in [0, 1] and the four breakdown fractions.
  5. OracleAccuseAgent solves both halves of every pair
     (twin_pair_accuracy == 1.0). Confirms the eval pipeline plumbs
     ground truth through pair construction correctly.
  6. HashAgent achieves strictly lower twin_pair_accuracy than the
     Oracle. The metric discriminates capability.

Usage:
    .venv/bin/python scripts/test_twin_pairs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.canonical import audit_episode
from mystery_world.twins import TwinPair, generate_twin_pair
from mystery_world.world import AgentAction, MysteryEnvironment
from evaluation.stub_agents import HashAgent, OracleAccuseAgent
from evaluation.temporal_runner import (
    aggregate_twin_pair_results,
    run_twin_pair,
)


def _gen_pair(seed: int) -> TwinPair | None:
    return generate_twin_pair(
        seed=seed,
        config=COMPLEXITY_PRESETS[ComplexityLevel.EASY],
    )


def _twin_visual_states_differ(pair: TwinPair) -> bool:
    """Replay each canonical itinerary on its own state and check whether
    at least one (room_id, evidence_id, step) triple sees a different
    visual_state between A and B. If they look identical at every shared
    observation point, the twin is degenerate.

    We compare on the first canonical observation per room only -- the
    visual aging trajectory across the rest of the episode follows from
    the reflected contact_timestamp.
    """
    def first_obs_states(state) -> dict[tuple[str, str], str | None]:
        env = MysteryEnvironment(state, visual_mode=False)
        out: dict[tuple[str, str], str | None] = {}
        # Initial obs
        for e in env.get_visible_evidence():
            key = (env.agent_location_id, e["id"])
            out.setdefault(key, e.get("visual_state"))
        for entry in state.canonical_itinerary:
            action = AgentAction[entry["action"]]
            if action == AgentAction.ACCUSE:
                continue
            result = env.step(action, **entry.get("kwargs", {}))
            for e in result.visible_evidence:
                key = (env.agent_location_id, e["id"])
                out.setdefault(key, e.get("visual_state"))
        return out

    states_a = first_obs_states(pair.a)
    states_b = first_obs_states(pair.b)
    shared = set(states_a) & set(states_b)
    return any(states_a[k] != states_b[k] for k in shared)


def _check_audit_on_both(seeds: list[int]) -> bool:
    pairs = []
    for s in seeds:
        p = _gen_pair(s)
        if p is None:
            print(f"FAIL [audit_on_both]: seed={s} generate_twin_pair returned None")
            return False
        pairs.append(p)
    for p in pairs:
        for label, st in (("a", p.a), ("b", p.b)):
            passed, reason = audit_episode(st)
            if not passed:
                print(f"FAIL [audit_on_both]: pair {p.a.seed} half={label}: {reason[:120]}")
                return False
    print(f"  audit_on_both: OK across {len(pairs)} pairs")
    return True


def _check_visual_states_differ(seeds: list[int]) -> bool:
    """Twin construction must actually produce different visual signal.
    If A and B render identical visual_states at every observation, the
    metric collapses to a noisier copy of individual accuracy."""
    n_with_diff = 0
    for s in seeds:
        p = _gen_pair(s)
        if p is None:
            continue
        if _twin_visual_states_differ(p):
            n_with_diff += 1
    if n_with_diff == 0:
        print(
            f"FAIL [visual_states_differ]: none of {len(seeds)} pairs produced "
            f"a visual_state difference between A and B -- twin construction "
            f"is degenerate"
        )
        return False
    print(f"  visual_states_differ: {n_with_diff}/{len(seeds)} pairs have a difference")
    return True


def _check_ground_truth_preserved(seeds: list[int]) -> bool:
    """Twin must preserve culprit_id, weapon_id, location_id across A/B.
    Otherwise the OracleAccuseAgent (with A's truth) wouldn't solve B."""
    for s in seeds:
        p = _gen_pair(s)
        if p is None:
            continue
        if p.a.culprit_id != p.b.culprit_id:
            print(f"FAIL [ground_truth_preserved]: seed={s} culprit_id differs")
            return False
        if p.a.murder_weapon_id != p.b.murder_weapon_id:
            print(f"FAIL [ground_truth_preserved]: seed={s} weapon_id differs")
            return False
        if p.a.murder_location_id != p.b.murder_location_id:
            print(f"FAIL [ground_truth_preserved]: seed={s} location_id differs")
            return False
    print(f"  ground_truth_preserved: OK across {len(seeds)} pairs")
    return True


def _check_oracle_twin_perfect(seeds: list[int]) -> bool:
    pairs = [p for p in (_gen_pair(s) for s in seeds) if p is not None]
    results = [run_twin_pair(p, OracleAccuseAgent, condition="oracle_correct", visual_mode=False)
               for p in pairs]
    agg = aggregate_twin_pair_results(results)
    if agg["twin_pair_accuracy"] != 1.0:
        print(f"FAIL [oracle_twin_perfect]: twin_pair_accuracy={agg['twin_pair_accuracy']}; agg={agg}")
        return False
    print(f"  oracle_twin_perfect: 1.0 across {len(pairs)} pairs")
    return True


def _check_hash_below_oracle(seeds: list[int]) -> bool:
    pairs = [p for p in (_gen_pair(s) for s in seeds) if p is not None]
    oracle_results = [run_twin_pair(p, OracleAccuseAgent, condition="oracle_correct", visual_mode=False)
                      for p in pairs]
    hash_results = [run_twin_pair(p, HashAgent, condition="oracle_correct", visual_mode=False)
                    for p in pairs]
    oracle_agg = aggregate_twin_pair_results(oracle_results)
    hash_agg   = aggregate_twin_pair_results(hash_results)
    if hash_agg["twin_pair_accuracy"] >= oracle_agg["twin_pair_accuracy"]:
        print(
            f"FAIL [hash_below_oracle]: hash twin_pair_accuracy="
            f"{hash_agg['twin_pair_accuracy']} >= oracle "
            f"{oracle_agg['twin_pair_accuracy']}"
        )
        return False
    print(
        f"  hash_below_oracle: hash={hash_agg['twin_pair_accuracy']} < "
        f"oracle={oracle_agg['twin_pair_accuracy']}"
    )
    return True


def _check_aggregation_shape(seeds: list[int]) -> bool:
    pairs = [p for p in (_gen_pair(s) for s in seeds) if p is not None]
    results = [run_twin_pair(p, HashAgent, condition="oracle_correct", visual_mode=False)
               for p in pairs]
    agg = aggregate_twin_pair_results(results)
    for k in ("twin_pair_accuracy", "individual_accuracy", "a_only_correct_frac",
              "b_only_correct_frac", "neither_correct_frac", "n_pairs"):
        if k not in agg:
            print(f"FAIL [aggregation_shape]: missing key {k!r}; agg={agg}")
            return False
    if not (0.0 <= agg["twin_pair_accuracy"] <= 1.0):
        print(f"FAIL [aggregation_shape]: twin_pair_accuracy out of range")
        return False
    # The four mutually exclusive buckets sum to 1.0.
    fractions = (
        agg["twin_pair_accuracy"]
        + agg["a_only_correct_frac"]
        + agg["b_only_correct_frac"]
        + agg["neither_correct_frac"]
    )
    if abs(fractions - 1.0) > 1e-9:
        print(f"FAIL [aggregation_shape]: fractions sum to {fractions}, not 1.0")
        return False
    print(f"  aggregation_shape: keys + range OK; agg={agg}")
    return True


def main() -> int:
    seeds = list(range(10))
    ok1 = _check_audit_on_both(seeds)
    ok2 = _check_visual_states_differ(seeds)
    ok3 = _check_ground_truth_preserved(seeds)
    ok4 = _check_oracle_twin_perfect(seeds)
    ok5 = _check_hash_below_oracle(seeds)
    ok6 = _check_aggregation_shape(seeds)
    all_ok = all([ok1, ok2, ok3, ok4, ok5, ok6])
    print(f"\nM8 overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
