# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Lossless JPEG metadata stripping and recompression."""

from __future__ import annotations

import os
import shutil
import struct
import tempfile

from ..tools import run_tool
from .exif import jpeg_orientation_segment, tiff_orientation

# Frame/table markers required to decode the image.
JPEG_KEEP_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC8,  # SOF0-8
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,              # SOF9-15
    0xC4,  # DHT
    0xDB,  # DQT
    0xCC,  # DAC
    0xDD,  # DRI
}
# APP14 (Adobe) can change how color channels are interpreted, so keep it.
JPEG_KEEP_APP = {0xEE}
# APP2 often carries an ICC profile; kept only with --keep-color.
JPEG_KEEP_APP_COLOR = {0xE2}


def strip_jpeg(data: bytes, keep_color: bool = False,
               keep_orientation: bool = True) -> bytes:
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG file")

    out = bytearray(b"\xff\xd8")
    i = 2
    n = len(data)
    found_eoi = False
    orientation = None

    while i < n:
        # Find the next marker (skip stray bytes).
        if data[i] != 0xFF:
            j = data.find(b"\xff", i)
            if j == -1:
                break
            i = j

        # Skip fill bytes (0xFF padding).
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1

        if marker == 0x00:
            # Stuffed byte; should not appear outside entropy data.
            continue
        if marker == 0xD9:  # EOI
            out += b"\xff\xd9"
            found_eoi = True
            break
        if marker == 0xD8:  # SOI
            out += b"\xff\xd8"
            continue
        if 0xD0 <= marker <= 0xD7:  # RSTn (no length)
            out += bytes([0xFF, marker])
            continue
        if marker == 0x01:  # TEM (no length)
            out += bytes([0xFF, marker])
            continue

        # Marker with a length field.
        if i + 2 > n:
            break
        length = struct.unpack(">H", data[i:i + 2])[0]
        if length < 2 or i + length > n:
            break

        if marker == 0xDA:  # SOS -> keep, plus entropy-coded scan data
            out += bytes([0xFF, marker])
            out += data[i:i + length]
            i += length
            while i < n:
                j = data.find(b"\xff", i)
                if j == -1:
                    out += data[i:]
                    i = n
                    break
                out += data[i:j]
                k = j
                while k < n and data[k] == 0xFF:
                    k += 1
                if k >= n:
                    out += data[j:]
                    i = n
                    break
                if data[k] == 0x00:
                    # Stuffed byte: copy FF 00.
                    out += data[j:k + 1]
                    i = k + 1
                    continue
                if 0xD0 <= data[k] <= 0xD7:
                    # Restart marker inside the scan.
                    out += bytes([0xFF, data[k]])
                    i = k + 1
                    continue
                # Real marker ahead; hand back to the outer loop.
                i = j
                break
            continue

        if marker == 0xE1 and orientation is None:
            payload = data[i + 2:i + length]
            if payload[:6] == b"Exif\x00\x00":
                orientation = tiff_orientation(payload[6:])

        keep = marker in JPEG_KEEP_MARKERS or marker in JPEG_KEEP_APP
        if keep_color and marker in JPEG_KEEP_APP_COLOR:
            keep = True
        if keep:
            out += bytes([0xFF, marker])
            out += data[i:i + length]
        i += length

    if not found_eoi:
        raise ValueError("invalid/truncated JPEG (missing EOI marker)")

    if keep_orientation and orientation in range(2, 9):
        out[2:2] = jpeg_orientation_segment(orientation)

    return bytes(out)


def optimize_jpeg(data: bytes, progressive: bool) -> tuple[bytes | None, str | None]:
    jpegtran = shutil.which("jpegtran")
    if not jpegtran:
        return None, None

    with tempfile.TemporaryDirectory(prefix="strip-opt-") as td:
        src = os.path.join(td, "in.jpg")
        dst = os.path.join(td, "out.jpg")
        with open(src, "wb") as f:
            f.write(data)
        cmd = [jpegtran, "-optimize"]
        if progressive:
            cmd.append("-progressive")
        cmd += ["-copy", "all", "-outfile", dst, src]
        if run_tool(cmd, 120):
            with open(dst, "rb") as f:
                candidate = f.read()
            if len(candidate) < len(data):
                return candidate, "jpegtran"
    return None, None
