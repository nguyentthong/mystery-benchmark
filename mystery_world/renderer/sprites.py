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
        if evidence_state in (EvidenceState.HIDDEN, EvidenceState.DESTROYED):
            return
        if obj.is_weapon:
            self._draw_weapon(surface, rect)
        elif obj.evidence_id and discovered:
            self._draw_magnifier(surface, rect)
        elif obj.evidence_id:
            self._draw_evidence_box(surface, rect)
        else:
            self._draw_chair(surface, rect)

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
        if evidence_state in (EvidenceState.HIDDEN, EvidenceState.DESTROYED):
            return
        # Choose the right glyph
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
