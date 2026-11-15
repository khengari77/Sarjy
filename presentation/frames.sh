#!/usr/bin/env bash
# usage: frames.sh scene_key [n]  -> media/frames/<scene>.png montage of n frames (from the -ql render)
set -e
k=$1; n=${2:-6}
f=$(ls media/videos/$k/480p15/*.mp4 | head -1)
d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
mkdir -p media/frames
ffmpeg -v error -y -i "$f" -vf "fps=$n/$d,scale=640:-1,tile=3x$(( (n+2)/3 ))" -frames:v 1 media/frames/$k.png
echo "$k duration=$d"
