#!/usr/bin/env python3
"""Run only Arena matches that are missing from the public HF dataset.

The public Arena dataset is keyed by match ids of the form:

    detective__vs__culprit__LEVEL__seed_N

This script downloads the current ``matches/all_matches.jsonl.gz`` from HF,
computes the target matrix, removes already-published successful match ids,
and optionally executes the remaining jobs through ``scripts.arena_run._run_one``.

Examples:
    # Inspect the current missing set only.
    uv run python scripts/run_missing_arena_matches.py

    # Smoke test the first two missing jobs without publishing.
    uv run python scripts/run_missing_arena_matches.py --execute --max-jobs 2 --no-publish-hf

    # Run and publish the full missing set.
    uv run python scripts/run_missing_arena_matches.py --execute --workers 4

    # Run with concurrent workers and publish after every completed match.
    uv run python scripts/run_missing_arena_matches.py --execute --workers 30 --publish-each-match

    # Include non-LLM baselines by adding them to the role-specific rosters.
    uv run python scripts/run_missing_arena_matches.py --execute --workers 20 \
        --detectives deepseek-v4-pro,glm-4.7,glm-5,glm-5.1,gpt-5.4-ptu,gpt-5.5,kimi-k2.5,minimax-m2.7,heuristic \
        --culprits deepseek-v4-pro,glm-4.7,glm-5,glm-5.1,gpt-5.4-ptu,gpt-5.5,kimi-k2.5,minimax-m2.7,passive
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.base_agent import BaseAgent
from arena.aggregate import write_outputs
from arena.hf_publish import HFPublishError, publish_run_to_hf
from arena.roster import ModelSpec, get_model
from mystery_world import ComplexityLevel
from scripts import arena_run as arena_run_mod
from scripts.arena_run import _load_env_file


DEFAULT_REPO_ID = "Elfsong/Mystery_Arena_Results"
DEFAULT_MODELS = [
    "deepseek-v4-pro",
    "glm-4.7",
    "glm-5",
    "glm-5.1",
    "gpt-5.4-ptu",
    "gpt-5.5",
    "kimi-k2.5",
    "minimax-m2.7",
]
DEFAULT_LEVELS = ["TRIVIAL", "EASY", "MEDIUM", "HARD", "EXPERT"]
LEVEL_ORDER = {level: i for i, level in enumerate(DEFAULT_LEVELS)}
MATCH_ID_RE = re.compile(
    r"^(?P<detective>.+)__vs__(?P<culprit>.+)__"
    r"(?P<level>[A-Z]+)__seed_(?P<seed>\d+)$"
)


Job = dict[str, str | int]
Pair = tuple[str, str]


def _now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in _parse_csv(spec):
        if "-" in part:
            start, end = part.split("-", 1)
            out.extend(range(int(start), int(end) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def _parse_levels(values: list[str]) -> list[str]:
    levels: list[str] = []
    for value in values:
        levels.extend(part.upper() for part in _parse_csv(value))
    return sorted(set(levels), key=lambda level: (LEVEL_ORDER.get(level, 99), level))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _load_hf_matches(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.existing_matches:
        return _read_jsonl(Path(args.existing_matches))

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise SystemExit(
            "huggingface_hub is required. Run with `uv run ...` so project dependencies are active."
        ) from exc

    path = hf_hub_download(
        repo_id=args.repo_id,
        repo_type="dataset",
        filename="matches/all_matches.jsonl.gz",
        revision=args.revision,
        token=os.environ.get("HF_TOKEN"),
    )
    return _read_jsonl(Path(path))


def _match_key(record: dict[str, Any]) -> tuple[str, str, str, int] | None:
    """Return (detective, culprit, level, seed_suffix).

    Prefer the seed suffix embedded in match_id. Historical records can have
    ``record["seed"]`` shifted by generator retries, while the public coverage
    contract is expressed by the match id.
    """
    match_id = str(record.get("match_id") or "")
    m = MATCH_ID_RE.match(match_id)
    if m:
        return (
            m.group("detective"),
            m.group("culprit"),
            m.group("level"),
            int(m.group("seed")),
        )

    detective = (record.get("detective") or {}).get("name")
    culprit = (record.get("culprit") or {}).get("name")
    level = record.get("level")
    seed = record.get("seed")
    if detective is None or culprit is None or level is None or seed is None:
        return None
    return (str(detective), str(culprit), str(level), int(seed))


def _existing_success_keys(
    records: list[dict[str, Any]],
    *,
    include_errors: bool,
) -> set[tuple[str, str, str, int]]:
    keys: set[tuple[str, str, str, int]] = set()
    for record in records:
        if record.get("error") and not include_errors:
            continue
        key = _match_key(record)
        if key is not None:
            keys.add(key)
    return keys


def _target_pairs(detectives: list[str], culprits: list[str]) -> list[Pair]:
    pairs: list[Pair] = []
    seen: set[Pair] = set()
    for detective in detectives:
        for culprit in culprits:
            pair = (detective, culprit)
            if pair in seen:
                continue
            seen.add(pair)
            pairs.append(pair)
    return pairs


def _target_jobs(
    *,
    pairs: list[Pair],
    levels: list[str],
    seeds: list[int],
    existing: set[tuple[str, str, str, int]],
) -> list[Job]:
    jobs: list[Job] = []
    for level in levels:
        for seed in seeds:
            for detective, culprit in pairs:
                if (detective, culprit, level, seed) in existing:
                    continue
                jobs.append(
                    {
                        "detective": detective,
                        "culprit": culprit,
                        "level": level,
                        "seed": seed,
                    }
                )
    return jobs


def _job_sort_key(job: Job) -> tuple[int, int, str, str]:
    # EXPERT first by default because it is completely empty in the current dataset.
    priority = {"EXPERT": 0, "HARD": 1, "EASY": 2, "MEDIUM": 3, "TRIVIAL": 4}
    level = str(job["level"])
    return (priority.get(level, 99), int(job["seed"]), str(job["detective"]), str(job["culprit"]))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _write_jobs_jsonl(path: Path, jobs: list[Job]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for job in jobs:
            fh.write(json.dumps(job, ensure_ascii=False) + "\n")


def _print_coverage(
    *,
    pairs: list[Pair],
    levels: list[str],
    seeds: list[int],
    existing: set[tuple[str, str, str, int]],
    missing: list[Job],
) -> None:
    expected_per_level = len(pairs) * len(seeds)
    missing_by_level = Counter(str(job["level"]) for job in missing)
    print("Coverage against target matrix:")
    for level in levels:
        missing_n = missing_by_level[level]
        current = expected_per_level - missing_n
        print(f"  {level:<7} {current:>4}/{expected_per_level:<4} missing={missing_n}")
    print(f"Total missing jobs: {len(missing)}")


def _configure_gateway(args: argparse.Namespace, specs: list[ModelSpec]) -> None:
    llm_specs = {
        (spec.name, spec.model): spec
        for spec in specs
        if spec.kind == "llm" and spec.model
    }
    if not llm_specs:
        print("No LLM models selected; skipping LLM gateway configuration.")
        return

    if not args.litellm_url:
        args.litellm_url = os.environ.get(args.gateway_url_env)
    if not args.litellm_key_env and os.environ.get(args.gateway_key_env):
        args.litellm_key_env = args.gateway_key_env
    if not args.litellm_url:
        raise SystemExit(
            f"Missing gateway URL. Set {args.gateway_url_env} in {args.env_file} "
            "or pass --litellm-url."
        )
    if not args.litellm_key_env or not os.environ.get(args.litellm_key_env):
        raise SystemExit(
            f"Missing gateway key env. Set {args.gateway_key_env} in {args.env_file} "
            "or pass --litellm-key-env."
        )
    BaseAgent.configure_litellm(
        args.litellm_url,
        api_key_env=args.litellm_key_env,
        model=args.litellm_model,
    )
    print(
        f"LLM gateway configured: {args.litellm_url} "
        f"(key_env={args.litellm_key_env}, model={args.litellm_model or 'per-role'})"
    )

    if args.preflight:
        _preflight_models(
            list(llm_specs.values()),
            base_url=args.litellm_url,
            api_key_env=args.litellm_key_env,
        )


def _preflight_models(
    specs: list[ModelSpec],
    *,
    base_url: str,
    api_key_env: str,
) -> None:
    from openai import OpenAI

    api_key = os.environ.get(api_key_env)
    client = OpenAI(base_url=base_url, api_key=api_key)
    failures: list[str] = []
    for spec in sorted(specs, key=lambda item: item.name):
        try:
            client.chat.completions.create(
                model=spec.model,
                messages=[{"role": "user", "content": "Reply with OK."}],
                max_tokens=16,
            )
            print(f"Preflight {spec.name}: ok")
        except Exception as exc:  # noqa: BLE001 - surface provider failures before a long run.
            message = str(exc).splitlines()[0][:240]
            print(f"Preflight {spec.name}: FAILED - {message}")
            failures.append(spec.name)
    if failures:
        raise SystemExit(f"Preflight failed for: {', '.join(failures)}")


def _specs_for_jobs(jobs: list[Job]) -> tuple[dict[str, ModelSpec], dict[str, ModelSpec]]:
    detective_names = sorted({str(job["detective"]) for job in jobs})
    culprit_names = sorted({str(job["culprit"]) for job in jobs})
    detectives = {name: get_model(name, role="detective") for name in detective_names}
    culprits = {name: get_model(name, role="culprit") for name in culprit_names}
    return detectives, culprits


def _write_run_metadata(
    *,
    out_dir: Path,
    run_id: str,
    jobs: list[Job],
    detectives: dict[str, ModelSpec],
    culprits: dict[str, ModelSpec],
    args: argparse.Namespace,
) -> None:
    levels = sorted({str(job["level"]) for job in jobs}, key=lambda level: (LEVEL_ORDER.get(level, 99), level))
    seeds = sorted({int(job["seed"]) for job in jobs})
    npc = {
        "provider": "fallback",
        "model": "gpt-4o-mini",
        "url": None,
        "seed": 42,
        "prompt_policy": "role_facts_only_no_strategy",
    }
    config = {
        "run_id": run_id,
        "mode": "missing_matrix",
        "levels": levels,
        "seeds": seeds,
        "npc": npc,
        "detectives": [spec.to_dict() for spec in detectives.values()],
        "culprits": [spec.to_dict() for spec in culprits.values()],
        "target_pairs": [
            {"detective": detective, "culprit": culprit}
            for detective, culprit in sorted(
                {
                    (str(job["detective"]), str(job["culprit"]))
                    for job in jobs
                }
            )
        ],
        "rating": {"bootstrap_samples": args.bootstrap_samples},
        "llm_gateway": {
            "url": args.litellm_url,
            "key_env": args.litellm_key_env,
            "model_override": args.litellm_model,
        },
        "source_dataset": {
            "repo_id": args.repo_id,
            "revision": args.revision,
            "coverage_seed_source": "match_id_suffix",
        },
        "missing_jobs": len(jobs),
        "smoke_action_budget": args.smoke_action_budget,
    }
    _write_json(out_dir / "config.json", config)
    _write_json(
        out_dir / "roster.json",
        {
            "detectives": [spec.to_dict() for spec in detectives.values()],
            "culprits": [spec.to_dict() for spec in culprits.values()],
        },
    )
    _write_jobs_jsonl(out_dir / "missing_jobs.jsonl", jobs)


def _write_matches(path: Path, matches: list[dict[str, Any]]) -> None:
    matches.sort(
        key=lambda m: (
            str(m.get("level") or ""),
            int(m.get("seed") or 0),
            str((m.get("detective") or {}).get("name") or ""),
            str((m.get("culprit") or {}).get("name") or ""),
            str(m.get("match_id") or ""),
        )
    )
    with path.open("w", encoding="utf-8") as fh:
        for match in matches:
            fh.write(json.dumps(match, ensure_ascii=False, default=str) + "\n")


def _run_jobs(
    *,
    jobs: list[Job],
    out_dir: Path,
    run_id: str,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    detectives, culprits = _specs_for_jobs(jobs)
    _write_run_metadata(
        out_dir=out_dir,
        run_id=run_id,
        jobs=jobs,
        detectives=detectives,
        culprits=culprits,
        args=args,
    )

    npc = {
        "provider": "fallback",
        "model": "gpt-4o-mini",
        "url": None,
        "seed": 42,
        "prompt_policy": "role_facts_only_no_strategy",
    }
    matches: list[dict[str, Any]] = []
    lock = threading.Lock()
    incremental = out_dir / "matches.incremental.jsonl"

    def publish_progress(completed: int, match_id: str) -> None:
        if not args.publish_hf or not args.publish_each_match:
            return
        last_error: Exception | None = None
        for attempt in range(1, args.publish_retries + 1):
            try:
                result = publish_run_to_hf(
                    out_dir,
                    repo_id=args.repo_id,
                    private=args.private,
                    revision=args.revision,
                    create_pr=args.create_pr,
                    include_model_responses=args.include_model_responses,
                    commit_message=f"Fill missing Arena match {match_id} ({completed}/{len(jobs)})",
                )
                break
            except Exception as exc:  # noqa: BLE001 - retry transient hub/network failures.
                last_error = exc
                if attempt >= args.publish_retries:
                    raise
                delay = min(60, 5 * attempt)
                print(
                    f"HF publish failed for {match_id} "
                    f"(attempt {attempt}/{args.publish_retries}): {exc}. "
                    f"Retrying in {delay}s.",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
        else:
            raise HFPublishError(f"HF publish failed for {match_id}: {last_error}")
        print(
            f"Published HF progress: {completed}/{len(jobs)} "
            f"run={result['run_id']} match={match_id}",
            flush=True,
        )

    def run_one(idx: int, job: Job) -> dict[str, Any]:
        detective = detectives[str(job["detective"])]
        culprit = culprits[str(job["culprit"])]
        level = str(job["level"])
        seed = int(job["seed"])
        print(
            f"[{idx}/{len(jobs)}] detective={detective.name} culprit={culprit.name} "
            f"level={level} seed={seed}",
            flush=True,
        )
        match = arena_run_mod._run_one(
            run_id=run_id,
            out_dir=out_dir,
            detective=detective,
            culprit=culprit,
            npc=npc,
            level=level,
            seed=seed,
            skip_existing=args.resume,
            progress=None,
        )
        with lock:
            matches.append(match)
            with incremental.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(match, ensure_ascii=False, default=str) + "\n")
            _write_matches(out_dir / "matches.jsonl", matches)
            publish_progress(len(matches), str(match.get("match_id") or idx))
        return match

    try:
        if args.workers <= 1:
            for idx, job in enumerate(jobs, 1):
                run_one(idx, job)
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {
                    pool.submit(run_one, idx, job): job
                    for idx, job in enumerate(jobs, 1)
                }
                for future in as_completed(futures):
                    future.result()
    except KeyboardInterrupt:
        print("Interrupted. Writing completed matches before exiting.", file=sys.stderr)
    finally:
        _write_matches(out_dir / "matches.jsonl", matches)

    return matches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", default=os.environ.get("ARENA_HF_DATASET", DEFAULT_REPO_ID))
    parser.add_argument("--revision", default=None)
    parser.add_argument("--existing-matches", default=None, help="Use a local all_matches jsonl/jsonl.gz instead of HF.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--detectives", default=None, help="Comma-separated detective roster. Defaults to --models.")
    parser.add_argument("--culprits", default=None, help="Comma-separated culprit roster. Defaults to --models.")
    parser.add_argument("--levels", nargs="+", default=DEFAULT_LEVELS)
    parser.add_argument("--seeds", default="0-4")
    parser.add_argument("--out", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--jobs-file", default=None)
    parser.add_argument("--max-jobs", type=int, default=None, help="Limit jobs after sorting; useful for smoke tests.")
    parser.add_argument(
        "--smoke-action-budget",
        type=int,
        default=None,
        help=(
            "Temporarily reduce max_agent_actions for smoke runs. "
            "Only allowed with --no-publish-hf."
        ),
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--execute", action="store_true", help="Actually run jobs. Without this, only writes a plan.")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-error-records", action="store_true", help="Treat errored HF records as already covered.")
    parser.add_argument("--gateway-url-env", default="LLM_GATEWAY_URL")
    parser.add_argument("--gateway-key-env", default="LLM_GATEWAY_API_KEY")
    parser.add_argument("--litellm-url", default=None)
    parser.add_argument("--litellm-key-env", default=None)
    parser.add_argument("--litellm-model", default=None)
    parser.add_argument("--preflight", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--publish-hf", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--publish-each-match",
        action="store_true",
        help="When publishing is enabled, upload the run to HF after every completed match.",
    )
    parser.add_argument("--publish-retries", type=int, default=3)
    parser.add_argument("--private", action="store_true")
    parser.add_argument("--create-pr", action="store_true")
    parser.add_argument("--include-model-responses", action=argparse.BooleanOptionalAction, default=False)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    _load_env_file(args.env_file)

    models = _parse_csv(args.models)
    detectives = _parse_csv(args.detectives) if args.detectives else list(models)
    culprits = _parse_csv(args.culprits) if args.culprits else list(models)
    target_pairs = _target_pairs(detectives, culprits)
    levels = _parse_levels(args.levels)
    seeds = _parse_seeds(args.seeds)
    records = _load_hf_matches(args)
    existing = _existing_success_keys(records, include_errors=args.include_error_records)
    all_missing_jobs = _target_jobs(pairs=target_pairs, levels=levels, seeds=seeds, existing=existing)
    all_missing_jobs.sort(key=_job_sort_key)
    _print_coverage(pairs=target_pairs, levels=levels, seeds=seeds, existing=existing, missing=all_missing_jobs)

    jobs = all_missing_jobs
    if args.max_jobs is not None:
        jobs = all_missing_jobs[: args.max_jobs]
        print(f"Selected jobs after --max-jobs: {len(jobs)} / {len(all_missing_jobs)}")

    run_id = args.run_id or f"fill_missing_arena_{_now_stamp()}"
    out_dir = Path(args.out) if args.out else Path("arena/results") / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs_file = Path(args.jobs_file) if args.jobs_file else out_dir / "missing_jobs.jsonl"
    _write_jobs_jsonl(jobs_file, jobs)
    print(f"Missing job plan: {jobs_file}")
    print(f"Run id: {run_id}")
    print(f"Out dir: {out_dir}")

    if not args.execute:
        print("Dry run only. Re-run with --execute to start jobs.")
        return 0
    if not jobs:
        print("No missing jobs to run.")
        return 0
    if args.smoke_action_budget is not None and args.publish_hf:
        raise SystemExit("--smoke-action-budget is only allowed with --no-publish-hf")
    if args.publish_each_match and not args.publish_hf:
        raise SystemExit("--publish-each-match requires --publish-hf")
    if args.smoke_action_budget is not None:
        for level in sorted({str(job["level"]) for job in jobs}):
            arena_run_mod.COMPLEXITY_PRESETS[ComplexityLevel[level]] = replace(
                arena_run_mod.COMPLEXITY_PRESETS[ComplexityLevel[level]],
                max_agent_actions=args.smoke_action_budget,
            )
        print(f"Smoke action budget override: max_agent_actions={args.smoke_action_budget}")

    detective_specs, culprit_specs = _specs_for_jobs(jobs)
    _configure_gateway(args, [*detective_specs.values(), *culprit_specs.values()])
    matches = _run_jobs(jobs=jobs, out_dir=out_dir, run_id=run_id, args=args)
    outputs = write_outputs(out_dir, bootstrap_samples=args.bootstrap_samples)
    print(
        f"Completed local run: matches={len(matches)} "
        f"summary={outputs.get('summary', {})}"
    )

    if args.publish_hf and not args.publish_each_match:
        try:
            result = publish_run_to_hf(
                out_dir,
                repo_id=args.repo_id,
                private=args.private,
                revision=args.revision,
                create_pr=args.create_pr,
                include_model_responses=args.include_model_responses,
                commit_message=f"Fill missing Arena matches {run_id}",
            )
            print(f"Published to HF: {result['repo_id']} run={result['run_id']}")
        except HFPublishError as exc:
            print(f"HF publish failed: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
