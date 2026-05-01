"""Thin async WebSocket client for the Godot M1 server.

Used by:
  * The smoke test below (a Python developer verifying the server works
    without launching the Godot binary).
  * The VLM benchmark code in later milestones (M3+) when the agent runs
    against an externally-launched server.

The Godot binary itself does NOT use this module — it speaks WebSocket
natively from GDScript via WebSocketPeer.

M1 protocol coverage: get_current_room, ping. See ``server/godot_server.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from typing import Any

import websockets


class GodotClient:
    def __init__(self, url: str = "ws://127.0.0.1:7777") -> None:
        self.url = url
        self._ws: Any = None

    async def __aenter__(self) -> "GodotClient":
        self._ws = await websockets.connect(self.url)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _request(self, msg_type: str) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("not connected; use `async with GodotClient(...)`")
        request_id = uuid.uuid4().hex[:8]
        await self._ws.send(json.dumps({"type": msg_type, "request_id": request_id}))
        while True:
            raw = await self._ws.recv()
            msg = json.loads(raw)
            if msg.get("request_id") != request_id:
                continue
            if msg.get("type") == "error":
                raise RuntimeError(f"server error: {msg.get('error')}")
            return msg

    async def get_current_room(self) -> dict[str, Any]:
        return await self._request("get_current_room")

    async def ping(self) -> dict[str, Any]:
        return await self._request("ping")


async def _smoke(url: str) -> None:
    async with GodotClient(url) as client:
        await client.ping()
        room = await client.get_current_room()
        print(f"connected: {url}")
        print(f"room:      {room['name']} ({room['room_id']})")
        print(f"size:      {room['width']} x {room['height']}")
        print(f"doors:     {len(room['doors'])}")
        for d in room["doors"]:
            print(f"  - {d['wall']:>6s}  ->  {d['leads_to_name']}")
        spawn = room["spawn"]
        print(f"spawn:     ({spawn['x']:.1f}, {spawn['y']:.1f}) facing {spawn['facing_deg']:.0f} deg")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default=os.environ.get("MYSTERY_SERVER_URL", "ws://127.0.0.1:7777"),
    )
    args = parser.parse_args()
    asyncio.run(_smoke(args.url))


if __name__ == "__main__":
    main()
