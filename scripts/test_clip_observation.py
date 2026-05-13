"""
M9 verification: optional N-frame clip return per action.

Properties checked:
  1. Default behaviour (no clip_dispatch): result.image is a PNG, result.frames
     is [result.image]. Single-image consumers unaffected.
  2. visual_mode=False: result.image is None, result.frames is [].
  3. clip_dispatch maps actions to N: WAIT -> 4 yields 4 PNG frames;
     ANALYZE -> 1 yields 1 frame. Dispatch is per-action.
  4. Last frame of a clip is byte-identical to the single-image render at
     the same step (consistency between clip endpoint and image API).
  5. Determinism: same (seed, action_history, dispatch) -> byte-identical
     frames at every step.
  6. Within-clip aging change: when a step crosses a VisualState band
     boundary, frames within the clip show different aging colours --
     proving the sub-step game-time override is wired through.

Usage:
    .venv/bin/python scripts/test_clip_observation.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.entities import (
    VISUAL_FRESH_THRESHOLD,
    VISUAL_STALE_THRESHOLD,
    EvidenceState,
    EvidenceType,
    compute_visual_state,
)
from mystery_world.generator import generate_mystery
from mystery_world.renderer import render_observation_png
from mystery_world.world import AgentAction, MysteryEnvironment


def _gen(seed: int):
    return generate_mystery(
        seed=seed,
        config=COMPLEXITY_PRESETS[ComplexityLevel.EASY],
        audit_temporal_necessity=True,
    )


def _is_png(b: object) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(b"\x89PNG\r\n\x1a\n")


def _check_default_single_image(seeds: list[int]) -> bool:
    for seed in seeds:
        env = MysteryEnvironment(_gen(seed), visual_mode=True)
        result = env.step(AgentAction.WAIT)
        if not _is_png(result.image):
            print(f"FAIL [default_single_image]: seed={seed} image not PNG")
            return False
        if len(result.frames) != 1:
            print(f"FAIL [default_single_image]: seed={seed} expected 1 frame, got {len(result.frames)}")
            return False
        if result.frames[0] != result.image:
            print(f"FAIL [default_single_image]: seed={seed} frames[0] != image")
            return False
    print(f"  default_single_image: OK across {len(seeds)} seeds")
    return True


def _check_visual_off(seeds: list[int]) -> bool:
    for seed in seeds:
        env = MysteryEnvironment(_gen(seed), visual_mode=False, clip_dispatch={AgentAction.WAIT: 4})
        result = env.step(AgentAction.WAIT)
        if result.image is not None:
            print(f"FAIL [visual_off]: seed={seed} image is not None")
            return False
        if result.frames != []:
            print(f"FAIL [visual_off]: seed={seed} frames not empty: {len(result.frames)}")
            return False
    print(f"  visual_off: OK across {len(seeds)} seeds")
    return True


def _check_dispatch(seeds: list[int]) -> bool:
    dispatch = {AgentAction.WAIT: 4, AgentAction.ANALYZE: 1}
    for seed in seeds:
        env = MysteryEnvironment(_gen(seed), visual_mode=True, clip_dispatch=dispatch)
        r_wait = env.step(AgentAction.WAIT)
        if len(r_wait.frames) != 4:
            print(f"FAIL [dispatch]: seed={seed} WAIT expected 4 frames, got {len(r_wait.frames)}")
            return False
        if not all(_is_png(f) for f in r_wait.frames):
            print(f"FAIL [dispatch]: seed={seed} WAIT some frames not PNG")
            return False
        # MOVE is not in dispatch -> default N=1
        loc = env.get_current_location()
        if loc and loc.adjacent_ids:
            tgt_name = env.state.locations[loc.adjacent_ids[0]].name
            r_move = env.step(AgentAction.MOVE, target_location=tgt_name)
            if len(r_move.frames) != 1:
                print(f"FAIL [dispatch]: seed={seed} MOVE (unset) expected 1 frame, got {len(r_move.frames)}")
                return False
    print(f"  dispatch: OK across {len(seeds)} seeds")
    return True


def _check_last_frame_matches_image(seeds: list[int]) -> bool:
    dispatch = {AgentAction.WAIT: 4}
    for seed in seeds:
        env = MysteryEnvironment(_gen(seed), visual_mode=True, clip_dispatch=dispatch)
        result = env.step(AgentAction.WAIT)
        if not result.frames:
            print(f"FAIL [last_frame_matches]: seed={seed} no frames")
            return False
        # By construction, the last frame is rendered at game_time = current_step,
        # which is the same as the single-image observation.
        single = render_observation_png(env)
        if result.frames[-1] != single:
            print(f"FAIL [last_frame_matches]: seed={seed} frames[-1] != independent render")
            return False
    print(f"  last_frame_matches_image: OK across {len(seeds)} seeds")
    return True


def _check_determinism(seeds: list[int]) -> bool:
    dispatch = {AgentAction.WAIT: 4}
    for seed in seeds:
        env_a = MysteryEnvironment(_gen(seed), visual_mode=True, clip_dispatch=dispatch)
        env_b = MysteryEnvironment(_gen(seed), visual_mode=True, clip_dispatch=dispatch)
        actions = [AgentAction.WAIT] * 3
        for a in actions:
            ra = env_a.step(a)
            rb = env_b.step(a)
            if len(ra.frames) != len(rb.frames):
                print(f"FAIL [determinism]: seed={seed} frame counts differ")
                return False
            for i, (fa, fb) in enumerate(zip(ra.frames, rb.frames)):
                if fa != fb:
                    print(
                        f"FAIL [determinism]: seed={seed} frame {i} differs "
                        f"md5={hashlib.md5(fa).hexdigest()[:8]} vs "
                        f"{hashlib.md5(fb).hexdigest()[:8]}"
                    )
                    return False
    print(f"  determinism: OK across {len(seeds)} seeds")
    return True


def _check_within_clip_band_change(seeds: list[int]) -> bool:
    """When a step crosses a VisualState band boundary, frames inside the clip
    must show different aging colours. Engineered by forcing current_step to
    a value where a piece of evidence's age straddles a threshold."""
    saw_within_clip_change = 0
    for seed in seeds:
        state = _gen(seed)
        # Find any temporally-relevant PHYSICAL evidence in a room.
        target = None
        for loc_id, loc in state.locations.items():
            for oid in loc.objects_here:
                obj = state.objects.get(oid)
                if obj is None or not obj.evidence_id:
                    continue
                ev = state.evidence.get(obj.evidence_id)
                if (ev is None or ev.relevance is None
                    or ev.state in (EvidenceState.HIDDEN, EvidenceState.DESTROYED)
                    or ev.evidence_type != EvidenceType.PHYSICAL):
                    continue
                target = (loc_id, ev)
                break
            if target:
                break
        if target is None:
            continue
        loc_id, ev = target
        contact_ts = ev.relevance.contact_timestamp

        env = MysteryEnvironment(state, visual_mode=True, clip_dispatch={AgentAction.WAIT: 4})
        env.agent_location_id = loc_id
        # Position current_step so the NEXT step crosses the BRIGHT->DULL
        # threshold: pre-step age must be < FRESH_THRESHOLD, post-step age
        # must be >= FRESH_THRESHOLD. Span is 1.0 (one step), so place
        # current_step at floor(contact_ts + FRESH_THRESHOLD).
        env._state.current_step = int(contact_ts + VISUAL_FRESH_THRESHOLD)
        # Sanity: pre-step BRIGHT, post-step DULL
        pre_age = env._state.current_step - contact_ts
        post_age = pre_age + 1
        if compute_visual_state(pre_age).name == compute_visual_state(post_age).name:
            continue  # this state doesn't actually cross; skip silently
        result = env.step(AgentAction.WAIT)
        if len(result.frames) < 2:
            continue
        if result.frames[0] != result.frames[-1]:
            saw_within_clip_change += 1
    if saw_within_clip_change == 0:
        print(
            f"FAIL [within_clip_band_change]: across {len(seeds)} seeds, no clip "
            f"showed differing frames despite an engineered band crossing -- "
            f"sub-step game-time override is not driving the render."
        )
        return False
    print(
        f"  within_clip_band_change: {saw_within_clip_change}/{len(seeds)} clips "
        f"had differing frames across the band boundary"
    )
    return True


def main() -> int:
    seeds = list(range(10))
    ok1 = _check_default_single_image(seeds)
    ok2 = _check_visual_off(seeds)
    ok3 = _check_dispatch(seeds)
    ok4 = _check_last_frame_matches_image(seeds)
    ok5 = _check_determinism(seeds)
    ok6 = _check_within_clip_band_change(seeds)
    all_ok = all([ok1, ok2, ok3, ok4, ok5, ok6])
    print(f"\nM9 overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
