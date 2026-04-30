# MysteryArena — Web build (pygbag)

Package the pygame game as a static WebAssembly site so anyone can play it
in their browser, no install required.

## How it works

`pygbag` bundles the Python source + a Pyodide runtime + pygame-on-WASM
into a folder of static files. Friends visit your URL, the page boots
Python in their browser, the game starts. Each visitor enters their own
OpenAI API key on first launch (saved to their browser's `localStorage`,
never sent anywhere except OpenAI's API).

## Build

The pygbag entrypoint lives at the **project root** (`main.py`) — not in
this `web/` folder — because pygbag only bundles files beneath the
entrypoint's directory. Run from the project root:

```bash
# 1. Install the optional 'web' deps (pygbag)
uv sync --extra web

# 2. Build (produces build/web/ — that's the folder you deploy)
uv run pygbag --build main.py
```

## Local preview

```bash
uv run pygbag main.py
# Opens http://localhost:8000 — auto-reloads on edits
```

## Deploy

The output of `--build` is plain static HTML/JS/WASM. Deploy with whichever
host you prefer:

### GitHub Pages
```bash
# from the repo root
git checkout -b gh-pages
cp -r build/web/* .
git add . && git commit -m "Deploy MysteryArena web build"
git push origin gh-pages
# Settings → Pages → branch: gh-pages → save.  URL appears within a minute.
```

### Vercel / Netlify / Cloudflare Pages
Point the project at the repo, set the build output directory to
`build/web/`. No build command needed — pygbag has already done it.

### itch.io (HTML5 game)
Zip the contents of `build/web/` and upload as an HTML game.

## Sharing with friends

Send the URL. They open it; the game boots in 5–15 seconds (Pyodide cold
start). On first launch they see a modal asking for their OpenAI API
key — they paste it, and the NPC interviews route through their key and
their billing. If they hit `ESC` instead, NPCs fall back to deterministic
canned responses (the case is still solvable).

The key lives in their browser's `localStorage` only; you (the host) never
see it, and it never leaves their browser except as part of an HTTPS
request to `api.openai.com`.

## Known limitations

- **Cold-start time**: ~5–15 seconds the first visit (Pyodide download).
  Repeat visits load from browser cache.
- **No emoji font**: WebAssembly Pyodide doesn't ship Noto Color Emoji,
  so the game uses the procedural humanoid sprites (head + body + arms,
  with deterministic colour variety per NPC) instead of emoji glyphs.
- **HTTP latency stalls a frame**: when you talk to an NPC, the OpenAI
  call (1–2s) blocks the main loop, so the game freezes briefly during
  the API call. The browser tab stays responsive (closeable). For now
  this is OK for a turn-based detective game; if it bothers you, the
  next step is to switch the responder to async + show a "thinking…"
  indicator.
- **Mobile**: keyboard input on phones/tablets isn't great; works
  best on desktop browsers.
- **API key safety**: friends supply their own key. There is *no*
  scenario where you should embed your own key in the build — anyone
  who views source can read it.
