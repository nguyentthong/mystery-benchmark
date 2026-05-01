"""WebSocket server that exposes a `MysteryEnvironment` to a Godot 4 client.

Protocol:

  Client -> Server:
    {"type": "ping",                 "request_id": "..."}
    {"type": "get_current_room",     "request_id": "..."}
    {"type": "move_to_room",         "request_id": "...", "target_location_id": str}
    {"type": "examine_object",       "request_id": "...", "object_name": str}
    {"type": "talk_to",              "request_id": "...", "character_name": str, "question": str}
    {"type": "take_object",          "request_id": "...", "object_name": str}
    {"type": "inventory",            "request_id": "..."}
    {"type": "accuse",               "request_id": "...", "suspect_name": str, "weapon_name": str, "location_name": str}
    {"type": "world_graph",          "request_id": "..."}

  Server -> Client:
    {"type": "pong",                 "request_id": ...}
    {"type": "room",                 "request_id": ..., room fields, "world_graph": [...]}
    {"type": "action_result",        "request_id": ..., "success": bool, "observation": str, "evidence_found": [...]}
    {"type": "inventory",            "request_id": ..., "items": [...]}
    {"type": "accusation_result",    "request_id": ..., "correct": bool, "details": {...}}
    {"type": "world_graph",          "request_id": ..., "locations": [...]}
    {"type": "error",                "request_id": ..., "error": str}

stdout contract:
  Exactly one machine-parseable line is printed to stdout when the server is
  ready, before any other stdout output:

      LISTEN ws://<host>:<port>

  M5's Godot binary spawns this server as a sidecar and parses that line to
  discover the chosen port (when invoked with --port 0). All logs go to
  stderr; stdout has only the LISTEN line.

NPC dialogue (talk_to action):
  If the OPENAI_API_KEY env var is set, an NPCResponder is attached to the
  environment and `talk_to` returns LLM-generated NPC replies. Otherwise the
  built-in deterministic template fallback is used. The server itself never
  reads or persists the API key — it stays in the process environment and is
  consumed by openai-sdk at request time.
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
from mystery_world.narrator import render_initial_briefing
from mystery_world.npc_responder import NPCResponder
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
    def __init__(self, seed: int, complexity: ComplexityLevel, npc_model: str | None):
        self.npc_model = npc_model
        self.new_game(
            seed=seed,
            complexity=complexity,
            api_key=os.environ.get("OPENAI_API_KEY"),
        )

    def new_game(
        self,
        seed: int,
        complexity: ComplexityLevel,
        api_key: str | None,
    ) -> None:
        """(Re)generate the world from scratch and reset client-visible
        state. Called once at startup and again whenever the Godot start-
        form submits new parameters."""
        self.seed = seed
        self.complexity = complexity
        config = COMPLEXITY_PRESETS[complexity]
        logger.info("generating mystery: seed=%d complexity=%s", seed, complexity.name)
        world = generate_mystery(config, seed)
        self.env = MysteryEnvironment(world)
        self.visited: set[str] = {self.env.agent_location_id}

        # Attach an NPC responder if an OpenAI API key was supplied (either
        # via the start-form or via the OPENAI_API_KEY env var). We never
        # persist the key — it stays in process memory only.
        if api_key and self.npc_model:
            try:
                base_url = os.environ.get("OPENAI_BASE_URL") or None
                responder = NPCResponder(
                    api_key=api_key,
                    base_url=base_url,
                    model=self.npc_model,
                )
                self.env.set_npc_responder(responder)
                logger.info(
                    "NPC responder attached: model=%s base_url=%s",
                    self.npc_model,
                    base_url or "openai-default",
                )
            except Exception:
                logger.exception("failed to attach NPC responder; using template fallback")
        else:
            logger.info("no OpenAI key supplied; talk_to uses deterministic template fallback")

        logger.info(
            "world ready: %d locations, agent at %s",
            len(world.locations),
            self.env.agent_location_id,
        )

    # ---------------------------------------------------------------- payload helpers

    def _world_graph(self) -> list[dict[str, Any]]:
        out = []
        for lid, loc in self.env.state.locations.items():
            out.append(
                {
                    "id": lid,
                    "name": loc.name,
                    "adjacents": list(loc.adjacent_ids),
                    "visited": lid in self.visited,
                    "current": lid == self.env.agent_location_id,
                }
            )
        out.sort(key=lambda x: x["name"])
        return out

    def _room_payload(
        self, request_id: str, from_location_id: str | None = None
    ) -> dict[str, Any]:
        room = serialize_room(
            self.env, self.env.agent_location_id, from_location_id=from_location_id
        )
        return {
            "type": "room",
            "request_id": request_id,
            "world_graph": self._world_graph(),
            **room,
        }

    def _case_file_payload(self, request_id: str) -> dict[str, Any]:
        state = self.env.state
        victim = state.characters.get(state.victim_id)
        body_loc = state.locations.get(state.body_location_id or state.murder_location_id)
        suspects = []
        innocents = []
        for char in state.characters.values():
            if char.id == state.victim_id:
                continue
            entry = {
                "id": char.id,
                "name": char.full_name,
                "alive": bool(char.is_alive),
                "personality": getattr(char, "personality", "") or "",
                "build": char.physical_traits.build,
                "hair": char.physical_traits.hair,
                "hands": char.physical_traits.hands,
            }
            if CharacterRole.SUSPECT in char.roles:
                entry["motive"] = char.motive or ""
                suspects.append(entry)
            else:
                innocents.append(entry)
        suspects.sort(key=lambda c: c["name"])
        innocents.sort(key=lambda c: c["name"])

        evidence_total = sum(
            1 for ev in state.evidence.values() if not getattr(ev, "is_red_herring", False)
        )

        try:
            briefing = render_initial_briefing(self.env)
        except Exception:
            logger.exception("render_initial_briefing failed")
            briefing = ""

        return {
            "type": "case_file",
            "request_id": request_id,
            "briefing": briefing,
            "victim_name": victim.full_name if victim else "",
            "body_location_name": body_loc.name if body_loc else "",
            "suspects": suspects,
            "innocents": innocents,
            "evidence_found": len(self.env._discovered_evidence),
            "evidence_total": evidence_total,
            "actions_taken": self.env.actions_taken,
            "budget_remaining": self.env.budget_remaining,
        }

    @staticmethod
    def _action_result_payload(
        request_id: str, result: Any
    ) -> dict[str, Any]:
        return {
            "type": "action_result",
            "request_id": request_id,
            "success": bool(result.success),
            "observation": result.observation,
            "evidence_found": list(result.evidence_found),
        }

    # ---------------------------------------------------------------- network loop

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

        try:
            if msg_type == "ping":
                await ws.send(
                    json.dumps({"type": "pong", "request_id": request_id})
                )

            elif msg_type == "get_current_room":
                await ws.send(json.dumps(self._room_payload(request_id)))

            elif msg_type == "move_to_room":
                await self._handle_move(ws, request_id, msg)

            elif msg_type == "examine_object":
                obj_name = str(msg.get("object_name", ""))
                result = self.env.step(AgentAction.EXAMINE_OBJECT, object_name=obj_name)
                await ws.send(
                    json.dumps(self._action_result_payload(request_id, result))
                )

            elif msg_type == "talk_to":
                char_name = str(msg.get("character_name", ""))
                question = str(msg.get("question", ""))
                # Run blocking LLM call in a thread so we don't stall other
                # clients (and the asyncio event loop).
                result = await asyncio.to_thread(
                    self.env.step,
                    AgentAction.TALK_TO,
                    character_name=char_name,
                    question=question,
                )
                await ws.send(
                    json.dumps(self._action_result_payload(request_id, result))
                )

            elif msg_type == "take_object":
                obj_name = str(msg.get("object_name", ""))
                result = self.env.step(AgentAction.TAKE_OBJECT, object_name=obj_name)
                await ws.send(
                    json.dumps(self._action_result_payload(request_id, result))
                )

            elif msg_type == "inventory":
                items = []
                for ev_id in self.env.agent_inventory:
                    ev = self.env.state.evidence.get(ev_id)
                    if ev is None:
                        continue
                    items.append(
                        {
                            "id": ev.id,
                            "name": getattr(ev, "name", ev.id),
                            "description": getattr(ev, "description", ""),
                        }
                    )
                await ws.send(
                    json.dumps(
                        {
                            "type": "inventory",
                            "request_id": request_id,
                            "items": items,
                        }
                    )
                )

            elif msg_type == "accuse":
                suspect = str(msg.get("suspect_name", ""))
                weapon = str(msg.get("weapon_name", ""))
                location = str(msg.get("location_name", ""))
                result = self.env.step(
                    AgentAction.ACCUSE,
                    suspect_name=suspect,
                    weapon_name=weapon,
                    location_name=location,
                )
                payload = {
                    "type": "accusation_result",
                    "request_id": request_id,
                    "correct": bool(self.env.accusation_correct),
                    "observation": result.observation,
                    "details": result.details or {},
                }
                await ws.send(json.dumps(payload))

            elif msg_type == "world_graph":
                await ws.send(
                    json.dumps(
                        {
                            "type": "world_graph",
                            "request_id": request_id,
                            "locations": self._world_graph(),
                        }
                    )
                )

            elif msg_type == "case_file":
                await ws.send(json.dumps(self._case_file_payload(request_id)))

            elif msg_type == "new_game":
                # Regenerate the world with the parameters from Godot's
                # start form. seed=0 / missing -> randomise.
                raw_seed = msg.get("seed")
                if raw_seed in (None, "", 0):
                    import secrets
                    seed_int = secrets.randbits(31)
                else:
                    try:
                        seed_int = int(raw_seed)
                    except (TypeError, ValueError):
                        seed_int = secrets.randbits(31)
                api_key = msg.get("openai_api_key") or None
                complexity_name = str(msg.get("complexity", self.complexity.name))
                try:
                    complexity = ComplexityLevel[complexity_name]
                except KeyError:
                    complexity = self.complexity
                self.new_game(seed=seed_int, complexity=complexity, api_key=api_key)
                await ws.send(json.dumps(self._room_payload(request_id)))

            else:
                await ws.send(
                    json.dumps(
                        {
                            "type": "error",
                            "request_id": request_id,
                            "error": f"unknown message type: {msg_type!r}",
                        }
                    )
                )
        except Exception as exc:
            logger.exception("dispatch failed for type=%r", msg_type)
            await ws.send(
                json.dumps(
                    {
                        "type": "error",
                        "request_id": request_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            )

    async def _handle_move(self, ws: Any, request_id: str, msg: dict[str, Any]) -> None:
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
        self.visited.add(self.env.agent_location_id)
        logger.info("agent moved %s -> %s", prev, self.env.agent_location_id)
        await ws.send(
            json.dumps(self._room_payload(request_id, from_location_id=prev))
        )


async def _run(host: str, port: int, server: GodotServer) -> None:
    async with websockets.serve(server.handle_client, host, port) as ws_server:
        actual_port = port
        try:
            sockets = ws_server.sockets  # type: ignore[attr-defined]
            if sockets:
                actual_port = sockets[0].getsockname()[1]
        except Exception:
            pass

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
        description="MysteryArena Godot WebSocket server",
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
    parser.add_argument(
        "--npc-model",
        default="gpt-4o-mini",
        help="OpenAI-compatible model id for NPC dialogue (used only if "
             "OPENAI_API_KEY is set; otherwise template fallback).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    server = GodotServer(args.seed, ComplexityLevel[args.complexity], args.npc_model)
    try:
        asyncio.run(_run(args.host, args.port, server))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
