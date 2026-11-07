# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Lossless PNG metadata stripping and recompression."""

from __future__ import annotations

import os
import shutil
import struct
import tempfile

from ..tools import run_tool
from .exif import png_orientation_chunk, tiff_orientation

PNG_SIG = b"\x89PNG\r\n\x1a\n"

# Chunks required to reconstruct the image itself.
PNG_STRUCTURAL = {
    b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS",
    b"acTL", b"fcTL", b"fdAT",  # APNG animation
}
# Chunks that affect color rendering (kept only with --keep-color).
PNG_COLOR = {b"cHRM", b"gAMA", b"iCCP", b"sRGB", b"cICP", b"mDCv", b"cLLi"}


def iter_chunks(data: bytes):
    """Yield (position, type, length, total_size) for each PNG chunk.

    Raises ValueError for a truncated chunk header or payload.
    """
    pos = 8
    n = len(data)
    while pos < n:
        if pos + 12 > n:
            raise ValueError("truncated PNG (incomplete chunk header)")
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        total = 12 + length
        if pos + total > n:
            raise ValueError("truncated PNG (incomplete chunk data)")
        yield pos, ctype, length, total
        pos += total


def has_chunk(data: bytes, ctype: bytes) -> bool:
    """Return True if a PNG contains a chunk; never raises on bad lengths."""
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        if data[pos + 4:pos + 8] == ctype:
            return True
        pos += 12 + length
    return False


def strip_png(data: bytes, keep_color: bool = False,
              keep_orientation: bool = True) -> bytes:
    if data[:8] != PNG_SIG:
        raise ValueError("not a PNG file")

    keep = set(PNG_STRUCTURAL)
    if keep_color:
        keep |= PNG_COLOR

    out = bytearray(PNG_SIG)
    first_type = None
    last_type = None
    orientation = None

    for pos, ctype, length, total in iter_chunks(data):
        if first_type is None:
            first_type = ctype
        last_type = ctype
        if ctype == b"eXIf" and orientation is None:
            orientation = tiff_orientation(data[pos + 8:pos + 8 + length])
        if ctype in keep:
            out.extend(data[pos:pos + total])

    if first_type != b"IHDR":
        raise ValueError("invalid PNG (missing IHDR)")
    if last_type != b"IEND":
        raise ValueError("invalid/truncated PNG (missing IEND)")

    if keep_orientation and orientation in range(2, 9):
        # Insert a minimal eXIf chunk immediately after IHDR.
        ihdr_len = struct.unpack(">I", out[8:12])[0]
        insert_at = 8 + 12 + ihdr_len
        out[insert_at:insert_at] = png_orientation_chunk(orientation)

    return bytes(out)


def optimize_png(data: bytes, max_effort: bool) -> tuple[bytes | None, str | None]:
    best = data
    tool = None
    timeout = 600 if max_effort else 120
    is_apng = has_chunk(data, b"acTL")

    with tempfile.TemporaryDirectory(prefix="strip-opt-") as td:
        oxipng = shutil.which("oxipng")
        if oxipng:
            path = os.path.join(td, "image.png")
            with open(path, "wb") as f:
                f.write(data)
            # Metadata has already been filtered by strip_png(). Do not ask
            # oxipng to strip more chunks here: --strip safe removes eXIf,
            # which can contain the orientation we intentionally retained.
            cmd = [oxipng, "-o", "6" if max_effort else "4", "--quiet"]
            if max_effort:
                cmd.append("--zopfli")
            cmd.append(path)
            if run_tool(cmd, timeout):
                with open(path, "rb") as f:
                    candidate = f.read()
                if len(candidate) < len(best):
                    best, tool = candidate, "oxipng"

        optipng = shutil.which("optipng")
        if optipng and not is_apng and best is data:
            path = os.path.join(td, "optipng.png")
            with open(path, "wb") as f:
                f.write(data)
            cmd = [optipng, "-quiet", "-o5" if max_effort else "-o2", path]
            if run_tool(cmd, timeout):
                with open(path, "rb") as f:
                    candidate = f.read()
                if len(candidate) < len(best):
                    best, tool = candidate, "optipng"

    return (best, tool) if tool else (None, None)
