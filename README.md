---
title: MysteryArena
emoji: 🔎
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# MysteryArena — Procedural Murder-Mystery Benchmark (2D)

A fully-automated benchmark for evaluating LLM- and VLM-based detective agents on procedurally generated murder-mystery scenarios. Agents must determine **who** committed the murder, **what** weapon was used, and **where** it occurred — under partial observability, within an action budget, against NPCs that may lie.

> **Branch:** `thong/graphics_2d` — adds a **2D top-down pygame renderer** plus a server-streamed **web client**. Vision-language agents observe rendered images of the current room alongside the textual narration.
>
> Sister branches:
> - [`master`](https://github.com/nguyentthong/mystery-benchmark/tree/master) — textual-only modality.
> - [`thong/graphics_3d`](https://github.com/nguyentthong/mystery-benchmark/tree/thong/graphics_3d) — first-person 3D Godot client.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         MysteryArena (2D)                            │
│                                                                      │
│  ┌────────────────┐   ┌──────────────────────┐   ┌───────────────┐  │
│  │   benchmark/   │──▶│   mystery_world/     │──▶│ evaluation/   │  │
│  │  generate.py   │   │                      │   │  runner.py    │  │
│  │  verify.py     │   │  ┌────────────────┐  │   │  metrics.py   │  │
│  └────────────────┘   │  │   WorldState   │  │   │  trajectory  │  │
│          │            │  │  locations     │  │   └───────┬───────┘  │
│  ┌───────▼──────────┐ │  │  characters    │  │           │          │
│  │  ComplexityConfig│ │  │  evidence      │  │   ┌───────▼───────┐  │
│  │  5 presets:      │ │  └────────────────┘  │   │   agents/     │  │
│  │  TRIVIAL → EXPERT│ │  ┌────────────────┐  │   │  LLMAgent     │  │
│  └──────────────────┘ │  │ renderer/      │  │   │  VLMAgent     │  │
│                       │  │  game.py (pyg) │──┼──▶│  SymbolicVLM  │  │
│  ┌──────────────────┐ │  │  observation   │  │   │  WorldModelVLM│  │
│  │  server/         │ │  │  sprites       │  │   │  HeuristicAgt │  │
│  │  server.py (WS)  │◀┤  │  layout        │  │   │  Oracle*      │  │
│  └──────────────────┘ │  └────────────────┘  │   └───────────────┘  │
│      ▲ HF Spaces      │  ┌────────────────┐  │                      │
│      │ Dockerfile     │  │ events/narrator│  │                      │
│                       │  │ npc_responder  │  │                      │
│                       │  └────────────────┘  │                      │
│                       └──────────────────────┘                      │
└──────────────────────────────────────────────────────────────────────┘
```

The 2D layer is purely a *renderer* over the same canonical `WorldState` and `MysteryEnvironment` used on the textual branch. Every action, score, and event semantics is identical — only the observation channel changes.

---

## Installation

```bash
git clone -b thong/graphics_2d https://github.com/nguyentthong/mystery-benchmark.git
cd mystery-benchmark
uv sync                       # core deps
uv sync --extra server        # add web/Spaces server stack
```

[uv](https://docs.astral.sh/uv/) is the recommended package manager. If you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### API Keys

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # Claude detective + Claude NPCs
export OPENAI_API_KEY="sk-..."          # GPT detective + OpenAI-direct NPCs
export GOOGLE_API_KEY="..."             # Gemini detective
export OPENROUTER_API_KEY="sk-or-..."   # OpenRouter detective + NPCs
```

---

## Quick Start

### Play it yourself

```bash
# Textual REPL (same as master branch)
uv run scripts/play.py --npc-backend openai --npc-model gpt-4o-mini

# 2D pygame window — WASD to walk, E to interact, ESC for menu
uv run scripts/play_game.py
uv run scripts/play_game.py --level EASY --seed 7
uv run scripts/play_game.py --example trivial_seed_0

# Browser (server-streamed pygame frames over WebSocket)
uv run python server/server.py
# open http://localhost:7860
```

### Pre-generated examples

Twenty curated cases (4 per difficulty level) ship in `examples/`:

```bash
python scripts/list_examples.py                       # list all
python scripts/list_examples.py --show easy_seed_0    # full details + oracle walkthrough
```

---

## Generate a benchmark suite

```bash
uv run scripts/generate_benchmark.py \
    --levels TRIVIAL EASY MEDIUM HARD EXPERT \
    --instances-per-level 20 \
    --seed 42 \
    --output-dir data/benchmark_v1
```

---

## Evaluation

There are **three** evaluation entry points on this branch:

1. `scripts/sweep_eval.py` — parallel multi-seed sweep for **text** agents (recommended).
2. `scripts/run_vlm_evaluation.py` — **VLM** agents that take rendered images.
3. `scripts/run_evaluation.py` — legacy single-process runner (kept for reproducibility).

### NPC backends

NPCs are stateful and lying-aware (deception flags injected from ground truth). Pick a backend per run:

| `--npc-provider` / `--npc-backend` | Endpoint | Auth |
|-----|-----|-----|
| `fallback` | deterministic templates | none |
| `openai` | `api.openai.com` | `OPENAI_API_KEY` |
| `openrouter` | `openrouter.ai` | `OPENROUTER_API_KEY` |
| `vllm` | self-hosted (`--npc-url ...`) | `EMPTY` |

`npc_responder.py` only sends the vLLM-only `chat_template_kwargs={"enable_thinking": false}` to vLLM endpoints; for OpenAI it puts `seed` at the top level.

### Text-agent sweep (recommended)

```bash
# Heuristic baseline — no API key needed
uv run scripts/sweep_eval.py --agent heuristic \
    --levels TRIVIAL EASY MEDIUM HARD EXPERT --seeds 0-19 \
    --trajectory-dir results/trajectories --workers 8

# Claude detective + OpenAI NPCs
uv run scripts/sweep_eval.py --agent claude --model claude-sonnet-4-6 \
    --levels TRIVIAL EASY MEDIUM --seeds 0-19 \
    --npc-provider openai --npc-model gpt-4o-mini \
    --trajectory-dir results/trajectories --workers 4

# GPT-4o-mini detective + OpenAI NPCs (cheap smoke test)
uv run scripts/sweep_eval.py --agent chatgpt-mini \
    --levels TRIVIAL --seeds 0-4 \
    --npc-provider openai --npc-model gpt-4o-mini \
    --trajectory-dir results/trajectories --workers 2

# Self-hosted vLLM NPCs
uv run scripts/sweep_eval.py --agent claude --model claude-sonnet-4-6 \
    --levels TRIVIAL EASY MEDIUM --seeds 0-19 \
    --npc-provider vllm --npc-url http://localhost:8200/v1 --npc-model Qwen/Qwen3.5-27B \
    --trajectory-dir results/trajectories --workers 4
```

Built-in agent slots:

| Slot | Provider | Default model |
|---|---|---|
| `heuristic` | — | rule-based |
| `oracle_min` / `oracle_max` | — | calibration oracles |
| `claude` / `claude-opus` | anthropic | `claude-sonnet-4-6` / `claude-opus-4-7` |
| `chatgpt` / `chatgpt-mini` | openai | `gpt-4o` / `gpt-4o-mini` |
| `gemini` | google | `gemini-2.0-flash` |
| `openrouter` | openrouter | `qwen/qwen3.5-27b` |

Output: `results/trajectories/{agent}/{LEVEL}/seed_{n}.jsonl`. Each file is `header → step₁ → … → footer`; the footer carries the full `EpisodeMetrics` dict. Re-running skips seeds whose JSONL already exists — delete a stale file to force re-run.

### VLM-agent evaluation

Three multimodal agent variants on the same JSON action protocol; vision is the new channel.

| `--agent` | Description |
|---|---|
| `vlm` | Pure multimodal baseline: `image + text → action`. |
| `symbolic_vlm` | VLM perception → symbolic KG / constraint solver → policy. |
| `wm_vlm` | VLM-as-world-model: imagined-future planner. |

```bash
# Pure VLM with Claude Sonnet
uv run scripts/run_vlm_evaluation.py \
    --benchmark-dir data/benchmark_v1 \
    --agent vlm --provider anthropic --model claude-sonnet-4-6 \
    --output-dir results/vlm_claude

# Symbolic-augmented VLM with GPT-4o
uv run scripts/run_vlm_evaluation.py \
    --benchmark-dir data/benchmark_v1 \
    --agent symbolic_vlm --provider openai --model gpt-4o \
    --output-dir results/symvlm_gpt4o

# World-model VLM with Gemini
uv run scripts/run_vlm_evaluation.py \
    --benchmark-dir data/benchmark_v1 \
    --agent wm_vlm --provider google --model gemini-2.0-flash \
    --output-dir results/wmvlm_gemini
```

Each run emits per-episode JSON + a `summary.json`. Default multimodal models per provider live in `scripts/run_vlm_evaluation.py:DEFAULT_MODELS` — override with `--model`.

### Aggregate results

```bash
uv run scripts/build_results_table.py \
    --trajectory-dir results/trajectories \
    --output results/
```

Produces `results/summary.csv` and `results/summary.md` with per-(agent, level) means: `n, solve_rate, composite, accusation, triangle, alibi, elimination, avg_actions, avg_tokens`.

For per-edge / per-seed plots: `uv run scripts/analyze_results.py --trajectory-dir results/trajectories`.

### Replay & reproducibility

```bash
uv run scripts/replay.py results/trajectories/claude/MEDIUM/seed_3.jsonl
uv run scripts/verify_reproducibility.py results/trajectories/claude/MEDIUM/seed_3.jsonl
```

### Web / Hugging Face Spaces deployment

Browser play uses a headless pygame loop streaming frames over WebSocket — full-speed pygame, no Pyodide. The `Dockerfile` and HF frontmatter at the top of this README make the repo deployable to a Spaces Docker SDK runtime.

```bash
# Push to a Hugging Face Space
git remote add space https://huggingface.co/spaces/<your-user>/mystery-arena
git push space thong/graphics_2d:main
```

Set `OPENAI_API_KEY` as a Space secret to enable LLM NPCs. See `server/README.md` and `apps/hf_space/README.md` for details.

---

## Metrics

The headline number is **`composite_score`**.

### Per-episode

| Metric | Range | What it measures |
|---|---|---|
| `solved` | 0/1 | All three of (suspect, weapon, location) correct |
| `accusation_score` | 0–1 | Fraction of (suspect, weapon, location) correct |
| `triangle_score` | 0–3 | Sum of F1 over Locard edges (SUSPECT_WEAPON, WEAPON_VICTIM, SUSPECT_ROOM) |
| `alibi_score` | 0–1 | Cited culprit alibi + valid contradiction |
| `elimination_score` | 0–1 | `max(0, (correct − 2·incorrect) / total_innocents)` |
| `examine_efficiency` | 0–1 | `examine_hit / examine_total` |
| `clue_efficiency` | 0–1 | discovered relevant evidence / total relevant |
| `final_belief_accuracy` | 0–1 | Probability mass on the true culprit at the final step |
| `action_efficiency` | 0–1 | `actions_used / budget` |
| `total_tokens` | — | LLM tokens consumed |

### Composite (headline)

```
base      = 0.35·accusation + 0.35·(triangle/3) + 0.15·alibi + 0.15·elimination
composite = base × (0.8 + 0.2·examine_efficiency)
```

VLM evaluation reuses the same scorer, so vision and text agents are directly comparable on the same seeds.

---

## ComplexityConfig Presets

| Level | Locations | Suspects | Budget | Freshness | Route Constraints |
|-------|-----------|----------|--------|-----------|-------------------|
| TRIVIAL | 3 | 2 | 40 | 3.0 | 0 |
| EASY | 4 | 3 | 50 | 2.5 | 0 |
| MEDIUM | 5 | 4 | 75 | 2.0 | 1 |
| HARD | 7 | 5 | 100 | 1.5 | 2 |
| EXPERT | 10 | 7 | 150 | 1.0 | 3 |

Full knob list in `mystery_world/__init__.py:ComplexityConfig`.

---

## NPC Lying System

Lying is deterministically controlled from ground-truth flags — the LLM has no autonomy over deception:

| Condition | Instruction injected |
|-----------|----------------------|
| `char.is_culprit == True` | Deny involvement; claim alibi |
| `alibi_corroboration_is_genuine == False` | Confirm culprit was with you (false alibi) |
| `char.alibi_has_gap == True` | (no instruction — honest but incomplete) |
| Otherwise | (no instruction — fully truthful) |

NPC movement uses Option A (TRIVIAL/EASY/MEDIUM, random walk) or Option B (HARD/EXPERT, routine-based) — controlled by `reactive_events` in `ComplexityConfig`.

---

## 2D-Specific Modules

| Path | Purpose |
|------|---------|
| `mystery_world/renderer/game.py` | Pygame top-down game loop (used by `play_game.py` and `server/`) |
| `mystery_world/renderer/observation.py` | `render_observation_png(state)` — image fed to VLM agents |
| `mystery_world/renderer/sprites.py` | Sprite atlas / category encoding (red diamond = weapon, etc.) |
| `mystery_world/renderer/layout.py` | Tile-grid layout for procedural rooms |
| `agents/_multimodal_client.py` | Unified vision-capable client across Anthropic / OpenAI / Google |
| `agents/vlm_agent.py` | Pure VLM baseline |
| `agents/symbolic_vlm_agent.py` | VLM perception + symbolic reasoning |
| `agents/world_model_vlm_agent.py` | VLM-as-world-model planner |
| `server/server.py` | WebSocket frame-streaming server |
| `apps/hf_space/app.py` | Gradio frontend for HF Spaces |

---

## Adding a New Agent

1. Subclass `BaseAgent` (text) or use `MultimodalClient` (vision).
2. Register the slot in `scripts/sweep_eval.py:AGENT_CONFIGS` (text) or `scripts/run_vlm_evaluation.py:AGENT_CLASSES` (vision).
3. Sweep:

```bash
uv run scripts/sweep_eval.py --agent my_agent \
    --levels TRIVIAL --seeds 0-9 --trajectory-dir results/trajectories
```

---

## Reproducibility

Every benchmark instance is fully determined by:
- A **world seed** (controls procedural generation).
- A **`ComplexityConfig`**.
- A separate **`--npc-seed`** for NPC-LLM determinism.
- Deterministic transition functions.
- JSONL trajectory logs verified by `verify_reproducibility.py`.

---

## Research Questions

1. **RQ1** — How well do current LLM agents gather clues, maintain belief states, and perform abductive/deductive inference over long horizons?
2. **RQ2** — Does explicit symbolic state tracking (text or VLM) improve solve rates and faithfulness vs pure prompting?
3. **RQ3** — How well do agents generalise to novel worlds? **2D-specific:** does adding the visual channel help or hurt?

---

## Citation

```bibtex
@inproceedings{mysteryarena2026,
  title     = {MysteryArena: A Procedural Benchmark for Evaluating
               LLM Agents on Abductive Reasoning in Dynamic Narrative Environments},
  author    = {...},
  booktitle = {...},
  year      = {2026}
}
```
