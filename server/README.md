# MysteryArena — Server-streamed web build

Real-time WASD play in a browser. The pygame game runs **headless on a server**
and streams frames over a WebSocket; the browser renders frames on a
`<canvas>` and forwards keystrokes back. No Pyodide cold start, no `micropip`
drama, full-speed pygame.

## Local preview

```bash
uv sync --extra server
uv run python server/server.py
# open http://localhost:7860
```

The lobby asks for difficulty / seed / OpenAI key, then you're in the game.

## Deploy to Hugging Face Spaces (free, public URL)

1. Create a new Space at <https://huggingface.co/new-space>
   - **SDK**: Docker
   - **Visibility**: Public (or private, your call)
2. Push this repo to the Space's git remote:
   ```bash
   git remote add space https://huggingface.co/spaces/<your-user>/mystery-arena
   git push space master
   ```
   The Space will build the Dockerfile and start the server on port 7860.
3. Share `https://huggingface.co/spaces/<your-user>/mystery-arena` with friends.

## How key handling works

| Browser key | pygame key | Action |
|---|---|---|
| `W A S D` | `K_w K_a K_s K_d` | Walk |
| `E` | `K_e` | Interact |
| `1` `2` `3` | `K_1 K_2 K_3` | Switch sidebar tab |
| `Arrow ↑/↓`, `PgUp/PgDn` | `K_UP K_DOWN K_PAGEUP K_PAGEDOWN` | Scroll active tab |
| `Esc` | `K_ESCAPE` | Open menu / close modal |
| Printable chars | passed through | Type into NPC question / accusation modals |

## Multi-session

Each WebSocket connection gets its own isolated `MysteryEnvironment` and
`MysteryGame` (headless mode). Sessions auto-expire after 30 minutes idle.
Memory per session is small (the env is just dataclasses + a 600×400 surface),
so the free HF Space tier comfortably handles ~10 concurrent players.

## API key handling

- The lobby form sends the OpenAI key once, over POST, to the server.
- The server attaches it to that session's `NPCResponder` and never persists
  it.
- When the session ends (disconnect / timeout), the key is dropped with the
  rest of the session state.
- We recommend you tell friends to **rotate the key after they're done playing**
  — defence in depth.

## Limitations

- ~30 FPS frame stream uses ~100 KB/s per active player. Not free, but free
  tier of HF Spaces is generous enough for a small group.
- HF Spaces free tier sleeps after 48 hrs of inactivity; first visitor wakes
  it (~10 sec).
- No mobile keyboard support — desktop browsers only.
