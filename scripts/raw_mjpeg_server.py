#!/usr/bin/env python3
"""Serve OV13850 BG10 frames as a diagnostic MJPEG stream.

The 6.18 RKCIF node exposes unpacked 10-bit samples in a 16-bit little-endian
container. This is a diagnostic software path, not ISP-quality rendering.
"""

from __future__ import annotations

import argparse
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np


BOUNDARY = b"frame"


class FrameStore:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.jpeg: bytes | None = None
        self.sequence = 0

    def publish(self, jpeg: bytes) -> None:
        with self.condition:
            self.jpeg = jpeg
            self.sequence += 1
            self.condition.notify_all()

    def wait_next(self, previous: int) -> tuple[int, bytes]:
        with self.condition:
            self.condition.wait_for(lambda: self.jpeg is not None and self.sequence > previous)
            assert self.jpeg is not None
            return self.sequence, self.jpeg


STORE = FrameStore()


class HardwareJpegEncoder:
    """Feed BGR previews to GStreamer and publish VEPU121 JPEG output."""

    def __init__(self, width: int, height: int, fps: int) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self.Gst = Gst
        self.duration = Gst.SECOND // fps
        self.frame_number = 0
        description = (
            "appsrc name=source is-live=true format=time block=true "
            f"caps=video/x-raw,format=BGR,width={width},height={height},framerate={fps}/1 "
            "! videoconvert ! video/x-raw,format=NV12 "
            "! v4l2jpegenc "
            "! appsink name=encoded emit-signals=true sync=false max-buffers=2 drop=true"
        )
        print("encoder:", description, flush=True)
        self.pipeline = Gst.parse_launch(description)
        self.source = self.pipeline.get_by_name("source")
        sink = self.pipeline.get_by_name("encoded")
        if self.source is None or sink is None:
            raise RuntimeError("GStreamer encoder source or sink is missing")
        sink.connect("new-sample", self._on_sample)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("VEPU121 GStreamer pipeline failed to start")

    def _on_sample(self, sink: object) -> object:
        sample = sink.emit("pull-sample")
        if sample is None:
            return self.Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        ok, mapping = buffer.map(self.Gst.MapFlags.READ)
        if not ok:
            return self.Gst.FlowReturn.ERROR
        try:
            STORE.publish(bytes(mapping.data))
        finally:
            buffer.unmap(mapping)
        return self.Gst.FlowReturn.OK

    def push(self, bgr: np.ndarray) -> None:
        payload = np.ascontiguousarray(bgr).tobytes()
        buffer = self.Gst.Buffer.new_allocate(None, len(payload), None)
        buffer.fill(0, payload)
        buffer.pts = self.frame_number * self.duration
        buffer.duration = self.duration
        self.frame_number += 1
        result = self.source.emit("push-buffer", buffer)
        if result != self.Gst.FlowReturn.OK:
            raise RuntimeError(f"VEPU121 push-buffer failed: {result}")
        bus = self.pipeline.get_bus()
        message = bus.pop_filtered(self.Gst.MessageType.ERROR)
        if message is not None:
            error, debug = message.parse_error()
            raise RuntimeError(f"VEPU121: {error}; {debug}")

    def close(self) -> None:
        self.pipeline.set_state(self.Gst.State.NULL)


def opencv_code(*names: str) -> int:
    for name in names:
        code = getattr(cv2, name, None)
        if code is not None:
            return code
    raise RuntimeError(f"OpenCV has no Bayer conversion code: {names}")


BAYER_CODES = {
    "bggr": opencv_code("COLOR_BayerBGGR2BGR", "COLOR_BayerBG2BGR"),
    "gbrg": opencv_code("COLOR_BayerGBRG2BGR", "COLOR_BayerGB2BGR"),
    "grbg": opencv_code("COLOR_BayerGRBG2BGR", "COLOR_BayerGR2BGR"),
    "rggb": opencv_code("COLOR_BayerRGGB2BGR", "COLOR_BayerRG2BGR"),
}


def block_demosaic(raw: np.ndarray, bayer: str) -> np.ndarray:
    """Convert each 2x2 Bayer block to one BGR pixel for fast preview."""
    p00 = raw[0::2, 0::2].astype(np.uint16)
    p01 = raw[0::2, 1::2].astype(np.uint16)
    p10 = raw[1::2, 0::2].astype(np.uint16)
    p11 = raw[1::2, 1::2].astype(np.uint16)
    green_a = p01
    green_b = p10
    if bayer == "bggr":
        blue, red = p00, p11
    elif bayer == "gbrg":
        blue, red = p01, p10
        green_a, green_b = p00, p11
    elif bayer == "grbg":
        blue, red = p10, p01
        green_a, green_b = p00, p11
    else:
        blue, red = p11, p00
    green = (green_a + green_b) // 2
    return np.dstack((blue, green, red))


def read_exact(stream: object, size: int) -> bytes:
    """Read one complete frame from a pipe that may return short chunks."""
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)  # type: ignore[attr-defined]
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class MjpegHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            body = (
                b"<!doctype html><meta name='viewport' content='width=device-width'>"
                b"<title>OV13850 6.18 RAW preview</title>"
                b"<img src='/stream.mjpg' style='max-width:100%;height:auto'>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path != "/stream.mjpg":
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        sequence = 0
        try:
            while True:
                sequence, jpeg = STORE.wait_next(sequence)
                self.wfile.write(b"--" + BOUNDARY + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.address_string()}] {fmt % args}")


