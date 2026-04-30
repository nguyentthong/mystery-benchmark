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
from mystery_world.renderer.sprites import ProceduralSprites, SpriteLoader
from mystery_world.world import AgentAction, MysteryEnvironment

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

TILE_PX = 44
HUD_H = 220             # bottom HUD strip
SIDEBAR_W = 280         # right sidebar (location info, hint, menu state)
PLAYER_SPEED_PX = 220   # pixels per second
INTERACT_KEY = pygame.K_e
MENU_KEY = pygame.K_ESCAPE

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
        self.sprites: SpriteLoader = sprites or ProceduralSprites()

        # Cache one layout per location id (built lazily)
        self._layouts: dict[str, RoomLayout] = {}
        self.current_layout: RoomLayout = self._layout_for(env.agent_location_id)

        # Player position in pixels (centre of the avatar)
        sx, sy = self.current_layout.default_spawn
        self.player_px: float = (sx + 0.5) * TILE_PX
        self.player_py: float = (sy + 0.5) * TILE_PX

        # HUD state
        self.briefing: str = render_initial_briefing(env)
        self.last_observation: str = self.briefing
        self.toast: str = "WASD to move · E to interact · ESC for menu"
        self.toast_until_ms: int = 0

        self.modal: Modal | None = None
        self.menu_open: bool = False
        self.running: bool = True

        # Pygame setup
        pygame.init()
        pygame.display.set_caption(window_title)
        room_w_px = self.current_layout.width * TILE_PX
        room_h_px = self.current_layout.height * TILE_PX
        self.win_w = room_w_px + SIDEBAR_W
        self.win_h = room_h_px + HUD_H
        self.screen = pygame.display.set_mode((self.win_w, self.win_h))
        self.clock = pygame.time.Clock()
        self.font_sm = pygame.font.SysFont("dejavusansmono,monospace", 14)
        self.font_md = pygame.font.SysFont("dejavusansmono,monospace", 16)
        self.font_lg = pygame.font.SysFont("dejavusansmono,monospace", 20, bold=True)

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
    # Drawing
    # ------------------------------------------------------------------

    def _draw_room(self) -> None:
        layout = self.current_layout
        for x in range(layout.width):
            for y in range(layout.height):
                rect = pygame.Rect(x * TILE_PX, y * TILE_PX, TILE_PX, TILE_PX)
                tile = layout.tiles[x][y]
                if tile == Tile.WALL:
                    self.sprites.draw_wall(self.screen, rect)
                elif tile == Tile.DOOR:
                    self.sprites.draw_door(self.screen, rect)
                else:
                    self.sprites.draw_floor(self.screen, rect)

    def _draw_objects(self) -> None:
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
            rect = pygame.Rect(x * TILE_PX, y * TILE_PX, TILE_PX, TILE_PX)
            self.sprites.draw_object(self.screen, rect, obj, discovered, ev_state)
            cx = rect.centerx
            cy = rect.bottom - 2
            self._draw_label(obj.name, cx, cy)

    def _draw_characters(self) -> None:
        for cid, (x, y) in self.current_layout.characters.items():
            char = self.state.characters.get(cid)
            if char is None:
                continue
            rect = pygame.Rect(x * TILE_PX, y * TILE_PX, TILE_PX, TILE_PX)
            self.sprites.draw_character(self.screen, rect, char)
            self._draw_label(char.full_name, rect.centerx, rect.bottom - 2)

    def _draw_player(self) -> None:
        cx = int(self.player_px)
        cy = int(self.player_py)
        self.sprites.draw_player(self.screen, cx, cy, TILE_PX // 3)

    def _draw_label(self, text: str, cx: int, by: int) -> None:
        surf = self.font_sm.render(text, True, HUD_TEXT)
        rect = surf.get_rect(midtop=(cx, by + 4))
        bg = rect.inflate(6, 2)
        pygame.draw.rect(self.screen, HUD_BG, bg)
        self.screen.blit(surf, rect)

    def _draw_hud(self) -> None:
        room_h_px = self.current_layout.height * TILE_PX
        room_w_px = self.current_layout.width * TILE_PX
        # Bottom HUD: last observation
        hud_rect = pygame.Rect(0, room_h_px, self.win_w, HUD_H)
        pygame.draw.rect(self.screen, HUD_BG, hud_rect)
        pygame.draw.rect(self.screen, HUD_BORDER, hud_rect, 2)

        loc = self.state.locations.get(self.env.agent_location_id)
        loc_name = loc.name if loc else "?"
        title = f"  {loc_name}    budget {self.env.budget_remaining}    step {self.state.current_step}"
        self.screen.blit(self.font_lg.render(title, True, HUD_TEXT), (8, room_h_px + 6))

        # Wrap observation across HUD strip
        max_chars = max(40, (self.win_w - 24) // 9)
        wrapped: list[str] = []
        for paragraph in (self.last_observation or "").splitlines():
            if not paragraph.strip():
                wrapped.append("")
                continue
            wrapped.extend(textwrap.wrap(paragraph, max_chars) or [""])
        for i, line in enumerate(wrapped[:9]):
            surf = self.font_md.render(line, True, HUD_TEXT)
            self.screen.blit(surf, (12, room_h_px + 36 + i * 18))

        # Sidebar: hint + people/objects-in-room
        sidebar_rect = pygame.Rect(room_w_px, 0, SIDEBAR_W, room_h_px)
        pygame.draw.rect(self.screen, HUD_BG, sidebar_rect)
        pygame.draw.rect(self.screen, HUD_BORDER, sidebar_rect, 2)

        x0 = room_w_px + 10
        y = 10
        self.screen.blit(self.font_lg.render("HERE", True, HUD_TEXT), (x0, y))
        y += 26
        if loc:
            for cid in loc.characters_here:
                ch = self.state.characters.get(cid)
                if ch:
                    color = HUD_TEXT if ch.is_alive else HUD_DIM
                    self.screen.blit(self.font_sm.render(f"• {ch.full_name}", True, color), (x0, y))
                    y += 16
            if loc.objects_here:
                y += 4
                for oid in loc.objects_here:
                    obj = self.state.objects.get(oid)
                    if obj:
                        self.screen.blit(self.font_sm.render(f"– {obj.name}", True, HUD_DIM), (x0, y))
                        y += 16
            y += 8
            self.screen.blit(self.font_lg.render("EXITS", True, HUD_TEXT), (x0, y))
            y += 26
            for aid in loc.adjacent_ids:
                adj = self.state.locations.get(aid)
                if adj:
                    self.screen.blit(self.font_sm.render(f"→ {adj.name}", True, HUD_DIM), (x0, y))
                    y += 16

        # Toast (bottom-right, time-limited)
        if self.toast and pygame.time.get_ticks() < self.toast_until_ms:
            surf = self.font_md.render(self.toast, True, HUD_TEXT)
            pad = 8
            rect = surf.get_rect()
            rect.bottomright = (self.win_w - 12, room_h_px - 12)
            bg = rect.inflate(pad * 2, pad)
            pygame.draw.rect(self.screen, PROMPT_BG, bg)
            pygame.draw.rect(self.screen, PROMPT_BORDER, bg, 2)
            self.screen.blit(surf, rect)

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

            # ---- continuous movement (only if no modal/menu and game live) ----
            if (
                self.modal is None
                and not self.menu_open
                and not self.env.is_solved
                and self.env.budget_remaining > 0
            ):
                keys = pygame.key.get_pressed()
                vx = vy = 0.0
                if keys[pygame.K_w] or keys[pygame.K_UP]:
                    vy -= 1
                if keys[pygame.K_s] or keys[pygame.K_DOWN]:
                    vy += 1
                if keys[pygame.K_a] or keys[pygame.K_LEFT]:
                    vx -= 1
                if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
                    vx += 1
                if vx or vy:
                    norm = (vx * vx + vy * vy) ** 0.5
                    vx /= norm
                    vy /= norm
                    self._try_move_player(vx * PLAYER_SPEED_PX * dt, vy * PLAYER_SPEED_PX * dt)

            # ---- draw ----
            self.screen.fill(BG_COLOR)
            self._draw_room()
            self._draw_objects()
            self._draw_characters()
            self._draw_player()
            self._draw_hud()
            self._draw_menu()
            self._draw_modal()
            self._draw_endscreen()
            pygame.display.flip()

        pygame.quit()
