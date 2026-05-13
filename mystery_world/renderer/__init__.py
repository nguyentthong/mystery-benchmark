"""2D top-down headless renderer for the visual-temporal benchmark.

Renders a single room of the current ``WorldState`` from above and returns the
result as PNG bytes / a numpy array / a PIL image. Used as the visual channel
of the post-action observation: alongside the existing textual observation,
the env can return a rendered image showing what's visible from the agent's
current room at ``current_step``.

Per-evidence aging is rendered as a coloured trace overlay whose hue/intensity
is driven by ``VisualState`` (BRIGHT / DULL / FADED). The same persistent
piece of evidence observed at different game-times therefore produces a
different image — that change-relation across observations is the load-bearing
temporal signal for the benchmark.

The renderer is fully deterministic given
``(env.state.seed, env.state.current_step, env.agent_location_id)``.
"""
from mystery_world.renderer.layout import RoomLayout, Tile, build_room_layout
from mystery_world.renderer.observation import (
    render_observation_image,
    render_observation_pil,
    render_observation_png,
)
from mystery_world.renderer.sprites import (
    EmojiSprites,
    KenneySprites,
    ProceduralSprites,
    SpriteLoader,
)

__all__ = [
    "RoomLayout",
    "Tile",
    "build_room_layout",
    "render_observation_image",
    "render_observation_png",
    "render_observation_pil",
    "SpriteLoader",
    "ProceduralSprites",
    "EmojiSprites",
    "KenneySprites",
]
