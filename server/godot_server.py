"""WebSocket server that exposes a `MysteryEnvironment` to a Godot 4 client.

Milestone 1 protocol:

  Client -> Server:
    {"type": "get_current_room", "request_id": "..."}
    {"type": "ping",             "request_id": "..."}

  Server -> Client:
    {"type": "room",  "request_id": ..., room fields ...}
    {"type": "pong",  "request_id": ...}
    {"type": "error", "request_id": ..., "error": "..."}

stdout contract:
  Exactly one machine-parseable line is printed to stdout when the server is
  ready, before any other stdout output:

      LISTEN ws://<host>:<port>

  M5's Godot binary spawns this server as a sidecar and parses that line to
  discover the chosen port (when invoked with --port 0). All logs go to
  stderr; stdout has only the LISTEN line.

The OpenAI key for `npc_responder` (used in later milestones) must reach this
process via the OPENAI_API_KEY env var. The server itself never reads or
persists the key.
"""

from __future__ import annotations

import os

# Importing the layout helpers below transitively pulls in
# `mystery_world.renderer.__init__`, which loads pygame. Pygame prints a
# greeting banner to stdout on import, which would corrupt the LISTEN-line
# contract documented in this module's docstring. Suppress it.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import argparse
import asyncio
import json
import logging
import signal
import sys
from typing import Any

import websockets

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel
from mystery_world.entities import CharacterRole
from mystery_world.generator import generate_mystery
from mystery_world.renderer.layout import (
    Tile,
    build_room_layout,
    compute_door_pairings,
)
from mystery_world.world import AgentAction, MysteryEnvironment


logger = logging.getLogger("godot_server")


_TILE_CODE = {Tile.FLOOR: "F", Tile.WALL: "W", Tile.DOOR: "D"}


def _wall_for(tile_x: int, tile_y: int, w: int, h: int) -> str:
    if tile_y == 0:
        return "north"
    if tile_y == h - 1:
        return "south"
    if tile_x == 0:
        return "west"
    if tile_x == w - 1:
        return "east"
    return "interior"


def _character_role(char: Any) -> str:
    if CharacterRole.VICTIM in char.roles:
        return "victim"
    if CharacterRole.SUSPECT in char.roles:
        return "suspect"
    if CharacterRole.WITNESS in char.roles:
        return "witness"
    return "innocent"


def _object_kind(obj: Any) -> str:
    if obj.is_murder_weapon:
        return "murder_weapon"
    if obj.is_weapon:
        return "weapon"
    return "object"


def serialize_room(
    env: MysteryEnvironment,
    location_id: str,
    from_location_id: str | None = None,
) -> dict[str, Any]:
    """Serialize the spawn-room layout for the Godot client.

    All objects and characters in the location are included, regardless of
    evidence state or alive/dead status (CLAUDE.md rule 5: always-visible).
    Visual styling is the renderer's job.

    If `from_location_id` is given and the layout has a corresponding entry
    in `spawn_from`, the player spawns at that door cell. Otherwise the
    room's default spawn (center) is used.
    """
    state = env.state
    location = state.locations[location_id]
    pairings = compute_door_pairings(state)
    layout = build_room_layout(location, pairings.get(location_id))

    tiles = [
        [_TILE_CODE.get(layout.tiles[x][y], "?") for y in range(layout.height)]
        for x in range(layout.width)
    ]

    doors = []
    for (dx, dy), adj_id in sorted(layout.doors.items()):
        adj = state.locations.get(adj_id)
        doors.append(
            {
                "x": dx,
                "y": dy,
                "wall": _wall_for(dx, dy, layout.width, layout.height),
                "leads_to": adj_id,
                "leads_to_name": adj.name if adj else adj_id,
            }
        )

    objects = []
    for oid, (ox, oy) in sorted(layout.objects.items()):
        obj = state.objects.get(oid)
        if obj is None:
            continue
        objects.append(
            {
                "id": oid,
                "name": obj.name,
                "x": ox,
                "y": oy,
                "kind": _object_kind(obj),
            }
        )

    characters = []
    for cid, (cx, cy) in sorted(layout.characters.items()):
        char = state.characters.get(cid)
        if char is None:
            continue
        characters.append(
            {
                "id": cid,
                "name": char.full_name,
                "x": cx,
                "y": cy,
                "role": _character_role(char),
                "alive": bool(char.is_alive),
            }
        )

    if from_location_id and from_location_id in layout.spawn_from:
        spawn_x, spawn_y = layout.spawn_from[from_location_id]
    else:
        spawn_x, spawn_y = layout.default_spawn

    return {
        "room_id": layout.location_id,
        "name": location.name,
        "description": location.description,
        "width": layout.width,
        "height": layout.height,
        "tiles": tiles,
        "doors": doors,
        "objects": objects,
        "characters": characters,
        "spawn": {
            "x": float(spawn_x) + 0.5,
            "y": float(spawn_y) + 0.5,
            "facing_deg": 0.0,
        },
    }


