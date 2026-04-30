"""Run VLM agent evaluation on a generated benchmark suite.

Three agent variants:
  vlm           — pure multimodal baseline: image + text → action
  symbolic_vlm  — VLM perception → symbolic KG / constraint solver → policy
  wm_vlm        — VLM-as-world-model: imagined-future planner

All three accept --provider {anthropic, openai, google} and --model.

Usage:
    # Pure VLM with Claude
    uv run scripts/run_vlm_evaluation.py \\
        --benchmark-dir data/benchmark_v1 \\
        --agent vlm --provider anthropic --model claude-sonnet-4-6 \\
        --output-dir results/vlm_claude

    # Symbolic-augmented VLM with GPT-4o
    uv run scripts/run_vlm_evaluation.py \\
        --benchmark-dir data/benchmark_v1 \\
        --agent symbolic_vlm --provider openai --model gpt-4o \\
        --output-dir results/symvlm_gpt4o

    # World-model VLM with Gemini
    uv run scripts/run_vlm_evaluation.py \\
        --benchmark-dir data/benchmark_v1 \\
        --agent wm_vlm --provider google --model gemini-2.0-flash \\
        --output-dir results/wmvlm_gemini
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.symbolic_vlm_agent import SymbolicVLMAgent
from agents.vlm_agent import VLMAgent
from agents.world_model_vlm_agent import WorldModelVLMAgent
from benchmark.generate import load_benchmark_suite
from evaluation.runner import run_benchmark

logger = structlog.get_logger()

AGENT_CLASSES = {
    "vlm": VLMAgent,
    "symbolic_vlm": SymbolicVLMAgent,
    "wm_vlm": WorldModelVLMAgent,
}

# Default multimodal model per provider — override with --model.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "google": "gemini-2.0-flash",
}


def make_agent_factory(agent_kind: str, provider: str, model: str | None):
    cls = AGENT_CLASSES[agent_kind]
    chosen_model = model or DEFAULT_MODELS[provider]

    def factory():
        return cls(
            agent_id=f"{agent_kind}_{provider}",
            provider=provider,
            model=chosen_model,
        )

    return factory


def main() -> None:
    parser = argparse.ArgumentParser(description="Run VLM evaluation on the mystery benchmark")
    parser.add_argument("--benchmark-dir", dest="benchmark", required=True)
    parser.add_argument(
        "--agent",
        choices=list(AGENT_CLASSES.keys()),
        default="vlm",
        help="VLM agent variant: vlm | symbolic_vlm | wm_vlm",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai", "google"],
        default="anthropic",
    )
    parser.add_argument("--model", default=None, help="Override default model for the chosen provider")
    parser.add_argument("--output-dir", dest="output", required=True)
    parser.add_argument("--max-instances", type=int, default=None)
    parser.add_argument(
        "--levels",
        default=None,
        help="Comma-separated complexity levels to run, e.g. '1,2,3'",
    )
    parser.add_argument(
        "--npc-url",
        default=None,
        help="OpenAI-compatible base URL for NPC responder",
    )
    parser.add_argument("--npc-model", default="Qwen/Qwen2.5-27B-Instruct")
    parser.add_argument("--npc-seed", type=int, default=42)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logger.info("loading benchmark", path=args.benchmark)
    instances = load_benchmark_suite(args.benchmark)

    if args.levels:
        target = {int(l) for l in args.levels.split(",")}
        instances = [(ws, lvl) for ws, lvl in instances if lvl in target]
    if args.max_instances:
        instances = instances[: args.max_instances]

    logger.info(
        "starting eval",
        agent=args.agent,
        provider=args.provider,
        model=args.model or DEFAULT_MODELS[args.provider],
        instances=len(instances),
    )

    npc_responder = None
    if args.npc_url:
        from mystery_world.npc_responder import NPCResponder
        npc_responder = NPCResponder(
            base_url=args.npc_url, model=args.npc_model, seed=args.npc_seed,
        )

    results = run_benchmark(
        agent_factory=make_agent_factory(args.agent, args.provider, args.model),
        instances=instances,
        output_dir=args.output,
        verbose=args.verbose,
        npc_responder=npc_responder,
    )

    solved = sum(1 for r in results if r.metrics and r.metrics.solved)
    total = len(results)
    errors = sum(1 for r in results if r.error)
    partial_scores = [
        r.metrics.to_dict().get("partial_score", 0.0)
        for r in results if r.metrics
    ]
    avg_partial = sum(partial_scores) / max(len(partial_scores), 1)

    summary = {
        "agent": args.agent,
        "provider": args.provider,
        "model": args.model or DEFAULT_MODELS[args.provider],
        "total_instances": total,
        "solved": solved,
        "solve_rate": round(solved / max(total, 1), 4),
        "avg_partial_score": round(avg_partial, 4),
        "errors": errors,
        "npc_model": args.npc_model if args.npc_url else "fallback",
    }

    print("\n" + "=" * 50)
    print("VLM RESULTS SUMMARY")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("=" * 50)

    Path(args.output).mkdir(parents=True, exist_ok=True)
    Path(args.output, "summary.json").write_text(json.dumps(summary, indent=2))
    logger.info("done", output=args.output)


if __name__ == "__main__":
    main()
