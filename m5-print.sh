#!/usr/bin/env bash
# m5-print — ONE-SHOT autonomous print: STL → M5 G-code → print on AnkerMake M5.
# Chains the eufyMake Studio headless slicer (real M5 profiles) + ankerctl PPPP upload.
#
# Usage:   m5-print <model.stl> [jobname]
# Depends: AnkerMake/eufyMake Studio 3D installed at $EUFY_BIN, ankerctl repo at
#          $ANKERCTL_DIR with valid auth config (run `m5-setup.sh` first).
set -euo pipefail

# --- Config (override via env) ---
ANKERCTL_DIR="${ANKERCTL_DIR:-$HOME/Documents/3D Printing/ankerctl}"
EUFY_BIN="${EUFY_BIN:-/Applications/eufyMake Studio 3D.app/Contents/MacOS/eufyStudio}"

STL="${1:?usage: m5-print <model.stl> [jobname]}"
NAME="${2:-$(basename "$STL" .stl)}"
OUT="$(dirname "$STL")/${NAME}_final.gcode"

echo "==> [1/4] Slicing with real M5 profiles (eufyMake Studio headless CLI)..."
"$EUFY_BIN" --export-gcode \
  --bed-shape="0x0,235x0,235x235,0x235" --max-print-height=250 \
  --nozzle-diameter=0.4 --gcode-flavor=marlin2 \
  --layer-height=0.2 --first-layer-height=0.14 \
  --first-layer-temperature=230 --temperature=200 \
  --first-layer-bed-temperature=60 --bed-temperature=60 \
  --fill-density=15% --perimeters=2 \
  -o "$OUT" "$STL" >/dev/null 2>&1 || { echo "!! slice failed"; exit 1; }
# CLI writes <out>.gcode.tmp then renames on completion
[ -f "$OUT.tmp" ] && mv "$OUT.tmp" "$OUT"
# Safety: verify the slice landed on the real 235 mm M5 bed, not a silent 200 mm default
grep -q "bed_shape = 0x0,235x0,235x235,0x235" "$OUT" \
  || { echo "!! slice produced wrong bed (not M5) — refusing to send"; exit 1; }
echo "    sliced OK: $(du -h "$OUT" | cut -f1)"

echo "==> [2/4] Ensuring eufyMake Studio GUI is closed (frees the printer session)..."
if pgrep -f eufyStudio >/dev/null 2>&1; then
  pkill -f eufyStudio 2>/dev/null || true
  sleep 2
fi
echo "    clear"

echo "==> [3/4] Sending to printer + starting print (PPPP)..."
cd "$ANKERCTL_DIR"
env -u PYTHONPATH ./.venv/bin/python3 ankerctl.py pppp print-file "$OUT" 2>&1 | tail -2

echo ""
echo "DONE — job '$NAME' sent to printer. Verify: m5 status (live stream)"
echo "Unattended? Run the watchdog: m5-watch.py --auto-resume"
