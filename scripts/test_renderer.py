"""
M3 verification: ensure the 2D top-down renderer produces a deterministic
image that visibly changes as evidence ages through VisualState bands.

Properties checked:
  1. render_observation_image / _png / _pil produce non-empty output of the
     expected shape / type.
  2. Determinism: same (state, current_step, agent_location_id) yields a
     byte-identical numpy array (and identical PNG bytes).
  3. Visible-evidence aging changes the image: rendering at current_step
     values that put a piece of evidence in BRIGHT vs DULL vs FADED bands
     produces three distinct images.
  4. Different agent rooms produce different images.

Usage:
    .venv/bin/python scripts/test_renderer.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.entities import EvidenceState, VisualState
from mystery_world.generator import generate_mystery
from mystery_world.renderer import (
    render_observation_image,
    render_observation_pil,
    render_observation_png,
)
from mystery_world.world import MysteryEnvironment


def _find_room_with_physical_evidence(env: MysteryEnvironment) -> tuple[str, str] | None:
    """Return (room_id, evidence_id) for any room with a non-hidden,
    temporally-relevant PHYSICAL evidence (the kind whose aging this test
    can drive). None if no such room exists in this generated world."""
    from mystery_world.entities import EvidenceType
    for loc_id, loc in env.state.locations.items():
        for oid in loc.objects_here:
            obj = env.state.objects.get(oid)
            if obj is None or not obj.evidence_id:
                continue
            ev = env.state.evidence.get(obj.evidence_id)
            if ev is None or ev.relevance is None:
                continue
            if ev.evidence_type != EvidenceType.PHYSICAL:
                continue
            if ev.state in (EvidenceState.HIDDEN, EvidenceState.DESTROYED):
                continue
            return loc_id, ev.id
    return None


def _check_shape_and_types(seed: int, level: ComplexityLevel) -> None:
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state)

    arr = render_observation_image(env)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise AssertionError(
            f"[seed={seed}, {level.name}] expected (H,W,3) array, got shape {arr.shape}"
        )
    if arr.dtype.name != "uint8":
        raise AssertionError(
            f"[seed={seed}, {level.name}] expected uint8 dtype, got {arr.dtype}"
        )

    png = render_observation_png(env)
    if not isinstance(png, (bytes, bytearray)) or len(png) < 100:
        raise AssertionError(
            f"[seed={seed}, {level.name}] PNG bytes too small: {len(png) if png else 0}"
        )
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError(
            f"[seed={seed}, {level.name}] PNG header missing"
        )

    pil = render_observation_pil(env)
    if pil.mode not in ("RGB", "RGBA"):
        raise AssertionError(
            f"[seed={seed}, {level.name}] PIL image mode unexpected: {pil.mode}"
        )


def _check_determinism(seed: int, level: ComplexityLevel) -> None:
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state)

    a1 = render_observation_image(env)
    a2 = render_observation_image(env)
    if not (a1 == a2).all():
        raise AssertionError(
            f"[seed={seed}, {level.name}] re-rendering same state produced different "
            f"pixels (non-deterministic renderer)"
        )

    p1 = render_observation_png(env)
    p2 = render_observation_png(env)
    if p1 != p2:
        raise AssertionError(
            f"[seed={seed}, {level.name}] PNG bytes differ across repeated renders"
        )


def _check_aging_changes_image(seed: int, level: ComplexityLevel) -> None:
    """Render the same room at three current_step values that put the target
    evidence in BRIGHT / DULL / FADED bands. The three resulting images must
    differ pairwise."""
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state)
    found = _find_room_with_physical_evidence(env)
    if found is None:
        return  # not exercised on this seed/level
    room_id, ev_id = found
    env.agent_location_id = room_id

    ev = state.evidence[ev_id]
    contact_ts = ev.relevance.contact_timestamp

    hashes: dict[VisualState, str] = {}
    for offset, expected_band in [(0, VisualState.BRIGHT),
                                  (3, VisualState.DULL),
                                  (8, VisualState.FADED)]:
        env._state.current_step = int(contact_ts) + offset
        # Confirm the env's visible_evidence agrees on the band; otherwise the
        # test premise (driving through three bands) is broken.
        snapshot = env.get_visible_evidence()
        match = [e for e in snapshot if e["id"] == ev_id]
        if not match:
            return  # evidence not visible at this step — skip silently
        actual_band = match[0]["visual_state"]
        if actual_band != expected_band.name:
            raise AssertionError(
                f"[seed={seed}, {level.name}] offset {offset} expected band "
                f"{expected_band.name} but visible_evidence says {actual_band}"
            )
        arr = render_observation_image(env)
        hashes[expected_band] = hashlib.md5(arr.tobytes()).hexdigest()

    # All three bands must yield distinct images.
    if len(set(hashes.values())) != 3:
        raise AssertionError(
            f"[seed={seed}, {level.name}] expected 3 distinct band images but "
            f"got hashes {hashes} — aging is not driving the rendered output"
        )


def _check_different_rooms_differ(seed: int, level: ComplexityLevel) -> None:
    state = generate_mystery(seed=seed, config=COMPLEXITY_PRESETS[level])
    env = MysteryEnvironment(state)
    room_ids = list(state.locations.keys())
    if len(room_ids) < 2:
        return  # only one room — can't test

    env.agent_location_id = room_ids[0]
    arr_a = render_observation_image(env)

    env.agent_location_id = room_ids[1]
    arr_b = render_observation_image(env)

    if arr_a.shape == arr_b.shape and (arr_a == arr_b).all():
        raise AssertionError(
            f"[seed={seed}, {level.name}] two different rooms rendered identical "
            f"images — renderer is ignoring room geometry"
        )


_CHECKS = (
    ("shape_and_types",       _check_shape_and_types),
    ("determinism",           _check_determinism),
    ("aging_changes_image",   _check_aging_changes_image),
    ("different_rooms_differ",_check_different_rooms_differ),
)


def main() -> int:
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
    print(f"{n_passed}/{n_total} renderer checks passed.")
    return 0 if n_passed == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
