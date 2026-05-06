# MysteryBench v1.0 - Evaluation Runbook

End-to-end commands to lock the seed split, generate canonical instances,
serve NPCs reproducibly, and run baseline evaluations across all three tracks
(textual / 2D / 3D).

> **Status legend.** Steps marked **(available now)** can be run today against
> the existing repo. Steps marked **(v1.0 release work)** describe artifacts
> that still need to be built before the public release; the runbook records
> what they will look like.

---

## 0. Prerequisites

```bash
# Repo
cd /mnt/ssd2/thong/mystery-benchmark

# Deps (uses uv; pyproject.toml is authoritative)
uv sync

# API keys (for closed-model baselines; not needed for heuristic / oracles)
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export GOOGLE_API_KEY=...

# GPU host for the NPC server (Qwen3.5-27B fp16 needs ~55GB VRAM;
# fits a single A100/H100 80GB, or 2x consumer 24GB with quantization)
```

---

## 1. Verify the seed split is locked  (available now)

```bash
uv run python - <<'PY'
from benchmark.seed_split import Split, seed_range, assigned_split
from mystery_world import ComplexityLevel

# Print the canonical table.
for split in Split:
    for level in ComplexityLevel:
        r = seed_range(split, level)
        print(f"{split.value:>18s}  level={level.name:<7s}  "
              f"[{r.start}, {r.stop})  n={len(r)}")

# Spot-check the reverse lookup.
assert assigned_split(3_010_005) == (Split.PUBLIC_TEST, ComplexityLevel.TRIVIAL)
assert assigned_split(1_050_500) == (Split.TRAIN, ComplexityLevel.EXPERT)
assert assigned_split(99) is None
print("\nSEED SPLIT LOCK: OK")
PY
```

Output must match `docs/SEED_SPLIT.md` exactly. If it doesn't, audit
`benchmark/seed_split.py` before continuing.

---

## 2. Generate canonical instances  (available now)

For v1.0 release, generate `val`, `public_test`, and `private_held_out` first.
Train can be deferred until training time (it's 40,000 instances and not
needed for evaluation).

The existing `scripts/generate_benchmark.py` accepts a base seed; we drive it
once per split using the locked base seeds.

```bash
# val
uv run scripts/generate_benchmark.py \
    --levels TRIVIAL EASY MEDIUM HARD EXPERT \
    --instances-per-level 200 \
    --seed 2000000 \
    --output-dir data/benchmark_v1/val

# public_test
uv run scripts/generate_benchmark.py \
    --levels TRIVIAL EASY MEDIUM HARD EXPERT \
    --instances-per-level 200 \
    --seed 3000000 \
    --output-dir data/benchmark_v1/public_test

# private_held_out -- write outside the public repo path
uv run scripts/generate_benchmark.py \
    --levels TRIVIAL EASY MEDIUM HARD EXPERT \
    --instances-per-level 200 \
    --seed 4000000 \
    --output-dir /mnt/ssd2/thong/mystery-benchmark-private/private_held_out
```

When you eventually need train data:

```bash
# train (lazy; do this when you start training)
uv run scripts/generate_benchmark.py \
    --levels TRIVIAL EASY MEDIUM HARD EXPERT \
    --instances-per-level 8000 \
    --seed 1000000 \
    --output-dir data/benchmark_v1/train
```

After each split is generated, freeze the realized seed list:

```bash
uv run python - <<'PY'
import json, glob, os
for split in ("val", "public_test"):
    seeds = sorted(
        int(os.path.basename(p).removeprefix("instance_").removesuffix(".json"))
        for p in glob.glob(f"data/benchmark_v1/{split}/level_*/instance_*.json")
    )
    out = f"data/benchmark_v1/{split}/seeds.json"
    json.dump({"split": split, "seeds": seeds, "n": len(seeds)}, open(out, "w"), indent=2)
    print(f"{split}: {len(seeds)} realized seeds -> {out}")
PY
```

`seeds.json` is the canonical reference once shipped. Commit it.

---

## 3. Serve NPCs with Qwen3.5-27B  (v1.0 release work)

