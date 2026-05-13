"""Canonical itinerary emission and temporal-necessity audit (M5).

The canonical itinerary is a deterministic action sequence emitted by the
generator alongside each mystery. When replayed against a fresh
``MysteryEnvironment``, it:

  1. Visits every reachable room (BFS order from the agent's starting room).
  2. Examines every accessible evidence-bearing object in each room.
  3. Waits long enough for temporally-relevant evidence to cross a
     ``VisualState`` band boundary.
  4. Re-visits the room hosting the most temporally-relevant physical
     evidence and re-examines its objects.
  5. Issues the ground-truth ``ACCUSE``.

It serves two purposes:

  - It is the **oracle-itinerary** observation source for the 2x2 evaluation
    matrix (planning removed, temporal reasoning isolated).
  - Replaying it under ``visual_mode=True`` exposes the same persistent
    evidence at two distinct ``VisualState`` bands, which is the necessary
    condition for cross-observation temporal reasoning to be possible.

The audit ``audit_episode(state)`` replays the itinerary against a fresh
env and checks that at least one piece of evidence is observed at two or
more distinct ``VisualState`` values. Episodes that fail are rejected by
the audited generator and a fresh seed is tried.
"""

from __future__ import annotations

import copy
from collections import deque
from typing import TYPE_CHECKING, Any

from mystery_world.entities import (
    EvidenceState,
    EvidenceType,
    VISUAL_FRESH_THRESHOLD,
    VISUAL_STALE_THRESHOLD,
)

if TYPE_CHECKING:
    from mystery_world.entities import Evidence, Location, WorldObject
    from mystery_world.world import WorldState


# ---------------------------------------------------------------------------
# Itinerary generation
# ---------------------------------------------------------------------------

def generate_canonical_itinerary(state: "WorldState") -> list[dict[str, Any]]:
    """Build a deterministic action sequence for ``state``.

    The output is a list of ``{"action": <AgentAction name>, "kwargs": {...}}``
    dicts. Replay via ``AgentAction[entry["action"]]`` and
    ``env.step(action, **entry["kwargs"])``.
    """
    locations = state.locations
    objects = state.objects
    evidence = state.evidence
    if not locations:
        return []

    # Agent starts at the first room (matches MysteryEnvironment.__init__).
    start_id = next(iter(locations))

    itinerary: list[dict[str, Any]] = []
    current_id = start_id

    # Phase 1: BFS-order visit of every reachable room, examining evidence-
    # bearing objects along the way.
    visit_order = _bfs_visit_order(locations, start_id)
    for room_id in visit_order:
        if current_id != room_id:
            current_id = _append_path_moves(itinerary, locations, current_id, room_id)
        _append_examines_for_room(itinerary, locations[room_id], objects, evidence)

    # Phase 2: re-visit the room with the most temporally-relevant physical
    # evidence, after enough WAIT steps to age the evidence across at least
    # one VisualState band boundary.
    key_room_id = _pick_key_room(locations, objects, evidence)
    if key_room_id is not None:
        # WAIT enough steps so age crosses BRIGHT->DULL (or further). The
        # boundary widths use the default thresholds in entities.py; one
        # extra step keeps us safely past the boundary.
        wait_steps = int(VISUAL_STALE_THRESHOLD - VISUAL_FRESH_THRESHOLD + 1)
        for _ in range(wait_steps):
            itinerary.append({"action": "WAIT", "kwargs": {}})
        if current_id != key_room_id:
            current_id = _append_path_moves(itinerary, locations, current_id, key_room_id)
        _append_examines_for_room(itinerary, locations[key_room_id], objects, evidence)

    # Phase 3: ACCUSE with ground truth.
    culprit = state.get_culprit()
    weapon = objects.get(state.murder_weapon_id)
    murder_loc = locations.get(state.murder_location_id)
    if culprit is not None and weapon is not None and murder_loc is not None:
        itinerary.append({
            "action": "ACCUSE",
            "kwargs": {
                "suspect_name": culprit.full_name,
                "weapon_name": weapon.name,
                "location_name": murder_loc.name,
            },
        })

    return itinerary


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def audit_episode(state: "WorldState") -> tuple[bool, str]:
    """Replay the canonical itinerary against a fresh env and verify the
    visual observation sequence contains at least one piece of evidence
    observed at two or more distinct VisualStates.

    Returns ``(passed, reason)``. Auditing runs with ``visual_mode=False``
    (no PNG rendering) for speed; ``visible_evidence`` is computed
    regardless of mode and carries the VisualState tokens the audit needs.

    The replay is performed against a deep-copied state so the caller's
    state is not mutated (env.step advances current_step, mutates
    event_log, runs process_all_events which can relocate NPCs etc.).
    """
    # Local import to avoid the canonical -> world -> canonical cycle.
    from mystery_world.world import AgentAction, MysteryEnvironment

    if not state.canonical_itinerary:
        return False, "no canonical itinerary present on state"

    audit_state = copy.deepcopy(state)
    env = MysteryEnvironment(audit_state, visual_mode=False)

    states_seen: dict[str, set[str]] = {}

    def _record(snapshot: list[dict[str, Any]]) -> None:
        for entry in snapshot:
            vs = entry.get("visual_state")
            if vs is None:
                continue
            states_seen.setdefault(entry["id"], set()).add(vs)

    _record(env.get_visible_evidence())

    for action_entry in state.canonical_itinerary:
        action_name = action_entry["action"]
        action = AgentAction[action_name]
        kwargs = action_entry.get("kwargs", {})
        if action == AgentAction.ACCUSE:
            # Don't take ACCUSE here; it ends the episode and contributes no
            # additional pre-accusation observation. The audit is about the
            # observation sequence the agent receives BEFORE accusing.
            continue
        env.step(action, **kwargs)
        _record(env.get_visible_evidence())

    multi_state = [eid for eid, vs in states_seen.items() if len(vs) >= 2]
    if not multi_state:
        return False, (
            f"no evidence observed in >=2 visual_states across the itinerary "
            f"(states_seen: { {eid: sorted(vs) for eid, vs in states_seen.items()} })"
        )
    return True, f"OK: {len(multi_state)} evidence with cross-observation change"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bfs_visit_order(locations: dict[str, "Location"], start_id: str) -> list[str]:
    """Return reachable room ids in BFS-distance order from ``start_id``."""
    visited = {start_id}
    queue = deque([start_id])
    order = [start_id]
    while queue:
        current = queue.popleft()
        loc = locations.get(current)
        if loc is None:
            continue
        for adj in loc.adjacent_ids:
            if adj not in visited and adj in locations:
                visited.add(adj)
                queue.append(adj)
                order.append(adj)
    return order


