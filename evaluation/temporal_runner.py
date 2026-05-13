"""Temporal-reasoning eval driver: 2x2 confound-isolation matrix (M6).

Runs one episode under each of four conditions:

         | correct order | shuffled order |
---------+---------------+----------------+
oracle   |       A       |       B        |
free     |       C       |       D        |

  - **Oracle vs free** controls for *planning*: under oracle, the agent
    follows the canonical itinerary attached to the WorldState; under
    free, the agent chooses every action.
  - **Correct vs shuffled** controls for *temporal reasoning*: under
    correct, the agent receives its observation history in chronological
    order; under shuffled, the same observations are randomly reordered
    before being passed to the agent for any decision.

The headline number is the **A − B** gap: the per-episode accuracy under
oracle-correct minus the accuracy under oracle-shuffled, averaged across
the episode set. With planning removed by the oracle itinerary, this gap
isolates the agent's ability to integrate observations *in order* — i.e.
its temporal-reasoning capability. ``C − D`` is the analogous gap when
planning is also part of the task.

This module does not depend on any LLM; it accepts any object that
implements the lightweight protocol:

    decide_accuse(history) -> {"suspect_name": ..., "weapon_name": ..., "location_name": ...}
    decide_action(history) -> {"action": <AgentAction name>, "kwargs": {...}} | None

Stub implementations live in ``evaluation.stub_agents``. Real LLM /
VLM agents will plug in here in M11.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any, Protocol

from evaluation.probes import EpisodeProbes, extract_probes, score_probes
from mystery_world.narrator import render_initial_briefing
from mystery_world.world import AgentAction, ActionResult, MysteryEnvironment, WorldState


CONDITIONS = ("oracle_correct", "oracle_shuffled", "free_correct", "free_shuffled")


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    """One agent-facing observation snapshot.

    The agent receives a list of these in either chronological or shuffled
    order, depending on the condition. The ``step`` field is preserved so
    a shuffled order is unambiguous to inspect during analysis.
    """
    step: int
    action_taken: str | None              # action that produced this obs (None for initial)
    textual: str                          # text channel
    visible_evidence: list[dict[str, Any]] = field(default_factory=list)
    image: bytes | None = None            # PNG bytes from M4 (None if visual_mode off)


class Agent(Protocol):
    def decide_action(self, history: list[Observation]) -> dict[str, Any] | None: ...
    def decide_accuse(self, history: list[Observation]) -> dict[str, str]: ...


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ConditionResult:
    condition: str
    accusation_correct: bool
    n_steps: int
    accusation_kwargs: dict[str, str] = field(default_factory=dict)
    # M7 per-skill probe scores. Each metric is optional; only present
    # when the agent implements the corresponding probe method.
    probe_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class EpisodeMatrix:
    """Per-episode 2x2 results."""
    seed: int
    per_condition: dict[str, ConditionResult] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _shuffle_history(history: list[Observation], seed: int) -> list[Observation]:
    """Return a copy of ``history`` shuffled deterministically by ``seed``.
    Uses a stdlib Random so the order is reproducible across processes."""
    indices = list(range(len(history)))
    random.Random(seed).shuffle(indices)
    return [history[i] for i in indices]


def _obs_from_result(
    env: MysteryEnvironment, result: ActionResult, action_taken: str | None
) -> Observation:
    return Observation(
        step=env.state.current_step,
        action_taken=action_taken,
        textual=result.observation,
        visible_evidence=list(result.visible_evidence),
        image=result.image,
    )


def _initial_observation(env: MysteryEnvironment) -> Observation:
    return Observation(
        step=env.state.current_step,
        action_taken=None,
        textual=render_initial_briefing(env),
        visible_evidence=env.get_visible_evidence(),
        image=env.get_observation_image(),
    )


def _present(history: list[Observation], shuffled: bool, seed: int) -> list[Observation]:
    return _shuffle_history(history, seed) if shuffled else history


# ---------------------------------------------------------------------------
# Per-condition run
# ---------------------------------------------------------------------------

def run_condition(
    state: WorldState,
    agent: Agent,
    condition: str,
    visual_mode: bool = True,
    probes: EpisodeProbes | None = None,
) -> ConditionResult:
    """Run one episode under one of the four conditions.

    The state is deep-copied so callers can re-run other conditions on the
    same seed without observing mutations from a prior run.

    If ``probes`` is provided, the M7 probe methods on the agent are called
    after the episode and their scores are attached to ``probe_scores``.
    The probe inputs are the same across all four conditions (extracted
    from a canonical replay), so condition-induced score differences
    reflect the agent's internal state, not different probe questions.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; expected one of {CONDITIONS}")
    is_oracle = condition.startswith("oracle")
    is_shuffled = condition.endswith("shuffled")

    fresh_state = copy.deepcopy(state)
    env = MysteryEnvironment(fresh_state, visual_mode=visual_mode)
    history: list[Observation] = [_initial_observation(env)]
    accuse_kwargs: dict[str, str] = {}

    if is_oracle:
        # Replay the canonical itinerary up to but not including the final
        # ACCUSE, so the agent makes that decision based on history.
        non_accuse_actions = [
            entry for entry in fresh_state.canonical_itinerary
            if entry["action"] != "ACCUSE"
        ]
        for entry in non_accuse_actions:
            action = AgentAction[entry["action"]]
            result = env.step(action, **entry.get("kwargs", {}))
            history.append(_obs_from_result(env, result, entry["action"]))
        # Agent's ACCUSE based on (possibly shuffled) history.
        shuffle_seed = fresh_state.seed * 31 + (1 if is_shuffled else 0)
        agent_view = _present(history, is_shuffled, shuffle_seed)
        accuse_kwargs = agent.decide_accuse(agent_view)
        env.step(AgentAction.ACCUSE, **accuse_kwargs)
    else:
        # Free mode: agent chooses every action.
        # Budget cap matches the env's own; we add a small overage guard.
        max_actions = env.state.config.max_agent_actions + 2
        for step_i in range(max_actions):
            shuffle_seed = fresh_state.seed * 31 + step_i + (1000 if is_shuffled else 0)
            agent_view = _present(history, is_shuffled, shuffle_seed)
            choice = agent.decide_action(agent_view)
            if choice is None:
                break
            action_name = choice["action"]
            action = AgentAction[action_name]
            kwargs = choice.get("kwargs", {})
            if action == AgentAction.ACCUSE:
                accuse_kwargs = dict(kwargs)
            result = env.step(action, **kwargs)
            history.append(_obs_from_result(env, result, action_name))
            if action == AgentAction.ACCUSE:
                break

    probe_scores: dict[str, float] = {}
    if probes is not None:
        probe_scores = score_probes(agent, probes)

    return ConditionResult(
        condition=condition,
        accusation_correct=bool(env.accusation_correct),
        n_steps=len(history) - 1,
        accusation_kwargs=accuse_kwargs,
        probe_scores=probe_scores,
    )


