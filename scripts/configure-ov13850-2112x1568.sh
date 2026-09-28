#!/bin/sh
set -eu

MEDIA=/dev/media0
VIDEO=/dev/video0

test -e "$MEDIA"
test -e "$VIDEO"
command -v media-ctl >/dev/null 2>&1
command -v v4l2-ctl >/dev/null 2>&1

media-ctl -d "$MEDIA" -V '"dw-mipi-csi2rx fdd10000.csi":0 [fmt:SBGGR10_1X10/2112x1568 field:none]'
media-ctl -d "$MEDIA" -V '"dw-mipi-csi2rx fdd10000.csi":1 [fmt:SBGGR10_1X10/2112x1568 field:none]'
media-ctl -d "$MEDIA" -V '"rkcif-mipi0":0 [fmt:SBGGR10_1X10/2112x1568 field:none]'
media-ctl -d "$MEDIA" -V '"rkcif-mipi0":1 [fmt:SBGGR10_1X10/2112x1568 field:none]'

v4l2-ctl -d "$VIDEO" \
  --set-fmt-video=width=2112,height=1568,pixelformat=BG10 \
  --get-fmt-video

printf '%s\n' 'OV13850 2112x1568 BG10 format configured.'