def capture_loop(args: argparse.Namespace, encoder: HardwareJpegEncoder | None) -> None:
    frame_bytes = args.width * args.height * 2
    gamma_table = None
    if args.gamma != 1.0:
        values = np.arange(256, dtype=np.float32) / 255.0
        gamma_table = np.clip(np.power(values, args.gamma) * 255.0, 0, 255).astype(np.uint8)
    command = [
        args.v4l2_ctl,
        "-d",
        args.device,
        f"--set-fmt-video=width={args.width},height={args.height},pixelformat=BG10",
        "--stream-mmap",
        "--stream-count=0",
        "--stream-to=-",
    ]
    print("capture:", " ".join(command), flush=True)
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    assert process.stdout is not None
    processed = 0
    skipped = 0
    next_process = 0.0
    report_at = time.monotonic() + 5.0
    report_start = time.monotonic()
    try:
        while True:
            payload = read_exact(process.stdout, frame_bytes)
            if len(payload) != frame_bytes:
                raise RuntimeError(f"short frame: got {len(payload)} bytes, expected {frame_bytes}")
            now = time.monotonic()
            if args.max_fps > 0 and now < next_process:
                skipped += 1
                continue
            if args.max_fps > 0:
                next_process = now + (1.0 / args.max_fps)
            raw = np.frombuffer(payload, dtype="<u2").reshape(args.height, args.width) & 0x03FF
            if args.fast:
                bgr16 = block_demosaic(raw, args.bayer)
                sample = bgr16[::8, ::8].reshape(-1)
                black, white = np.percentile(sample, (args.black_percentile, args.white_percentile))
                if args.black_level is not None:
                    black = args.black_level
                if args.white_level is not None:
                    white = args.white_level
            else:
                bgr16 = None
                black, white = np.percentile(raw, (args.black_percentile, args.white_percentile))
            if white <= black:
                continue
            if bgr16 is None:
                mosaic = np.clip((raw.astype(np.float32) - black) * 255.0 / (white - black), 0, 255)
                bgr = cv2.cvtColor(mosaic.astype(np.uint8), BAYER_CODES[args.bayer])
            else:
                bgr = np.clip((bgr16.astype(np.float32) - black) * 255.0 / (white - black), 0, 255).astype(np.uint8)
            if not args.no_white_balance:
                means = bgr.mean(axis=(0, 1))
                gains = np.array(
                    [means[1] / max(means[0], 1e-6), 1.0, means[1] / max(means[2], 1e-6)],
                    dtype=np.float32,
                )
                gains = np.clip(gains, 0.5, 2.5)
                bgr = np.clip(bgr.astype(np.float32) * gains, 0, 255).astype(np.uint8)
            if gamma_table is not None:
                bgr = cv2.LUT(bgr, gamma_table)
            if encoder is None:
                ok, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, args.quality])
                if ok:
                    STORE.publish(encoded.tobytes())
            else:
                encoder.push(bgr)
            if encoder is not None or ok:
                processed += 1
                now = time.monotonic()
                if now >= report_at:
                    elapsed = now - report_start
                    print(
                        f"submitted_fps={processed / elapsed:.2f} jpeg_frames={STORE.sequence} "
                        f"skipped={skipped} levels={black:.0f}..{white:.0f} "
                        f"mode={'fast' if args.fast else 'full'} encoder={args.jpeg_encoder}",
                        flush=True,
                    )
                    report_at = now + 5.0
    except Exception as exc:
        print(f"capture stopped: {exc}", flush=True)
    finally:
        process.terminate()
        process.wait(timeout=3)
        if encoder is not None:
            encoder.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int, default=2112)
    parser.add_argument("--height", type=int, default=1568)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--quality", type=int, default=80)
    parser.add_argument("--black-percentile", type=float, default=0.1)
    parser.add_argument("--white-percentile", type=float, default=99.9)
    parser.add_argument("--fast", action="store_true", help="use 2x2 block demosaic at half resolution")
    parser.add_argument("--max-fps", type=float, default=0.0, help="limit processed preview frames; 0 means every frame")
    parser.add_argument("--black-level", type=float, help="override automatic black level in fast mode")
    parser.add_argument("--white-level", type=float, help="override automatic white level in fast mode")
    parser.add_argument("--bayer", choices=sorted(BAYER_CODES), default="bggr")
    parser.add_argument("--gamma", type=float, default=1.0, help="display gamma; below 1 brightens shadows")
    parser.add_argument("--jpeg-encoder", choices=("software", "vepu121"), default="software")
    parser.add_argument("--no-white-balance", action="store_true")
    parser.add_argument("--v4l2-ctl", default="v4l2-ctl")
    args = parser.parse_args()
    if args.gamma <= 0:
        parser.error("--gamma must be greater than zero")
    if args.jpeg_encoder == "vepu121" and args.max_fps <= 0:
        parser.error("--jpeg-encoder vepu121 requires --max-fps above zero")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), MjpegHandler)
    try:
        encoder = None
        if args.jpeg_encoder == "vepu121":
            output_width = args.width // 2 if args.fast else args.width
            output_height = args.height // 2 if args.fast else args.height
            encoder = HardwareJpegEncoder(
                output_width, output_height, max(1, round(args.max_fps))
            )
        threading.Thread(target=capture_loop, args=(args, encoder), daemon=True).start()
        print(f"open http://<board-ip>:{args.port}/", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
