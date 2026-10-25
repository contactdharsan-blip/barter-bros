#!/bin/sh
# Build the static site for GitHub Pages: the web app, which falls back to the recorded demo
# (web/public/demo-replay.json, from scripts/record_demo.py) when no server answers, plus the video.
#
#   scripts/build_pages.sh <outdir>
#
# Publish <outdir> as the Pages root (e.g. the gh-pages branch). Asset URLs are relative, so it
# also works under a subpath such as /barter-bros/.
set -eu

if [ $# -ne 1 ]; then
  echo "usage: scripts/build_pages.sh <outdir>" >&2
  exit 2
fi
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$1

npm --prefix "$ROOT/web" run build
mkdir -p "$OUT"
cp -R "$ROOT/web/dist/." "$OUT/"
if [ -f "$ROOT/docs/demo.mp4" ]; then
  cp "$ROOT/docs/demo.mp4" "$OUT/demo.mp4"
else
  echo "build_pages: docs/demo.mp4 is missing; the site is built without the video" >&2
fi
touch "$OUT/.nojekyll"
# The app asks ./api/health whether a server is there; answer "no" so it plays the recording
# without a 404 in the console.
mkdir -p "$OUT/api"
printf '{"ok": false, "mode": "recorded"}\n' > "$OUT/api/health"
echo "build_pages: site in $OUT"
