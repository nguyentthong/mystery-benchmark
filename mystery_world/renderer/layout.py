"""Procedurally lays out a `Location` as a 2D tile grid.

Each location becomes a rectangular room with walls around the perimeter,
doors carved into edges based on its `adjacent_ids`, and deterministic
spawn positions for objects and characters. The mapping is stable for a
given location id, so reloading the same world gives the same layout.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from mystery_world.entities import Location

ROOM_W = 18
ROOM_H = 13


class Tile(Enum):
    FLOOR = 0
    WALL = 1
    DOOR = 2


# Cardinal door positions (in tile coords). One door per side, max 4 sides.
_SIDES = ["N", "S", "E", "W"]
_DOOR_TILES: dict[str, tuple[int, int]] = {
    "N": (ROOM_W // 2, 0),
    "S": (ROOM_W // 2, ROOM_H - 1),
    "E": (ROOM_W - 1, ROOM_H // 2),
    "W": (0, ROOM_H // 2),
}
# Opposite side: where the player ends up when entering through a given door.
_OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
# One tile inward from each door, used as the spawn position for that side.
_SPAWN_OFFSET: dict[str, tuple[int, int]] = {
    "N": (0, 1),
    "S": (0, -1),
    "E": (-1, 0),
    "W": (1, 0),
}


@dataclass
class RoomLayout:
    location_id: str
    width: int
    height: int
    tiles: list[list[Tile]]
    # Door tile -> adjacent location id
    doors: dict[tuple[int, int], str]
    # Object id -> tile position
    objects: dict[str, tuple[int, int]] = field(default_factory=dict)
    # Character id -> initial tile position (NPCs are static within a room)
    characters: dict[str, tuple[int, int]] = field(default_factory=dict)
    # Default spawn (room center)
    default_spawn: tuple[int, int] = (ROOM_W // 2, ROOM_H // 2)
    # When entering from neighbour `loc_id`, spawn at this tile (inside door)
    spawn_from: dict[str, tuple[int, int]] = field(default_factory=dict)

    def is_walkable(self, x: int, y: int) -> bool:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        return self.tiles[x][y] != Tile.WALL

    def door_at(self, x: int, y: int) -> str | None:
        return self.doors.get((x, y))

    def object_at(self, x: int, y: int) -> str | None:
        for oid, (ox, oy) in self.objects.items():
            if ox == x and oy == y:
                return oid
        return None

    def character_at(self, x: int, y: int) -> str | None:
        for cid, (cx, cy) in self.characters.items():
            if cx == x and cy == y:
                return cid
        return None


def build_room_layout(location: Location) -> RoomLayout:
    """Deterministically tile-out a single location."""
    rng = random.Random(f"layout::{location.id}")

    tiles: list[list[Tile]] = [
        [Tile.WALL for _ in range(ROOM_H)] for _ in range(ROOM_W)
    ]
    for x in range(1, ROOM_W - 1):
        for y in range(1, ROOM_H - 1):
            tiles[x][y] = Tile.FLOOR

    # Pick door sides for each adjacency. Sort for determinism, cap at 4.
    adj_ids = sorted(location.adjacent_ids)[:4]
    doors: dict[tuple[int, int], str] = {}
    spawn_from: dict[str, tuple[int, int]] = {}
    for i, adj_id in enumerate(adj_ids):
        side = _SIDES[i]
        dx, dy = _DOOR_TILES[side]
        tiles[dx][dy] = Tile.DOOR
        doors[(dx, dy)] = adj_id
        ox, oy = _SPAWN_OFFSET[side]
        spawn_from[adj_id] = (dx + ox, dy + oy)

    # Reserve the default spawn (room centre) and the four tiles cardinally
    # adjacent to it so the player isn't surrounded by props on entry.
    default_spawn = (ROOM_W // 2, ROOM_H // 2)
    reserved = {
        default_spawn,
        (default_spawn[0] + 1, default_spawn[1]),
        (default_spawn[0] - 1, default_spawn[1]),
        (default_spawn[0], default_spawn[1] + 1),
        (default_spawn[0], default_spawn[1] - 1),
    }
    # Free interior tiles (avoid the 1-tile band next to walls so spawns look ok)
    free: list[tuple[int, int]] = [
        (x, y)
        for x in range(2, ROOM_W - 2)
        for y in range(2, ROOM_H - 2)
        if tiles[x][y] == Tile.FLOOR and (x, y) not in reserved
    ]
    rng.shuffle(free)

    objects: dict[str, tuple[int, int]] = {}
    for oid in location.objects_here:
        if not free:
            break
        objects[oid] = free.pop()

    characters: dict[str, tuple[int, int]] = {}
    for cid in location.characters_here:
        if not free:
            break
        characters[cid] = free.pop()

    return RoomLayout(
        location_id=location.id,
        width=ROOM_W,
        height=ROOM_H,
        tiles=tiles,
        doors=doors,
        objects=objects,
        characters=characters,
        default_spawn=default_spawn,
        spawn_from=spawn_from,
    )
