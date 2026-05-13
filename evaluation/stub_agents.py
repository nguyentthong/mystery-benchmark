"""Stub agents for the M6 temporal-eval driver.

Two cheap, deterministic agents that exercise the 2x2 confound-isolation
matrix without requiring an LLM. They are research scaffolding: the
``OracleAccuseAgent`` is the upper-bound sanity check (always answers
correctly), and the ``HashAgent`` is a deterministic, order-sensitive
random-equivalent that demonstrates the eval matrix discriminates
between ``oracle_correct`` and ``oracle_shuffled`` conditions.

Real LLM- and VLM-based agents land in ``agents/`` and will plug into
the same protocol in M11.

Agent protocol (informal):
  - ``decide_action(history) -> dict | None`` for free-mode steps.
    Returns ``{"action": <AgentAction name>, "kwargs": {...}}`` or
    ``None`` to end the episode.
  - ``decide_accuse(history) -> dict`` returns the kwargs for ACCUSE
    (``suspect_name``, ``weapon_name``, ``location_name``).

``history`` is a list of ``Observation`` dataclasses (see
``evaluation.temporal_runner``). Stubs that need ground truth take it
at construction time so the protocol stays history-only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mystery_world.entities import CharacterRole

if TYPE_CHECKING:
    from mystery_world.world import WorldState
    from evaluation.temporal_runner import Observation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _AccuseCandidates:
    suspects: list[str]          # full names of alive suspects
    weapons: list[str]           # all weapon-like object names
    locations: list[str]         # all room names


def _extract_candidates(state: "WorldState") -> _AccuseCandidates:
    suspects = [
        c.full_name for c in state.characters.values()
        if CharacterRole.SUSPECT in c.roles and c.is_alive
    ]
    # For M6 we don't yet have a clean "weapon vs not" distinction in
    # WorldObject, so we approximate by including every object that is
    # the murder weapon plus any object whose evidence_id points to a
    # weapon-related piece of evidence. For the HashAgent's purposes this
    # over-set is fine (we just need a deterministic candidate pool).
    weapon_ids = {state.murder_weapon_id}
    weapons = sorted(
        {obj.name for oid, obj in state.objects.items() if oid in weapon_ids or oid == state.murder_weapon_id}
    )
    if not weapons:
        # Fallback: any object name. Keeps the agent picking *something*.
        weapons = sorted({obj.name for obj in state.objects.values()})
    locations = sorted({loc.name for loc in state.locations.values()})
    return _AccuseCandidates(
        suspects=suspects or ["unknown_suspect"],
        weapons=weapons or ["unknown_weapon"],
        locations=locations or ["unknown_location"],
    )


# ---------------------------------------------------------------------------
# OracleAccuseAgent
# ---------------------------------------------------------------------------

class OracleAccuseAgent:
    """Always answers with the ground-truth triple.

    Useful as an upper bound on the eval pipeline: a working runner must
    return ``accusation_correct=True`` for this agent in all four
    conditions. If it doesn't, the driver, not the agent, is broken.
    """

    def __init__(self, state: "WorldState") -> None:
        culprit = state.get_culprit()
        weapon = state.objects.get(state.murder_weapon_id)
        room = state.locations.get(state.murder_location_id)
        self._accuse_kwargs: dict[str, str] = {
            "suspect_name": culprit.full_name if culprit else "",
            "weapon_name":  weapon.name      if weapon  else "",
            "location_name": room.name       if room    else "",
        }

    def decide_action(self, history: list["Observation"]) -> dict[str, Any] | None:
        # Free mode: ACCUSE immediately. No exploration needed since the
        # answer is already known.
        return {"action": "ACCUSE", "kwargs": dict(self._accuse_kwargs)}

    def decide_accuse(self, history: list["Observation"]) -> dict[str, str]:
        return dict(self._accuse_kwargs)


# ---------------------------------------------------------------------------
# HashAgent
# ---------------------------------------------------------------------------

class HashAgent:
    """Deterministic, order-sensitive stub.

    Its ACCUSE choice is a function of a SHA-256 digest computed over the
    ordered sequence of ``visible_evidence`` entries in ``history``.
    Two consequences:

      - Determinism: same history -> same accusation.
      - Order-sensitivity: the same observations in shuffled order produce
        a different digest, so the agent's accusation typically differs
        between ``*_correct`` and ``*_shuffled`` conditions. That is the
        property the 2x2 matrix exists to detect, and the test exploits
        it to verify the matrix discriminates.

    In free mode the agent wanders deterministically (a small hash-driven
    walk of MOVEs and EXAMINEs) before issuing ACCUSE, so its history is
    non-trivial and shuffling has bite.
    """

    _MAX_WANDER_STEPS = 6

    def __init__(self, state: "WorldState") -> None:
        self._candidates = _extract_candidates(state)
        # Pre-build a name->id map of rooms so MOVE can target by name.
        self._room_names = [loc.name for loc in state.locations.values()]
        # Object names per room for EXAMINE choices.
        self._objects_per_room: dict[str, list[str]] = {}
        for loc in state.locations.values():
            names = []
            for oid in loc.objects_here:
                obj = state.objects.get(oid)
                if obj is not None:
                    names.append(obj.name)
            self._objects_per_room[loc.name] = names

    @staticmethod
    def _history_digest(history: list["Observation"]) -> bytes:
        h = hashlib.sha256()
        for i, obs in enumerate(history):
            for entry in obs.visible_evidence:
                h.update(
                    f"{i}:{entry['id']}:{entry.get('visual_state')}|".encode()
                )
        return h.digest()

    def decide_action(self, history: list["Observation"]) -> dict[str, Any] | None:
        # After enough exploration steps, ACCUSE.
        n_taken = sum(1 for o in history if o.action_taken is not None)
        if n_taken >= self._MAX_WANDER_STEPS:
            return {"action": "ACCUSE", "kwargs": self.decide_accuse(history)}

        # Hash-driven choice of MOVE or EXAMINE. We try MOVE first; if no
        # adjacent room is recorded in the most recent observation (the
        # textual obs doesn't expose adjacency in a structured way), fall
        # back to EXAMINE on a known object name from the current room
        # name in the most recent observation, then WAIT.
        digest = self._history_digest(history)
        choice = digest[0] % 3
        # MOVE
        if choice == 0 and self._room_names:
            target_idx = digest[1] % len(self._room_names)
            return {"action": "MOVE", "kwargs": {"target_location": self._room_names[target_idx]}}
        # EXAMINE — pick the most recent visible_evidence's name if any
        if choice == 1 and history and history[-1].visible_evidence:
            ev_idx = digest[2] % len(history[-1].visible_evidence)
            ev_name = history[-1].visible_evidence[ev_idx]["name"]
            return {"action": "EXAMINE_OBJECT", "kwargs": {"object_name": ev_name}}
        return {"action": "WAIT", "kwargs": {}}

    def decide_accuse(self, history: list["Observation"]) -> dict[str, str]:
        digest = self._history_digest(history)
        s_idx = digest[0] % len(self._candidates.suspects)
        w_idx = digest[1] % len(self._candidates.weapons)
        r_idx = digest[2] % len(self._candidates.locations)
        return {
            "suspect_name": self._candidates.suspects[s_idx],
            "weapon_name":  self._candidates.weapons[w_idx],
            "location_name": self._candidates.locations[r_idx],
        }