class GodotServer:
    def __init__(self, seed: int, complexity: ComplexityLevel):
        self.seed = seed
        self.complexity = complexity
        config = COMPLEXITY_PRESETS[complexity]
        logger.info("generating mystery: seed=%d complexity=%s", seed, complexity.name)
        world = generate_mystery(config, seed)
        self.env = MysteryEnvironment(world)
        logger.info(
            "world ready: %d locations, agent at %s",
            len(world.locations),
            self.env.agent_location_id,
        )

    async def handle_client(self, ws: Any) -> None:
        peer = ws.remote_address
        logger.info("client connected: %s", peer)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError as exc:
                    await ws.send(
                        json.dumps({"type": "error", "error": f"invalid json: {exc}"})
                    )
                    continue
                await self._dispatch(ws, msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            logger.info("client disconnected: %s", peer)

    async def _dispatch(self, ws: Any, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type")
        request_id = msg.get("request_id", "")

        if msg_type == "ping":
            await ws.send(json.dumps({"type": "pong", "request_id": request_id}))
            return

        if msg_type == "get_current_room":
            try:
                room = serialize_room(self.env, self.env.agent_location_id)
            except Exception as exc:
                logger.exception("serialize_room failed")
                await ws.send(
                    json.dumps(
                        {
                            "type": "error",
                            "request_id": request_id,
                            "error": str(exc),
                        }
                    )
                )
                return
            payload = {"type": "room", "request_id": request_id, **room}
            await ws.send(json.dumps(payload))
            return

        if msg_type == "move_to_room":
            target = str(msg.get("target_location_id", ""))
            if not target:
                await ws.send(
                    json.dumps(
                        {
                            "type": "error",
                            "request_id": request_id,
                            "error": "move_to_room requires target_location_id",
                        }
                    )
                )
                return
            prev = self.env.agent_location_id
            result = self.env.step(AgentAction.MOVE, target_location=target)
            if not result.success:
                await ws.send(
                    json.dumps(
                        {
                            "type": "error",
                            "request_id": request_id,
                            "error": result.observation,
                        }
                    )
                )
                return
            try:
                room = serialize_room(
                    self.env,
                    self.env.agent_location_id,
                    from_location_id=prev,
                )
            except Exception as exc:
                logger.exception("serialize_room (post-move) failed")
                await ws.send(
                    json.dumps(
                        {
                            "type": "error",
                            "request_id": request_id,
                            "error": str(exc),
                        }
                    )
                )
                return
            logger.info("agent moved %s -> %s", prev, self.env.agent_location_id)
            payload = {"type": "room", "request_id": request_id, **room}
            await ws.send(json.dumps(payload))
            return

        await ws.send(
            json.dumps(
                {
                    "type": "error",
                    "request_id": request_id,
                    "error": f"unknown message type: {msg_type!r}",
                }
            )
        )


async def _run(host: str, port: int, server: GodotServer) -> None:
    async with websockets.serve(server.handle_client, host, port) as ws_server:
        # Resolve the actual bound port (port=0 picks one)
        actual_port = port
        try:
            sockets = ws_server.sockets  # type: ignore[attr-defined]
            if sockets:
                actual_port = sockets[0].getsockname()[1]
        except Exception:
            pass

        # The sidecar contract: one parseable LISTEN line on stdout, then
        # nothing else on stdout. Godot M5 reads this line to discover the
        # chosen port when launched with --port 0.
        sys.stdout.write(f"LISTEN ws://{host}:{actual_port}\n")
        sys.stdout.flush()
        logger.info("listening on ws://%s:%d", host, actual_port)

        stop = asyncio.get_running_loop().create_future()

        def _shutdown(*_: Any) -> None:
            if not stop.done():
                stop.set_result(None)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _shutdown)
            except NotImplementedError:
                pass

        await stop
        logger.info("shutting down")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MysteryArena Godot WebSocket server (Milestone 1)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--port",
        type=int,
        default=7777,
        help="WebSocket port. Use 0 to bind a random free port.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--complexity",
        default="EASY",
        choices=[c.name for c in ComplexityLevel],
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    server = GodotServer(args.seed, ComplexityLevel[args.complexity])
    try:
        asyncio.run(_run(args.host, args.port, server))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
