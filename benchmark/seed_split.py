"""Canonical train/val/test seed split for MysteryBench v1.0.

This module is the *lock*: once instances are generated for these seed
ranges and shipped as v1.0, these constants must not change. Any change
is a benchmark version bump (v1.0 -> v1.1).

Layout:
    Splits are spaced by 1_000_000 (room to grow). Within a split,
    levels are spaced by 10_000:

        base_seed(split)                    -- table below
        seed_for(split, level, i)
            = BASE_SEEDS[split] + int(level) * LEVEL_OFFSET + i,
              for i in [0, N_PER_LEVEL[split]).

Realized vs. nominal:
    The world generator skips seeds whose mystery is not solvable.
    The *nominal* range above is generous; the *realized* set
    (committed as data/benchmark_v1/seeds.json) is the canonical
    reference once generated.
"""

from __future__ import annotations

from enum import Enum

from mystery_world import ComplexityLevel


class Split(str, Enum):
    TRAIN = "train"
    VAL = "val"
    PUBLIC_TEST = "public_test"
    PRIVATE_HELD_OUT = "private_held_out"


BASE_SEEDS: dict[Split, int] = {
    Split.TRAIN:            1_000_000,
    Split.VAL:              2_000_000,
    Split.PUBLIC_TEST:      3_000_000,
    Split.PRIVATE_HELD_OUT: 4_000_000,
}

N_PER_LEVEL: dict[Split, int] = {
    Split.TRAIN:            8_000,
    Split.VAL:              200,
    Split.PUBLIC_TEST:      200,
    Split.PRIVATE_HELD_OUT: 200,
}

LEVEL_OFFSET = 10_000
LEVELS: tuple[ComplexityLevel, ...] = tuple(ComplexityLevel)


def seed_range(split: Split, level: ComplexityLevel) -> range:
    """Nominal seed range for (split, level)."""
    base = BASE_SEEDS[split] + int(level) * LEVEL_OFFSET
    return range(base, base + N_PER_LEVEL[split])


def assigned_split(seed: int) -> tuple[Split, ComplexityLevel] | None:
    """Reverse lookup: which (split, level) does this seed belong to, if any?"""
    for split in Split:
        for level in LEVELS:
            if seed in seed_range(split, level):
                return split, level
    return None


def assert_no_leakage(eval_seeds: list[int], training_seeds: list[int]) -> None:
    """Guard against train/eval contamination. Call before any training run."""
    overlap = set(eval_seeds) & set(training_seeds)
    if overlap:
        raise AssertionError(
            f"Seed leakage: {len(overlap)} seed(s) appear in both eval and training. "
            f"Examples: {sorted(overlap)[:5]}"
        )
