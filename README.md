# RK3588S OV13850 Linux 6.18 Bring-up

This repository contains the 44-patch series for OV13850 bring-up on Orange Pi 5 Pro (RK3588S).

Base: Linux v6.18.39
Base commit: f89c296854b755a66657065c35b05406fc18264
Final commit: 6f899b4798bc

Apply the patches to the matching Linux v6.18.39 base tree:

    git am patches/*.patch

The series covers RK3588S DCPHY, CSI-2, VICAP/RKCIF, Orange Pi 5 Pro DTS, OV13850 binding and driver, runtime PM, timing metadata, and frame interval enumeration.