# ---------------------------------------------------------------------------
# 2x2 across one episode
# ---------------------------------------------------------------------------

def run_2x2(
    state: WorldState,
    agent_factory,
    visual_mode: bool = True,
    with_probes: bool = False,
) -> EpisodeMatrix:
    """Run all four conditions on ``state`` and return per-condition results.

    ``agent_factory`` is called once per condition with ``state`` to produce
    a fresh agent. Stateless agents may ignore the argument.

    If ``with_probes=True``, M7 probes are extracted once (from a canonical
    replay) and scored per condition. The same probe inputs are used across
    all four conditions, so per-condition probe-score differences reflect
    the agent's internal state rather than different probe questions.
    """
    probes = extract_probes(state) if with_probes else None
    matrix = EpisodeMatrix(seed=state.seed)
    for cond in CONDITIONS:
        agent = agent_factory(state)
        result = run_condition(state, agent, cond, visual_mode=visual_mode, probes=probes)
        matrix.per_condition[cond] = result
    return matrix


# ---------------------------------------------------------------------------
# Aggregation across episodes
# ---------------------------------------------------------------------------

def aggregate_2x2_results(matrices: list[EpisodeMatrix]) -> dict[str, float]:
    """Aggregate per-episode 2x2 results into headline accuracies + gaps."""
    if not matrices:
        return {}
    n = len(matrices)
    accuracies: dict[str, float] = {}
    for cond in CONDITIONS:
        n_correct = sum(1 for m in matrices if m.per_condition[cond].accusation_correct)
        accuracies[cond] = n_correct / n
    return {
        **accuracies,
        # Headline: with planning removed by the oracle itinerary, this is
        # the agent's pure temporal-reasoning score.
        "oracle_temporal_reasoning_gap": accuracies["oracle_correct"] - accuracies["oracle_shuffled"],
        # Same diff but in the full-agent setting (planning included).
        "free_temporal_reasoning_gap":   accuracies["free_correct"] - accuracies["free_shuffled"],
        "n_episodes": float(n),
    }
