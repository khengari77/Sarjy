#!/usr/bin/env bash
# usage: render.sh [-ql|-qm|-qh|-qk] [scene_key ...]   default: all scenes at 1080p60 (-qh)
# Output: renders/<res>/<scene>.mp4 and renders/<res>/sarjy_presentation.mp4 (no audio).
# manim is single-threaded, so scenes render in parallel (JOBS=3 by default).
set -euo pipefail
cd "$(dirname "$0")"
Q=-qh; [[ ${1:-} == -q* ]] && { Q=$1; shift; }
ORDER=(s01_title s02_assignment s03_why_memory s04_why_voice_ui s05_architecture s06_pipeline s07_memory s08_ui_commands s09_tools s10_personas s11_multi_agent s12_closing)
SCENES=("$@"); [[ ${#SCENES[@]} -eq 0 ]] && SCENES=("${ORDER[@]}")
case $Q in -ql) RES=480p15;; -qm) RES=720p30;; -qh) RES=1080p60;; -qk) RES=2160p60;; esac
OUT=renders/$RES; mkdir -p "$OUT" media
export TMPDIR=$PWD/.tmp; mkdir -p "$TMPDIR"
export Q RES OUT
render_one() {
  local s=$1 cls
  cls=$(grep -h "^class " "scenes/$s.py" | head -1 | sed 's/class \([A-Za-z0-9]*\).*/\1/')
  .venv/bin/manim $Q --media_dir media "scenes/$s.py" "$cls" >"media/$s.log" 2>&1 || { tail -20 "media/$s.log"; return 1; }
  cp "media/videos/$s/$RES/$cls.mp4" "$OUT/$s.mp4"
  echo "done $s"
}
export -f render_one
printf '%s\n' "${SCENES[@]}" | xargs -P "${JOBS:-3}" -I{} bash -c 'render_one {}'
# Concatenate every scene present, in order (stream copy; identical encoder settings).
: > "$OUT/list.txt"
for s in "${ORDER[@]}"; do [[ -f $OUT/$s.mp4 ]] && echo "file '$PWD/$OUT/$s.mp4'" >> "$OUT/list.txt"; done
ffmpeg -v error -y -f concat -safe 0 -i "$OUT/list.txt" -c copy -an "$OUT/sarjy_presentation.mp4"
echo "wrote $OUT/sarjy_presentation.mp4"
