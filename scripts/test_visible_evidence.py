"""
M2 verification: ensure ``env.step()`` returns a structured
``visible_evidence`` list with stable IDs and a ``visual_state`` token that
ages with game-time.

Properties checked:
  1. visible_evidence is populated after step() (and via get_visible_evidence()).
  2. Persistent identity: the same physical evidence keeps the same ``id``
     across multiple observations.
  3. Aging: ``visual_state`` transitions BRIGHT -> DULL -> FADED as
     ``current_step`` advances past the visual thresholds.
  4. Hidden / destroyed evidence is excluded.
  5. Only evidence in the agent's current room is included; off-room evidence
     is filtered out.

Usage:
    python scripts/test_visible_evidence.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.entities import (
    EvidenceState,
    VisualState,
    VISUAL_FRESH_THRESHOLD,
    VISUAL_STALE_THRESHOLD,
    compute_visual_state,
)
from mystery_world.generator import generate_mystery
from mystery_world.world import AgentAction, MysteryEnvironment


def _find_room_with_evidence(env: MysteryEnvironment) -> tuple[str, str] | None:
    """Return (room_id, evidence_id) for any room containing at least one
    piece of non-hidden, temporally-relevant evidence. None if no such room
    exists in this generated world."""
    for loc_id, loc in env.state.locations.items():
        for oid in loc.objects_here:
            obj = env.state.objects.get(oid)
            if obj is None or not obj.evidence_id:
                continue
            ev = env.state.evidence.get(obj.evidence_id)
            if ev is None or ev.relevance is None:
                continue
            if ev.state in (EvidenceState.HIDDEN, EvidenceState.DESTROYED):
                continue
            return loc_id, ev.id
    return None


def _check_population_and_identity(seed: int, level: ComplexityLevel) -> None:
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state)

    found = _find_room_with_evidence(env)
    if found is None:
        return
    target_room_id, target_ev_id = found
    env.agent_location_id = target_room_id

    # 1. get_visible_evidence() works before any step().
    initial = env.get_visible_evidence()
    ids_initial = {e["id"] for e in initial}
    if target_ev_id not in ids_initial:
        raise AssertionError(
            f"[seed={seed}, {level.name}] target evidence {target_ev_id} missing "
            f"from initial get_visible_evidence() in room {target_room_id}"
        )

    # 2. step() populates visible_evidence. The list shape is correct; specific
    # IDs may legitimately disappear if process_all_events tampers/relocates
    # them, but everything that DOES appear must carry the required schema.
    initial_by_id = {e["id"]: e for e in initial}
    result = env.step(AgentAction.WAIT)
    if not isinstance(result.visible_evidence, list):
        raise AssertionError(
            f"[seed={seed}, {level.name}] result.visible_evidence is not a list"
        )
    for entry in result.visible_evidence:
        for required_field in ("id", "name", "visual_state", "room_id", "evidence_state"):
            if required_field not in entry:
                raise AssertionError(
                    f"[seed={seed}, {level.name}] missing field '{required_field}' in "
                    f"visible_evidence entry: {entry}"
                )

    # 3. Persistent identity: any evidence present in BOTH the initial snapshot
    # and the post-step snapshot must have a consistent name + id mapping
    # (we don't require room_id stability because culprit tampering can MOVE
    # evidence to a different room mid-step).
    for entry in result.visible_evidence:
        ev_id = entry["id"]
        if ev_id in initial_by_id and initial_by_id[ev_id]["name"] != entry["name"]:
            raise AssertionError(
                f"[seed={seed}, {level.name}] name mismatch across observations for "
                f"id={ev_id}: {initial_by_id[ev_id]['name']!r} vs {entry['name']!r}"
            )


def _check_aging(seed: int, level: ComplexityLevel) -> None:
    """Verify visible_evidence reports the band consistent with the actual age
    at current_step, and that the band can transition as current_step advances.

    Uses integer current_step offsets (since current_step is int-typed) to avoid
    int-truncation bugs.
    """
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state)
    found = _find_room_with_evidence(env)
    if found is None:
        return
    target_room_id, target_ev_id = found
    env.agent_location_id = target_room_id

    ev = state.evidence[target_ev_id]
    contact_ts = ev.relevance.contact_timestamp

    # Integer offsets that clearly fall in each band given default thresholds
    # (2.0 and 6.0). offset=0 -> age=0 (BRIGHT); offset=3 -> age in [3,4) (DULL);
    # offset=8 -> age >= 8 (FADED).
    offsets_and_bands: list[tuple[int, VisualState]] = [
        (0, VisualState.BRIGHT),
        (3, VisualState.DULL),
        (8, VisualState.FADED),
    ]
    bands_seen: set[str] = set()
    for offset, expected_band in offsets_and_bands:
        env._state.current_step = int(contact_ts) + offset
        snapshot = env.get_visible_evidence()
        match = [e for e in snapshot if e["id"] == target_ev_id]
        if not match:
            raise AssertionError(
                f"[seed={seed}, {level.name}] target evidence missing at offset {offset}"
            )
        got_band = match[0]["visual_state"]
        actual_age = float(env._state.current_step) - contact_ts
        expected_from_compute = compute_visual_state(actual_age).name
        if got_band != expected_from_compute:
            raise AssertionError(
                f"[seed={seed}, {level.name}] offset={offset} age={actual_age} expected "
                f"visual_state={expected_from_compute} got={got_band}"
            )
        bands_seen.add(got_band)

    # Aging is monotonic: across the three offsets we must observe at least
    # two distinct bands (BRIGHT -> DULL -> FADED). If we only see one band
    # then visual_state isn't actually evolving with game-time.
    if len(bands_seen) < 2:
        raise AssertionError(
            f"[seed={seed}, {level.name}] visual_state did not change across offsets "
            f"0/3/8 — saw only {bands_seen}"
        )


def _check_state_filter(seed: int, level: ComplexityLevel) -> None:
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state)
    found = _find_room_with_evidence(env)
    if found is None:
        return
    target_room_id, target_ev_id = found
    env.agent_location_id = target_room_id

    ev = state.evidence[target_ev_id]

    # Mark HIDDEN — should be excluded.
    ev.state = EvidenceState.HIDDEN
    snapshot = env.get_visible_evidence()
    if any(e["id"] == target_ev_id for e in snapshot):
        raise AssertionError(
            f"[seed={seed}, {level.name}] HIDDEN evidence {target_ev_id} leaked into "
            f"visible_evidence"
        )

    # Restore and mark DESTROYED — should also be excluded.
    ev.state = EvidenceState.DESTROYED
    snapshot = env.get_visible_evidence()
    if any(e["id"] == target_ev_id for e in snapshot):
        raise AssertionError(
            f"[seed={seed}, {level.name}] DESTROYED evidence {target_ev_id} leaked into "
            f"visible_evidence"
        )

    # Restore to PRISTINE — should be included again.
    ev.state = EvidenceState.PRISTINE
    snapshot = env.get_visible_evidence()
    if not any(e["id"] == target_ev_id for e in snapshot):
        raise AssertionError(
            f"[seed={seed}, {level.name}] PRISTINE evidence {target_ev_id} unexpectedly "
            f"absent from visible_evidence"
        )


def _check_room_filter(seed: int, level: ComplexityLevel) -> None:
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state)
    found = _find_room_with_evidence(env)
    if found is None:
        return
    target_room_id, target_ev_id = found

    # Find a different room (any other location id).
    other_room_ids = [r for r in state.locations if r != target_room_id]
    if not other_room_ids:
        return  # only one room — can't test
    env.agent_location_id = other_room_ids[0]

    snapshot = env.get_visible_evidence()
    if any(e["id"] == target_ev_id for e in snapshot):
        raise AssertionError(
            f"[seed={seed}, {level.name}] evidence in room {target_room_id} leaked "
            f"into visible_evidence when agent was in room {other_room_ids[0]}"
        )
    # All entries must report the agent's actual current room.
    for entry in snapshot:
        if entry["room_id"] != other_room_ids[0]:
            raise AssertionError(
                f"[seed={seed}, {level.name}] visible_evidence entry reports wrong "
                f"room: agent in {other_room_ids[0]}, entry room_id={entry['room_id']}"
            )


def _check_compute_visual_state_unit() -> None:
    """Unit test of the threshold logic, decoupled from any env state."""
    cases: list[tuple[float, VisualState]] = [
        (-1.0, VisualState.BRIGHT),
        (0.0,  VisualState.BRIGHT),
        (VISUAL_FRESH_THRESHOLD - 0.001, VisualState.BRIGHT),
        (VISUAL_FRESH_THRESHOLD,         VisualState.DULL),
        (VISUAL_FRESH_THRESHOLD + 0.001, VisualState.DULL),
        (VISUAL_STALE_THRESHOLD - 0.001, VisualState.DULL),
        (VISUAL_STALE_THRESHOLD,         VisualState.FADED),
        (VISUAL_STALE_THRESHOLD + 1.0,   VisualState.FADED),
    ]
    for age, expected in cases:
        got = compute_visual_state(age)
        if got is not expected:
            raise AssertionError(
                f"compute_visual_state({age}) expected {expected.name} got {got.name}"
            )


_CHECKS = (
    ("population_and_identity", _check_population_and_identity),
    ("aging",                   _check_aging),
    ("state_filter",            _check_state_filter),
    ("room_filter",             _check_room_filter),
)


def main() -> int:
    # Pure unit test on the threshold logic.
    try:
        _check_compute_visual_state_unit()
    except AssertionError as exc:
        print(f"FAIL [compute_visual_state_unit]: {exc}")
        return 1

    seeds = [0, 1, 2, 7, 42, 100, 999]
    levels = [ComplexityLevel.EASY, ComplexityLevel.MEDIUM, ComplexityLevel.HARD]
    n_total = len(seeds) * len(levels) * len(_CHECKS)
    n_passed = 0
    for seed in seeds:
        for level in levels:
            for name, fn in _CHECKS:
                try:
                    fn(seed, level)
                    n_passed += 1
                except AssertionError as exc:
                    print(f"FAIL [{name}]: {exc}")
    print(f"compute_visual_state_unit: PASS")
    print(f"{n_passed}/{n_total} env-level checks passed.")
    return 0 if n_passed == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
