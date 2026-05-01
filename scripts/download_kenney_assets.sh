#!/usr/bin/env bash
#
# Set up Kenney CC0 3D assets for the Godot game.
#
# Two modes:
#   1. Manual extraction: pass paths to .zip files you've already downloaded
#      from kenney.nl.
#   2. Sanity check: with no args, lists which assets are present / missing.
#
# Why no auto-download? Kenney's CDN URLs include rotating cache hashes that
# break frequently and aren't a stable contract. A 30-second manual download
# is more robust than a fragile script that fails three months from now.
#
# Usage:
#
#   # 1. Visit https://kenney.nl/assets and download (free / CC0):
#   #      - Furniture Kit            (~5 MB)
#   #      - Mini Characters Kit      (~1 MB)        # or Character Kit
#   #      - Blaster Kit / Weapon Pack (~3 MB)
#   #
#   # 2. Each downloads as a .zip file. Then:
#
#   ./scripts/download_kenney_assets.sh ~/Downloads/kenney_*.zip
#
#   # The script extracts model files (.glb / .gltf / .obj) into
#   # game/assets/kenney/, organised by kit.
#
# After extraction, edit game/config/asset_map.json if your filenames don't
# match the defaults — the room builder uses substring matching, so partial
# matches are fine.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS_DIR="$REPO_ROOT/game/assets/kenney"
mkdir -p "$ASSETS_DIR"

print_status() {
    echo
    echo "Asset directory: $ASSETS_DIR"
    local count
    count=$(find "$ASSETS_DIR" -type f \( -name '*.glb' -o -name '*.gltf' -o -name '*.obj' \) 2>/dev/null | wc -l | tr -d ' ')
    echo "Model files present: $count"
    if [ "$count" = "0" ]; then
        echo
        echo "Nothing detected yet. The game will use colored cubes/capsules until"
        echo "you provide real meshes."
    else
        echo
        echo "Sample files:"
        find "$ASSETS_DIR" -type f \( -name '*.glb' -o -name '*.gltf' \) 2>/dev/null | head -10 | sed 's|.*kenney/||; s|^|  |'
    fi
}

if [ $# -eq 0 ]; then
    cat <<'EOF'
No zip files provided. Two options:

  A) Quickest: keep the colored-cube/capsule fallback. The game is fully
     playable without meshes.

  B) For real Kenney models:
     1. Visit https://kenney.nl/assets
     2. Download (free / CC0):
          - Furniture Kit
          - Mini Characters Kit (or Character Kit)
          - Blaster Kit (or Weapon Pack)
     3. Re-run this script with the .zip paths:
          ./scripts/download_kenney_assets.sh /path/to/*.zip
EOF
    print_status
    exit 0
fi

require_unzip() {
    if ! command -v unzip >/dev/null 2>&1; then
        echo "ERROR: 'unzip' not found. Install it or extract the zips manually" >&2
        echo "       into $ASSETS_DIR" >&2
        exit 1
    fi
}

require_unzip

for zip in "$@"; do
    if [ ! -f "$zip" ]; then
        echo "WARNING: not a file, skipping: $zip" >&2
        continue
    fi

    base="$(basename "$zip")"
    name_lower="$(echo "$base" | tr '[:upper:]' '[:lower:]')"

    case "$name_lower" in
        *furniture*)  subdir="furniture" ;;
        *character*|*mini*)  subdir="characters" ;;
        *weapon*|*blaster*)  subdir="weapons" ;;
        *)            subdir="other" ;;
    esac

    dest="$ASSETS_DIR/$subdir"
    mkdir -p "$dest"

    echo "Extracting $base -> $dest/"

    # Pull only model files; skip licenses, READMEs, etc.
    # -j flattens directory structure so all .glb files end up at the top
    # level of the kit subdirectory (room_builder paths assume this).
    unzip -j -o "$zip" \
        '*.glb' '*.gltf' '*.obj' '*.bin' '*.png' \
        -d "$dest" 2>/dev/null || true
done

print_status
echo
echo "Done. Restart Godot to pick up the new assets."
