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

# MysteryArena — Procedural Murder-Mystery Benchmark (3D)

A fully-automated benchmark for evaluating LLM- and VLM-based detective agents on procedurally generated murder-mystery scenarios. Agents must determine **who** committed the murder, **what** weapon was used, and **where** it occurred — under partial observability, within an action budget, against NPCs that may lie.

> **Branch:** `thong/graphics_3d` — adds a **first-person 3D Godot 4 client** that talks to a Python WebSocket wrapper around `MysteryEnvironment`. Vision-language agents observe rendered first-person frames; humans walk around the crime scene with WASD + mouse-look.
>
> Sister branches:
> - [`master`](https://github.com/nguyentthong/mystery-benchmark/tree/master) — textual-only modality.
> - [`thong/graphics_2d`](https://github.com/nguyentthong/mystery-benchmark/tree/thong/graphics_2d) — 2D top-down pygame renderer + web client.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         MysteryArena (3D)                            │
│                                                                      │
│  ┌────────────────┐   ┌──────────────────────┐   ┌───────────────┐  │
│  │   benchmark/   │──▶│   mystery_world/     │──▶│ evaluation/   │  │
│  │  generate.py   │   │                      │   │  runner.py    │  │
│  │  verify.py     │   │  ┌────────────────┐  │   │  metrics.py   │  │
│  │  seed_split    │   │  │   WorldState   │  │   │  trajectory  │  │
│  └────────────────┘   │  │  locations     │  │   └───────┬───────┘  │
│          │            │  │  characters    │  │           │          │
│  ┌───────▼──────────┐ │  │  evidence      │  │   ┌───────▼───────┐  │
│  │  ComplexityConfig│ │  └────────────────┘  │   │   agents/     │  │
│  └──────────────────┘ │  ┌────────────────┐  │   │  LLMAgent     │  │
│                       │  │  godot_client  │  │   │  VLMAgent     │  │
│  ┌──────────────────┐ │  │  renderer/     │  │   │  SymbolicVLM  │  │
│  │  game/  (Godot4) │◀┤  │  events        │  │   │  WorldModelVLM│  │
│  │   project.godot  │ │  │  narrator      │  │   │  Oracle*      │  │
│  │   scenes / *.gd  │ │  │  npc_responder │  │   └───────────────┘  │
│  └────────┬─────────┘ │  └────────────────┘  │                      │
│           │           └──────────────────────┘                      │
│           │ WebSocket  ┌──────────────────────┐                     │
│           └───────────▶│ server/godot_server  │                     │
│                        │ (ws://127.0.0.1:7777)│                     │
│                        └──────────────────────┘                     │
└──────────────────────────────────────────────────────────────────────┘
```

The 3D branch is a *client* over the same canonical `WorldState` and `MysteryEnvironment`. All scoring, events, and NPC behaviour are identical to the textual branch — only the rendering / control channel differs.

---

## Installation

```bash
git clone -b thong/graphics_3d https://github.com/nguyentthong/mystery-benchmark.git
cd mystery-benchmark
uv sync
```

[uv](https://docs.astral.sh/uv/) is the recommended package manager. If you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Godot 4

The 3D client requires **Godot 4.x** (tested on 4.2+). Install from <https://godotengine.org/download> or your package manager.

### Kenney CC0 assets (optional but recommended)

The Godot project loads Kenney CC0 3D models for furniture/items. Run:

```bash
bash scripts/download_kenney_assets.sh                      # check status
bash scripts/download_kenney_assets.sh path/to/kit-1.zip    # extract one
```

Without the assets, the game falls back to colored primitives.

### API Keys

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # Claude detective + Claude NPCs
export OPENAI_API_KEY="sk-..."          # GPT detective + OpenAI-direct NPCs
export GOOGLE_API_KEY="..."             # Gemini detective
export OPENROUTER_API_KEY="sk-or-..."   # OpenRouter detective + NPCs
```

---

## Quick Start

### Play it yourself (3D, first-person)

Run the Python WebSocket server and the Godot client in two terminals.

**Terminal A — Python WebSocket server:**

```bash
uv run python -m server.godot_server --seed 42
# stdout: LISTEN ws://127.0.0.1:7777
```

Server flags:
- `--seed N` — world seed (default `42`)
- `--port N` — TCP port; `0` for an OS-assigned free port
- `--host H` — default `127.0.0.1`
- `--complexity LEVEL` — `TRIVIAL|EASY|MEDIUM|HARD|EXPERT` (default `EASY`)
- `--npc-model MODEL` — OpenAI-compatible model id (default `gpt-4o-mini`)
- `--log-level LEVEL` — default `INFO`

**Terminal B — Godot client:**

```bash
# Linux
godot4 --path game

# macOS
/Applications/Godot.app/Contents/MacOS/Godot --path game
```

Or open `Godot.app` → **Import** → `game/project.godot` → press F5.

Controls: `WASD` move, mouse look, `E` interact, `TAB` inventory, `F` accuse, `ESC` release mouse.

### Play it yourself (textual REPL)

```bash
uv run scripts/play.py --npc-backend openai --npc-model gpt-4o-mini
uv run scripts/play.py --example trivial_seed_0 --npc-backend openai
```

### Pre-generated examples

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

For held-out generalisation evaluation (RQ3), see `benchmark/seed_split.py` and `docs/SEED_SPLIT.md`.

---

## Evaluation

There are **three** evaluation entry points on this branch:

1. `scripts/sweep_eval.py` — parallel multi-seed sweep for **text** agents (recommended).
2. `scripts/run_vlm_evaluation.py` — **VLM** agents (image + text).
3. `scripts/run_evaluation.py` — legacy single-process runner.

### NPC backends

NPCs are stateful and lying-aware. Pick a backend per run:

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

# Gemini detective + OpenRouter NPCs
uv run scripts/sweep_eval.py --agent gemini --model gemini-2.0-flash \
    --levels TRIVIAL EASY MEDIUM --seeds 0-19 \
    --npc-provider openrouter --npc-model qwen/qwen-2.5-72b-instruct \
    --trajectory-dir results/trajectories --workers 4

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

Output: `results/trajectories/{agent}/{LEVEL}/seed_{n}.jsonl`. Each file is `header → step₁ → … → footer`; the footer carries the full `EpisodeMetrics` dict. Re-running skips seeds whose JSONL already exists.

### VLM-agent evaluation

```bash
# Pure VLM with Claude
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

| `--agent` | Description |
|---|---|
| `vlm` | Pure multimodal baseline: `image + text → action`. |
| `symbolic_vlm` | VLM perception → symbolic KG / constraint solver → policy. |
| `wm_vlm` | VLM-as-world-model: imagined-future planner. |

### Aggregate results

```bash
uv run scripts/build_results_table.py \
    --trajectory-dir results/trajectories \
    --output results/
```

Produces `results/summary.csv` and `results/summary.md` with per-(agent, level) means.

For per-edge / per-seed plots: `uv run scripts/analyze_results.py --trajectory-dir results/trajectories`.

For dataset-bias sanity checks: `uv run scripts/audit_bias.py`.

### Replay & reproducibility

```bash
uv run scripts/replay.py results/trajectories/claude/MEDIUM/seed_3.jsonl
uv run scripts/verify_reproducibility.py results/trajectories/claude/MEDIUM/seed_3.jsonl
```

### Generalisation evaluation (RQ3)

```bash
uv run scripts/eval_generalisation.py --help
```

See `docs/SEED_SPLIT.md` for the held-out seed-split protocol and `docs/RUNBOOK.md` for end-to-end experiment recipes.

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

The 3D client uses the *same* scorer as the text and 2D branches — a Locard triangle filed in Godot is identical to one filed in the REPL.

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

## 3D-Specific Modules

| Path | Purpose |
|------|---------|
| `game/project.godot` | Godot 4 project file |
| `game/scenes/main.tscn` | Main scene (player, world, HUD) |
| `game/scripts/player.gd` | First-person controller |
| `game/scripts/room_builder.gd` | Procedural room geometry from `WorldState` |
| `game/scripts/ws_client.gd` | WebSocket client to `godot_server` |
| `game/scripts/hud.gd` | Inventory / accusation / NPC dialog UI |
| `game/config/asset_map.json` | Maps entity ids → Kenney 3D model paths |
| `server/godot_server.py` | WebSocket protocol bridging Godot ↔ `MysteryEnvironment` |
| `mystery_world/godot_client.py` | Python-side helper used by the WS server |
| `agents/_multimodal_client.py` | Unified vision-capable client across providers |
| `agents/vlm_agent.py` | Pure VLM baseline |
| `agents/symbolic_vlm_agent.py` | VLM perception + symbolic reasoning |
| `agents/world_model_vlm_agent.py` | VLM-as-world-model planner |
| `benchmark/seed_split.py` | Train / held-out seed splits for RQ3 |
| `docs/RUNBOOK.md` | End-to-end experiment recipes |
| `docs/SEED_SPLIT.md` | Generalisation-eval methodology |

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
- A **world seed**.
- A **`ComplexityConfig`**.
- A separate **`--npc-seed`** for NPC-LLM determinism.
- Deterministic transition functions.
- JSONL trajectory logs verified by `verify_reproducibility.py`.

For multi-machine reproducibility, lock Godot version and Kenney asset checksums (the WS protocol is deterministic given the same server seed and NPC seed regardless of the client).

---

## Research Questions

1. **RQ1** — How well do current LLM agents gather clues, maintain belief states, and perform abductive/deductive inference over long horizons?
2. **RQ2** — Does explicit symbolic state tracking (text or VLM) improve solve rates and faithfulness vs pure prompting?
3. **RQ3** — How well do agents generalise to novel worlds? **3D-specific:** does first-person spatial perception help, hurt, or have no effect on solve rate vs the textual baseline?

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
