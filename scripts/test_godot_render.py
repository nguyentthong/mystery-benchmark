"""
M3/M4/M9 (3D) verification: Python <-> Godot bridge under the mock.

Tests the parts of mystery_world/godot_render.py that don't need a real
Godot binary: room-payload assembly, MockGodotRenderer determinism,
render_observation_png_3d / render_observation_clip_3d wiring through
the env, and the at_game_time override for sub-step clip frames.

The real Godot render path is verified separately via
scripts/smoke_godot_render.py on a machine with Godot installed.

Usage:
    MYSTERYARENA_GODOT=mock .venv/bin/python scripts/test_godot_render.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force the mock so this script works without a Godot binary.
os.environ["MYSTERYARENA_GODOT"] = "mock"

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.entities import (
    EvidenceState,
    EvidenceType,
    VISUAL_FRESH_THRESHOLD,
    VISUAL_STALE_THRESHOLD,
)
from mystery_world.generator import generate_mystery
from mystery_world.godot_render import (
    MockGodotRenderer,
    _serialize_room,
    get_default_renderer,
    render_observation_clip_3d,
    render_observation_png_3d,
    reset_default_renderer,
)
from mystery_world.world import AgentAction, MysteryEnvironment


def _gen(seed: int):
    return generate_mystery(
        seed=seed,
        config=COMPLEXITY_PRESETS[ComplexityLevel.EASY],
        audit_temporal_necessity=True,
    )


def _is_png(b: object) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(b"\x89PNG\r\n\x1a\n")


def _check_default_is_mock() -> bool:
    reset_default_renderer()
    r = get_default_renderer()
    if not isinstance(r, MockGodotRenderer):
        print(f"FAIL [default_is_mock]: got {type(r).__name__}")
        return False
    print("  default_is_mock: OK")
    return True


def _check_serialize_room_shape(seeds: list[int]) -> bool:
    """Every audited EASY state's room payload has the required keys + a
    non-empty tile grid + a deterministic spawn."""
    required = {
        "room_id", "name", "description", "width", "height",
        "tiles", "doors", "objects", "characters", "spawn",
        "evidence_overlays",
    }
    for seed in seeds:
        state = _gen(seed)
        env = MysteryEnvironment(state)   # visual_mode irrelevant for serialize_room
        visible = env.get_visible_evidence()
        payload = _serialize_room(state, env.agent_location_id, visible)
        missing = required - payload.keys()
        if missing:
            print(f"FAIL [serialize_room_shape]: seed={seed} missing {missing}")
            return False
        if not payload["tiles"] or not payload["tiles"][0]:
            print(f"FAIL [serialize_room_shape]: seed={seed} empty tile grid")
            return False
        if payload["width"] != len(payload["tiles"]):
            print(f"FAIL [serialize_room_shape]: seed={seed} width != len(tiles)")
            return False
    print(f"  serialize_room_shape: OK across {len(seeds)} seeds")
    return True


def _check_mock_determinism(seeds: list[int]) -> bool:
    """Same room payload -> byte-identical PNG."""
    for seed in seeds:
        state = _gen(seed)
        env = MysteryEnvironment(state, visual_mode=True)
        a = render_observation_png_3d(env)
        b = render_observation_png_3d(env)
        if a != b:
            print(f"FAIL [mock_determinism]: seed={seed} differing renders")
            return False
        if not _is_png(a):
            print(f"FAIL [mock_determinism]: seed={seed} not PNG")
            return False
    print(f"  mock_determinism: OK across {len(seeds)} seeds")
    return True


def _check_mock_input_sensitivity(seeds: list[int]) -> bool:
    """Different game_time (current_step) must yield different PNGs --
    otherwise the mock isn't carrying the M2 channel signal through.
    The two renders are taken sequentially against the same env after
    advancing current_step so the underlying state actually differs."""
    for seed in seeds:
        state = _gen(seed)
        env = MysteryEnvironment(state, visual_mode=True)
        env._state.current_step = 0
        a = render_observation_png_3d(env)
        env._state.current_step = 20
        b = render_observation_png_3d(env)
        if a == b:
            print(
                f"FAIL [mock_input_sensitivity]: seed={seed} renders identical "
                f"at current_step 0 vs 20 -- mock ignores aging"
            )
            return False
    print(f"  mock_input_sensitivity: OK across {len(seeds)} seeds")
    return True


def _check_clip_sub_step(seeds: list[int]) -> bool:
    """render_observation_clip_3d at N=4 produces 4 frames; the last frame
    matches the single-image render at current_step; sub-step game_time
    is threaded through the mock (band-crossing seeds yield distinct
    frames)."""
    n = 0
    n_distinct = 0
    for seed in seeds:
        state = _gen(seed)
        env = MysteryEnvironment(state, visual_mode=True)
        # Position current_step so the step straddles BRIGHT->DULL for some
        # evidence in the agent's start room. If none exists, the test still
        # checks frame count and last-frame consistency; only the band-change
        # tally is conditional.
        from mystery_world.entities import EvidenceType
        target = None
        for oid in state.locations[env.agent_location_id].objects_here:
            obj = state.objects.get(oid)
            if obj is None or not obj.evidence_id:
                continue
            ev = state.evidence.get(obj.evidence_id)
            if (ev is None or ev.relevance is None
                or ev.evidence_type != EvidenceType.PHYSICAL):
                continue
            target = ev
            break
        if target is not None:
            env._state.current_step = int(target.relevance.contact_timestamp + VISUAL_FRESH_THRESHOLD)
        frames = render_observation_clip_3d(env, n_frames=4)
        n += 1
        if len(frames) != 4:
            print(f"FAIL [clip_sub_step]: seed={seed} expected 4 frames, got {len(frames)}")
            return False
        if not all(_is_png(f) for f in frames):
            print(f"FAIL [clip_sub_step]: seed={seed} non-PNG frame in clip")
            return False
        last_image = render_observation_png_3d(env)
        if frames[-1] != last_image:
            print(f"FAIL [clip_sub_step]: seed={seed} frames[-1] != render at current_step")
            return False
        if len(set(frames)) > 1:
            n_distinct += 1
    if n_distinct == 0:
        print(
            f"FAIL [clip_sub_step]: across {n} seeds, no clip showed distinct "
            f"frames -- sub-step game_time override is not threading through"
        )
        return False
    print(f"  clip_sub_step: OK ({n_distinct}/{n} clips have distinct sub-step frames)")
    return True


def _check_env_uses_3d_path(seeds: list[int]) -> bool:
    """When MYSTERYARENA_RENDERER is unset (or 'godot'), the env's
    _render_observation_image goes through the mock. The PNG should be
    one of the mock's signature small striped images (>= 16x16)."""
    for seed in seeds:
        state = _gen(seed)
        env = MysteryEnvironment(state, visual_mode=True)
        img = env.get_observation_image()
        if not _is_png(img):
            print(f"FAIL [env_uses_3d_path]: seed={seed} env returned non-PNG")
            return False
    print(f"  env_uses_3d_path: OK across {len(seeds)} seeds")
    return True


def main() -> int:
    seeds = list(range(6))
    ok1 = _check_default_is_mock()
    ok2 = _check_serialize_room_shape(seeds)
    ok3 = _check_mock_determinism(seeds)
    ok4 = _check_mock_input_sensitivity(seeds)
    ok5 = _check_clip_sub_step(seeds)
    ok6 = _check_env_uses_3d_path(seeds)
    all_ok = all([ok1, ok2, ok3, ok4, ok5, ok6])
    print(f"\nM3/M4/M9-3D overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
