# Hugging Face Spaces / generic Docker host for MysteryArena server.
# HF Spaces conventions: listen on port 7860, run as user 1000.
FROM python:3.13-slim

# SDL needs a small set of system libs even when running headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsdl2-2.0-0 \
        libsdl2-ttf-2.0-0 \
        libsdl2-image-2.0-0 \
        libsdl2-mixer-2.0-0 \
        libfreetype6 \
        libpng16-16 \
        libjpeg62-turbo \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Headless SDL — pygame renders to off-screen surfaces only.
ENV SDL_VIDEODRIVER=dummy \
    SDL_AUDIODRIVER=dummy \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR /app

# Install Python deps first for layer caching.
COPY pyproject.toml .
RUN pip install --no-cache-dir \
        "anthropic>=0.94.0" "networkx>=3.4" "numpy>=2.2.6" "openai>=2.31.0" \
        "pillow>=11.0.0" "pygame>=2.6.0" "pyyaml>=6.0.3" "structlog>=25.5.0" \
        "fastapi>=0.110.0" "uvicorn[standard]>=0.27.0" "jinja2>=3.1.0" \
        "python-multipart>=0.0.9"

# Project source
COPY mystery_world ./mystery_world
COPY agents ./agents
COPY server ./server

EXPOSE 7860
CMD ["python", "-u", "server/server.py"]
