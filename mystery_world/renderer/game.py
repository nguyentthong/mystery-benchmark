"""Pygame top-down view of the mystery world.

Architecture
------------
The simulation engine (`MysteryEnvironment`) still owns ground truth and is
driven by discrete `AgentAction` calls. This module is purely a presentation +
input layer:

    real-time WASD / E / ESC  ─►  translated to AgentAction  ─►  env.step(...)
                                                                      │
                                              observation text  ◄─────┘
                                                      │
                                              shown in HUD overlay

Walking through a door tile triggers MOVE; pressing E next to an object/NPC
triggers EXAMINE_OBJECT or TALK_TO; ESC opens a menu (inventory/accuse/quit).

Sprites are placeholder coloured rectangles with text labels — the engine is
intentionally asset-free so the MVP runs without external downloads. Swapping
in Kenney.nl tile/character sprites later is a one-function change in
`_draw_*` below.
"""
from __future__ import annotations

import os
import sys
import textwrap
from dataclasses import dataclass
from typing import Callable

import pygame

from mystery_world.entities import Character, EvidenceState, Location, WorldObject
from mystery_world.narrator import render_initial_briefing, render_step_observation
from mystery_world.renderer.layout import (
    RoomLayout,
    Tile,
    build_room_layout,
    compute_door_pairings,
)
from mystery_world.renderer.sprites import EmojiSprites, ProceduralSprites, SpriteLoader
from mystery_world.world import AgentAction, MysteryEnvironment

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

TILE_PX = 44
TOPBAR_H = 44           # top status bar
STATUS_H = 76           # bottom 2-line status (last action result)
SIDEBAR_W = 460         # right notebook (Case File / Interviews / Evidence)
TAB_BAR_H = 32          # tab strip at top of sidebar
PLAYER_SPEED_PX = 220   # pixels per second
INTERACT_KEY = pygame.K_e
MENU_KEY = pygame.K_ESCAPE

# Tab IDs
TAB_CASE = 0
TAB_INTERVIEWS = 1
TAB_EVIDENCE = 2
TAB_NAMES = ["[1] Case File", "[2] Interviews", "[3] Evidence"]

BG_COLOR = (18, 16, 22)
PLAYER_COLOR = (255, 240, 220)
PLAYER_OUTLINE = (40, 30, 20)
HUD_BG = (10, 8, 14)
HUD_BORDER = (60, 50, 70)
HUD_TEXT = (235, 225, 215)
HUD_DIM = (160, 150, 140)
PROMPT_BG = (24, 22, 30)
PROMPT_BORDER = (180, 130, 60)


# ---------------------------------------------------------------------------
# Modal text input (used for talking to NPCs and entering accusations)
# ---------------------------------------------------------------------------

@dataclass
class Modal:
    title: str
    prompt: str
    on_submit: Callable[[str], None]
    text: str = ""
    mask: bool = False                # True → render input as asterisks (API key)
    allow_empty: bool = True          # True → ENTER on empty input still submits

    def handle_key(self, event: pygame.event.Event) -> bool:
        """Return True when the modal should close."""
        if event.type != pygame.KEYDOWN:
            return False
        if event.key == pygame.K_RETURN:
            value = self.text.strip()
            if not value and not self.allow_empty:
                return False
            self.on_submit(value)
            return True
        if event.key == pygame.K_ESCAPE:
            return True
        if event.key == pygame.K_BACKSPACE:
            self.text = self.text[:-1]
            return False
        if event.unicode and event.unicode.isprintable():
            self.text += event.unicode
        return False


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

