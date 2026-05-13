"""
M10 verification: cross-hardware reproducibility primitives.

Properties checked:
  1. semantic_checksum is stable across re-generations of the same seed.
  2. semantic_checksum differs across different seeds.
  3. perceptual_hash is stable across re-renders of the same env.
  4. dhash returns a 64-bit hex string (16 chars).
  5. hamming_distance is symmetric and self-zero.
  6. compute_reference round-trips through ReferenceData.save/load.
  7. verify_against_reference reports semantic_match=True and
     frames_within_threshold=True for a state vs. its own reference.
  8. A reference from seed X vs. state seed Y reports semantic_match=False
     (i.e. the verifier detects divergence).

Usage:
    .venv/bin/python scripts/test_reproducibility.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.generator import generate_mystery
from benchmark.verify import (
    DEFAULT_PHASH_HAMMING_THRESHOLD,
    ReferenceData,
    compute_reference,
    export_reference,
    hamming_distance,
    perceptual_hash,
    semantic_checksum,
    verify_against_reference,
)


def _gen(seed: int):
    return generate_mystery(
        seed=seed,
        config=COMPLEXITY_PRESETS[ComplexityLevel.EASY],
        audit_temporal_necessity=True,
    )


def _check_semantic_stable(seeds: list[int]) -> bool:
    for seed in seeds:
        a = _gen(seed)
        b = _gen(seed)
        cs_a = semantic_checksum(a)
        cs_b = semantic_checksum(b)
        if cs_a != cs_b:
            print(f"FAIL [semantic_stable]: seed={seed} re-gen produced different checksums {cs_a[:12]} vs {cs_b[:12]}")
            return False
    print(f"  semantic_stable: OK across {len(seeds)} seeds")
    return True


def _check_semantic_distinct(seeds: list[int]) -> bool:
    """Distinct state.seed values must produce distinct checksums. Input
    seeds can collapse onto the same state.seed if the generator's
    audit-retry loop advances seed+attempt past one another, and that is
    expected: same state -> same checksum. Group by state.seed and verify
    intra-group equality + inter-group inequality."""
    by_state_seed: dict[int, str] = {}
    for seed in seeds:
        state = _gen(seed)
        cs = semantic_checksum(state)
        if state.seed in by_state_seed:
            if cs != by_state_seed[state.seed]:
                print(
                    f"FAIL [semantic_distinct]: state.seed={state.seed} reached "
                    f"from two inputs produced different checksums"
                )
                return False
        else:
            if cs in by_state_seed.values():
                print(
                    f"FAIL [semantic_distinct]: state.seed={state.seed} has same "
                    f"checksum as a different state.seed (cross-state collision)"
                )
                return False
            by_state_seed[state.seed] = cs
    print(f"  semantic_distinct: OK ({len(by_state_seed)} unique state.seeds across {len(seeds)} inputs)")
    return True


def _check_perceptual_hash_stable(seeds: list[int]) -> bool:
    from mystery_world.world import MysteryEnvironment
    for seed in seeds:
        state = _gen(seed)
        env_a = MysteryEnvironment(state, visual_mode=True)
        env_b = MysteryEnvironment(state, visual_mode=True)
        img_a = env_a.get_observation_image()
        img_b = env_b.get_observation_image()
        if img_a is None or img_b is None:
            print(f"FAIL [perceptual_hash_stable]: seed={seed} missing image")
            return False
        ha = perceptual_hash(img_a)
        hb = perceptual_hash(img_b)
        if ha != hb:
            print(f"FAIL [perceptual_hash_stable]: seed={seed} dhash differs {ha} vs {hb}")
            return False
    print(f"  perceptual_hash_stable: OK across {len(seeds)} seeds")
    return True


def _check_dhash_shape() -> bool:
    # Render a single image and verify dhash format.
    state = _gen(0)
    from mystery_world.world import MysteryEnvironment
    env = MysteryEnvironment(state, visual_mode=True)
    img = env.get_observation_image()
    h = perceptual_hash(img)
    if len(h) != 16 or any(c not in "0123456789abcdef" for c in h):
        print(f"FAIL [dhash_shape]: bad hex hash {h!r}")
        return False
    print(f"  dhash_shape: OK ({h})")
    return True


def _check_hamming_properties() -> bool:
    cases = [
        ("0000000000000000", "0000000000000000", 0),
        ("ffffffffffffffff", "0000000000000000", 64),
        ("aaaaaaaaaaaaaaaa", "5555555555555555", 64),  # all bits flipped
        ("0123456789abcdef", "0123456789abcdef", 0),
    ]
    for h1, h2, expected in cases:
        d = hamming_distance(h1, h2)
        if d != expected:
            print(f"FAIL [hamming_properties]: hamming({h1},{h2})={d}, expected {expected}")
            return False
        # symmetry
        if hamming_distance(h2, h1) != d:
            print(f"FAIL [hamming_properties]: asymmetric for ({h1},{h2})")
            return False
    print("  hamming_properties: OK")
    return True


def _check_reference_roundtrip(seeds: list[int]) -> bool:
    with tempfile.TemporaryDirectory() as td:
        for seed in seeds:
            state = _gen(seed)
            path = Path(td) / f"ref_{seed}.json"
            ref = export_reference(state, path)
            loaded = ReferenceData.load(path)
            if loaded.seed != ref.seed:
                print(f"FAIL [reference_roundtrip]: seed mismatch on load")
                return False
            if loaded.semantic_checksum != ref.semantic_checksum:
                print(f"FAIL [reference_roundtrip]: semantic_checksum mismatch on load")
                return False
            if loaded.config_hash != ref.config_hash:
                print(f"FAIL [reference_roundtrip]: config_hash mismatch on load")
                return False
            if loaded.rendered_hashes != ref.rendered_hashes:
                print(f"FAIL [reference_roundtrip]: rendered_hashes mismatch on load")
                return False
    print(f"  reference_roundtrip: OK across {len(seeds)} seeds")
    return True


def _check_self_verification(seeds: list[int]) -> bool:
    """State verified against its own reference must report semantic_match
    and frames_within_threshold."""
    for seed in seeds:
        state = _gen(seed)
        ref = compute_reference(state)
        report = verify_against_reference(state, ref)
        if not report["semantic_match"]:
            print(f"FAIL [self_verification]: seed={seed} semantic_match False")
            return False
        if not report["config_match"]:
            print(f"FAIL [self_verification]: seed={seed} config_match False")
            return False
        if not report["frame_count_match"]:
            print(f"FAIL [self_verification]: seed={seed} frame_count_match False")
            return False
        if not report["frames_within_threshold"]:
            print(f"FAIL [self_verification]: seed={seed} max_hamming={report['max_frame_hamming']} > {report['hamming_threshold']}")
            return False
        # Same-machine same-process: max hamming should be 0 (byte-identical).
        if report["max_frame_hamming"] != 0:
            print(
                f"FAIL [self_verification]: seed={seed} same-process max_hamming "
                f"={report['max_frame_hamming']}, expected 0"
            )
            return False
    print(f"  self_verification: OK across {len(seeds)} seeds")
    return True


def _check_detects_divergence(seeds: list[int]) -> bool:
    """A reference from one state verified against a state with a different
    state.seed must report semantic_match=False. Input seeds may collapse
    onto the same state.seed under audit-retry, so we search for a pair
    that reached distinct state.seeds before running the check."""
    state_a = _gen(seeds[0])
    state_b = None
    for s in seeds[1:]:
        cand = _gen(s)
        if cand.seed != state_a.seed:
            state_b = cand
            break
    if state_b is None:
        print("SKIP [detects_divergence]: all input seeds collapsed to one state.seed")
        return True
    ref_a = compute_reference(state_a)
    report = verify_against_reference(state_b, ref_a)
    if report["semantic_match"]:
        print(
            f"FAIL [detects_divergence]: distinct state.seeds "
            f"({state_a.seed}, {state_b.seed}) collided in checksum"
        )
        return False
    print(f"  detects_divergence: OK (state.seed {state_a.seed} vs {state_b.seed} flagged different)")
    return True


def main() -> int:
    seeds = list(range(8))
    ok1 = _check_semantic_stable(seeds)
    ok2 = _check_semantic_distinct(seeds)
    ok3 = _check_perceptual_hash_stable(seeds)
    ok4 = _check_dhash_shape()
    ok5 = _check_hamming_properties()
    ok6 = _check_reference_roundtrip(seeds)
    ok7 = _check_self_verification(seeds)
    ok8 = _check_detects_divergence(seeds)
    all_ok = all([ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8])
    print(f"\nM10 overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
