#!/usr/bin/env bash
# usage: render.sh [-ql|-qh|-qk] [scene_key ...]   default: all scenes at 1080p60 (-qh)
# Output: renders/<quality>/<scene>.mp4 and renders/<quality>/sarjy_presentation.mp4 (no audio).
set -euo pipefail
cd "$(dirname "$0")"
Q=-qh; [[ ${1:-} == -q* ]] && { Q=$1; shift; }
declare -A CLS=( [s01_title]=S01Title [s02_assignment]=S02Assignment [s03_why_memory]=S03WhyMemory
  [s04_why_voice_ui]=S04WhyVoiceUI [s05_architecture]=S05Architecture [s06_pipeline]=S06Pipeline
  [s07_memory]=S07Memory [s08_ui_commands]=S08UICommands [s09_tools]=S09Tools
  [s10_personas]=S10Personas [s11_multi_agent]=S11MultiAgent [s12_closing]=S12Closing )
ORDER=(s01_title s02_assignment s03_why_memory s04_why_voice_ui s05_architecture s06_pipeline s07_memory s08_ui_commands s09_tools s10_personas s11_multi_agent s12_closing)
SCENES=("$@"); [[ ${#SCENES[@]} -eq 0 ]] && SCENES=("${ORDER[@]}")
case $Q in -ql) RES=480p15;; -qm) RES=720p30;; -qh) RES=1080p60;; -qk) RES=2160p60;; esac
OUT=renders/${RES}; mkdir -p "$OUT"
export TMPDIR=$PWD/.tmp; mkdir -p "$TMPDIR"
for s in "${SCENES[@]}"; do
  .venv/bin/manim $Q --media_dir media scenes/$s.py ${CLS[$s]} >/dev/null 2>&1 || .venv/bin/manim $Q --media_dir media scenes/$s.py ${CLS[$s]}
  cp media/videos/$s/$RES/${CLS[$s]}.mp4 "$OUT/$s.mp4"
done
# Concatenate every scene present, in order (re-encode-free; identical codec settings).
: > "$OUT/list.txt"
for s in "${ORDER[@]}"; do [[ -f $OUT/$s.mp4 ]] && echo "file '$PWD/$OUT/$s.mp4'" >> "$OUT/list.txt"; done
ffmpeg -v error -y -f concat -safe 0 -i "$OUT/list.txt" -c copy -an "$OUT/sarjy_presentation.mp4"
echo "wrote $OUT/sarjy_presentation.mp4"