NPC dialogue must be deterministic for reproducibility. We pin
[`Qwen/Qwen3.5-27B`](https://huggingface.co/Qwen/Qwen3.5-27B) (Apache-2.0).

```bash
# One-time: pull weights
uv pip install vllm
huggingface-cli download Qwen/Qwen3.5-27B --local-dir /models/Qwen3.5-27B

# Serve at OpenAI-compatible endpoint on port 8000
vllm serve /models/Qwen3.5-27B \
    --served-model-name Qwen/Qwen3.5-27B \
    --host 0.0.0.0 --port 8000 \
    --max-model-len 32768 \
    --dtype float16 \
    --seed 0
```

Pin the **vLLM version**, **dtype**, and **seed**. Bake them into a Docker
image (`mysterybench/npc-server:v1.0`) and ship that image alongside the
benchmark. Outputs change across vLLM versions even at temp=0.

---

## 4. Build the frozen NPC response cache  (v1.0 release work)

The cache is a `(seed, character_id, question_template) -> response` lookup,
generated once and shipped with the benchmark so users don't need a GPU to
evaluate.

This script does not exist yet. Sketch:

```bash
# scripts/build_npc_cache.py  (TO BE WRITTEN)
uv run scripts/build_npc_cache.py \
    --benchmark-dir data/benchmark_v1/public_test \
    --npc-url http://localhost:8000/v1 \
    --npc-model Qwen/Qwen3.5-27B \
    --output data/benchmark_v1/npc_cache_public_test.sqlite \
    --temperature 0.0 \
    --batch-size 1 \
    --question-templates prompts/npc_question_templates.json
```

Cache size estimate: ~200 seeds/level x 5 levels x ~6 characters x ~20
question templates ~= 120k generations per split. Single A100 with vLLM
at ~30 gen/s -> roughly 1 hour per split.

Cache-miss policy at eval time (must be documented in the spec):
- **Replay-trust** (recommended): submitter's trajectory log records the
  response, scorer trusts the log. Free-form questions are allowed; the
  cache only locks responses for canonical question templates.
- Alternative: live-call the same Qwen3.5-27B server. Slower for users.
- Alternative: canonical fallback ("the witness shrugs"). Reproducible but
  caps NPC realism.

---

## 5. Run baselines on `val`  (available now, with current agent set)

`val` is for failure analysis and tuning. Keep `public_test` untouched until
you are confident in the prompt and parsing code -- otherwise you are
implicitly tuning on the test set.

### Lower / upper bounds (no API keys)

```bash
# Heuristic baseline (lower bound)
uv run scripts/run_evaluation.py \
    --benchmark-dir data/benchmark_v1/val \
    --agent heuristic \
    --output-dir results/val/heuristic

# Oracle agents (upper bounds)
# NOTE: scripts/run_evaluation.py currently knows heuristic + closed-model agents.
# Add max/min oracles to its registry, OR run directly:
uv run python - <<'PY'
from agents.maximum_score_oracle_agent import MaximumScoreOracleAgent
from agents.minimum_action_oracle_agent import MinimumActionOracleAgent
from benchmark.generate import load_benchmark_suite
from evaluation.runner import run_benchmark

instances = load_benchmark_suite("data/benchmark_v1/val")
run_benchmark(lambda: MaximumScoreOracleAgent(), instances, "results/val/oracle_max")
run_benchmark(lambda: MinimumActionOracleAgent(), instances, "results/val/oracle_min")
PY
```

### Frontier closed-model baselines (text track)

```bash
# Claude Opus 4.7
uv run scripts/run_evaluation.py \
    --benchmark-dir data/benchmark_v1/val \
    --agent claude --model claude-opus-4-7 \
    --output-dir results/val/claude_opus_4_7

# Claude Sonnet 4.6
uv run scripts/run_evaluation.py \
    --benchmark-dir data/benchmark_v1/val \
    --agent claude --model claude-sonnet-4-6 \
    --output-dir results/val/claude_sonnet_4_6

# Claude Haiku 4.5
uv run scripts/run_evaluation.py \
    --benchmark-dir data/benchmark_v1/val \
    --agent claude --model claude-haiku-4-5 \
    --output-dir results/val/claude_haiku_4_5

# GPT-5 (substitute exact dated model id)
uv run scripts/run_evaluation.py \
    --benchmark-dir data/benchmark_v1/val \
    --agent chatgpt --model gpt-5 \
    --output-dir results/val/gpt_5

# Gemini 2.5 Pro
uv run scripts/run_evaluation.py \
    --benchmark-dir data/benchmark_v1/val \
    --agent gemini --model gemini-2.5-pro \
    --output-dir results/val/gemini_2_5_pro
```

### Open-weights baselines (text track)

Requires the OpenAI-compatible client to accept a custom `base_url`. As of
this runbook, `agents/llm_agent.py` and `agents/_multimodal_client.py` do
not yet pass `base_url` through -- add a ~5-line param to both classes and
wire it via a CLI flag.

```bash
# Once the base_url plumb is done:
uv run scripts/run_evaluation.py \
    --benchmark-dir data/benchmark_v1/val \
    --agent openai-compatible \
    --base-url http://your-vllm-host:8001/v1 \
    --model Qwen/Qwen2.5-72B-Instruct \
    --output-dir results/val/qwen2_5_72b
```

### VLM baselines (2D and 3D tracks)

```bash
# 2D, Claude Opus 4.7
uv run scripts/run_vlm_evaluation.py \
    --benchmark-dir data/benchmark_v1/val \
    --track 2d \
    --agent claude --model claude-opus-4-7 \
    --output-dir results/val/2d/claude_opus_4_7

# 3D, same agent
uv run scripts/run_vlm_evaluation.py \
    --benchmark-dir data/benchmark_v1/val \
    --track 3d \
    --agent claude --model claude-opus-4-7 \
    --output-dir results/val/3d/claude_opus_4_7
```

> The current `scripts/run_vlm_evaluation.py` may not yet expose `--track`.
> When wiring it up, the track selector must only swap the observation
> adapter (per CLAUDE.md invariants 2 and 22). The agent code does not change.

### Run with reproducible NPCs

Once the NPC server is up (step 3), append to any of the above:

```bash
    --npc-url http://localhost:8000/v1 \
    --npc-model Qwen/Qwen3.5-27B
```

Or, when the cache exists (step 4):

```bash
    --npc-cache data/benchmark_v1/npc_cache_val.sqlite
```

---

## 6. Aggregate and inspect

```bash
uv run scripts/analyze_results.py \
    --results-dir results/val \
    --output reports/val_summary.json
```

Per-cell metric to track: solve rate, accusation accuracy, steps-to-solve
normalized by `MinimumActionOracleAgent` length, perception sub-score
(VLM only). Stratify by complexity level -- a flat overall number hides
where models break.

---

## 7. Run on `public_test` for headline numbers  (after val is dialed in)

Identical to step 5, but with `--benchmark-dir data/benchmark_v1/public_test`.
Do this **once** per (model, track), with N=3 reruns for closed models to
quantify decoding nondeterminism.

```bash
for run in 1 2 3; do
  uv run scripts/run_evaluation.py \
      --benchmark-dir data/benchmark_v1/public_test \
      --agent claude --model claude-opus-4-7 \
      --output-dir results/public_test/run${run}/claude_opus_4_7
done
```

Report median solve rate across the three runs, plus the std as the noise
floor.

---

## 8. Re-score top submissions on `private_held_out`  (maintainer only)

```bash
uv run scripts/run_evaluation.py \
    --benchmark-dir /mnt/ssd2/thong/mystery-benchmark-private/private_held_out \
    --agent <submitted-config> \
    --output-dir /mnt/ssd2/thong/mystery-benchmark-private/results/<submission_id>
```

Publish both `public_test` and `private_held_out` numbers on the leaderboard.
A gap > ~5% indicates overfitting; flag those submissions.

---

## 9. Submission protocol  (v1.0 release work)

Goal: the maintainer (you) re-runs the canonical scorer over a submitter's
trajectory log; submitters do not run the scorer themselves.

```
submission/
  metadata.json       # exact API model id, decoding params, agent config, repo SHA
  trajectories/
    seed_3010000.json # full action+observation sequence for one episode
    seed_3010001.json
    ...
```

The replay scorer (`scripts/replay_score.py`, **TO BE WRITTEN**) takes that
directory, regenerates the world from each seed, replays the actions, and
computes scores. It must run with no API keys -- only generator + scorer +
NPC cache. This is what makes verification cheap.

---

## What's already built vs. what's still needed for v1.0

| Capability                                | State |
|-------------------------------------------|-------|
| World generator, deterministic by seed    | Built |
| Heuristic, oracle, LLM, VLM agents        | Built |
| Per-episode runner + metrics              | Built |
| Seed split lock (`benchmark/seed_split.py`) | Built (this PR) |
| Generate val / public_test / private_held_out | Command ready, not yet run |
| `--base-url` for self-hosted models       | Needs ~5 line client change |
| 3D track wired into VLM runner            | Track adapter needed |
| NPC server image + cache builder          | Not built |
| Replay-based offline scorer               | Not built |
| Public Docker image + HF Space submission | Not built |

The first three rows in the "not built" group are the critical path to a
public v1.0 release.
