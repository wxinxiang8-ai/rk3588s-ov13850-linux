# RK3588S OV13850 Linux 6.18 Bring-up

This repository contains the 44-patch series for OV13850 bring-up on Orange Pi 5 Pro (RK3588S).

Base: Linux v6.18.39
Base commit: f89c296854b755a66657065c35b05406fc18264
Final commit: 6f899b4798bc

Apply the patches to the matching Linux v6.18.39 base tree:

    git am patches/*.patch

The series covers RK3588S DCPHY, CSI-2, VICAP/RKCIF, Orange Pi 5 Pro DTS, OV13850 binding and driver, runtime PM, timing metadata, and frame interval enumeration.

## Userspace bring-up

After booting the patched Linux 6.18 kernel, configure the tested
2112x1568 RAW10 path:

    chmod +x scripts/configure-ov13850-2112x1568.sh
    ./scripts/configure-ov13850-2112x1568.sh

Expected capture format:

    BG10 2112x1568
    field: none
    bytesperline: 4224
    sizeimage: 6623232

The diagnostic preview server can then be started with:

    python3 scripts/raw_mjpeg_server.py \
      --device /dev/video0 --width 2112 --height 1568 \
      --port 8080 --bayer bggr --fast --max-fps 10 \
      --gamma 0.55 --jpeg-encoder software

The preview path is a userspace RAW Bayer diagnostic preview. It is not
an RKISP2/NV12 production image-quality pipeline.

The tested board is Orange Pi 5 Pro (RK3588S), with OV13850 on I2C
address 0x10 and the tested Linux kernel base is v6.18.39.
