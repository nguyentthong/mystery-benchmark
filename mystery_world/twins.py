"""Twin-pair episode generation (M8).

A *twin pair* is a pair of audited mysteries (A, B) constructed so that the
visual evidence aging trajectories run in opposite directions, while the
underlying who/what/where structure is identical. The twin-pair metric is
then: did the agent solve BOTH episodes correctly?

  - An agent that genuinely tracks persistent objects across observations
    and reasons about freshness gradients should solve both A and B
    correctly: the culprit, weapon, and location are unchanged across
    the pair, only the visual aging changes.
  - An agent that overfits to specific visual-aging templates (e.g.
    "always-FADED bloodstain implies X") may succeed on one and fail on
    the other.

Construction: ``_time_reverse(state)`` returns a deep-copied state with
every evidence's ``contact_timestamp`` reflected around
``num_time_steps``, plus the murder step / timestamp reflected the same
way. The canonical itinerary is re-emitted for the reflected world so the
audit invariant (one piece of evidence must be observed across two or
more VisualState bands during the canonical replay) still holds.

The current implementation is a *soft* twin: A and B share the same
culprit-weapon-location triple. A stricter "opposite solutions" twin --
two episodes whose unordered observation bag is identical but whose
ground-truth accusations differ -- requires editing alibi-evidence and
witness-statement templates downstream of generation and is deferred
to a later milestone. The metric scaffold here will work unchanged when
that lands.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from mystery_world.canonical import audit_episode, generate_canonical_itinerary
from mystery_world.generator import generate_mystery

from mystery_world import COMPLEXITY_PRESETS, ComplexityConfig, ComplexityLevel
from mystery_world.world import WorldState


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class TwinPair:
    """A pair of audited mysteries with reflected temporal structure.

    ``a`` is the base mystery; ``b`` is its time-reversed twin. Both have
    the same culprit_id, weapon_id, location_id; only contact_timestamps
    (and the murder time) are reflected around ``num_time_steps``.
    """
    a: WorldState
    b: WorldState


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def _reflect(t: float, axis: float) -> float:
    """Reflect t around axis. Used to flip a contact_timestamp."""
    return axis - t


def _time_reverse(state: WorldState) -> WorldState:
    """Return a deep copy of ``state`` with all temporal markers reflected
    around ``num_time_steps``."""
    twin = copy.deepcopy(state)
    axis = float(twin.config.num_time_steps)

    # Reflect every evidence's contact_timestamp.
    for ev in twin.evidence.values():
        if ev.relevance is not None:
            ev.relevance.contact_timestamp = _reflect(
                ev.relevance.contact_timestamp, axis
            )

    # Reflect the murder time itself. Clamp into a valid range so the
    # episode is still solvable (murder must happen at some point during
    # the simulated timeline).
    new_murder_step = int(round(axis - twin.murder_step))
    new_murder_step = max(1, min(twin.config.num_time_steps - 1, new_murder_step))
    twin.murder_step = new_murder_step
    twin.murder_timestamp = float(new_murder_step)

    # Re-emit the canonical itinerary so the audit invariant still holds on
    # B (the WAIT-then-revisit cadence needs to straddle a VisualState
    # boundary in the reflected world, which is a different boundary).
    twin.canonical_itinerary = generate_canonical_itinerary(twin)

    return twin


def generate_twin_pair(
    seed: int,
    config: ComplexityConfig | None = None,
    max_retries: int = 10,
) -> TwinPair | None:
    """Return an audited (A, B) twin pair, or None if no such pair could
    be constructed within ``max_retries`` attempts at increasing seeds.

    Both A and B must pass ``audit_episode``: the audit's cross-observation
    VisualState-change invariant has to hold under the reflected
    contact_timestamps too, and depending on the murder_step / num_time_steps
    ratio that may require a few seed attempts.
    """
    cfg = config or COMPLEXITY_PRESETS[ComplexityLevel.EASY]
    for attempt in range(max_retries):
        a = generate_mystery(
            seed=seed + attempt,
            config=cfg,
            audit_temporal_necessity=True,
        )
        b = _time_reverse(a)
        b_passed, _ = audit_episode(b)
        if b_passed:
            return TwinPair(a=a, b=b)
    return None
