#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_svg="$root/assets/tokyo_notes_icon.svg"
iconset="$root/packaging/macos/TokyoNotes.iconset"
output="$root/packaging/macos/TokyoNotes.icns"

command -v rsvg-convert >/dev/null 2>&1 || {
  echo "rsvg-convert is required. Install librsvg first." >&2
  exit 1
}

rm -rf "$iconset"
mkdir -p "$iconset"

rsvg-convert -w 16 -h 16 "$source_svg" -o "$iconset/icon_16x16.png"
rsvg-convert -w 32 -h 32 "$source_svg" -o "$iconset/icon_16x16@2x.png"
rsvg-convert -w 32 -h 32 "$source_svg" -o "$iconset/icon_32x32.png"
rsvg-convert -w 64 -h 64 "$source_svg" -o "$iconset/icon_32x32@2x.png"
rsvg-convert -w 128 -h 128 "$source_svg" -o "$iconset/icon_128x128.png"
rsvg-convert -w 256 -h 256 "$source_svg" -o "$iconset/icon_128x128@2x.png"
rsvg-convert -w 256 -h 256 "$source_svg" -o "$iconset/icon_256x256.png"
rsvg-convert -w 512 -h 512 "$source_svg" -o "$iconset/icon_256x256@2x.png"
rsvg-convert -w 512 -h 512 "$source_svg" -o "$iconset/icon_512x512.png"
rsvg-convert -w 1024 -h 1024 "$source_svg" -o "$iconset/icon_512x512@2x.png"

iconutil -c icns "$iconset" -o "$output"
rm -rf "$iconset"

echo "$output"
