"""Per-skill probes (M7).

Four post-episode probes that decompose a model's temporal-reasoning
capability into specific sub-skills, reported alongside (but separate
from) the headline who/what/where accuracy:

  - event_ordering_tau: Kendall's tau between the agent's predicted
    chronological order of K observations and the ground-truth order.
    Stripped of step numbers, so the agent must use content cues
    (e.g. evidence aging from BRIGHT -> DULL -> FADED).
  - change_detection_acc: binary accuracy on "did the visible state of
    this room change between observation A and observation B?".
  - time_of_death_mae: mean absolute error between the agent's predicted
    murder_step and the ground truth.
  - persistent_identity_acc: binary accuracy on "are these two snapshots
    of the same persistent object?".

Probes are extracted once per episode from a canonical replay (so
the same probe inputs are presented across all 2x2 conditions). The
agent's response may differ between conditions if it maintains internal
state that depends on how it processed history (real LLM agents); stub
agents are stateless so their responses are condition-invariant.

Each probe method on the agent is optional. Missing methods or methods
that raise are silently skipped — the corresponding metric is omitted
from the score dict.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mystery_world.world import AgentAction, MysteryEnvironment

if TYPE_CHECKING:
    from mystery_world.world import WorldState


# ---------------------------------------------------------------------------
# Probe data classes
# ---------------------------------------------------------------------------

@dataclass
class EventOrderingProbe:
    """K observation summaries presented in shuffled order. The agent
    returns a permutation: ``pred[i]`` is the shuffled-index whose
    observation belongs at chronological position ``i``.

    Step numbers are stripped from ``shuffled_obs_summaries`` so the
    agent cannot trivially read off the order — it must use content
    cues (e.g. the VisualState progression of persistent evidence).
    """
    shuffled_obs_summaries: list[dict[str, Any]]
    true_chronological_order: list[int]


@dataclass
class ChangeDetectionProbe:
    """Two observation summaries from the SAME room at different
    game-times. Ground truth: did any persistent evidence change
    (different visual_state, or evidence presence flipped)?"""
    obs_a: dict[str, Any]
    obs_b: dict[str, Any]
    truly_changed: bool


@dataclass
class TimeOfDeathProbe:
    """Agent predicts the murder game-time in current_step units."""
    true_step: float


@dataclass
class PersistentIdentityProbe:
    """Two single-evidence snapshots. Ground truth: do they refer to
    the same persistent object (matching evidence id)?"""
    snapshot_a: dict[str, Any]
    snapshot_b: dict[str, Any]
    are_same_object: bool


@dataclass
class EpisodeProbes:
    event_ordering: EventOrderingProbe | None = None
    change_detection: list[ChangeDetectionProbe] = field(default_factory=list)
    time_of_death: TimeOfDeathProbe | None = None
    persistent_identity: list[PersistentIdentityProbe] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Canonical-history replay (used to extract probe inputs)
# ---------------------------------------------------------------------------

def _build_canonical_history(state: "WorldState") -> list[dict[str, Any]]:
    """Replay state.canonical_itinerary on a deep-copied state, returning
    per-step observation summaries. Mutations stay on the copy."""
    s = copy.deepcopy(state)
    env = MysteryEnvironment(s, visual_mode=False)
    history: list[dict[str, Any]] = [{
        "step": env.state.current_step,
        "action": None,
        "room_id": env.agent_location_id,
        "visible_evidence": [dict(e) for e in env.get_visible_evidence()],
    }]
    for entry in state.canonical_itinerary:
        action = AgentAction[entry["action"]]
        if action == AgentAction.ACCUSE:
            continue
        result = env.step(action, **entry.get("kwargs", {}))
        history.append({
            "step": env.state.current_step,
            "action": entry["action"],
            "room_id": env.agent_location_id,
            "visible_evidence": [dict(e) for e in result.visible_evidence],
        })
    return history


def _strip_step(obs: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": obs["action"],
        "room_id": obs["room_id"],
        "visible_evidence": obs["visible_evidence"],
    }


def _detect_change(obs_a: dict[str, Any], obs_b: dict[str, Any]) -> bool:
    ev_a = {e["id"]: e for e in obs_a["visible_evidence"]}
    ev_b = {e["id"]: e for e in obs_b["visible_evidence"]}
    if set(ev_a.keys()) != set(ev_b.keys()):
        return True
    for k in ev_a:
        if ev_a[k].get("visual_state") != ev_b[k].get("visual_state"):
            return True
    return False


# ---------------------------------------------------------------------------
# Probe extraction
# ---------------------------------------------------------------------------

def extract_probes(
    state: "WorldState",
    n_event_picks: int = 5,
    n_change_pairs: int = 3,
    n_identity_pairs: int = 3,
) -> EpisodeProbes:
    """Extract all four probes from a canonical replay of ``state``.

    Deterministic: same state -> same probes (the rng is seeded from
    state.seed). All four conditions of the 2x2 matrix should use the
    same probe set for fair cross-condition comparison.
    """
    history = _build_canonical_history(state)
    rng = random.Random(state.seed * 17 + 1)

    # Event-ordering probe ---------------------------------------------------
    event_ordering: EventOrderingProbe | None = None
    if len(history) >= n_event_picks:
        # K evenly-spaced picks across the chronology maximize VisualState
        # spread, making the ordering task content-discriminable.
        stride = max(1, (len(history) - 1) // (n_event_picks - 1))
        picks = [history[min(i * stride, len(history) - 1)] for i in range(n_event_picks)]
        indices = list(range(len(picks)))
        rng.shuffle(indices)
        shuffled = [picks[i] for i in indices]
        # For each original (chronological) pick, find where it ended up in
        # the shuffled list. Use identity (`is`) since picks may contain
        # equal-content dicts at different positions.
        true_order = [next(j for j, s in enumerate(shuffled) if s is original) for original in picks]
        event_ordering = EventOrderingProbe(
            shuffled_obs_summaries=[_strip_step(s) for s in shuffled],
            true_chronological_order=true_order,
        )

    # Change-detection probe -------------------------------------------------
    change_pairs: list[ChangeDetectionProbe] = []
    by_room: dict[str, list[dict[str, Any]]] = {}
    for obs in history:
        by_room.setdefault(obs["room_id"], []).append(obs)
    for room_obs in by_room.values():
        if len(change_pairs) >= n_change_pairs:
            break
        if len(room_obs) < 2:
            continue
        room_obs_sorted = sorted(room_obs, key=lambda o: o["step"])
        # Pair (earliest, latest): biggest chance of detected change.
        first, last = room_obs_sorted[0], room_obs_sorted[-1]
        change_pairs.append(ChangeDetectionProbe(
            obs_a=_strip_step(first),
            obs_b=_strip_step(last),
            truly_changed=_detect_change(first, last),
        ))

    # Time-of-death probe ----------------------------------------------------
    time_of_death = TimeOfDeathProbe(true_step=float(state.murder_step))

    # Persistent-identity probe ----------------------------------------------
    identity_pairs: list[PersistentIdentityProbe] = []
    ev_observations: dict[str, list[dict[str, Any]]] = {}
    for obs in history:
        for entry in obs["visible_evidence"]:
            ev_observations.setdefault(entry["id"], []).append(entry)
    # Positive pairs: same id at first and last observation (different
    # VisualStates likely; the agent must look at id not appearance).
    pos = 0
    for snaps in ev_observations.values():
        if pos >= n_identity_pairs:
            break
        if len(snaps) >= 2:
            identity_pairs.append(PersistentIdentityProbe(
                snapshot_a=dict(snaps[0]),
                snapshot_b=dict(snaps[-1]),
                are_same_object=True,
            ))
            pos += 1
    # Negative pairs: different ids paired together.
    ev_ids = list(ev_observations.keys())
    neg = 0
    for i in range(len(ev_ids)):
        if neg >= n_identity_pairs:
            break
        for j in range(i + 1, len(ev_ids)):
            if neg >= n_identity_pairs:
                break
            identity_pairs.append(PersistentIdentityProbe(
                snapshot_a=dict(ev_observations[ev_ids[i]][0]),
                snapshot_b=dict(ev_observations[ev_ids[j]][0]),
                are_same_object=False,
            ))
            neg += 1

    return EpisodeProbes(
        event_ordering=event_ordering,
        change_detection=change_pairs,
        time_of_death=time_of_death,
        persistent_identity=identity_pairs,
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def kendall_tau(pred: list[int], truth: list[int]) -> float:
    """Kendall's tau-a between two permutations of the same length.
    Returns a value in [-1, 1]: 1 = perfect agreement, -1 = reverse,
    0 = no relationship."""
    n = len(pred)
    if n <= 1:
        return 1.0
    if len(truth) != n:
        return 0.0
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            sign_pred = pred[j] - pred[i]
            sign_truth = truth[j] - truth[i]
            if sign_pred * sign_truth > 0:
                concordant += 1
            elif sign_pred * sign_truth < 0:
                discordant += 1
    total = n * (n - 1) / 2
    return (concordant - discordant) / total if total > 0 else 0.0


def score_probes(agent: Any, probes: EpisodeProbes) -> dict[str, float]:
    """Score an agent's probe responses. Missing or erroring methods are
    silently skipped (the corresponding metric is omitted)."""
    scores: dict[str, float] = {}

    if probes.event_ordering is not None and hasattr(agent, "probe_event_order"):
        try:
            pred = agent.probe_event_order(probes.event_ordering.shuffled_obs_summaries)
            if isinstance(pred, list) and len(pred) == len(probes.event_ordering.true_chronological_order):
                scores["event_ordering_tau"] = kendall_tau(
                    pred, probes.event_ordering.true_chronological_order
                )
        except Exception:
            pass

    if probes.change_detection and hasattr(agent, "probe_change_detection"):
        n_correct = 0
        n_attempted = 0
        for p in probes.change_detection:
            try:
                pred = agent.probe_change_detection(p.obs_a, p.obs_b)
                n_attempted += 1
                if bool(pred) == p.truly_changed:
                    n_correct += 1
            except Exception:
                continue
        if n_attempted > 0:
            scores["change_detection_acc"] = n_correct / n_attempted

    if probes.time_of_death is not None and hasattr(agent, "probe_time_of_death"):
        try:
            pred = float(agent.probe_time_of_death())
            scores["time_of_death_mae"] = abs(pred - probes.time_of_death.true_step)
        except Exception:
            pass

    if probes.persistent_identity and hasattr(agent, "probe_persistent_identity"):
        n_correct = 0
        n_attempted = 0
        for p in probes.persistent_identity:
            try:
                pred = agent.probe_persistent_identity(p.snapshot_a, p.snapshot_b)
                n_attempted += 1
                if bool(pred) == p.are_same_object:
                    n_correct += 1
            except Exception:
                continue
        if n_attempted > 0:
            scores["persistent_identity_acc"] = n_correct / n_attempted

    return scores


def aggregate_probe_scores(per_episode: list[dict[str, float]]) -> dict[str, float]:
    """Mean across episodes of every probe metric present in any episode.
    Missing keys (probe skipped on a given episode) contribute nothing to
    the mean; we report the mean over the episodes that had the metric."""
    if not per_episode:
        return {}
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for ep in per_episode:
        for k, v in ep.items():
            sums[k] = sums.get(k, 0.0) + v
            counts[k] = counts.get(k, 0) + 1
    return {k: sums[k] / counts[k] for k in sums}
