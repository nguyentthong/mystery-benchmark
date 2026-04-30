"""Pluggable sprite loaders.

The renderer never draws primitives directly — it goes through a `SpriteLoader`
so the visual layer can be swapped (procedural icons today, Kenney.nl tile
sheets tomorrow) without touching the game loop.

Two implementations:
- `ProceduralSprites` — pygame primitives only, no external assets. Distinct
  shape+colour per category so a VLM has a discriminative visual channel.
- `KenneySprites`    — placeholder; loads bitmaps from a sprite directory.
  Drop a Kenney.nl pack into `assets/kenney/` and switch the loader.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import pygame

from mystery_world.entities import Character, EvidenceState, WorldObject

# ---------------------------------------------------------------------------
# Palette (shared)
# ---------------------------------------------------------------------------

FLOOR_COLOR = (60, 50, 70)
FLOOR_GRID = (52, 44, 60)
WALL_COLOR = (32, 28, 38)
WALL_HIGHLIGHT = (80, 70, 95)
DOOR_COLOR = (180, 130, 60)
DOOR_HIGHLIGHT = (240, 200, 110)

OBJECT_COLOR = (220, 200, 110)        # tan square — generic prop
EVIDENCE_COLOR = (110, 220, 180)      # teal circle — discovered evidence
WEAPON_COLOR = (240, 110, 110)        # red diamond — weapon
NPC_COLOR = (130, 170, 240)           # blue oval — alive NPC
CULPRIT_HINT_COLOR = (130, 170, 240)  # never visually distinguished from NPC
VICTIM_COLOR = (90, 90, 100)          # dark grey oval — body
PLAYER_COLOR = (255, 240, 220)
PLAYER_OUTLINE = (40, 30, 20)
TEXT_COLOR = (235, 225, 215)
HUD_BG = (10, 8, 14)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class SpriteLoader(ABC):
    """All drawing goes through these methods so the visual layer is pluggable."""

    @abstractmethod
    def draw_floor(self, surface: pygame.Surface, rect: pygame.Rect) -> None: ...

    @abstractmethod
    def draw_wall(self, surface: pygame.Surface, rect: pygame.Rect) -> None: ...

    @abstractmethod
    def draw_door(self, surface: pygame.Surface, rect: pygame.Rect) -> None: ...

    @abstractmethod
    def draw_object(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        obj: WorldObject,
        discovered: bool,
        evidence_state: EvidenceState | None,
    ) -> None: ...

    @abstractmethod
    def draw_character(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        char: Character,
    ) -> None: ...

    @abstractmethod
    def draw_player(self, surface: pygame.Surface, cx: int, cy: int, radius: int) -> None: ...


# ---------------------------------------------------------------------------
# Procedural — pygame primitives, no external assets
# ---------------------------------------------------------------------------

class ProceduralSprites(SpriteLoader):
    """Distinct shape+colour per category. Sufficient for VLM evaluation as
    long as text labels are kept out of the agent observation image."""

    def draw_floor(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        pygame.draw.rect(surface, FLOOR_COLOR, rect)
        pygame.draw.rect(surface, FLOOR_GRID, rect, 1)

    def draw_wall(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        pygame.draw.rect(surface, WALL_COLOR, rect)
        pygame.draw.rect(surface, WALL_HIGHLIGHT, rect, 1)

    def draw_door(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        pygame.draw.rect(surface, DOOR_COLOR, rect)
        pygame.draw.rect(surface, DOOR_HIGHLIGHT, rect, 2)

    def draw_object(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        obj: WorldObject,
        discovered: bool,
        evidence_state: EvidenceState | None,
    ) -> None:
        cx, cy = rect.center
        size = int(min(rect.w, rect.h) * 0.55)
        half = size // 2

        if evidence_state in (EvidenceState.HIDDEN, EvidenceState.DESTROYED):
            return  # not visible to the agent

        if obj.is_weapon:
            # Red diamond (rotated square)
            pts = [(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)]
            pygame.draw.polygon(surface, WEAPON_COLOR, pts)
            pygame.draw.polygon(surface, (40, 20, 20), pts, 2)
        elif obj.evidence_id and discovered:
            # Teal circle — discovered evidence
            pygame.draw.circle(surface, EVIDENCE_COLOR, (cx, cy), half)
            pygame.draw.circle(surface, (20, 60, 50), (cx, cy), half, 2)
        elif obj.evidence_id:
            # Tan square with corner mark — interactable but not yet examined
            pygame.draw.rect(
                surface, OBJECT_COLOR,
                (cx - half, cy - half, size, size), border_radius=4,
            )
            pygame.draw.rect(
                surface, (40, 30, 10),
                (cx - half, cy - half, size, size), 2, border_radius=4,
            )
            pygame.draw.circle(surface, (40, 30, 10), (cx + half - 4, cy - half + 4), 3)
        else:
            # Plain tan square — non-evidence prop
            pygame.draw.rect(
                surface, OBJECT_COLOR,
                (cx - half, cy - half, size, size), border_radius=4,
            )
            pygame.draw.rect(
                surface, (40, 30, 10),
                (cx - half, cy - half, size, size), 2, border_radius=4,
            )

    def draw_character(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        char: Character,
    ) -> None:
        cx, cy = rect.center
        radius = int(min(rect.w, rect.h) * 0.35)
        if not char.is_alive:
            # Body: dark grey ellipse on the floor
            pygame.draw.ellipse(
                surface, VICTIM_COLOR,
                (cx - radius, cy - radius // 2, 2 * radius, radius),
            )
            pygame.draw.ellipse(
                surface, (20, 20, 30),
                (cx - radius, cy - radius // 2, 2 * radius, radius), 2,
            )
            return
        # Alive NPC: blue circle with a little face mark (eyes) so VLMs can
        # distinguish "person" from "round object"
        pygame.draw.circle(surface, NPC_COLOR, (cx, cy), radius)
        pygame.draw.circle(surface, (20, 30, 60), (cx, cy), radius, 2)
        eye = max(2, radius // 5)
        pygame.draw.circle(surface, (20, 30, 60), (cx - radius // 3, cy - radius // 4), eye)
        pygame.draw.circle(surface, (20, 30, 60), (cx + radius // 3, cy - radius // 4), eye)

    def draw_player(self, surface: pygame.Surface, cx: int, cy: int, radius: int) -> None:
        pygame.draw.circle(surface, PLAYER_OUTLINE, (cx, cy), radius + 2)
        pygame.draw.circle(surface, PLAYER_COLOR, (cx, cy), radius)
        # Forward triangle so orientation/identity is unambiguous
        pts = [(cx, cy - radius - 4), (cx - 5, cy - radius + 2), (cx + 5, cy - radius + 2)]
        pygame.draw.polygon(surface, PLAYER_OUTLINE, pts)


# ---------------------------------------------------------------------------
# Kenney.nl loader (stub) — drop tile sheets in assets/kenney/ to enable
# ---------------------------------------------------------------------------

class KenneySprites(SpriteLoader):
    """Loads tiles from `assets/kenney/`. Currently a stub that delegates to
    ProceduralSprites for any tile it doesn't have a bitmap for. Wire up by:

        1. Download a Kenney top-down/RPG pack
        2. Put PNG files at: assets/kenney/{floor,wall,door,weapon,evidence,object,npc,victim,player}.png
        3. Pass `KenneySprites()` instead of `ProceduralSprites()` to the renderer
    """

    def __init__(self, asset_dir: Path | None = None) -> None:
        self._procedural = ProceduralSprites()
        self._dir = asset_dir or (Path(__file__).resolve().parents[2] / "assets" / "kenney")
        self._cache: dict[str, pygame.Surface | None] = {}

    def _load(self, name: str) -> pygame.Surface | None:
        if name in self._cache:
            return self._cache[name]
        path = self._dir / f"{name}.png"
        surf: pygame.Surface | None = None
        if path.exists():
            try:
                surf = pygame.image.load(str(path)).convert_alpha()
            except Exception:
                surf = None
        self._cache[name] = surf
        return surf

    def _blit_or_fallback(
        self,
        name: str,
        surface: pygame.Surface,
        rect: pygame.Rect,
        fallback,
    ) -> None:
        sprite = self._load(name)
        if sprite is None:
            fallback()
            return
        scaled = pygame.transform.smoothscale(sprite, (rect.w, rect.h))
        surface.blit(scaled, rect.topleft)

    def draw_floor(self, surface, rect):
        self._blit_or_fallback("floor", surface, rect, lambda: self._procedural.draw_floor(surface, rect))

    def draw_wall(self, surface, rect):
        self._blit_or_fallback("wall", surface, rect, lambda: self._procedural.draw_wall(surface, rect))

    def draw_door(self, surface, rect):
        self._blit_or_fallback("door", surface, rect, lambda: self._procedural.draw_door(surface, rect))

    def draw_object(self, surface, rect, obj, discovered, evidence_state):
        if evidence_state in (EvidenceState.HIDDEN, EvidenceState.DESTROYED):
            return
        if obj.is_weapon:
            name = "weapon"
        elif obj.evidence_id and discovered:
            name = "evidence_discovered"
        elif obj.evidence_id:
            name = "evidence"
        else:
            name = "object"
        self._blit_or_fallback(
            name, surface, rect,
            lambda: self._procedural.draw_object(surface, rect, obj, discovered, evidence_state),
        )

    def draw_character(self, surface, rect, char):
        name = "victim" if not char.is_alive else "npc"
        self._blit_or_fallback(
            name, surface, rect,
            lambda: self._procedural.draw_character(surface, rect, char),
        )

    def draw_player(self, surface, cx, cy, radius):
        rect = pygame.Rect(cx - radius, cy - radius, 2 * radius, 2 * radius)
        self._blit_or_fallback(
            "player", surface, rect,
            lambda: self._procedural.draw_player(surface, cx, cy, radius),
        )
