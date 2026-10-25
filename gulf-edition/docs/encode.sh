#!/usr/bin/env bash
# raw recording -> docs/demo.mp4 (H.264, muted), docs/demo.gif, docs/poster.png (final frame: donut)
set -e; cd "$(dirname "$0")"
RAW=$(ls _raw/*.webm | head -1)
ffmpeg -y -v error -ss 0.7 -i "$RAW" -an -c:v libx264 -pix_fmt yuv420p -crf 28 -preset slow -movflags +faststart demo.mp4
ffmpeg -y -v error -ss 0.7 -i "$RAW" -vf "fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" demo.gif
cp _raw/final3.png poster.png
