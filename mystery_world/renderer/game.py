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
from mystery_world.renderer.layout import RoomLayout, Tile, build_room_layout
from mystery_world.renderer.sprites import EmojiSprites, ProceduralSprites, SpriteLoader
from mystery_world.world import AgentAction, MysteryEnvironment

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

TILE_PX = 44
TOPBAR_H = 36           # top status bar
STATUS_H = 64           # bottom 2-line status (last action result)
SIDEBAR_W = 380         # right notebook (Case File / Interviews / Evidence)
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

    def handle_key(self, event: pygame.event.Event) -> bool:
        """Return True when the modal should close."""
        if event.type != pygame.KEYDOWN:
            return False
        if event.key == pygame.K_RETURN:
            self.on_submit(self.text.strip())
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
    ) -> None:
        self.env = env
        self.state = env.state
        self.window_title = window_title

        # Pygame must be initialised before EmojiSprites loads its font
        pygame.init()
        pygame.display.set_caption(window_title)

        # Default sprite loader: emoji icons (real icons, not geometric shapes).
        # Falls back to procedural primitives if Noto Color Emoji isn't found.
        self.sprites: SpriteLoader = sprites or EmojiSprites()

        self._layouts: dict[str, RoomLayout] = {}
        self.current_layout: RoomLayout = self._layout_for(env.agent_location_id)

        # Player position in pixels (centre of the avatar)
        sx, sy = self.current_layout.default_spawn
        self.player_px: float = (sx + 0.5) * TILE_PX
        self.player_py: float = (sy + 0.5) * TILE_PX

        # HUD state
        self.briefing: str = render_initial_briefing(env)
        self.last_observation: str = "Click WASD to walk · E to interact · ESC for menu · 1/2/3 to switch tabs"
        self.toast: str = ""
        self.toast_until_ms: int = 0

        # Persistent UI state — tab + per-tab scroll offset
        self.active_tab: int = TAB_CASE
        self._scroll: dict[int, int] = {TAB_CASE: 0, TAB_INTERVIEWS: 0, TAB_EVIDENCE: 0}

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
        self.screen = pygame.display.set_mode((self.win_w, self.win_h))
        self.clock = pygame.time.Clock()
        self.font_sm = pygame.font.SysFont("dejavusansmono,monospace", 13)
        self.font_md = pygame.font.SysFont("dejavusansmono,monospace", 15)
        self.font_lg = pygame.font.SysFont("dejavusansmono,monospace", 18, bold=True)
        self.font_xl = pygame.font.SysFont("dejavusansmono,monospace", 22, bold=True)

    def _update_last_seen(self) -> None:
        """Record where each character is right now (called each step)."""
        for cid, char in self.state.characters.items():
            loc = self.state.locations.get(char.location_id)
            if loc:
                self.last_seen[cid] = loc.name

    # ------------------------------------------------------------------
    # Layout / room transitions
    # ------------------------------------------------------------------

    def _layout_for(self, location_id: str) -> RoomLayout:
        if location_id not in self._layouts:
            loc = self.state.locations[location_id]
            self._layouts[location_id] = build_room_layout(loc)
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
        # Place any new objects/characters that arrived
        import random
        rng = random.Random(f"refresh::{layout.location_id}::{self.state.current_step}")
        rng.shuffle(free)
        for oid in loc.objects_here:
            if oid not in layout.objects and free:
                layout.objects[oid] = free.pop()
        for cid in loc.characters_here:
            if cid not in layout.characters and free:
                layout.characters[cid] = free.pop()

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
        self.last_observation = render_step_observation(self.env, result.observation)
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
                title="Accusation 2/3 — weapon",
                prompt="Name the murder weapon (ENTER).",
                on_submit=ask_weapon,
            )

        def ask_weapon(value: str) -> None:
            captured["weapon_name"] = value
            self.modal = Modal(
                title="Accusation 3/3 — location",
                prompt="Name the room where the murder happened (ENTER).",
                on_submit=ask_location,
            )

        def ask_location(value: str) -> None:
            captured["location_name"] = value
            self._do_action(AgentAction.ACCUSE, **captured)

        self.modal = Modal(
            title="Accusation 1/3 — suspect",
            prompt="Name the culprit (ENTER).",
            on_submit=ask_suspect,
        )

    # ------------------------------------------------------------------
    # Movement (with collision)
    # ------------------------------------------------------------------

    def _try_move_player(self, dx: float, dy: float) -> None:
        """Move axis-by-axis with AABB-vs-tile collision."""
        radius = TILE_PX * 0.30  # avatar half-extent

        def blocked_at(px: float, py: float) -> bool:
            # Sample the four corners of the avatar's AABB
            for cx in (px - radius, px + radius):
                for cy in (py - radius, py + radius):
                    tx = int(cx // TILE_PX)
                    ty = int(cy // TILE_PX)
                    if not self.current_layout.is_walkable(tx, ty):
                        return True
            return False

        new_x = self.player_px + dx
        if not blocked_at(new_x, self.player_py):
            self.player_px = new_x
        new_y = self.player_py + dy
        if not blocked_at(self.player_px, new_y):
            self.player_py = new_y

        # Walking onto a door tile = auto-trigger MOVE (feels natural in a
        # top-down adventure). We still respect the discrete action cost.
        tx = int(self.player_px // TILE_PX)
        ty = int(self.player_py // TILE_PX)
        adj = self.current_layout.door_at(tx, ty)
        if adj is not None:
            self._trigger_move(adj)

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

    def _draw_characters(self) -> None:
        ox, oy = self._room_origin()
        for cid, (x, y) in self.current_layout.characters.items():
            char = self.state.characters.get(cid)
            if char is None:
                continue
            rect = pygame.Rect(ox + x * TILE_PX, oy + y * TILE_PX, TILE_PX, TILE_PX)
            self.sprites.draw_character(self.screen, rect, char)

    def _draw_player(self) -> None:
        ox, oy = self._room_origin()
        cx = int(ox + self.player_px)
        cy = int(oy + self.player_py)
        self.sprites.draw_player(self.screen, cx, cy, TILE_PX // 3)

    def _draw_topbar(self) -> None:
        bar = pygame.Rect(0, 0, self.win_w, TOPBAR_H)
        pygame.draw.rect(self.screen, HUD_BG, bar)
        pygame.draw.rect(self.screen, HUD_BORDER, bar, 1)
        loc = self.state.locations.get(self.env.agent_location_id)
        loc_name = loc.name if loc else "?"
        victim = self.state.characters.get(self.state.victim_id)
        v_name = victim.full_name if victim else "?"
        text = (
            f" 🕵️  {loc_name}     ⏱ step {self.state.current_step}     "
            f"⚖ budget {self.env.budget_remaining}     ☠ victim: {v_name}"
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
        line_h = 18
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
            interviewed = s.id in self.env._interviewed_characters
            mark = "✓" if interviewed else "•"
            _h(f"  {mark} {s.full_name}")
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
            interviewed = s.id in self.env._interviewed_characters
            mark = "✓" if interviewed else "•"
            _b(f"  {mark} {s.full_name}  ({seen})")
        lines.append(("", HUD_DIM))

        if loc:
            _h(f"HERE — {loc.name}")
            for cid in loc.characters_here:
                ch = self.state.characters.get(cid)
                if ch:
                    _b(f"  · {ch.full_name}{'' if ch.is_alive else ' (deceased)'}")
            for oid in loc.objects_here:
                obj = self.state.objects.get(oid)
                if obj:
                    _b(f"  – {obj.name}")
            lines.append(("", HUD_DIM))
            _h("EXITS")
            for aid in loc.adjacent_ids:
                adj = self.state.locations.get(aid)
                if adj:
                    _b(f"  → {adj.name}")
            lines.append(("", HUD_DIM))

        _h("CONTROLS")
        for ln in [
            "WASD  walk",
            "E     interact",
            "1/2/3 switch tab",
            "↑↓    scroll tab",
            "ESC   menu",
        ]:
            _b(f"  {ln}")

        self._render_lines(body, lines, self._scroll[TAB_CASE])

    # --- Interviews tab ---

    def _draw_interviews_tab(self, body: pygame.Rect) -> None:
        body_w_chars = max(20, (body.w - 24) // 8)
        histories = self.env._interview_histories  # {char_id: [{role, content}, ...]}

        lines: list[tuple[str, tuple[int, int, int]]] = []
        if not histories:
            lines.append(("(No interviews yet — talk to an NPC with E.)", HUD_DIM))
        else:
            for cid, hist in histories.items():
                char = self.state.characters.get(cid)
                if not char:
                    continue
                lines.append((char.full_name.upper(), HUD_TEXT))
                lines.append(("─" * (body_w_chars - 2), HUD_DIM))
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
            lines.append(("(No evidence collected yet — examine objects with E.)", HUD_DIM))
        else:
            for eid in self.env._discovered_evidence:
                ev = self.state.evidence.get(eid)
                if not ev:
                    continue
                lines.append((f"[{ev.id}]  {ev.name}", HUD_TEXT))
                edge = ev.relevance.edge_type.name if ev.relevance else "—"
                loc = self.state.locations.get(ev.location_id)
                loc_name = loc.name if loc else "?"
                lines.append((f"  {ev.evidence_type.name.lower()} · {edge} · {loc_name}", HUD_DIM))
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
        self.screen.blit(
            self.font_md.render(self.modal.text + cursor, True, HUD_TEXT),
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

    def _draw_endscreen(self) -> None:
        if not self.env.is_solved and self.env.budget_remaining > 0:
            return
        summary = self.env.get_episode_summary()
        culprit = self.state.get_culprit()
        weapon = self.state.objects.get(self.state.murder_weapon_id)
        room = self.state.locations.get(self.state.murder_location_id)

        verdict = "CASE CLOSED" if summary.get("accusation_correct") else "CASE FAILED"
        lines = [
            verdict,
            "",
            f"True answer : {culprit.full_name if culprit else '?'}"
            f" / {weapon.name if weapon else '?'}"
            f" / {room.name if room else '?'}",
            f"Actions used : {summary.get('actions_taken', '?')} / {summary.get('budget', '?')}",
            "",
            "Press ESC to exit.",
        ]
        overlay = pygame.Surface((self.win_w, self.win_h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 200))
        self.screen.blit(overlay, (0, 0))
        for i, line in enumerate(lines):
            font = self.font_lg if i == 0 else self.font_md
            surf = font.render(line, True, HUD_TEXT)
            rect = surf.get_rect(center=(self.win_w // 2, self.win_h // 2 - 80 + i * 28))
            self.screen.blit(surf, rect)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        while self.running:
            dt = self.clock.tick(60) / 1000.0  # seconds since last frame

            # ---- events ----
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    continue

                if self.modal is not None:
                    active = self.modal
                    if active.handle_key(event):
                        # Only clear if on_submit didn't chain to a new modal
                        if self.modal is active:
                            self.modal = None
                    continue

                if event.type == pygame.KEYDOWN:
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
                keys = pygame.key.get_pressed()
                vx = vy = 0.0
                # WASD only — arrow keys are reserved for sidebar scroll.
                if keys[pygame.K_w]:
                    vy -= 1
                if keys[pygame.K_s]:
                    vy += 1
                if keys[pygame.K_a]:
                    vx -= 1
                if keys[pygame.K_d]:
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
            pygame.display.flip()

        pygame.quit()
