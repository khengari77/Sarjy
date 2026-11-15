#!/usr/bin/env bash
# usage: lastframe.sh key...  -> media/frames/last_<key>.png (frame at 97% of the -ql render)
for k in "$@"; do
  f=$(ls media/videos/$k/480p15/*.mp4 | head -1)
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
  ffmpeg -v error -y -ss $(python3 -c "print($d*0.97)") -i "$f" -frames:v 1 media/frames/last_$k.png
done
