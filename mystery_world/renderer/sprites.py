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

import hashlib
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
        # The object remains in the room even if its associated evidence is
        # HIDDEN or DESTROYED — only the *clue* on it has gone, not the prop
        # itself. So we always draw the icon.
        draw_fn = self._pick_object_icon(obj.name)
        draw_fn(surface, rect)

    # ---- icon picker --------------------------------------------------

    @classmethod
    def _pick_object_icon(cls, name: str):
        """Match the object's name against a small library; fall back to
        a generic prop. Uses word-boundary matching so e.g. "urn" doesn't
        match inside "b-urn-ed" or "overt-urn-ed". The library stores method
        names as strings so we can reference methods defined later in the
        class body."""
        import re
        n = (name or "").lower()
        for keys, fn_name in cls._ICON_LIBRARY:
            for k in keys:
                if re.search(rf"\b{re.escape(k)}\b", n):
                    return getattr(cls, fn_name)
        return cls._draw_chair

    # ---- pictograph primitives ---------------------------------------

    @staticmethod
    def _draw_mirror(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.6)
        body = pygame.Rect(cx - s // 3, cy - s // 2, 2 * s // 3, s)
        pygame.draw.rect(surface, (200, 220, 240), body, border_radius=int(s * 0.3))
        pygame.draw.rect(surface, (40, 30, 20), body, 2, border_radius=int(s * 0.3))
        # Shimmer
        pygame.draw.line(surface, (255, 255, 255), (body.x + 4, body.y + 6), (body.x + 4, body.y + s // 3), 2)

    @staticmethod
    def _draw_clock(surface, rect):
        cx, cy = rect.center
        r = int(min(rect.w, rect.h) * 0.32)
        pygame.draw.circle(surface, (240, 230, 210), (cx, cy), r)
        pygame.draw.circle(surface, (40, 30, 20), (cx, cy), r, 2)
        pygame.draw.line(surface, (40, 30, 20), (cx, cy), (cx, cy - r + 4), 2)   # 12 o'clock
        pygame.draw.line(surface, (40, 30, 20), (cx, cy), (cx + r // 2, cy), 2)  # 3 o'clock-ish
        pygame.draw.circle(surface, (40, 30, 20), (cx, cy), 2)

    @staticmethod
    def _draw_candle(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.6)
        # Candle body
        body = pygame.Rect(cx - s // 6, cy - s // 4, s // 3, s // 2)
        pygame.draw.rect(surface, (240, 230, 200), body)
        pygame.draw.rect(surface, (40, 30, 20), body, 2)
        # Wick
        pygame.draw.line(surface, (40, 30, 20), (cx, cy - s // 4), (cx, cy - s // 4 - 4), 2)
        # Flame
        pygame.draw.polygon(surface, (240, 160, 60), [
            (cx, cy - s // 2 - 2), (cx - 4, cy - s // 4 - 2), (cx + 4, cy - s // 4 - 2),
        ])

    @staticmethod
    def _draw_book(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.6)
        body = pygame.Rect(cx - s // 2, cy - s // 3, s, 2 * s // 3)
        pygame.draw.rect(surface, (160, 60, 60), body, border_radius=2)
        pygame.draw.rect(surface, (40, 20, 20), body, 2, border_radius=2)
        # Pages: vertical lines on the right edge
        for dy in range(-s // 4, s // 4, 4):
            pygame.draw.line(surface, (240, 230, 200), (body.right - 4, cy + dy), (body.right - 1, cy + dy), 1)
        # Spine
        pygame.draw.line(surface, (240, 200, 110), (cx, body.y + 4), (cx, body.bottom - 4), 1)

    @staticmethod
    def _draw_vase(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.55)
        # Vase silhouette: trapezoid with bulge
        pts = [
            (cx - s // 4, cy - s // 2),
            (cx + s // 4, cy - s // 2),
            (cx + s // 3, cy),
            (cx + s // 4, cy + s // 2),
            (cx - s // 4, cy + s // 2),
            (cx - s // 3, cy),
        ]
        pygame.draw.polygon(surface, (120, 150, 170), pts)
        pygame.draw.polygon(surface, (40, 50, 60), pts, 2)
        # Floral hint
        pygame.draw.circle(surface, (220, 100, 100), (cx, cy - s // 2 - 4), 4)
        pygame.draw.circle(surface, (220, 200, 100), (cx - 6, cy - s // 2 - 2), 3)

    @staticmethod
    def _draw_key(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.6)
        # Bow
        pygame.draw.circle(surface, (220, 180, 80), (cx - s // 4, cy), s // 6)
        pygame.draw.circle(surface, (40, 30, 10), (cx - s // 4, cy), s // 6, 2)
        # Shaft
        pygame.draw.rect(surface, (220, 180, 80), (cx - s // 4 + s // 6, cy - 3, s // 2, 6))
        pygame.draw.rect(surface, (40, 30, 10), (cx - s // 4 + s // 6, cy - 3, s // 2, 6), 1)
        # Teeth
        pygame.draw.rect(surface, (220, 180, 80), (cx + s // 4 - 4, cy + 3, 6, 4))

    @staticmethod
    def _draw_letter(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.6)
        body = pygame.Rect(cx - s // 2, cy - s // 3, s, 2 * s // 3)
        pygame.draw.rect(surface, (240, 230, 200), body)
        pygame.draw.rect(surface, (40, 30, 20), body, 2)
        # Flap diagonals
        pygame.draw.line(surface, (40, 30, 20), body.topleft, body.center, 2)
        pygame.draw.line(surface, (40, 30, 20), body.topright, body.center, 2)

    @staticmethod
    def _draw_umbrella(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.6)
        # Canopy: half-circle
        canopy = pygame.Rect(cx - s // 2, cy - s // 2, s, s)
        pygame.draw.arc(surface, (60, 100, 140), canopy, 0, 3.14159, max(2, s // 4))
        pygame.draw.line(surface, (40, 50, 60), (cx, cy), (cx, cy + s // 2), 2)
        # Handle hook
        pygame.draw.arc(surface, (40, 50, 60), (cx - 8, cy + s // 2 - 4, 12, 8), 3.14159, 6.28318, 2)

    @staticmethod
    def _draw_bottle(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.55)
        # Neck
        pygame.draw.rect(surface, (90, 130, 90), (cx - 3, cy - s // 2, 6, s // 4))
        # Body
        body = pygame.Rect(cx - s // 3, cy - s // 4, 2 * s // 3, 3 * s // 4)
        pygame.draw.rect(surface, (90, 130, 90), body, border_radius=4)
        pygame.draw.rect(surface, (20, 40, 20), body, 2, border_radius=4)
        # Label
        pygame.draw.rect(surface, (240, 230, 200), (body.x + 4, cy + 4, body.w - 8, 8))

    @staticmethod
    def _draw_gun(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.55)
        # Barrel
        pygame.draw.rect(surface, (60, 60, 70), (cx - s // 2, cy - 4, s, 8))
        pygame.draw.rect(surface, (20, 20, 30), (cx - s // 2, cy - 4, s, 8), 1)
        # Grip
        pygame.draw.polygon(surface, (90, 60, 40), [
            (cx, cy + 4), (cx + s // 5, cy + 4),
            (cx + s // 4, cy + s // 3), (cx - s // 12, cy + s // 3),
        ])

    @staticmethod
    def _draw_rug(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.7)
        body = pygame.Rect(cx - s // 2, cy - s // 4, s, s // 2)
        pygame.draw.rect(surface, (140, 60, 60), body, border_radius=3)
        pygame.draw.rect(surface, (40, 20, 20), body, 2, border_radius=3)
        # Pattern stripes
        for y in (cy - s // 8, cy, cy + s // 8):
            pygame.draw.line(surface, (240, 200, 110), (body.x + 4, y), (body.right - 4, y), 1)

    @staticmethod
    def _draw_painting(surface, rect):
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.65)
        body = pygame.Rect(cx - s // 2, cy - s // 2, s, s)
        pygame.draw.rect(surface, (240, 200, 110), body)
        pygame.draw.rect(surface, (60, 40, 20), body, 3)
        # Inner image hint: an arc + a triangle (mountains/sun)
        pygame.draw.polygon(surface, (90, 130, 90), [
            (body.x + 4, body.bottom - 4),
            (cx - 4, body.y + 8),
            (cx + s // 6, body.bottom - 4),
        ])
        pygame.draw.circle(surface, (240, 160, 60), (body.right - 8, body.y + 8), 4)

    # --- additional pictographs ---

    @staticmethod
    def _draw_glass(surface, rect):
        """Drinking glass / tumbler with lipstick smudge."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.55)
        # Tapered tumbler shape (slightly narrower at base)
        top_w = s
        bot_w = int(s * 0.78)
        top = cy - s // 2
        bot = cy + s // 2
        pts = [
            (cx - top_w // 2, top),
            (cx + top_w // 2, top),
            (cx + bot_w // 2, bot),
            (cx - bot_w // 2, bot),
        ]
        # Glass body — translucent pale blue
        pygame.draw.polygon(surface, (190, 215, 230), pts)
        pygame.draw.polygon(surface, (40, 60, 70), pts, 2)
        # Liquid line near the top
        pygame.draw.line(surface, (60, 110, 150),
                         (cx - top_w // 2 + 3, top + 6),
                         (cx + top_w // 2 - 3, top + 6), 2)
        # Lipstick smudge on the rim
        pygame.draw.arc(surface, (200, 50, 80),
                        (cx + top_w // 6, top - 2, top_w // 4, 6),
                        0, 3.14159, 3)

    @staticmethod
    def _draw_basket(surface, rect):
        """Wicker basket — woven cross-hatch pattern with handle arc."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.65)
        body = pygame.Rect(cx - s // 2, cy - s // 6, s, 2 * s // 3)
        # Body
        pygame.draw.rect(surface, (170, 110, 60), body, border_radius=4)
        pygame.draw.rect(surface, (60, 30, 15), body, 2, border_radius=4)
        # Wicker weave: diagonal lines both ways
        for dx in range(-s // 2 + 4, s // 2, 5):
            pygame.draw.line(surface, (110, 70, 40),
                             (cx + dx, body.y + 2),
                             (cx + dx + 6, body.bottom - 2), 1)
            pygame.draw.line(surface, (130, 85, 50),
                             (cx + dx, body.bottom - 2),
                             (cx + dx + 6, body.y + 2), 1)
        # Handle arc above
        pygame.draw.arc(surface, (60, 30, 15),
                        (cx - s // 2 + 4, body.y - s // 3, s - 8, s // 2),
                        0, 3.14159, 3)

    @staticmethod
    def _draw_chess(surface, rect):
        """Chess board with a couple of piece silhouettes."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.7)
        # 4x4 checker pattern (close enough at icon size)
        cell = s // 4
        x0 = cx - s // 2
        y0 = cy - s // 2
        for r in range(4):
            for c in range(4):
                color = (240, 220, 180) if (r + c) % 2 == 0 else (90, 60, 30)
                pygame.draw.rect(surface, color,
                                 (x0 + c * cell, y0 + r * cell, cell, cell))
        pygame.draw.rect(surface, (40, 25, 15), (x0, y0, s, s), 2)
        # Two simple pawn silhouettes (one light, one dark)
        for (px, py, pcolor) in [
            (x0 + cell // 2, y0 + cell // 2, (255, 255, 255)),
            (x0 + 3 * cell + cell // 2, y0 + 3 * cell + cell // 2, (20, 20, 20)),
        ]:
            pygame.draw.circle(surface, pcolor, (px, py), max(2, cell // 3))
            pygame.draw.circle(surface, (40, 25, 15), (px, py), max(2, cell // 3), 1)

    @staticmethod
    def _draw_radiator(surface, rect):
        """Old cast-iron radiator: stack of vertical fins."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.7)
        body = pygame.Rect(cx - s // 2, cy - s // 2, s, s)
        # Backing plate
        pygame.draw.rect(surface, (140, 140, 145), body, border_radius=2)
        pygame.draw.rect(surface, (40, 40, 50), body, 2, border_radius=2)
        # Vertical fins
        n_fins = 6
        gap = s // (n_fins + 1)
        for i in range(1, n_fins + 1):
            fx = body.x + i * gap
            pygame.draw.line(surface, (60, 60, 70), (fx, body.y + 4), (fx, body.bottom - 4), 2)

    @staticmethod
    def _draw_fireplace(surface, rect):
        """Fireplace with hearth + flame."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.7)
        # Mantel (wider than hearth)
        mantel = pygame.Rect(cx - s // 2, cy - s // 2, s, s // 6)
        pygame.draw.rect(surface, (90, 70, 50), mantel)
        pygame.draw.rect(surface, (30, 20, 10), mantel, 2)
        # Hearth opening
        hearth = pygame.Rect(cx - s // 3, cy - s // 3, 2 * s // 3, 2 * s // 3)
        pygame.draw.rect(surface, (30, 20, 15), hearth)
        pygame.draw.rect(surface, (60, 40, 25), hearth, 2)
        # Flame
        pygame.draw.polygon(surface, (240, 130, 50), [
            (cx, cy - s // 6),
            (cx - s // 8, cy + s // 6),
            (cx + s // 8, cy + s // 6),
        ])
        pygame.draw.polygon(surface, (240, 200, 80), [
            (cx, cy + s // 12),
            (cx - s // 16, cy + s // 6),
            (cx + s // 16, cy + s // 6),
        ])

    @staticmethod
    def _draw_boots(surface, rect):
        """A pair of boots seen from the side."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.6)
        for offset in (-s // 4, s // 4):
            # Boot body (L-shape)
            shaft = pygame.Rect(cx + offset - s // 8, cy - s // 3, s // 4, s // 2)
            foot = pygame.Rect(cx + offset - s // 8, cy + s // 8, s // 3, s // 6)
            pygame.draw.rect(surface, (80, 50, 30), shaft, border_radius=2)
            pygame.draw.rect(surface, (80, 50, 30), foot, border_radius=2)
            pygame.draw.rect(surface, (20, 10, 5), shaft, 1, border_radius=2)
            pygame.draw.rect(surface, (20, 10, 5), foot, 1, border_radius=2)

    @staticmethod
    def _draw_glove(surface, rect):
        """A glove silhouette."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.55)
        # Palm
        palm = pygame.Rect(cx - s // 3, cy - s // 6, 2 * s // 3, s // 2)
        pygame.draw.rect(surface, (180, 220, 220), palm, border_radius=4)
        pygame.draw.rect(surface, (40, 50, 60), palm, 2, border_radius=4)
        # Fingers (4 small rectangles up top)
        finger_w = (palm.w - 8) // 4
        for i in range(4):
            f = pygame.Rect(palm.x + 2 + i * (finger_w + 2),
                            palm.y - s // 3, finger_w, s // 3)
            pygame.draw.rect(surface, (180, 220, 220), f, border_radius=2)
            pygame.draw.rect(surface, (40, 50, 60), f, 1, border_radius=2)
        # Thumb (sticks out left)
        thumb = pygame.Rect(palm.x - s // 8, cy, s // 4, s // 4)
        pygame.draw.rect(surface, (180, 220, 220), thumb, border_radius=4)
        pygame.draw.rect(surface, (40, 50, 60), thumb, 1, border_radius=4)

    @staticmethod
    def _draw_safe(surface, rect):
        """Combination safe with a dial."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.65)
        body = pygame.Rect(cx - s // 2, cy - s // 2, s, s)
        pygame.draw.rect(surface, (70, 70, 80), body, border_radius=2)
        pygame.draw.rect(surface, (30, 30, 35), body, 3, border_radius=2)
        # Dial
        pygame.draw.circle(surface, (40, 40, 45), (cx + s // 6, cy), s // 6)
        pygame.draw.circle(surface, (200, 200, 210), (cx + s // 6, cy), s // 6, 2)
        # Dial pointer
        pygame.draw.line(surface, (220, 200, 80),
                         (cx + s // 6, cy), (cx + s // 6, cy - s // 6 + 3), 2)
        # Hinge
        pygame.draw.rect(surface, (200, 200, 210),
                         (body.x + 3, body.y + s // 5, 4, 4))
        pygame.draw.rect(surface, (200, 200, 210),
                         (body.x + 3, body.bottom - s // 5 - 4, 4, 4))

    @staticmethod
    def _draw_table(surface, rect):
        """Side table / writing desk — flat top with two legs."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.6)
        # Tabletop
        top = pygame.Rect(cx - s // 2, cy - s // 8, s, s // 6)
        pygame.draw.rect(surface, (140, 90, 50), top, border_radius=2)
        pygame.draw.rect(surface, (40, 25, 10), top, 2, border_radius=2)
        # Legs
        for lx in (cx - s // 2 + 3, cx + s // 2 - 7):
            leg = pygame.Rect(lx, top.bottom, 4, s // 2)
            pygame.draw.rect(surface, (90, 60, 30), leg)
            pygame.draw.rect(surface, (40, 25, 10), leg, 1)
        # A subtle drawer line on the front of the top (suggests "desk")
        pygame.draw.line(surface, (40, 25, 10),
                         (top.x + 4, top.y + top.h // 2),
                         (top.right - 4, top.y + top.h // 2), 1)

    @staticmethod
    def _draw_spectacles(surface, rect):
        """Pair of round spectacles connected by a bridge."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.55)
        r = s // 4
        # Two lenses
        pygame.draw.circle(surface, (200, 220, 230), (cx - r, cy), r)
        pygame.draw.circle(surface, (200, 220, 230), (cx + r, cy), r)
        pygame.draw.circle(surface, (40, 30, 25), (cx - r, cy), r, 2)
        pygame.draw.circle(surface, (40, 30, 25), (cx + r, cy), r, 2)
        # Bridge between them
        pygame.draw.line(surface, (40, 30, 25), (cx - r + 2, cy), (cx + r - 2, cy), 2)
        # Crack across the right lens to suggest "broken"
        pygame.draw.line(surface, (40, 30, 25),
                         (cx + r // 2, cy - r // 2), (cx + 2 * r - 4, cy + r // 2), 1)

    @staticmethod
    def _draw_coat_rack(surface, rect):
        """Vertical post with hook arms — coat rack / hat stand."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.65)
        # Post
        pygame.draw.line(surface, (90, 60, 30), (cx, cy - s // 2), (cx, cy + s // 2), 4)
        # Hooks (top crossbar + two angled hooks)
        pygame.draw.line(surface, (90, 60, 30),
                         (cx - s // 4, cy - s // 2 + 2),
                         (cx + s // 4, cy - s // 2 + 2), 3)
        for hx in (cx - s // 4, cx + s // 4):
            pygame.draw.line(surface, (90, 60, 30),
                             (hx, cy - s // 2 + 2), (hx, cy - s // 2 + 8), 3)
        # Base (small triangle)
        pygame.draw.polygon(surface, (90, 60, 30), [
            (cx - s // 5, cy + s // 2),
            (cx + s // 5, cy + s // 2),
            (cx, cy + s // 2 - s // 6),
        ])

    @staticmethod
    def _draw_trunk(surface, rect):
        """Storage trunk / chest with a curved lid."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.65)
        # Body
        body = pygame.Rect(cx - s // 2, cy - s // 6, s, s // 2)
        pygame.draw.rect(surface, (110, 70, 35), body)
        pygame.draw.rect(surface, (40, 25, 10), body, 2)
        # Curved lid (semi-ellipse)
        lid = pygame.Rect(cx - s // 2, cy - s // 2, s, s // 3)
        pygame.draw.ellipse(surface, (130, 90, 50), lid)
        pygame.draw.arc(surface, (40, 25, 10), lid, 0, 3.14159, 2)
        # Strap
        pygame.draw.rect(surface, (60, 40, 20), (cx - 3, lid.y, 6, body.bottom - lid.y))
        # Lock plate
        pygame.draw.rect(surface, (220, 200, 80), (cx - 5, body.y - 2, 10, 6))
        pygame.draw.rect(surface, (40, 25, 10), (cx - 5, body.y - 2, 10, 6), 1)

    @staticmethod
    def _draw_rope(surface, rect):
        """Coiled rope / silk scarf — wavy nested loops."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.6)
        # Three nested ovals to suggest a coil
        for i, scale in enumerate((1.0, 0.7, 0.4)):
            w = int(s * scale)
            h = int(s * scale * 0.55)
            ring = pygame.Rect(cx - w // 2, cy - h // 2, w, h)
            color = (200, 160, 100) if i % 2 == 0 else (160, 120, 70)
            pygame.draw.ellipse(surface, color, ring, 3)
        # Tail strand falling off
        pygame.draw.line(surface, (200, 160, 100),
                         (cx + s // 2, cy + 4),
                         (cx + s // 2 + 6, cy + s // 4), 3)

    @staticmethod
    def _draw_figurine(surface, rect):
        """Decorative statuette / marble bookend — tall pedestal with bust."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.55)
        # Pedestal base
        base = pygame.Rect(cx - s // 3, cy + s // 6, 2 * s // 3, s // 6)
        pygame.draw.rect(surface, (220, 220, 215), base)
        pygame.draw.rect(surface, (60, 60, 70), base, 2)
        # Column
        col = pygame.Rect(cx - s // 5, cy - s // 6, 2 * s // 5, s // 3)
        pygame.draw.rect(surface, (210, 210, 205), col)
        pygame.draw.rect(surface, (60, 60, 70), col, 2)
        # Bust (head + shoulders silhouette)
        # Shoulders
        sh = pygame.Rect(cx - s // 4, cy - s // 4, s // 2, s // 6)
        pygame.draw.ellipse(surface, (200, 200, 200), sh)
        pygame.draw.ellipse(surface, (60, 60, 70), sh, 2)
        # Head
        pygame.draw.circle(surface, (200, 200, 200), (cx, cy - s // 3), s // 8)
        pygame.draw.circle(surface, (60, 60, 70), (cx, cy - s // 3), s // 8, 2)

    @staticmethod
    def _draw_cigar(surface, rect):
        """Cigar stub — brown cylinder with red ember and curling smoke."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.55)
        # Cigar body (horizontal cylinder)
        body = pygame.Rect(cx - s // 2, cy + s // 8, s, s // 5)
        pygame.draw.rect(surface, (110, 70, 35), body, border_radius=2)
        pygame.draw.rect(surface, (40, 25, 10), body, 1, border_radius=2)
        # Ash band near right end
        pygame.draw.rect(surface, (200, 200, 200),
                         (body.right - s // 4, body.y + 1, s // 6, body.h - 2))
        # Lit ember
        pygame.draw.circle(surface, (240, 100, 40),
                           (body.right - 2, body.y + body.h // 2), 3)
        # Smoke wisps curling up
        from math import sin, radians
        for i, dy in enumerate([-s // 4, -s // 2, -3 * s // 4]):
            wave = int(sin(radians(i * 60)) * 4)
            pygame.draw.circle(surface, (200, 200, 200, 200),
                               (body.right - 2 + wave, body.y + dy), 3)

    @staticmethod
    def _draw_tray(surface, rect):
        """Serving tray — flat oval with handle notches at the ends."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.7)
        # Tray oval
        tray = pygame.Rect(cx - s // 2, cy - s // 4, s, s // 2)
        pygame.draw.ellipse(surface, (200, 170, 110), tray)
        pygame.draw.ellipse(surface, (40, 25, 10), tray, 2)
        # Handle indents (left and right)
        pygame.draw.arc(surface, (40, 25, 10),
                        (tray.x - 4, cy - 6, 12, 12), 1.57, 4.71, 2)
        pygame.draw.arc(surface, (40, 25, 10),
                        (tray.right - 8, cy - 6, 12, 12), 4.71, 7.85, 2)
        # Two small "items" on the tray (suggests tableware)
        pygame.draw.circle(surface, (220, 220, 215), (cx - s // 5, cy), 4)
        pygame.draw.circle(surface, (40, 25, 10), (cx - s // 5, cy), 4, 1)
        pygame.draw.rect(surface, (220, 200, 80),
                         (cx + s // 8, cy - 3, 6, 6))
        pygame.draw.rect(surface, (40, 25, 10),
                         (cx + s // 8, cy - 3, 6, 6), 1)

    @staticmethod
    def _draw_window(surface, rect):
        """Window frame with cross-mullions."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.65)
        body = pygame.Rect(cx - s // 2, cy - s // 2, s, s)
        # Frame
        pygame.draw.rect(surface, (180, 200, 220), body)
        pygame.draw.rect(surface, (60, 50, 40), body, 3)
        # Cross mullions
        pygame.draw.line(surface, (60, 50, 40), (cx, body.y), (cx, body.bottom), 2)
        pygame.draw.line(surface, (60, 50, 40), (body.x, cy), (body.right, cy), 2)
        # Subtle highlight on top-left pane
        pygame.draw.line(surface, (240, 250, 255),
                         (body.x + 4, body.y + 4),
                         (cx - 3, body.y + 4), 1)

    @staticmethod
    def _draw_flower(surface, rect):
        """Wilted bouquet — drooping flower silhouette."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.6)
        # Stem
        pygame.draw.line(surface, (90, 110, 60), (cx, cy + s // 3), (cx + s // 6, cy - s // 6), 3)
        # Petals (drooping)
        head_x = cx + s // 6
        head_y = cy - s // 6
        for ang_offset, color in [(0, (220, 130, 130)), (60, (220, 130, 130)),
                                   (120, (220, 130, 130)), (180, (220, 130, 130))]:
            from math import sin, cos, radians
            ang = radians(ang_offset + 30)
            px = head_x + int(s // 6 * cos(ang))
            py = head_y + int(s // 6 * sin(ang)) + 4   # droop
            pygame.draw.circle(surface, color, (px, py), max(3, s // 12))
        # Centre
        pygame.draw.circle(surface, (220, 200, 80), (head_x, head_y + 2), max(2, s // 14))

    # Library: keys to look for (lowercased) -> draw callback.
    # ORDER MATTERS — earlier entries win. Blade weapons MUST come before
    # ("letter") and ("fireplace") so e.g. "ornate letter opener" hits the
    # blade icon, not the envelope, and "iron fireplace poker" hits the
    # blade icon, not the fireplace.
    _ICON_LIBRARY = [
        # --- weapons (blade) — must be near the top ---
        (("cleaver", "knife", "dagger", "blade", "sword",
          "poker", "opener", "shears", "scissors"),     "_draw_weapon"),
        (("pistol", "revolver", "rifle", "shotgun",
          "firearm", "gun"),                            "_draw_gun"),
        # --- weapons (blunt / fabric) ---
        (("rope", "cord", "scarf"),                     "_draw_rope"),
        (("statuette", "statue", "figurine", "bust",
          "bookend"),                                   "_draw_figurine"),
        # --- room props with specific icons ---
        (("fireplace", "hearth"),                       "_draw_fireplace"),
        (("radiator",),                                 "_draw_radiator"),
        (("chess",),                                    "_draw_chess"),
        (("basket",),                                   "_draw_basket"),
        (("glass", "tumbler", "goblet"),                "_draw_glass"),
        (("safe", "vault"),                             "_draw_safe"),
        (("boots", "shoes"),                            "_draw_boots"),
        (("glove",),                                    "_draw_glove"),
        (("bouquet", "flowers"),                        "_draw_flower"),
        (("mirror",),                                   "_draw_mirror"),
        (("clock", "watch", "timepiece"),               "_draw_clock"),
        (("candle", "candelabra", "candlestick",
          "lamp", "lantern"),                           "_draw_candle"),
        (("book", "diary", "journal", "ledger",
          "tome", "shelf"),                             "_draw_book"),
        (("spectacles", "eyeglasses", "monocle"),       "_draw_spectacles"),
        (("vase", "pot"),                               "_draw_vase"),
        (("key",),                                      "_draw_key"),
        (("letter", "note", "envelope", "telegram",
          "memo", "receipt", "ticket"),                 "_draw_letter"),
        (("umbrella", "parasol"),                       "_draw_umbrella"),
        (("bottle", "decanter", "flask", "vial"),       "_draw_bottle"),
        (("rug", "carpet", "tapestry", "curtain"),      "_draw_rug"),
        (("painting", "portrait", "canvas", "frame"),   "_draw_painting"),
        (("desk", "table"),                             "_draw_table"),
        (("trunk", "chest"),                            "_draw_trunk"),
        (("window", "ledge"),                           "_draw_window"),
        (("rack", "stand"),                             "_draw_coat_rack"),
        (("cigar", "cigarette", "stub"),                "_draw_cigar"),
        (("tray", "platter"),                           "_draw_tray"),
    ]

    # ---- status badges -----------------------------------------------

    @staticmethod
    def _badge_weapon(surface, rect):
        # Red diamond in top-right corner
        x, y = rect.right - 10, rect.top + 6
        pts = [(x, y - 5), (x + 5, y), (x, y + 5), (x - 5, y)]
        pygame.draw.polygon(surface, (220, 80, 80), pts)
        pygame.draw.polygon(surface, (40, 20, 20), pts, 1)

    @staticmethod
    def _badge_clue(surface, rect):
        # Yellow "!" in top-right corner (interactable, undiscovered)
        x, y = rect.right - 8, rect.top + 6
        pygame.draw.circle(surface, (240, 200, 80), (x, y), 7)
        pygame.draw.circle(surface, (60, 40, 10), (x, y), 7, 1)
        pygame.draw.line(surface, (60, 40, 10), (x, y - 3), (x, y + 1), 2)
        pygame.draw.circle(surface, (60, 40, 10), (x, y + 3), 1)

    @staticmethod
    def _badge_examined(surface, rect):
        # Green check in top-right (already discovered)
        x, y = rect.right - 8, rect.top + 6
        pygame.draw.circle(surface, (110, 200, 130), (x, y), 7)
        pygame.draw.circle(surface, (20, 60, 30), (x, y), 7, 1)
        pygame.draw.line(surface, (20, 60, 30), (x - 3, y), (x - 1, y + 2), 2)
        pygame.draw.line(surface, (20, 60, 30), (x - 1, y + 2), (x + 3, y - 2), 2)

    # ---- pictographs ------------------------------------------------

    @staticmethod
    def _draw_weapon(surface: pygame.Surface, rect: pygame.Rect) -> None:
        cx, cy = rect.center
        size = int(min(rect.w, rect.h) * 0.7)
        # Dagger silhouette: blade triangle on top of crossguard + handle
        blade_top = (cx, cy - size // 2)
        blade_mid_l = (cx - size // 8, cy + size // 8)
        blade_mid_r = (cx + size // 8, cy + size // 8)
        pygame.draw.polygon(surface, (210, 210, 220), [blade_top, blade_mid_l, blade_mid_r])
        pygame.draw.polygon(surface, (40, 20, 20), [blade_top, blade_mid_l, blade_mid_r], 2)
        # Crossguard
        pygame.draw.rect(
            surface, WEAPON_COLOR,
            (cx - size // 3, cy + size // 8, 2 * size // 3, size // 12),
        )
        # Handle
        pygame.draw.rect(
            surface, (90, 50, 30),
            (cx - size // 12, cy + size // 8 + size // 12, size // 6, size // 4),
        )
        pygame.draw.rect(
            surface, (40, 20, 20),
            (cx - size // 12, cy + size // 8 + size // 12, size // 6, size // 4), 1,
        )

    @staticmethod
    def _draw_magnifier(surface: pygame.Surface, rect: pygame.Rect) -> None:
        cx, cy = rect.center
        radius = int(min(rect.w, rect.h) * 0.27)
        # Lens
        pygame.draw.circle(surface, (200, 230, 255), (cx - 2, cy - 2), radius)
        pygame.draw.circle(surface, EVIDENCE_COLOR, (cx - 2, cy - 2), radius, 3)
        # Handle
        end = (cx + radius, cy + radius)
        pygame.draw.line(surface, (90, 50, 30), (cx + 2, cy + 2), end, 5)

    @staticmethod
    def _draw_evidence_box(surface: pygame.Surface, rect: pygame.Rect) -> None:
        cx, cy = rect.center
        size = int(min(rect.w, rect.h) * 0.55)
        half = size // 2
        # Cube body
        pygame.draw.rect(
            surface, OBJECT_COLOR,
            (cx - half, cy - half, size, size), border_radius=3,
        )
        pygame.draw.rect(
            surface, (40, 30, 10),
            (cx - half, cy - half, size, size), 2, border_radius=3,
        )
        # Ribbon / band: two crossing strips
        strip = max(2, size // 8)
        pygame.draw.rect(surface, (160, 80, 50), (cx - strip // 2, cy - half, strip, size))
        pygame.draw.rect(surface, (160, 80, 50), (cx - half, cy - strip // 2, size, strip))

    @staticmethod
    def _draw_chair(surface: pygame.Surface, rect: pygame.Rect) -> None:
        cx, cy = rect.center
        size = int(min(rect.w, rect.h) * 0.55)
        half = size // 2
        # Seat
        pygame.draw.rect(
            surface, OBJECT_COLOR,
            (cx - half, cy - half // 4, size, half),
            border_radius=2,
        )
        pygame.draw.rect(
            surface, (40, 30, 10),
            (cx - half, cy - half // 4, size, half), 2, border_radius=2,
        )
        # Backrest
        back = pygame.Rect(cx - half, cy - half - 2, size, half + 2)
        pygame.draw.rect(surface, OBJECT_COLOR, back, border_radius=2)
        pygame.draw.rect(surface, (40, 30, 10), back, 2, border_radius=2)
        # Inner backrest line for definition
        pygame.draw.line(
            surface, (40, 30, 10),
            (cx, cy - half), (cx, cy - 2), 2,
        )

    # ---- humanoid characters ---------------------------------------

    def draw_character(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        char: Character,
    ) -> None:
        if not char.is_alive:
            self._draw_victim(surface, rect)
            return
        # Hash the character id for deterministic colour variety
        h = int(hashlib.sha1(char.id.encode()).hexdigest(), 16)
        skin = [(255, 220, 180), (240, 200, 160), (220, 180, 130), (180, 130, 95), (140, 90, 60)][h % 5]
        shirt = [(70, 130, 200), (180, 70, 80), (60, 140, 110), (180, 140, 60), (140, 90, 170)][(h // 5) % 5]
        hair = [(40, 30, 20), (140, 90, 50), (220, 200, 110), (200, 100, 60), (120, 120, 130)][(h // 25) % 5]
        self._draw_humanoid(surface, rect, skin=skin, shirt=shirt, hair=hair)

    @staticmethod
    def _draw_humanoid(
        surface: pygame.Surface,
        rect: pygame.Rect,
        skin: tuple[int, int, int],
        shirt: tuple[int, int, int],
        hair: tuple[int, int, int],
        hat_color: tuple[int, int, int] | None = None,
    ) -> None:
        """Top-down humanoid: head + shoulders + torso outline."""
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.85)
        # Body / shoulders (rounded rect, slightly wider than head)
        body_w = int(s * 0.55)
        body_h = int(s * 0.45)
        body = pygame.Rect(cx - body_w // 2, cy, body_w, body_h)
        pygame.draw.rect(surface, shirt, body, border_radius=6)
        pygame.draw.rect(surface, (20, 20, 30), body, 2, border_radius=6)
        # Arms (small ovals on sides, slightly behind shoulders)
        arm_r = int(s * 0.13)
        pygame.draw.circle(surface, shirt, (body.left, cy + arm_r), arm_r)
        pygame.draw.circle(surface, shirt, (body.right, cy + arm_r), arm_r)
        pygame.draw.circle(surface, (20, 20, 30), (body.left, cy + arm_r), arm_r, 2)
        pygame.draw.circle(surface, (20, 20, 30), (body.right, cy + arm_r), arm_r, 2)
        # Head (circle at top)
        head_r = int(s * 0.20)
        head_cx, head_cy = cx, cy - head_r // 2
        pygame.draw.circle(surface, skin, (head_cx, head_cy), head_r)
        pygame.draw.circle(surface, (20, 20, 30), (head_cx, head_cy), head_r, 2)
        # Hair: a cap on top half
        hair_rect = pygame.Rect(head_cx - head_r, head_cy - head_r, 2 * head_r, head_r)
        pygame.draw.ellipse(surface, hair, hair_rect)
        pygame.draw.ellipse(surface, (20, 20, 30), hair_rect, 1)
        # Optional hat overlay (used by player avatar)
        if hat_color is not None:
            brim = pygame.Rect(head_cx - head_r - 2, head_cy - head_r // 2, 2 * head_r + 4, 4)
            pygame.draw.rect(surface, hat_color, brim)
            crown = pygame.Rect(head_cx - head_r * 3 // 4, head_cy - head_r - 4, head_r * 3 // 2, head_r)
            pygame.draw.rect(surface, hat_color, crown, border_radius=2)
            pygame.draw.rect(surface, (20, 20, 30), crown, 1, border_radius=2)
        # Tiny eye dots for friendliness
        eye = max(1, head_r // 5)
        pygame.draw.circle(surface, (20, 20, 30), (head_cx - head_r // 3, head_cy + head_r // 6), eye)
        pygame.draw.circle(surface, (20, 20, 30), (head_cx + head_r // 3, head_cy + head_r // 6), eye)

    @staticmethod
    def _draw_victim(surface: pygame.Surface, rect: pygame.Rect) -> None:
        cx, cy = rect.center
        s = int(min(rect.w, rect.h) * 0.8)
        # Body lying down (horizontal ellipse) with separate head circle
        body = pygame.Rect(cx - s // 2, cy - s // 6, s, s // 3)
        pygame.draw.ellipse(surface, VICTIM_COLOR, body)
        pygame.draw.ellipse(surface, (20, 20, 30), body, 2)
        # Head off to one side
        head_r = s // 6
        pygame.draw.circle(surface, (180, 160, 150), (cx - s // 2 + head_r // 2, cy), head_r)
        pygame.draw.circle(surface, (20, 20, 30), (cx - s // 2 + head_r // 2, cy), head_r, 2)
        # X-eyes to make "deceased" unambiguous
        pygame.draw.line(surface, (120, 30, 30), (cx - s // 2 + 2, cy - 3), (cx - s // 2 + 8, cy + 3), 2)
        pygame.draw.line(surface, (120, 30, 30), (cx - s // 2 + 2, cy + 3), (cx - s // 2 + 8, cy - 3), 2)

    def draw_player(self, surface: pygame.Surface, cx: int, cy: int, radius: int) -> None:
        # Reuse the humanoid renderer with detective styling
        rect = pygame.Rect(cx - radius * 2, cy - radius * 2, radius * 4, radius * 4)
        self._draw_humanoid(
            surface, rect,
            skin=(240, 210, 175),
            shirt=(60, 90, 140),     # detective coat
            hair=(40, 25, 15),
            hat_color=(30, 30, 40),  # dark fedora
        )


# ---------------------------------------------------------------------------
# Emoji sprite loader (default — uses Noto Color Emoji for real-world icons)
# ---------------------------------------------------------------------------

# Cross-platform paths for colour-emoji fonts. We probe each in order; the
# first that exists AND actually renders a colour glyph wins. On macOS,
# Apple Color Emoji uses the sbix format which older SDL_ttf builds can't
# rasterise — the at-startup self-test below catches that case and falls
# back to ProceduralSprites.
_EMOJI_FONT_CANDIDATES = [
    # Linux (Noto Color Emoji)
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/google-noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto-color-emoji/NotoColorEmoji.ttf",
    # macOS (Apple Color Emoji)
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/Library/Fonts/Apple Color Emoji.ttc",
    str(Path.home() / "Library/Fonts/Apple Color Emoji.ttc"),
    # Windows (Segoe UI Emoji)
    "C:\\Windows\\Fonts\\seguiemj.ttf",
    # User-local Noto installs
    str(Path.home() / ".fonts/NotoColorEmoji.ttf"),
    str(Path.home() / ".local/share/fonts/NotoColorEmoji.ttf"),
]

# Pool of "person" emoji to give NPCs visual variety. Single-codepoint glyphs
# only — Noto Color Emoji renders these reliably.
_NPC_EMOJI_POOL = ["👨", "👩", "🧓", "👴", "👵", "🧔", "👨‍🦰", "👩‍🦰", "👨‍🦱", "👩‍🦱"]


def _find_emoji_font() -> str | None:
    for p in _EMOJI_FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


class EmojiSprites(SpriteLoader):
    """Real Unicode emoji rendered through Noto Color Emoji.

    Tiles (floor/wall/door) stay coloured for legibility. Entities (player,
    NPCs, victim, weapon, evidence, props) are rendered as emoji glyphs scaled
    to the tile size. If the emoji font isn't found, the loader transparently
    falls back to `ProceduralSprites`.
    """

    # Native render size of bitmap colour emoji fonts. We render once at this
    # size per glyph and cache; downscaling to tile size is one smoothscale.
    _NATIVE_SIZE = 109

    # Glyph map per category. NPC variants are picked per character below.
    _GLYPH_PLAYER = "🕵️"
    _GLYPH_VICTIM = "💀"
    _GLYPH_WEAPON = "⚔️"
    _GLYPH_EVIDENCE_DISCOVERED = "🔍"
    _GLYPH_EVIDENCE_HIDDEN = "📦"
    _GLYPH_OBJECT_PROP = "🪑"
    _GLYPH_DOOR = "🚪"

    def __init__(self) -> None:
        self._procedural = ProceduralSprites()
        self._font_path = _find_emoji_font()
        self._font: pygame.font.Font | None = None
        self._cache: dict[tuple[str, int], pygame.Surface | None] = {}
        if self._font_path:
            self._font = self._try_load_font(self._font_path)

    @classmethod
    def _try_load_font(cls, path: str) -> pygame.font.Font | None:
        """Load the font, then self-test that it actually renders a colour
        glyph. Some platforms can open Apple Color Emoji but produce all-black
        bitmaps — we treat that as a failure and fall back to procedural."""
        # Try several sizes; bitmap colour fonts are picky.
        for size in (cls._NATIVE_SIZE, 64, 48, 32):
            try:
                font = pygame.font.Font(path, size)
                surf = font.render("🧑", True, (255, 255, 255))
                if cls._surface_is_colourful(surf):
                    return font
            except Exception:
                continue
        return None

    @staticmethod
    def _surface_is_colourful(surf: pygame.Surface) -> bool:
        """A successful colour emoji render has multiple non-grey pixels.
        A failed render is either empty, a black box, or monochrome tofu."""
        if surf.get_width() < 4 or surf.get_height() < 4:
            return False
        # Sample a small grid of pixels; if any has notable colour saturation,
        # we have a real emoji glyph.
        sx = max(1, surf.get_width() // 12)
        sy = max(1, surf.get_height() // 12)
        for x in range(0, surf.get_width(), sx):
            for y in range(0, surf.get_height(), sy):
                px = surf.get_at((x, y))
                r, g, b = px[0], px[1], px[2]
                # Saturated pixel (max channel - min channel large enough)
                if max(r, g, b) - min(r, g, b) > 40:
                    return True
        return False

    @property
    def available(self) -> bool:
        return self._font is not None

    # --- glyph rendering -------------------------------------------------

    def _glyph(self, text: str, target_px: int) -> pygame.Surface | None:
        if self._font is None:
            return None
        key = (text, target_px)
        if key in self._cache:
            return self._cache[key]
        try:
            native = self._font.render(text, True, (255, 255, 255))
            # scale longest side to target_px, preserving aspect ratio
            w, h = native.get_size()
            scale = target_px / max(w, h)
            new = pygame.transform.smoothscale(
                native, (max(1, int(w * scale)), max(1, int(h * scale)))
            )
            self._cache[key] = new
            return new
        except Exception:
            self._cache[key] = None
            return None

    def _blit_glyph(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        glyph: str,
        scale: float = 0.85,
    ) -> bool:
        target = int(min(rect.w, rect.h) * scale)
        s = self._glyph(glyph, target)
        if s is None:
            return False
        r = s.get_rect(center=rect.center)
        surface.blit(s, r)
        return True

    @staticmethod
    def _npc_emoji_for(char: Character) -> str:
        h = int(hashlib.sha1(char.id.encode()).hexdigest(), 16)
        return _NPC_EMOJI_POOL[h % len(_NPC_EMOJI_POOL)]

    # --- SpriteLoader API -----------------------------------------------

    def draw_floor(self, surface, rect):
        # No emoji for floor — clean tile keeps the room readable
        self._procedural.draw_floor(surface, rect)

    def draw_wall(self, surface, rect):
        self._procedural.draw_wall(surface, rect)

    def draw_door(self, surface, rect):
        self._procedural.draw_door(surface, rect)
        if self._font is not None:
            self._blit_glyph(surface, rect, self._GLYPH_DOOR, scale=0.7)

    def draw_object(self, surface, rect, obj, discovered, evidence_state):
        # Always draw the prop — evidence state (HIDDEN / DESTROYED) only
        # affects what examining the object yields, not whether the prop
        # itself is visible.
        if obj.is_weapon:
            glyph = self._GLYPH_WEAPON
        elif obj.evidence_id and discovered:
            glyph = self._GLYPH_EVIDENCE_DISCOVERED
        elif obj.evidence_id:
            glyph = self._GLYPH_EVIDENCE_HIDDEN
        else:
            glyph = self._GLYPH_OBJECT_PROP
        if not self._blit_glyph(surface, rect, glyph, scale=0.85):
            self._procedural.draw_object(surface, rect, obj, discovered, evidence_state)

    def draw_character(self, surface, rect, char):
        if not char.is_alive:
            if not self._blit_glyph(surface, rect, self._GLYPH_VICTIM, scale=0.9):
                self._procedural.draw_character(surface, rect, char)
            return
        glyph = self._npc_emoji_for(char)
        if not self._blit_glyph(surface, rect, glyph, scale=0.9):
            self._procedural.draw_character(surface, rect, char)

    def draw_player(self, surface, cx, cy, radius):
        rect = pygame.Rect(cx - radius * 2, cy - radius * 2, radius * 4, radius * 4)
        if not self._blit_glyph(surface, rect, self._GLYPH_PLAYER, scale=0.9):
            self._procedural.draw_player(surface, cx, cy, radius)


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
        # Always draw the prop — evidence state only affects what examining
        # yields, not whether the prop itself is visible.
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