def _shortest_path(locations: dict[str, "Location"], src: str, dst: str) -> list[str]:
    """Shortest-edge-count path src -> ... -> dst (inclusive). Empty list if
    ``dst`` is unreachable."""
    if src == dst:
        return [src]
    parent: dict[str, str | None] = {src: None}
    queue = deque([src])
    while queue:
        current = queue.popleft()
        if current == dst:
            break
        loc = locations.get(current)
        if loc is None:
            continue
        for adj in loc.adjacent_ids:
            if adj not in parent and adj in locations:
                parent[adj] = current
                queue.append(adj)
    if dst not in parent:
        return []
    path = []
    node: str | None = dst
    while node is not None:
        path.append(node)
        node = parent[node]
    return list(reversed(path))


def _append_path_moves(
    itinerary: list[dict[str, Any]],
    locations: dict[str, "Location"],
    src: str,
    dst: str,
) -> str:
    """Append MOVE actions walking the shortest path src -> dst. Returns the
    final room id reached (may be `src` if `dst` is unreachable)."""
    path = _shortest_path(locations, src, dst)
    if len(path) < 2:
        return src
    current = src
    for next_id in path[1:]:
        next_loc = locations[next_id]
        itinerary.append({
            "action": "MOVE",
            "kwargs": {"target_location": next_loc.name},
        })
        current = next_id
    return current


def _append_examines_for_room(
    itinerary: list[dict[str, Any]],
    loc: "Location",
    objects: dict[str, "WorldObject"],
    evidence: dict[str, "Evidence"],
) -> None:
    """Append EXAMINE_OBJECT actions for every accessible evidence-bearing
    object in ``loc``. Hidden/destroyed evidence is skipped."""
    for oid in loc.objects_here:
        obj = objects.get(oid)
        if obj is None or not obj.evidence_id:
            continue
        ev = evidence.get(obj.evidence_id)
        if ev is None:
            continue
        if ev.state in (EvidenceState.HIDDEN, EvidenceState.DESTROYED):
            continue
        itinerary.append({
            "action": "EXAMINE_OBJECT",
            "kwargs": {"object_name": obj.name},
        })


def _pick_key_room(
    locations: dict[str, "Location"],
    objects: dict[str, "WorldObject"],
    evidence: dict[str, "Evidence"],
) -> str | None:
    """Return the room id with the largest count of temporally-relevant,
    visible, PHYSICAL evidence (i.e. the strongest candidate for a
    cross-observation aging signal)."""
    best_room: str | None = None
    best_count = 0
    for room_id, loc in locations.items():
        count = 0
        for oid in loc.objects_here:
            obj = objects.get(oid)
            if obj is None or not obj.evidence_id:
                continue
            ev = evidence.get(obj.evidence_id)
            if ev is None or ev.relevance is None:
                continue
            if ev.state in (EvidenceState.HIDDEN, EvidenceState.DESTROYED):
                continue
            if ev.evidence_type != EvidenceType.PHYSICAL:
                continue
            count += 1
        if count > best_count:
            best_count = count
            best_room = room_id
    return best_room
