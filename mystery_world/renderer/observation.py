"""Headless image-observation rendering for agents.

`render_observation_image(env)` returns the current room as a numpy array of
shape (H, W, 3), uint8. No display window is opened. Critically, the rendered
image contains **no in-scene text labels** — object/character names are not
drawn on top of sprites, so a VLM cannot read the answer off the pixels. The
text channel (room name, last observation, exits, who-is-here) is supplied
separately to the agent as a normal textual observation.

Usage:

    img = render_observation_image(env)              # (H, W, 3) uint8 numpy
    # ... pass to a multimodal API as base64 PNG / PIL Image ...

The renderer is fully deterministic given (env.state.seed, location_id), so
two episodes with the same seed produce identical pixel observations.
"""
from __future__ import annotations

import io
import os
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from PIL import Image as PILImage

import pygame

from mystery_world.entities import EvidenceState
from mystery_world.renderer.layout import RoomLayout, Tile, build_room_layout
from mystery_world.renderer.sprites import ProceduralSprites, SpriteLoader
from mystery_world.world import MysteryEnvironment

# Tile size for agent observations. Smaller than the human-play view so the
# image stays under typical multimodal API size limits.
OBS_TILE_PX = 32
OBS_BG = (18, 16, 22)


# ---------------------------------------------------------------------------
# Headless pygame init
# ---------------------------------------------------------------------------

_pygame_inited = False


def _ensure_pygame() -> None:
    """Initialise pygame in headless (dummy SDL) mode if not already inited."""
    global _pygame_inited
    if _pygame_inited:
        return
    # Only override SDL driver if no display is configured.
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.display.init()
    pygame.font.init()
    _pygame_inited = True


# ---------------------------------------------------------------------------
# Layout cache (per-process, keyed by location id)
# ---------------------------------------------------------------------------

_layout_cache: dict[str, RoomLayout] = {}


def _layout_for(env: MysteryEnvironment, location_id: str) -> RoomLayout:
    if location_id not in _layout_cache:
        loc = env.state.locations[location_id]
        _layout_cache[location_id] = build_room_layout(loc)
    layout = _layout_cache[location_id]
    # Refresh dynamic content (NPCs may have moved into / out of this room)
    loc = env.state.locations[location_id]
    used: set[tuple[int, int]] = set(layout.doors.keys())
    layout.objects = {oid: pos for oid, pos in layout.objects.items() if oid in loc.objects_here}
    layout.characters = {cid: pos for cid, pos in layout.characters.items() if cid in loc.characters_here}
    used.update(layout.objects.values())
    used.update(layout.characters.values())
    free = [
        (x, y)
        for x in range(2, layout.width - 2)
        for y in range(2, layout.height - 2)
        if layout.tiles[x][y] == Tile.FLOOR and (x, y) not in used
    ]
    import random
    rng = random.Random(f"obs::{location_id}::{env.state.current_step}")
    rng.shuffle(free)
    for oid in loc.objects_here:
        if oid not in layout.objects and free:
            layout.objects[oid] = free.pop()
    for cid in loc.characters_here:
        if cid not in layout.characters and free:
            layout.characters[cid] = free.pop()
    return layout


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_observation_image(
    env: MysteryEnvironment,
    sprites: SpriteLoader | None = None,
    tile_px: int = OBS_TILE_PX,
) -> np.ndarray:
    """Render the agent's current room as an (H, W, 3) uint8 numpy array.

    No text labels are drawn on the image — vision must be discriminative on
    its own. The agent's avatar appears at the room's default spawn point.
    """
    _ensure_pygame()
    sprites = sprites or ProceduralSprites()
    layout = _layout_for(env, env.agent_location_id)

    w_px = layout.width * tile_px
    h_px = layout.height * tile_px
    surface = pygame.Surface((w_px, h_px))
    surface.fill(OBS_BG)

    # Tiles (floor / wall / door)
    for x in range(layout.width):
        for y in range(layout.height):
            rect = pygame.Rect(x * tile_px, y * tile_px, tile_px, tile_px)
            tile = layout.tiles[x][y]
            if tile == Tile.WALL:
                sprites.draw_wall(surface, rect)
            elif tile == Tile.DOOR:
                sprites.draw_door(surface, rect)
            else:
                sprites.draw_floor(surface, rect)

    # Objects
    for oid, (x, y) in layout.objects.items():
        obj = env.state.objects.get(oid)
        if obj is None:
            continue
        ev_state = None
        discovered = False
        if obj.evidence_id:
            ev = env.state.evidence.get(obj.evidence_id)
            if ev:
                ev_state = ev.state
                discovered = ev.id in env._discovered_evidence
        rect = pygame.Rect(x * tile_px, y * tile_px, tile_px, tile_px)
        sprites.draw_object(surface, rect, obj, discovered, ev_state)

    # Characters
    for cid, (x, y) in layout.characters.items():
        char = env.state.characters.get(cid)
        if char is None:
            continue
        rect = pygame.Rect(x * tile_px, y * tile_px, tile_px, tile_px)
        sprites.draw_character(surface, rect, char)

    # Player avatar at default spawn (canonical agent pose per room)
    sx, sy = layout.default_spawn
    cx = sx * tile_px + tile_px // 2
    cy = sy * tile_px + tile_px // 2
    sprites.draw_player(surface, cx, cy, tile_px // 3)

    # Convert to numpy (H, W, 3) uint8 RGB
    raw = pygame.surfarray.array3d(surface)
    # pygame returns (W, H, 3); transpose to (H, W, 3)
    return raw.transpose(1, 0, 2).copy()


def render_observation_png(env: MysteryEnvironment, **kwargs) -> bytes:
    """Same as `render_observation_image` but returns PNG-encoded bytes,
    suitable for direct upload to multimodal APIs."""
    arr = render_observation_image(env, **kwargs)
    try:
        from PIL import Image
    except ImportError as e:
        raise ImportError(
            "PIL/Pillow required for PNG encoding. `uv add pillow`"
        ) from e
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def render_observation_pil(env: MysteryEnvironment, **kwargs) -> "PILImage.Image":
    """Same but returns a PIL Image object."""
    arr = render_observation_image(env, **kwargs)
    try:
        from PIL import Image
    except ImportError as e:
        raise ImportError("PIL/Pillow required. `uv add pillow`") from e
    return Image.fromarray(arr)