class MysteryGame:
    """Real-time pygame front-end driving a MysteryEnvironment."""

    def __init__(
        self,
        env: MysteryEnvironment,
        window_title: str = "MysteryArena",
        sprites: SpriteLoader | None = None,
        headless: bool = False,
    ) -> None:
        """
        headless=True: render to an off-screen Surface (no display window).
        Use this when the game runs server-side and frames are streamed
        elsewhere (e.g. WebSocket → browser canvas). Each headless instance
        carries its own event queue and pressed-key state, so multiple
        instances can coexist in one process.
        """
        self.env = env
        self.state = env.state
        self.window_title = window_title
        self.headless = headless

        # Per-instance input state (used in headless mode; ignored otherwise)
        self._pending_events: list[pygame.event.Event] = []
        self._pressed_keys: set[int] = set()

        # Pygame must be initialised before EmojiSprites loads its font
        pygame.init()
        if not headless:
            pygame.display.set_caption(window_title)

        # Default sprite loader: emoji icons (real icons, not geometric shapes).
        # Falls back to procedural primitives if Noto Color Emoji isn't found.
        self.sprites: SpriteLoader = sprites or EmojiSprites()

        self._layouts: dict[str, RoomLayout] = {}
        # Pre-compute global door pairings so paired doors land on opposite
        # sides between connected rooms (walking through A's NORTH door
        # spawns you at B's SOUTH side, etc.).
        self._door_pairings = compute_door_pairings(env.state)
        self.current_layout: RoomLayout = self._layout_for(env.agent_location_id)

        # Player position in pixels (centre of the avatar)
        sx, sy = self.current_layout.default_spawn
        self.player_px: float = (sx + 0.5) * TILE_PX
        self.player_py: float = (sy + 0.5) * TILE_PX

        # HUD state
        self.briefing: str = render_initial_briefing(env)
        self.last_observation: str = "WASD = walk  |  E = interact  |  ESC = menu  |  1/2/3 = switch tab"
        self.toast: str = ""
        self.toast_until_ms: int = 0

        # Persistent UI state — tab + per-tab scroll offset
        self.active_tab: int = TAB_CASE
        self._scroll: dict[int, int] = {TAB_CASE: 0, TAB_INTERVIEWS: 0, TAB_EVIDENCE: 0}
        # Separate scroll for the post-game solution screen
        self._endscreen_scroll: int = 0

        # Track last-seen room per character for the Case File tab
        self.last_seen: dict[str, str] = {}
        self._update_last_seen()

        self.modal: Modal | None = None
        self.menu_open: bool = False
        self.running: bool = True

        # Window sizing — room view + slim top bar + slim bottom status + sidebar
        room_w_px = self.current_layout.width * TILE_PX
        room_h_px = self.current_layout.height * TILE_PX
        self.win_w = room_w_px + SIDEBAR_W
        self.win_h = TOPBAR_H + room_h_px + STATUS_H
        if headless:
            # Off-screen surface — no window, multiple instances OK in one process.
            self.screen = pygame.Surface((self.win_w, self.win_h))
        else:
            self.screen = pygame.display.set_mode((self.win_w, self.win_h))
        self.clock = pygame.time.Clock()
        self.font_sm = pygame.font.SysFont("dejavusansmono,monospace", 16)
        self.font_md = pygame.font.SysFont("dejavusansmono,monospace", 18)
        self.font_lg = pygame.font.SysFont("dejavusansmono,monospace", 22, bold=True)
        self.font_xl = pygame.font.SysFont("dejavusansmono,monospace", 28, bold=True)

    def _update_last_seen(self) -> None:
        """Record where each character is right now (called each step)."""
        for cid, char in self.state.characters.items():
            loc = self.state.locations.get(char.location_id)
            if loc:
                self.last_seen[cid] = loc.name

    # ------------------------------------------------------------------
    # API-key entry (used by web build so each player brings their own key)
    # ------------------------------------------------------------------

    @staticmethod
    def _browser_localstorage_get(key: str) -> str | None:
        """Read a value from window.localStorage when running under pygbag.
        Returns None on desktop or if anything fails."""
        try:
            import platform as plat_mod  # pygbag injects .window
            if hasattr(plat_mod, "window"):
                v = plat_mod.window.localStorage.getItem(key)
                return v if v else None
        except Exception:
            pass
        return None

    @staticmethod
    def _browser_localstorage_set(key: str, value: str) -> None:
        try:
            import platform as plat_mod
            if hasattr(plat_mod, "window"):
                plat_mod.window.localStorage.setItem(key, value)
        except Exception:
            pass

    def prompt_for_openai_key(self, default_model: str = "gpt-4o-mini") -> None:
        """Open a startup modal asking for the user's OpenAI API key.

        - If a key is already saved in browser localStorage, use it silently.
        - If the user submits a non-empty key, attach an NPCResponder.
        - If they submit empty (or hit ESC), fall back to deterministic NPCs.
        """
        # Already configured? Skip.
        if self.env._npc_responder is not None:
            return

        saved = self._browser_localstorage_get("OPENAI_API_KEY")
        if saved:
            self._attach_openai_responder(saved, default_model)
            return

        def _on_submit(value: str) -> None:
            if value:
                self._attach_openai_responder(value, default_model)
                self._browser_localstorage_set("OPENAI_API_KEY", value)
                self._show_toast("ChatGPT NPCs enabled")
            else:
                self._show_toast("Using fallback NPCs (no key entered)")

        self.modal = Modal(
            title="Enter your OpenAI API key",
            prompt="Paste your sk-... key for ChatGPT-powered NPC interviews. ENTER to save, ESC to skip.",
            on_submit=_on_submit,
            mask=True,
        )

    def _attach_openai_responder(self, api_key: str, model: str) -> None:
        from mystery_world.npc_responder import NPCResponder
        self.env.set_npc_responder(NPCResponder(
            base_url=None,           # default OpenAI endpoint
            model=model,
            api_key=api_key,
        ))

    # ------------------------------------------------------------------
    # Layout / room transitions
    # ------------------------------------------------------------------

    def _layout_for(self, location_id: str) -> RoomLayout:
        if location_id not in self._layouts:
            loc = self.state.locations[location_id]
            sides = self._door_pairings.get(location_id, {})
            self._layouts[location_id] = build_room_layout(loc, neighbor_sides=sides)
        layout = self._layouts[location_id]
        # Refresh dynamic content (NPCs may have moved between steps)
        self._refresh_dynamic_content(layout)
        return layout

    def _refresh_dynamic_content(self, layout: RoomLayout) -> None:
        """Re-place objects + characters from current world state.

        Keeps tile positions stable for objects that were already laid out;
        adds new ones (e.g. NPCs that walked into the room) at any free tile.
        """
        loc = self.state.locations[layout.location_id]
        used: set[tuple[int, int]] = set(layout.doors.keys())
        # Drop entries for things no longer in this room
        layout.objects = {
            oid: pos for oid, pos in layout.objects.items()
            if oid in loc.objects_here
        }
        layout.characters = {
            cid: pos for cid, pos in layout.characters.items()
            if cid in loc.characters_here
        }
        used.update(layout.objects.values())
        used.update(layout.characters.values())

        free = [
            (x, y)
            for x in range(2, layout.width - 2)
            for y in range(2, layout.height - 2)
            if layout.tiles[x][y] == Tile.FLOOR and (x, y) not in used
        ]
        # Place any new objects/characters that arrived. Keep them at least
        # 3 tiles apart from each other and from existing entities so labels
        # don't collide.
        import random
        rng = random.Random(f"refresh::{layout.location_id}::{self.state.current_step}")
        rng.shuffle(free)
        MIN_SPACING = 3
        placed = list(used)

        def _take_one() -> tuple[int, int] | None:
            if not free:
                return None
            for i, t in enumerate(free):
                if all(max(abs(t[0]-p[0]), abs(t[1]-p[1])) >= MIN_SPACING for p in placed):
                    free.pop(i); placed.append(t); return t
            # No tile satisfies spacing — pick the most-distant one anyway
            best = max(range(len(free)),
                       key=lambda i: min((max(abs(free[i][0]-p[0]), abs(free[i][1]-p[1]))
                                          for p in placed), default=99))
            t = free.pop(best); placed.append(t); return t

        for oid in loc.objects_here:
            if oid not in layout.objects:
                t = _take_one()
                if t is None:
                    break
                layout.objects[oid] = t
        for cid in loc.characters_here:
            if cid not in layout.characters:
                t = _take_one()
                if t is None:
                    break
                layout.characters[cid] = t

    def _enter_room(self, new_loc_id: str, from_loc_id: str) -> None:
        self.current_layout = self._layout_for(new_loc_id)
        spawn = self.current_layout.spawn_from.get(
            from_loc_id, self.current_layout.default_spawn
        )
        self.player_px = (spawn[0] + 0.5) * TILE_PX
        self.player_py = (spawn[1] + 0.5) * TILE_PX

    # ------------------------------------------------------------------
    # Action bridge — translates input events to env.step() calls
    # ------------------------------------------------------------------

    def _show_toast(self, text: str, ms: int = 2200) -> None:
        self.toast = text
        self.toast_until_ms = pygame.time.get_ticks() + ms

    def _do_action(self, action: AgentAction, **kwargs) -> None:
        if self.env.is_solved or self.env.budget_remaining <= 0:
            return
        result = self.env.step(action, **kwargs)
        obs = render_step_observation(self.env, result.observation)
        # After a MOVE the new room is already drawn on screen — strip the
        # verbose "you see ... there are also ..." enumeration from the
        # status line so it only reads "You move to the Greenhouse."
        if action == AgentAction.MOVE:
            obs = obs.split("\n", 1)[0]
        self.last_observation = obs
        if not result.success:
            self._show_toast(result.observation)
        # World may have ticked → refresh dynamic content for the now-current room
        self.current_layout = self._layout_for(self.env.agent_location_id)
        self._update_last_seen()

    def _trigger_move(self, target_loc_id: str) -> None:
        from_loc_id = self.env.agent_location_id
        target = self.state.locations[target_loc_id]
        self._do_action(AgentAction.MOVE, target_location=target.name)
        if self.env.agent_location_id == target_loc_id:
            self._enter_room(target_loc_id, from_loc_id)

    def _trigger_examine(self, obj_id: str) -> None:
        obj = self.state.objects.get(obj_id)
        if obj is None:
            return
        self._do_action(AgentAction.EXAMINE_OBJECT, object_name=obj.name)

    def _trigger_talk(self, char_id: str) -> None:
        char = self.state.characters.get(char_id)
        if char is None:
            return
        if not char.is_alive:
            self._show_toast(f"{char.full_name} is dead.")
            return

        def _submit(question: str) -> None:
            q = question or "Where were you at the time of the murder?"
            self._do_action(AgentAction.TALK_TO, character_name=char.full_name, question=q)

        self.modal = Modal(
            title=f"Interview: {char.full_name}",
            prompt="Type your question, ENTER to ask, ESC to cancel.",
            on_submit=_submit,
        )

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def _interact_at_player(self) -> None:
        """Pressed E — interact with whatever the player is standing on/next to."""
        layout = self.current_layout
        px = int(self.player_px // TILE_PX)
        py = int(self.player_py // TILE_PX)

        # Doorway: trigger MOVE
        adj_loc = layout.door_at(px, py)
        if adj_loc:
            self._trigger_move(adj_loc)
            return

        # Adjacent (4-neighbourhood) object or NPC
        for dx, dy in [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]:
            nx, ny = px + dx, py + dy
            obj_id = layout.object_at(nx, ny)
            if obj_id:
                self._trigger_examine(obj_id)
                return
            char_id = layout.character_at(nx, ny)
            if char_id:
                self._trigger_talk(char_id)
                return
        self._show_toast("Nothing to interact with here.")

    def _open_menu(self) -> None:
        self.menu_open = True

    def _close_menu(self) -> None:
        self.menu_open = False

    def _menu_action(self, key: int) -> None:
        if key == pygame.K_q:
            self.running = False
        elif key == pygame.K_i:
            self._do_action(AgentAction.CHECK_INVENTORY)
            self._close_menu()
        elif key == pygame.K_l:
            self._do_action(AgentAction.EXAMINE_LOCATION)
            self._close_menu()
        elif key == pygame.K_a:
            self._close_menu()
            self._begin_accuse_flow()
        elif key in (pygame.K_ESCAPE, pygame.K_x):
            self._close_menu()

    def _begin_accuse_flow(self) -> None:
        """Minimal accuse flow: three sequential prompts (suspect, weapon, room)."""
        captured: dict[str, str] = {}

        def ask_suspect(value: str) -> None:
            captured["suspect_name"] = value
            self.modal = Modal(
                title="Accusation 2/3 - weapon",
                prompt="Name the murder weapon (ENTER).",
                on_submit=ask_weapon,
            )

        def ask_weapon(value: str) -> None:
            captured["weapon_name"] = value
            self.modal = Modal(
                title="Accusation 3/3 - location",
                prompt="Name the room where the murder happened (ENTER).",
                on_submit=ask_location,
            )

        def ask_location(value: str) -> None:
            captured["location_name"] = value
            self._do_action(AgentAction.ACCUSE, **captured)

        self.modal = Modal(
            title="Accusation 1/3 - suspect",
            prompt="Name the culprit (ENTER).",
            on_submit=ask_suspect,
        )

    # ------------------------------------------------------------------
    # Movement (with collision)
    # ------------------------------------------------------------------

    def _try_move_player(self, dx: float, dy: float) -> None:
        """Move axis-by-axis with AABB-vs-tile collision against walls,
        objects, and characters."""
        # Slightly tighter than the visual radius (TILE_PX/3 ~= 0.33) so the
        # avatar can squeeze through 1-tile doorways without snagging on
        # adjacent walls when slightly off-centre.
        radius = TILE_PX * 0.22

        def blocked_at(px: float, py: float) -> bool:
            # Player tile (centre)
            ctx = int(px // TILE_PX)
            cty = int(py // TILE_PX)
            # Sample the four corners of the avatar's AABB
            for cx in (px - radius, px + radius):
                for cy in (py - radius, py + radius):
                    tx = int(cx // TILE_PX)
                    ty = int(cy // TILE_PX)
                    if not self.current_layout.is_walkable(tx, ty):
                        return True
                    # Solid collision against objects and characters, but
                    # only on the centre tile we'd be standing on (so we
                    # can still brush past adjacent props).
                    if (tx, ty) == (ctx, cty):
                        if self.current_layout.object_at(tx, ty):
                            return True
                        if self.current_layout.character_at(tx, ty):
                            return True
            return False

        new_x = self.player_px + dx
        if not blocked_at(new_x, self.player_py):
            self.player_px = new_x
        new_y = self.player_py + dy
        if not blocked_at(self.player_px, new_y):
            self.player_py = new_y

        # Door MOVE trigger: as soon as ANY part of the avatar overlaps a
        # door tile (centre tile or any AABB corner), step through. Lets
        # players brush against a doorway without needing perfect alignment.
        for tx, ty in {
            (int(self.player_px // TILE_PX), int(self.player_py // TILE_PX)),
            (int((self.player_px - radius) // TILE_PX), int(self.player_py // TILE_PX)),
            (int((self.player_px + radius) // TILE_PX), int(self.player_py // TILE_PX)),
            (int(self.player_px // TILE_PX), int((self.player_py - radius) // TILE_PX)),
            (int(self.player_px // TILE_PX), int((self.player_py + radius) // TILE_PX)),
        }:
            adj = self.current_layout.door_at(tx, ty)
            if adj is not None:
                self._trigger_move(adj)
                return

    # ------------------------------------------------------------------
    # Drawing — coordinates
    #
    # Window layout:
    #   y = 0        ── top status bar (TOPBAR_H)
    #   y = TOPBAR_H ── room view (height = room rows × TILE_PX)
    #                   right of room: tabbed notebook (SIDEBAR_W wide)
    #   y = ...      ── bottom status line (STATUS_H)
    # ------------------------------------------------------------------

    def _room_origin(self) -> tuple[int, int]:
        return (0, TOPBAR_H)

    def _draw_room(self) -> None:
        ox, oy = self._room_origin()
        layout = self.current_layout
        for x in range(layout.width):
            for y in range(layout.height):
                rect = pygame.Rect(ox + x * TILE_PX, oy + y * TILE_PX, TILE_PX, TILE_PX)
                tile = layout.tiles[x][y]
                if tile == Tile.WALL:
                    self.sprites.draw_wall(self.screen, rect)
                elif tile == Tile.DOOR:
                    self.sprites.draw_door(self.screen, rect)
                else:
                    self.sprites.draw_floor(self.screen, rect)

    def _draw_label(
        self,
        text: str,
        cx: int,
        by: int,
        color: tuple[int, int, int] = HUD_TEXT,
        bg: tuple[int, int, int] = (0, 0, 0),
    ) -> None:
        """Pill-shaped label centred horizontally below a sprite."""
        surf = self.font_sm.render(text, True, color)
        pad_x, pad_y = 6, 2
        rect = surf.get_rect(midtop=(cx, by + 2))
        bg_rect = rect.inflate(pad_x * 2, pad_y * 2)
        bg_surf = pygame.Surface(bg_rect.size, pygame.SRCALPHA)
        bg_surf.fill((*bg, 200))
        self.screen.blit(bg_surf, bg_rect.topleft)
        self.screen.blit(surf, rect)

    def _draw_objects(self) -> None:
        ox, oy = self._room_origin()
        for oid, (x, y) in self.current_layout.objects.items():
            obj = self.state.objects.get(oid)
            if obj is None:
                continue
            ev_state = None
            discovered = False
            if obj.evidence_id:
                ev = self.state.evidence.get(obj.evidence_id)
                if ev:
                    ev_state = ev.state
                    discovered = ev.id in self.env._discovered_evidence
            rect = pygame.Rect(ox + x * TILE_PX, oy + y * TILE_PX, TILE_PX, TILE_PX)
            self.sprites.draw_object(self.screen, rect, obj, discovered, ev_state)
            # Object name underneath
            self._draw_label(obj.name, rect.centerx, rect.bottom - 4, color=HUD_DIM)

    def _draw_characters(self) -> None:
        ox, oy = self._room_origin()
        for cid, (x, y) in self.current_layout.characters.items():
            char = self.state.characters.get(cid)
            if char is None:
                continue
            rect = pygame.Rect(ox + x * TILE_PX, oy + y * TILE_PX, TILE_PX, TILE_PX)
            self.sprites.draw_character(self.screen, rect, char)
            color = HUD_TEXT if char.is_alive else HUD_DIM
            self._draw_label(char.full_name, rect.centerx, rect.bottom - 4, color=color)

    def _draw_player(self) -> None:
        ox, oy = self._room_origin()
        cx = int(ox + self.player_px)
        cy = int(oy + self.player_py)
        # Bright halo behind the avatar so the human can spot themselves
        # at a glance even when standing among NPCs of similar palette.
        halo = pygame.Surface((TILE_PX * 2, TILE_PX * 2), pygame.SRCALPHA)
        pygame.draw.circle(halo, (255, 220, 90, 80), (TILE_PX, TILE_PX), TILE_PX - 4)
        pygame.draw.circle(halo, (255, 220, 90, 160), (TILE_PX, TILE_PX), TILE_PX - 4, 3)
        self.screen.blit(halo, (cx - TILE_PX, cy - TILE_PX))
        self.sprites.draw_player(self.screen, cx, cy, TILE_PX // 3)
        # YOU label — bold, gold
        self._draw_label("** YOU **", cx, cy + TILE_PX // 2 - 4, color=(255, 220, 90))

    def _draw_topbar(self) -> None:
        bar = pygame.Rect(0, 0, self.win_w, TOPBAR_H)
        pygame.draw.rect(self.screen, HUD_BG, bar)
        pygame.draw.rect(self.screen, HUD_BORDER, bar, 1)
        loc = self.state.locations.get(self.env.agent_location_id)
        loc_name = loc.name if loc else "?"
        victim = self.state.characters.get(self.state.victim_id)
        v_name = victim.full_name if victim else "?"
        text = (
            f"  {loc_name}    |    step {self.state.current_step}    |    "
            f"budget {self.env.budget_remaining}    |    victim: {v_name}"
        )
        self.screen.blit(self.font_lg.render(text, True, HUD_TEXT), (8, 8))

    def _draw_status_line(self) -> None:
        room_h_px = self.current_layout.height * TILE_PX
        y0 = TOPBAR_H + room_h_px
        bar = pygame.Rect(0, y0, self.win_w, STATUS_H)
        pygame.draw.rect(self.screen, HUD_BG, bar)
        pygame.draw.rect(self.screen, HUD_BORDER, bar, 1)

        # Show only the latest two lines of the action result + a controls hint.
        max_chars = max(40, (self.win_w - 24) // 9)
        # Take the most recent paragraph only (drop the briefing)
        last = (self.last_observation or "").strip().splitlines()
        compact: list[str] = []
        for line in reversed(last):
            line = line.strip()
            if line:
                compact.insert(0, line)
                if len(compact) >= 2:
                    break
        wrapped: list[str] = []
        for line in compact:
            wrapped.extend(textwrap.wrap(line, max_chars) or [""])
        for i, line in enumerate(wrapped[:2]):
            self.screen.blit(self.font_md.render(line, True, HUD_TEXT), (12, y0 + 6 + i * 22))

        # Toast in bottom-right
        if self.toast and pygame.time.get_ticks() < self.toast_until_ms:
            surf = self.font_md.render(self.toast, True, HUD_TEXT)
            r = surf.get_rect(bottomright=(self.win_w - 12, y0 + STATUS_H - 6))
            bg = r.inflate(12, 6)
            pygame.draw.rect(self.screen, PROMPT_BG, bg)
            pygame.draw.rect(self.screen, PROMPT_BORDER, bg, 1)
            self.screen.blit(surf, r)

    # ------------------------------------------------------------------
    # Right-hand notebook: Case File / Interviews / Evidence
    # ------------------------------------------------------------------

    def _sidebar_rect(self) -> pygame.Rect:
        room_w_px = self.current_layout.width * TILE_PX
        room_h_px = self.current_layout.height * TILE_PX
        return pygame.Rect(room_w_px, TOPBAR_H, SIDEBAR_W, room_h_px)

    def _draw_sidebar(self) -> None:
        sb = self._sidebar_rect()
        pygame.draw.rect(self.screen, HUD_BG, sb)
        pygame.draw.rect(self.screen, HUD_BORDER, sb, 1)

        # Tab strip
        tab_strip = pygame.Rect(sb.x, sb.y, sb.w, TAB_BAR_H)
        pygame.draw.rect(self.screen, (24, 22, 30), tab_strip)
        tab_w = sb.w // len(TAB_NAMES)
        for i, name in enumerate(TAB_NAMES):
            tab = pygame.Rect(sb.x + i * tab_w, sb.y, tab_w, TAB_BAR_H)
            if i == self.active_tab:
                pygame.draw.rect(self.screen, HUD_BG, tab)
                pygame.draw.line(
                    self.screen, PROMPT_BORDER,
                    (tab.x + 6, tab.bottom - 2), (tab.right - 6, tab.bottom - 2), 2,
                )
            self.screen.blit(
                self.font_md.render(name, True, HUD_TEXT),
                (tab.x + 8, tab.y + 7),
            )
        pygame.draw.line(self.screen, HUD_BORDER, (sb.x, sb.y + TAB_BAR_H), (sb.right, sb.y + TAB_BAR_H), 1)

        # Body
        body = pygame.Rect(sb.x, sb.y + TAB_BAR_H, sb.w, sb.h - TAB_BAR_H)
        if self.active_tab == TAB_CASE:
            self._draw_case_tab(body)
        elif self.active_tab == TAB_INTERVIEWS:
            self._draw_interviews_tab(body)
        else:
            self._draw_evidence_tab(body)

    # --- helpers for sidebar text rendering ---

    def _wrap(self, text: str, width_chars: int) -> list[str]:
        out: list[str] = []
        for paragraph in (text or "").splitlines():
            if not paragraph.strip():
                out.append("")
            else:
                out.extend(textwrap.wrap(paragraph, width_chars) or [""])
        return out

    def _render_lines(
        self,
        body: pygame.Rect,
        lines: list[tuple[str, tuple[int, int, int]]],
        scroll: int,
    ) -> None:
        """Lines are (text, color) tuples. Clipped to the body rect, scrolled."""
        clip = self.screen.get_clip()
        self.screen.set_clip(body)
        y = body.y + 8 - scroll
        line_h = 22
        for text, color in lines:
            if y + line_h >= body.y and y < body.bottom:
                surf = self.font_sm.render(text, True, color)
                self.screen.blit(surf, (body.x + 10, y))
            y += line_h
        self.screen.set_clip(clip)

    # --- Case File tab ---

    def _draw_case_tab(self, body: pygame.Rect) -> None:
        from mystery_world.entities import CharacterRole

        loc = self.state.locations.get(self.env.agent_location_id)
        victim = self.state.characters.get(self.state.victim_id)
        body_loc = self.state.locations.get(self.state.body_location_id)
        body_w_chars = max(20, (body.w - 24) // 8)

        lines: list[tuple[str, tuple[int, int, int]]] = []

        def _h(text: str) -> None:
            lines.append((text, HUD_TEXT))

        def _b(text: str) -> None:
            lines.append((text, HUD_DIM))

        _h("THE CASE")
        if victim:
            _b(f"Victim:  {victim.full_name}")
        if body_loc:
            _b(f"Found in: {body_loc.name}")
        evidence_total = len([e for e in self.state.evidence.values() if not e.is_red_herring])
        _b(f"Evidence found: {len(self.env._discovered_evidence)} / {evidence_total}")
        lines.append(("", HUD_DIM))

        _h("SUSPECTS")
        suspects = [
            c for c in self.state.characters.values()
            if CharacterRole.SUSPECT in c.roles and c.is_alive
        ]
        for s in sorted(suspects, key=lambda c: c.full_name):
            seen = self.last_seen.get(s.id, "?")
            _h(f"  {s.full_name}")
            _b(f"      last seen: {seen}")
            if s.motive:
                for line in self._wrap(f"motive: {s.motive}", body_w_chars - 6):
                    _b(f"      {line}")
        lines.append(("", HUD_DIM))

        _h("INNOCENTS / WITNESSES")
        innocents = [
            c for c in self.state.characters.values()
            if CharacterRole.SUSPECT not in c.roles
            and c.id != self.state.victim_id
            and c.is_alive
        ]
        for s in sorted(innocents, key=lambda c: c.full_name):
            seen = self.last_seen.get(s.id, "?")
            _b(f"  {s.full_name}  ({seen})")
        lines.append(("", HUD_DIM))

        if loc:
            _h(f"HERE -- {loc.name}")
            for cid in loc.characters_here:
                ch = self.state.characters.get(cid)
                if ch:
                    _b(f"  - {ch.full_name}{'' if ch.is_alive else ' (deceased)'}")
            for oid in loc.objects_here:
                obj = self.state.objects.get(oid)
                if obj:
                    _b(f"  - {obj.name}")
            lines.append(("", HUD_DIM))
            _h("EXITS")
            for aid in loc.adjacent_ids:
                adj = self.state.locations.get(aid)
                if adj:
                    _b(f"  -> {adj.name}")
            lines.append(("", HUD_DIM))

        _h("CONTROLS")
        for ln in [
            "WASD     walk",
            "E        interact",
            "1/2/3    switch tab",
            "Up/Down  scroll tab",
            "ESC      menu",
        ]:
            _b(f"  {ln}")

        self._render_lines(body, lines, self._scroll[TAB_CASE])

    # --- Interviews tab ---

    def _draw_interviews_tab(self, body: pygame.Rect) -> None:
        body_w_chars = max(20, (body.w - 24) // 8)
        histories = self.env._interview_histories  # {char_id: [{role, content}, ...]}

        lines: list[tuple[str, tuple[int, int, int]]] = []
        if not histories:
            lines.append(("(No interviews yet - talk to an NPC with E.)", HUD_DIM))
        else:
            for cid, hist in histories.items():
                char = self.state.characters.get(cid)
                if not char:
                    continue
                lines.append((char.full_name.upper(), HUD_TEXT))
                lines.append(("-" * (body_w_chars - 2), HUD_DIM))
                for msg in hist:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    label = "Q" if role == "user" else "A"
                    color = HUD_TEXT if role == "user" else HUD_DIM
                    for i, line in enumerate(self._wrap(content, body_w_chars - 4)):
                        prefix = f"{label}: " if i == 0 else "   "
                        lines.append((prefix + line, color))
                lines.append(("", HUD_DIM))

        self._render_lines(body, lines, self._scroll[TAB_INTERVIEWS])

    # --- Evidence tab ---

    def _draw_evidence_tab(self, body: pygame.Rect) -> None:
        body_w_chars = max(20, (body.w - 24) // 8)
        lines: list[tuple[str, tuple[int, int, int]]] = []

        if not self.env._discovered_evidence:
            lines.append(("(No evidence collected yet - examine objects with E.)", HUD_DIM))
        else:
            for eid in self.env._discovered_evidence:
                ev = self.state.evidence.get(eid)
                if not ev:
                    continue
                lines.append((f"[{ev.id}]  {ev.name}", HUD_TEXT))
                edge = ev.relevance.edge_type.name if ev.relevance else "-"
                loc = self.state.locations.get(ev.location_id)
                loc_name = loc.name if loc else "?"
                lines.append((f"  {ev.evidence_type.name.lower()} | {edge} | {loc_name}", HUD_DIM))
                for line in self._wrap(ev.description, body_w_chars - 4):
                    lines.append(("  " + line, HUD_DIM))
                lines.append(("", HUD_DIM))

        self._render_lines(body, lines, self._scroll[TAB_EVIDENCE])

    def _draw_modal(self) -> None:
        if not self.modal:
            return
        overlay = pygame.Surface((self.win_w, self.win_h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        box_w, box_h = 640, 200
        box = pygame.Rect((self.win_w - box_w) // 2, (self.win_h - box_h) // 2, box_w, box_h)
        pygame.draw.rect(self.screen, PROMPT_BG, box)
        pygame.draw.rect(self.screen, PROMPT_BORDER, box, 3)

        self.screen.blit(self.font_lg.render(self.modal.title, True, HUD_TEXT), (box.x + 16, box.y + 14))
        self.screen.blit(self.font_md.render(self.modal.prompt, True, HUD_DIM), (box.x + 16, box.y + 50))
        # Input field
        field = pygame.Rect(box.x + 16, box.y + 90, box.w - 32, 40)
        pygame.draw.rect(self.screen, HUD_BG, field)
        pygame.draw.rect(self.screen, HUD_BORDER, field, 2)
        cursor = "_" if (pygame.time.get_ticks() // 500) % 2 else " "
        display = ("*" * len(self.modal.text)) if self.modal.mask else self.modal.text
        self.screen.blit(
            self.font_md.render(display + cursor, True, HUD_TEXT),
            (field.x + 8, field.y + 10),
        )

    def _draw_menu(self) -> None:
        if not self.menu_open:
            return
        overlay = pygame.Surface((self.win_w, self.win_h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))
        items = [
            "[L] Look around",
            "[I] Inventory",
            "[A] Make accusation",
            "[Q] Quit",
            "[ESC] Close menu",
        ]
        box_w, box_h = 360, 40 + len(items) * 32
        box = pygame.Rect((self.win_w - box_w) // 2, (self.win_h - box_h) // 2, box_w, box_h)
        pygame.draw.rect(self.screen, PROMPT_BG, box)
        pygame.draw.rect(self.screen, PROMPT_BORDER, box, 3)
        self.screen.blit(self.font_lg.render("MENU", True, HUD_TEXT), (box.x + 16, box.y + 8))
        for i, item in enumerate(items):
            self.screen.blit(self.font_md.render(item, True, HUD_TEXT), (box.x + 16, box.y + 40 + i * 28))

    def _is_episode_over(self) -> bool:
        return self.env.is_solved or self.env.budget_remaining <= 0

    @staticmethod
    def _star_rating(score: float) -> str:
        """Composite 0..1 -> 0..5 stars (rounded to nearest). ASCII-safe."""
        score = max(0.0, min(1.0, float(score or 0.0)))
        n = round(score * 5)
        return "*" * n + "." * (5 - n)

    def _build_solution_lines(self) -> list[tuple[str, tuple[int, int, int]]]:
        """Detective-novel reveal — gamer-friendly story first, numbers last."""
        from mystery_world.entities import CharacterRole, EdgeType

        state = self.state
        env = self.env
        summary = env.get_episode_summary()
        score = summary.get("score_result") or {}
        composite = float(score.get("composite_score", 0.0) or 0.0)

        culprit = state.get_culprit()
        weapon = state.objects.get(state.murder_weapon_id)
        room = state.locations.get(state.murder_location_id)
        victim = state.characters.get(state.victim_id)

        culprit_name = culprit.full_name if culprit else "the killer"
        weapon_name = weapon.name if weapon else "an unknown weapon"
        room_name = room.name if room else "an unknown room"
        victim_name = victim.full_name if victim else "the victim"

        lines: list[tuple[str, tuple[int, int, int]]] = []

        # Headline colours
        GOLD = (255, 220, 90)
        GREEN = (130, 220, 150)
        RED = (240, 110, 110)

        def push(text: str, color: tuple[int, int, int] = HUD_TEXT) -> None:
            lines.append((text, color))

        def hdr(text: str) -> None:
            lines.append(("", HUD_DIM))
            lines.append((text, GOLD))
            lines.append(("-" * 42, HUD_DIM))

        def body(text: str, color: tuple[int, int, int] = HUD_TEXT) -> None:
            for line in self._wrap(text, 78):
                lines.append((line, color))

        def bullet(text: str, color: tuple[int, int, int] = HUD_TEXT) -> None:
            wrapped = self._wrap(text, 74)
            if wrapped:
                lines.append(("  *  " + wrapped[0], color))
                for cont in wrapped[1:]:
                    lines.append(("     " + cont, color))

        # ── Verdict + star rating ──
        if env.is_solved and summary.get("accusation_correct"):
            push(f"  {self._star_rating(composite)}    CASE CLOSED    {self._star_rating(composite)}", GREEN)
            push("  You caught the killer.", GREEN)
        elif env.is_solved:
            push(f"  {self._star_rating(composite)}    CASE FAILED    {self._star_rating(composite)}", RED)
            push("  Your accusation was wrong. The real killer walks free.", RED)
        else:
            push(f"  {self._star_rating(composite)}    TIME RAN OUT    {self._star_rating(composite)}", RED)
            push("  You ran out of time before naming a suspect.", RED)
        push("")

        # ── The reveal: one-line drama ──
        push(f"  It was {culprit_name},", HUD_TEXT)
        push(f"  in the {room_name},", HUD_TEXT)
        push(f"  with the {weapon_name}.", HUD_TEXT)

        # ── Why ──
        hdr("WHY?")
        if culprit and culprit.motive:
            body(f"{culprit_name} was driven by {culprit.motive}.")
        else:
            body(f"The motive remains murky -- but the evidence places {culprit_name} at the scene.")

        # ── How they tried to lie ──
        hdr("HOW THEY TRIED TO COVER IT UP")
        if culprit and culprit.alibi_claims:
            claim = culprit.alibi_claims[0]
            body(
                f"{culprit_name} claimed to be at the {claim.location_name} "
                f"at {claim.clock_time_str}."
            )
            if culprit.alibi_corroborator_id:
                corr = state.characters.get(culprit.alibi_corroborator_id)
                corr_name = corr.full_name if corr else "someone"
                if culprit.alibi_corroboration_is_genuine:
                    body(
                        f"{corr_name} backed up the story -- but {corr_name}'s "
                        "account didn't square with what the physical evidence revealed."
                    )
                else:
                    body(
                        f"{corr_name} backed up the story -- but {corr_name} was "
                        f"lying for them. A house of cards."
                    )
            else:
                body("No one could back up the story. A flimsy alibi at best.")
            body(
                f"While the alibi pointed to the {claim.location_name}, the evidence "
                f"placed {culprit_name} firmly in the {room_name} when {victim_name} died."
            )
        else:
            body(f"{culprit_name} offered no alibi -- and the evidence wasn't kind.")

        # ── The trail of evidence ──
        hdr("THE TRAIL OF EVIDENCE")
        edges = [
            (EdgeType.SUSPECT_WEAPON, "[1] Linking the killer to the weapon"),
            (EdgeType.WEAPON_VICTIM,  "[2] Linking the weapon to the victim"),
            (EdgeType.SUSPECT_ROOM,   "[3] Placing the killer at the scene"),
        ]
        for edge_type, label in edges:
            valid = [
                ev for ev in state.evidence.values()
                if not ev.is_red_herring
                and ev.relevance is not None
                and ev.relevance.edge_type == edge_type
                and abs(ev.relevance.contact_timestamp - state.murder_timestamp)
                    < state.freshness_threshold
            ]
            if not valid:
                continue
            push("")
            push(label, HUD_TEXT)
            for ev in valid:
                loc = state.locations.get(ev.location_id)
                where = f" -- found in the {loc.name}" if loc else ""
                check = " (you found this!)" if ev.id in env._discovered_evidence else " (you missed this)"
                bullet(f"{ev.description}{where}.{check}",
                       color=(GREEN if ev.id in env._discovered_evidence else HUD_DIM))

        # ── Your detective work ──
        hdr("YOUR DETECTIVE WORK")
        n_found = len(summary.get("evidence_discovered", []))
        n_total = len([e for e in state.evidence.values() if not e.is_red_herring])
        n_intv = len(summary.get("characters_interviewed", []))
        n_suspects = len([
            c for c in state.characters.values() if CharacterRole.SUSPECT in c.roles
        ])
        actions_used = summary.get("actions_taken", "?")
        budget = summary.get("budget", "?")
        push(f"  Evidence found:        {n_found} / {n_total}")
        push(f"  Suspects interviewed:  {n_intv} / {n_suspects}")
        if score:
            push(f"  Innocents cleared:     {score.get('correct_eliminations', 0)} / {score.get('total_innocents', 0)}")
        push(f"  Actions used:          {actions_used} / {budget}")

        # ── Try next ──
        hdr("WHAT'S NEXT?")
        push("  *  Replay this case -- see if you can solve it cleaner")
        push(f"  *  Try seed {state.seed + 1} -- same difficulty, fresh case")
        push("  *  Step up to the next difficulty for a real challenge")
        push("")
        push("  Press Up/Down or PgUp/PgDn to scroll  |  ESC to exit", HUD_DIM)

        # ── For the curious: technical breakdown ──
        if score:
            hdr("THE NUMBERS  (for the curious)")
            push(f"  Composite score:        {composite:.3f}", HUD_DIM)
            push(f"  Accusation:             {score.get('accusation_score', 0):.2f}"
                 f"   suspect={int(score.get('correct_suspect', 0))}"
                 f"   weapon={int(score.get('correct_weapon', 0))}"
                 f"   room={int(score.get('correct_room', 0))}", HUD_DIM)
            push(f"  Locard triangle:", HUD_DIM)
            push(f"    suspect <-> weapon  F1 = {score.get('suspect_weapon_score', 0):.2f}", HUD_DIM)
            push(f"    weapon  <-> victim  F1 = {score.get('weapon_victim_score', 0):.2f}", HUD_DIM)
            push(f"    suspect <-> room    F1 = {score.get('suspect_room_score', 0):.2f}", HUD_DIM)
            push(f"  Alibi consistency:      {score.get('alibi_score', 0):.2f}", HUD_DIM)
            push(f"  Innocent eliminations:  {score.get('elimination_score', 0):.2f}", HUD_DIM)
        return lines

    def _draw_endscreen(self) -> None:
        if not self._is_episode_over():
            return
        # Full-window dimmed overlay
        overlay = pygame.Surface((self.win_w, self.win_h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 220))
        self.screen.blit(overlay, (0, 0))

        # Title bar at the top of the overlay
        title_h = 60
        title_rect = pygame.Rect(40, 30, self.win_w - 80, title_h)
        pygame.draw.rect(self.screen, PROMPT_BG, title_rect)
        pygame.draw.rect(self.screen, PROMPT_BORDER, title_rect, 2)
        summary = self.env.get_episode_summary()
        if self.env.is_solved and summary.get("accusation_correct"):
            verdict = "CASE CLOSED"
        elif self.env.is_solved:
            verdict = "CASE FAILED"
        else:
            verdict = "TIME RAN OUT"
        self.screen.blit(
            self.font_xl.render(verdict, True, HUD_TEXT),
            self.font_xl.render(verdict, True, HUD_TEXT).get_rect(center=title_rect.center),
        )

        # Solution body — scrollable
        body = pygame.Rect(
            40, 30 + title_h + 12,
            self.win_w - 80, self.win_h - (30 + title_h + 12) - 30,
        )
        pygame.draw.rect(self.screen, HUD_BG, body)
        pygame.draw.rect(self.screen, PROMPT_BORDER, body, 1)
        # Pad the body a bit so text doesn't kiss the border
        inner = body.inflate(-16, -16)
        self._render_lines(inner, self._build_solution_lines(), self._endscreen_scroll)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # One frame of game logic. Split out so both sync run() and async
    # run_async() can drive it. Returns True while the game is still alive.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Headless input API — server uses these to feed user input into the
    # game from a WebSocket. Mirror what pygame would normally surface.
    # ------------------------------------------------------------------

    def inject_key_down(self, key: int, unicode: str = "") -> None:
        """Queue a synthetic KEYDOWN and mark the key held."""
        self._pending_events.append(
            pygame.event.Event(pygame.KEYDOWN, {"key": key, "unicode": unicode, "mod": 0})
        )
        self._pressed_keys.add(key)

    def inject_key_up(self, key: int) -> None:
        """Queue a synthetic KEYUP and mark the key released."""
        self._pending_events.append(
            pygame.event.Event(pygame.KEYUP, {"key": key, "mod": 0})
        )
        self._pressed_keys.discard(key)

    def _drain_events(self) -> list[pygame.event.Event]:
        if self.headless:
            events = self._pending_events
            self._pending_events = []
            return events
        return list(pygame.event.get())

    def _is_key_held(self, key: int) -> bool:
        if self.headless:
            return key in self._pressed_keys
        return bool(pygame.key.get_pressed()[key])

    def get_frame_bytes(self, fmt: str = "WEBP", quality: int = 85) -> bytes:
        """Serialise the current screen Surface as image bytes.

        format='WEBP' (default, lossless) — for our flat-colour sprite art
          this is ~35% the size of PNG and ~25% the size of JPEG. Perfect
          for low-bandwidth streaming.
        format='PNG' — lossless, larger file. Use for the VLM observation
          pipeline where byte-exact reproducibility matters.
        format='JPEG' — fastest encode, but for sprite art it's actually
          *larger* than PNG (gradients are expensive). Avoid unless the
          encode time of WebP is a problem.
        """
        import io
        from PIL import Image
        import numpy as np
        arr = pygame.surfarray.array3d(self.screen).transpose(1, 0, 2)
        buf = io.BytesIO()
        img = Image.fromarray(arr.astype(np.uint8))
        f = fmt.upper()
        if f == "WEBP":
            img.save(buf, format="WEBP", lossless=True, quality=quality)
        elif f == "JPEG":
            img.save(buf, format="JPEG", quality=quality, optimize=False)
        else:
            img.save(buf, format=f)
        return buf.getvalue()

    def get_frame_png(self) -> bytes:
        """Backward-compatible wrapper for callers that need lossless PNG."""
        return self.get_frame_bytes(fmt="PNG")

    def _tick(self) -> bool:
        if not self.running:
            return False
        dt = self.clock.tick(60) / 1000.0

        # ---- events ----
        for event in self._drain_events():
            if event.type == pygame.QUIT:
                self.running = False
                continue

            if self.modal is not None:
                active = self.modal
                if active.handle_key(event):
                    if self.modal is active:
                        self.modal = None
                continue

            if event.type == pygame.KEYDOWN:
                if self._is_episode_over():
                    if event.key == MENU_KEY:
                        self.running = False
                    elif event.key in (pygame.K_UP, pygame.K_PAGEUP):
                        self._endscreen_scroll = max(0, self._endscreen_scroll - 36)
                    elif event.key in (pygame.K_DOWN, pygame.K_PAGEDOWN):
                        self._endscreen_scroll += 36
                    continue

                if self.menu_open:
                    self._menu_action(event.key)
                    continue
                if event.key == MENU_KEY:
                    self._open_menu()
                elif event.key == INTERACT_KEY:
                    self._interact_at_player()
                elif event.key in (pygame.K_1, pygame.K_KP1):
                    self.active_tab = TAB_CASE
                elif event.key in (pygame.K_2, pygame.K_KP2):
                    self.active_tab = TAB_INTERVIEWS
                elif event.key in (pygame.K_3, pygame.K_KP3):
                    self.active_tab = TAB_EVIDENCE
                elif event.key in (pygame.K_UP, pygame.K_PAGEUP):
                    self._scroll[self.active_tab] = max(0, self._scroll[self.active_tab] - 36)
                elif event.key in (pygame.K_DOWN, pygame.K_PAGEDOWN):
                    self._scroll[self.active_tab] += 36

        # ---- continuous movement (only if no modal/menu and game live) ----
        if (
            self.modal is None
            and not self.menu_open
            and not self.env.is_solved
            and self.env.budget_remaining > 0
        ):
            vx = vy = 0.0
            if self._is_key_held(pygame.K_w):
                vy -= 1
            if self._is_key_held(pygame.K_s):
                vy += 1
            if self._is_key_held(pygame.K_a):
                vx -= 1
            if self._is_key_held(pygame.K_d):
                vx += 1
            if vx or vy:
                norm = (vx * vx + vy * vy) ** 0.5
                vx /= norm
                vy /= norm
                self._try_move_player(vx * PLAYER_SPEED_PX * dt, vy * PLAYER_SPEED_PX * dt)

        # ---- draw ----
        self.screen.fill(BG_COLOR)
        self._draw_topbar()
        self._draw_room()
        self._draw_objects()
        self._draw_characters()
        self._draw_player()
        self._draw_status_line()
        self._draw_sidebar()
        self._draw_menu()
        self._draw_modal()
        self._draw_endscreen()
        if not self.headless:
            pygame.display.flip()
        return True

    def run(self) -> None:
        while self._tick():
            pass
        pygame.quit()

    async def run_async(self) -> None:
        """Async loop for pygbag/Pyodide: yields to the browser event loop
        each frame so the page stays responsive."""
        import asyncio
        while self._tick():
            await asyncio.sleep(0)
        pygame.quit()
