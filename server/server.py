"""FastAPI WebSocket server that streams pygame frames to a browser canvas.

Architecture
------------
* Each visitor opens the lobby (`/`), picks difficulty/seed, optionally pastes
  their OpenAI key, and clicks Start.
* The server creates a fresh `MysteryEnvironment` + headless `MysteryGame`,
  assigns a session id, and redirects to `/game/<session_id>`.
* The game page opens a WebSocket to `/ws/<session_id>`. The server runs a
  per-session asyncio task at ~30 FPS: tick the game, encode the surface as
  PNG, push to client. The client sends keyboard events back as JSON.

Sessions are kept in process memory and time out after 30 minutes idle.
Each session is fully isolated (its own env, its own game, its own queue).
"""
from __future__ import annotations

import asyncio
import base64
import os
import random
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Headless pygame (no display required)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

# Make the parent package importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame  # noqa: E402
from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from mystery_world import COMPLEXITY_PRESETS, ComplexityLevel  # noqa: E402
from mystery_world.generator import generate_mystery  # noqa: E402
from mystery_world.npc_responder import NPCResponder  # noqa: E402
from mystery_world.renderer.game import MysteryGame  # noqa: E402
from mystery_world.world import MysteryEnvironment  # noqa: E402

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

SESSION_TIMEOUT_SECONDS = 30 * 60   # 30 minutes idle timeout
TARGET_FPS = 30


@dataclass
class Session:
    id: str
    game: MysteryGame
    seed: int
    level: str
    api_key_provided: bool
    last_activity: float = field(default_factory=time.time)
    closed: bool = False

    def touch(self) -> None:
        self.last_activity = time.time()

    def is_stale(self) -> bool:
        return (time.time() - self.last_activity) > SESSION_TIMEOUT_SECONDS


sessions: dict[str, Session] = {}


def _create_session(level_name: str, seed: int | None, api_key: str | None) -> Session:
    level = ComplexityLevel[level_name.upper()]
    config = COMPLEXITY_PRESETS[level]
    if seed is None:
        seed = random.randint(0, 999_999)
    world = generate_mystery(config, seed)
    env = MysteryEnvironment(world)
    if api_key:
        env.set_npc_responder(NPCResponder(
            base_url=None, model="gpt-4o-mini", api_key=api_key,
        ))
    game = MysteryGame(env, headless=True)
    sid = uuid.uuid4().hex[:12]
    sess = Session(
        id=sid, game=game, seed=seed,
        level=level.name, api_key_provided=bool(api_key),
    )
    sessions[sid] = sess
    return sess


def _cleanup_stale_sessions() -> None:
    stale = [sid for sid, s in sessions.items() if s.is_stale() or s.closed]
    for sid in stale:
        sessions.pop(sid, None)


# ---------------------------------------------------------------------------
# App + lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # background reaper
    async def _reaper() -> None:
        while True:
            await asyncio.sleep(60)
            _cleanup_stale_sessions()

    task = asyncio.create_task(_reaper())
    yield
    task.cancel()


