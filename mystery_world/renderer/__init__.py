"""2D embodied renderer for the mystery world.

Provides a pygame-based top-down view of the same `WorldState` used by the
text benchmark. Humans drive with WASD + E; their inputs are translated into
the same discrete `AgentAction` calls the LLM agents use, so scoring stays
identical across modalities.
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


def __getattr__(name):
    """Lazy-load MysteryGame so headless-only imports avoid pygame display init."""
    if name == "MysteryGame":
        from mystery_world.renderer.game import MysteryGame
        return MysteryGame
    raise AttributeError(name)
