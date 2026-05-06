"""
Generator bias audit.

Generates a sample of mystery worlds at a given complexity level and reports
distributional statistics that paraphrase diversity will not fix on its own:

    - Culprit identity index   (is index 0 over-represented?)
    - Weapon-room joint        (do certain weapon-room pairs dominate?)
    - Evidence count per case  (mean / median / spread)
    - Alibi pattern frequencies
    - Speech archetype frequencies (Tier C)

Use this BEFORE generating the full train set: a non-uniform prior in any
of these is exploitable by SFT models without doing actual reasoning.

Usage:
    uv run scripts/audit_bias.py --level MEDIUM --n 200 --seed-base 1000000
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.entities import CharacterRole
from mystery_world.generator import generate_mystery


def chi_square_uniform(counts: list[int]) -> float:
    """Chi-square statistic against the uniform null. Larger = more skewed.
    Rough rule of thumb: > 3 * (k-1) means inspect; > 6 * (k-1) is suspicious."""
    if not counts:
        return 0.0
    total = sum(counts)
    k = len(counts)
    if total == 0 or k <= 1:
        return 0.0
    expected = total / k
    return sum((c - expected) ** 2 / expected for c in counts)


def fmt_pct(c: int, total: int) -> str:
    return f"{c} ({100.0 * c / max(total, 1):.1f}%)"


def audit(level: ComplexityLevel, n: int, seed_base: int) -> dict:
    cfg = COMPLEXITY_PRESETS[level]
    culprit_idx = Counter()      # index of culprit in suspect list
    weapon_room = Counter()      # (weapon_name, room_name)
    evidence_counts = []         # int per case
    archetype_counts = Counter()
    alibi_pattern = Counter()    # has_alibi / corroborated / solo
    skipped = 0

    for i in range(n):
        seed = seed_base + i
        try:
            state = generate_mystery(cfg, seed=seed)
        except Exception:
            skipped += 1
            continue

        suspects = [
            c for c in state.characters.values()
            if CharacterRole.SUSPECT in c.roles
        ]
        suspects.sort(key=lambda c: c.id)
        culprit = state.get_culprit()
        if culprit and culprit in suspects:
            culprit_idx[suspects.index(culprit)] += 1

        weapon = state.objects.get(state.murder_weapon_id)
        room = state.locations.get(state.murder_location_id)
        if weapon and room:
            weapon_room[(weapon.name, room.name)] += 1

        evidence_counts.append(len(state.evidence))

        for c in state.characters.values():
            if c.speech_archetype:
                archetype_counts[c.speech_archetype] += 1

        for c in suspects:
            if c.has_alibi and c.alibi_corroborator_id:
                alibi_pattern["corroborated"] += 1
            elif c.has_alibi:
                alibi_pattern["solo"] += 1
            else:
                alibi_pattern["none"] += 1

    return {
        "level": level.name,
        "n_requested": n,
        "n_realized": n - skipped,
        "skipped": skipped,
        "culprit_idx": dict(culprit_idx),
        "weapon_room_top": weapon_room.most_common(10),
        "evidence_count": {
            "mean": round(mean(evidence_counts), 2) if evidence_counts else 0,
            "median": median(evidence_counts) if evidence_counts else 0,
            "stdev": round(pstdev(evidence_counts), 2) if evidence_counts else 0,
            "min": min(evidence_counts) if evidence_counts else 0,
            "max": max(evidence_counts) if evidence_counts else 0,
        },
        "archetype_counts": dict(archetype_counts),
        "alibi_pattern": dict(alibi_pattern),
        "chi2_culprit_idx_vs_uniform": round(
            chi_square_uniform(list(culprit_idx.values())), 2,
        ),
    }


def render(report: dict) -> str:
    lines = [f"=== BIAS AUDIT  level={report['level']}  "
             f"n={report['n_realized']}/{report['n_requested']} "
             f"(skipped={report['skipped']}) ==="]

    cidx = report["culprit_idx"]
    total = sum(cidx.values()) or 1
    lines.append("\n[culprit identity index] -- uniform expected; large skew = leak risk")
    for k in sorted(cidx):
        lines.append(f"  index {k}: {fmt_pct(cidx[k], total)}")
    chi2 = report["chi2_culprit_idx_vs_uniform"]
    lines.append(f"  chi-square vs. uniform: {chi2}  (rough flag if > 6 * (k-1))")

    lines.append("\n[evidence count per case]")
    ec = report["evidence_count"]
    lines.append(f"  mean={ec['mean']}  median={ec['median']}  stdev={ec['stdev']}  "
                 f"range=[{ec['min']}, {ec['max']}]")

    lines.append("\n[weapon-room joint, top 10]")
    for (w, r), c in report["weapon_room_top"]:
        lines.append(f"  {c:>4d}  {w} in the {r}")

    ap = report["alibi_pattern"]
    apt = sum(ap.values()) or 1
    lines.append("\n[suspect alibi pattern]")
    for k in ("corroborated", "solo", "none"):
        lines.append(f"  {k:<14s}: {fmt_pct(ap.get(k, 0), apt)}")

    arc = report["archetype_counts"]
    arc_total = sum(arc.values()) or 1
    lines.append(f"\n[speech archetype frequencies] -- expect roughly uniform across {len(arc)} archetypes")
    for k, c in sorted(arc.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {k:<24s}: {fmt_pct(c, arc_total)}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", default="MEDIUM",
                        choices=[L.name for L in ComplexityLevel])
    parser.add_argument("--n", type=int, default=200,
                        help="Number of cases to generate")
    parser.add_argument("--seed-base", type=int, default=1_000_000,
                        help="First seed (default: train base)")
    args = parser.parse_args()

    level = ComplexityLevel[args.level]
    report = audit(level, args.n, args.seed_base)
    print(render(report))


if __name__ == "__main__":
    main()
