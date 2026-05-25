# Mystery Arena Results Insights - 2026-05-25

## Data Snapshot

Source: `Elfsong/Mystery_Arena_Results` on Hugging Face, `matches/all_matches.jsonl.gz`.

The analysis below uses the dataset state observed on 2026-05-25 after the baseline upload.
No new matches were executed for this analysis.

| Metric | Count |
| --- | ---: |
| Raw rows | 1,759 |
| Unique `match_id` values | 1,694 |
| Duplicate `match_id` values | 64 |
| Extra duplicate rows | 65 |
| Baseline rows | 424 |
| Main partial run rows | 584 |

The duplicate rows are mostly early TRIVIAL reruns. For model-level conclusions, use
deduplicated `match_id` rows or stratify by run to avoid overweighting early smoke/rerun data.

## Coverage

Current coverage for the target 8-model LLM matrix:

| Difficulty | Completed | Expected | Notes |
| --- | ---: | ---: | --- |
| TRIVIAL | 320 | 320 | Complete |
| EASY | 16 | 320 | Very sparse |
| MEDIUM | 320 | 320 | Complete |
| HARD | 293 | 320 | Partial |
| EXPERT | 320 | 320 | Complete |

Baseline coverage is complete at 424 matches:

- `heuristic` detective against all LLM culprits plus `passive`.
- all LLM detectives against `passive` culprit.
- includes `heuristic` vs `passive`.

## Difficulty Signal

The clearest result is a steep difficulty cliff. On the deduplicated 8-model LLM matrix:

| Difficulty | Matches | Detective payoff | Solve rate | Evidence discovered | Actions |
| --- | ---: | ---: | ---: | ---: | ---: |
| TRIVIAL | 320 | 0.444 | 73.1% | 4.32 | 33.5 |
| EASY | 16 | 0.259 | 12.5% | 3.56 | 46.2 |
| MEDIUM | 320 | 0.195 | 26.2% | 2.24 | 69.6 |
| HARD | 293 | 0.046 | 0.0% | 0.44 | 99.3 |
| EXPERT | 320 | 0.036 | 0.0% | 1.14 | 145.1 |

HARD and EXPERT are not near-misses. The detectives almost never solve the case and discover
very little evidence before exhausting a large part of the action budget.

## Baseline Signal

The non-LLM baseline is more informative than expected.

`heuristic` detective has a much stronger partial-score profile than LLM detectives in the
`passive` culprit setting:

| Setting | Detective payoff | Solve rate | Partial score | Evidence discovered |
| --- | ---: | ---: | ---: | ---: |
| `heuristic` detective, all baseline culprits | 0.353 | 4.0% | 0.439 | 4.0 |
| LLM detective vs `passive` culprit | near 0 | 0.0% | near 0 | near 0 |

For LLM detective vs `passive`, TRIVIAL through HARD show average `examine = 0` and
`evidence_discovered = 0`. This looks more like an action policy or interface failure than a
pure model-capability result. The LLM detectives spend actions but do not enter a useful
evidence-gathering loop.

## Model-Level Read

Because EASY is sparse and HARD is partial, the safest balanced comparison uses the currently
complete TRIVIAL, MEDIUM, and EXPERT levels.

Balanced detective payoff across TRIVIAL, MEDIUM, and EXPERT:

| Detective | Payoff |
| --- | ---: |
| `gpt-5.5` | 0.261 |
| `glm-5.1` | 0.247 |
| `glm-5` | 0.238 |
| `deepseek-v4-pro` | 0.221 |
| `gpt-5.4-ptu` | 0.211 |
| `glm-4.7` | 0.211 |
| `minimax-m2.7` | 0.207 |
| `kimi-k2.5` | 0.203 |

Balanced culprit payoff across the same levels:

| Culprit | Payoff |
| --- | ---: |
| `gpt-5.5` | 0.790 |
| `minimax-m2.7` | 0.781 |
| `glm-5.1` | 0.780 |
| `kimi-k2.5` | 0.778 |
| `glm-4.7` | 0.776 |
| `glm-5` | 0.775 |
| `deepseek-v4-pro` | 0.764 |
| `gpt-5.4-ptu` | 0.758 |

The culprit-side spread is smaller than the detective-side spread. On HARD and EXPERT, nearly
all culprits receive high payoff because detectives rarely solve any case.

## Operational Notes

- Baseline upload is complete on Hugging Face.
- The main missing-run experiment is partial. The remote dataset has 584 rows from
  `fill_missing_arena_20260524_w30_each_match`.
- The local main run had one additional completed match not present remotely at the time of
  inspection: `gpt-5.4-ptu__vs__gpt-5.5__HARD__seed_4`.
- No local evaluation or publish process was running during the analysis.

## Recommendations

1. Inspect trajectories for `LLM detective vs passive` before treating the baseline results as
   final model rankings. The zero-evidence pattern strongly suggests the detective action policy
   is not behaving as intended.
2. Fix or validate the LLM detective evidence-gathering loop, then rerun at least a small
   passive smoke test across TRIVIAL and EASY.
3. Complete the EASY matrix before making broad ranking claims. Current EASY coverage is only
   16 of 320 target LLM matches.
4. Finish the remaining HARD matches after the action-policy issue is understood. HARD is close
   to complete, but current results still show zero solves.
5. Report model rankings with stratification by difficulty and role. A single global leaderboard
   hides the difficulty cliff and can overweight incomplete levels.
