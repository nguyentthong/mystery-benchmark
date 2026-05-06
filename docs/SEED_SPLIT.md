# MysteryBench v1.0 - Seed Split Specification

This is the **canonical, frozen** train/val/test seed split for MysteryBench v1.0.
The lock lives in code at `benchmark/seed_split.py`; this document is the spec
that humans read.

Once v1.0 is published, **these ranges do not change**. Any modification is a
benchmark version bump (v1.0 -> v1.1).

## Splits

| Split              | Purpose                                                         | Public? |
|--------------------|-----------------------------------------------------------------|---------|
| `train`            | Supervision data for SFT / RL. Never used for evaluation.       | Yes     |
| `val`              | Failure analysis, ablations, hyperparam choice. No leaderboard. | Yes     |
| `public_test`      | The headline leaderboard number. Submitted scores live here.    | Yes     |
| `private_held_out` | Re-scoring for top submissions. Detects test-set overfitting.   | **No**  |

## Seed layout

For each `(split, level)`, seeds occupy a contiguous range:

```
seed_for(split, level, i) = BASE_SEEDS[split] + int(level) * LEVEL_OFFSET + i
```

with `LEVEL_OFFSET = 10_000` and:

| Split              | base seed | seeds per level | total (5 levels) |
|--------------------|----------:|----------------:|-----------------:|
| `train`            | 1,000,000 |           8,000 |           40,000 |
| `val`              | 2,000,000 |             200 |            1,000 |
| `public_test`      | 3,000,000 |             200 |            1,000 |
| `private_held_out` | 4,000,000 |             200 |            1,000 |

Concretely (truncated for brevity):

```
train,            TRIVIAL : seeds [1_010_000, 1_018_000)   -- 8,000 seeds
train,            EXPERT  : seeds [1_050_000, 1_058_000)   -- 8,000 seeds
val,              TRIVIAL : seeds [2_010_000, 2_010_200)   --   200 seeds
public_test,      TRIVIAL : seeds [3_010_000, 3_010_200)   --   200 seeds
private_held_out, EXPERT  : seeds [4_050_000, 4_050_200)   --   200 seeds
```

The 1,000,000 gap between splits and 10,000 gap between levels leave generous
headroom; train can grow to 10,000/level without colliding with val.

## Nominal vs. realized

The world generator skips seeds whose mystery is provably unsolvable
(`benchmark/generate.py` -- `verify_solvability`). The ranges above are the
*nominal* allocation. The *realized* set -- the seeds that actually generated
solvable instances -- is committed as `data/benchmark_v1/seeds.json` once the
public test + val + private held-out have been generated. From then on,
`seeds.json` is the canonical reference.

## Rules

1. **Never train on `val`, `public_test`, or `private_held_out` seeds.** Use
   `benchmark.seed_split.assert_no_leakage(eval_seeds, training_seeds)` as a
   guard at the top of any training script.
2. **Never publish the `private_held_out` set.** It lives outside the public
   repo; only the maintainer re-scores submissions on it.
3. **Use the same seed list across every model.** Seeds are paired
   observations -- this halves the n needed for a given statistical power
   compared to independent sampling.
4. **Bump the version on any spec change.** If the generator, scoring code,
   NPC cache, or these seed ranges change in a way that affects scores,
   that is a new version (`v1.0` -> `v1.1`). Do not silently mutate v1.0.

## Verifying the lock

After generating instances, the realized seed set must be a deterministic
function of (generator code, seed range). Verify with:

```bash
uv run python - <<'PY'
from benchmark.seed_split import Split, seed_range
from mystery_world import ComplexityLevel
for split in Split:
    for level in ComplexityLevel:
        r = seed_range(split, level)
        print(f"{split.value:>18s}  level={level.name:<7s}  "
              f"[{r.start}, {r.stop})  n={len(r)}")
PY
```

Output should match the table above exactly. If it doesn't, the lock has been
modified -- audit `benchmark/seed_split.py` before doing anything else.

## Why a private held-out set

Public benchmarks rot. MMLU, GSM8K, HumanEval, ARC -- all leaked into training
sets within a year of release. The cheapest defense is a private set you
control: when a submission claims a state-of-the-art score on `public_test`,
re-score it on `private_held_out`. A large gap means the model overfit to the
public set (intentionally or not).