app = FastAPI(title="MysteryArena", lifespan=lifespan)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(Exception)
async def _log_unhandled(request: Request, exc: Exception) -> HTMLResponse:
    """Print full tracebacks to stdout so HF's container-log tab shows them."""
    import traceback as _tb
    tb_text = _tb.format_exc()
    print(f"[error] {request.url.path} raised {type(exc).__name__}: {exc}", flush=True)
    print(tb_text, flush=True)
    return HTMLResponse(
        f"<pre style='font:14px monospace;padding:1rem;color:#f44'>"
        f"500 internal error on {request.url.path}\n\n{tb_text}</pre>",
        status_code=500,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def lobby(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "lobby.html",
        {
            "request": request,
            "levels": [lvl.name for lvl in ComplexityLevel],
        },
    )


@app.post("/start")
async def start(
    level: str = Form(...),
    seed: str = Form(""),
    api_key: str = Form(""),
) -> RedirectResponse:
    seed_int: int | None = None
    if seed.strip():
        try:
            seed_int = int(seed.strip())
        except ValueError:
            seed_int = None
    sess = _create_session(level, seed_int, api_key.strip() or None)
    return RedirectResponse(url=f"/game/{sess.id}", status_code=303)


@app.get("/game/{session_id}", response_class=HTMLResponse)
async def game_page(request: Request, session_id: str) -> HTMLResponse:
    sess = sessions.get(session_id)
    if sess is None:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(
        "game.html",
        {
            "request": request,
            "session_id": session_id,
            "level": sess.level,
            "seed": sess.seed,
            "win_w": sess.game.win_w,
            "win_h": sess.game.win_h,
            "ai": "ChatGPT" if sess.api_key_provided else "deterministic",
        },
    )


# ---------------------------------------------------------------------------
# WebSocket — frame stream out, key events in
# ---------------------------------------------------------------------------

# Mapping browser-side key names → pygame key codes.
_KEY_MAP: dict[str, int] = {
    "w": pygame.K_w, "a": pygame.K_a, "s": pygame.K_s, "d": pygame.K_d,
    "e": pygame.K_e,
    "1": pygame.K_1, "2": pygame.K_2, "3": pygame.K_3,
    "Enter": pygame.K_RETURN,
    "Escape": pygame.K_ESCAPE,
    "Backspace": pygame.K_BACKSPACE,
    "ArrowUp": pygame.K_UP, "ArrowDown": pygame.K_DOWN,
    "ArrowLeft": pygame.K_LEFT, "ArrowRight": pygame.K_RIGHT,
    "PageUp": pygame.K_PAGEUP, "PageDown": pygame.K_PAGEDOWN,
    " ": pygame.K_SPACE,
}


def _resolve_key(name: str) -> int | None:
    if name in _KEY_MAP:
        return _KEY_MAP[name]
    if len(name) == 1:
        # printable ASCII fallback
        return _KEY_MAP.get(name.lower(), ord(name) if name.isprintable() else None)
    return None


@app.websocket("/ws/{session_id}")
async def play_socket(ws: WebSocket, session_id: str) -> None:
    sess = sessions.get(session_id)
    if sess is None:
        await ws.close(code=4404)
        return
    await ws.accept()
    sess.touch()
    game = sess.game
    frame_interval = 1.0 / TARGET_FPS

    async def _input_pump() -> None:
        try:
            while not sess.closed:
                msg = await ws.receive_json()
                sess.touch()
                t = msg.get("type")
                if t == "keydown":
                    key = _resolve_key(msg.get("key", ""))
                    if key is not None:
                        unicode = msg.get("unicode", "")
                        # For text-input modals, unicode comes from JS event.key;
                        # our injection translates printable single chars to unicode.
                        if not unicode and len(msg.get("key", "")) == 1 and msg["key"].isprintable():
                            unicode = msg["key"]
                        game.inject_key_down(key, unicode=unicode)
                elif t == "keyup":
                    key = _resolve_key(msg.get("key", ""))
                    if key is not None:
                        game.inject_key_up(key)
                elif t == "ping":
                    pass
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    input_task = asyncio.create_task(_input_pump())

    try:
        while not sess.closed:
            t0 = time.monotonic()
            still_running = game._tick()
            if not still_running:
                # Send one last frame then break
                pass
            png = game.get_frame_png()
            await ws.send_bytes(png)
            sess.touch()
            if not still_running:
                break
            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0.0, frame_interval - elapsed))
    except WebSocketDisconnect:
        pass
    finally:
        sess.closed = True
        input_task.cancel()


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"sessions": len(sessions), "ok": True}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 7860))   # HF Spaces convention
    # Explicit startup logging so HF's container-log panel shows *something*
    # even if uvicorn's own logs are buffered weirdly.
    print(f"[startup] MysteryArena server booting on 0.0.0.0:{port}", flush=True)
    print(f"[startup] templates dir: {TEMPLATES_DIR}  exists={TEMPLATES_DIR.exists()}", flush=True)
    print(f"[startup] static dir:    {STATIC_DIR}  exists={STATIC_DIR.exists()}", flush=True)
    print(f"[startup] python={sys.version.split()[0]}, "
          f"pygame_video_driver={os.environ.get('SDL_VIDEODRIVER')}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
