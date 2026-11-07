# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Lossless WebP metadata stripping and recompression."""

from __future__ import annotations

import io
import struct

from ..tools import run_tool
from .exif import ORIENTATION_TAG, build_minimal_tiff, tiff_orientation

# WebP chunks required for decoding.
WEBP_KEEP_CHUNKS = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF"}


def iter_chunks(data: bytes):
    """Yield (fourcc, payload, pad_bytes) for each WebP chunk."""
    pos = 12
    n = len(data)
    while pos < n:
        if pos + 8 > n:
            raise ValueError("truncated WebP (incomplete chunk header)")
        fourcc = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        payload_start = pos + 8
        payload_end = payload_start + size
        if payload_end > n:
            raise ValueError("truncated WebP (incomplete chunk data)")
        padded_end = payload_end + (size & 1)
        if padded_end > n:
            padded_end = payload_end  # tolerate a missing final pad byte

        yield (fourcc, data[payload_start:payload_end],
               data[payload_end:padded_end])
        pos = padded_end


def has_chunk(data: bytes, fourcc: bytes) -> bool:
    """Return True if a WebP contains a chunk; never raises on bad lengths."""
    pos = 12
    while pos + 8 <= len(data):
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if data[pos:pos + 4] == fourcc:
            return True
        pos += 8 + size + (size & 1)
    return False


def strip_webp(data: bytes, keep_color: bool = False,
               keep_orientation: bool = True) -> bytes:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("not a WebP file")

    # First pass: parse chunks and look for an EXIF orientation.
    parsed = list(iter_chunks(data))
    has_image = False
    has_vp8x = False
    orientation = None
    exif_prefix = b""

    for fourcc, payload, _pad in parsed:
        if fourcc in (b"VP8 ", b"VP8L", b"ANMF"):
            has_image = True
        elif fourcc == b"VP8X":
            has_vp8x = True
        elif fourcc == b"EXIF" and orientation is None:
            if payload[:6] == b"Exif\x00\x00":
                exif_prefix = b"Exif\x00\x00"
                orientation = tiff_orientation(payload[6:])
            else:
                orientation = tiff_orientation(payload)

    if not has_image:
        raise ValueError("invalid WebP (no image data)")

    inject = keep_orientation and orientation in range(2, 9) and has_vp8x
    chunks = []

    for fourcc, payload, pad in parsed:
        keep = fourcc in WEBP_KEEP_CHUNKS
        if keep_color and fourcc == b"ICCP":
            keep = True
        if not keep:
            continue

        if fourcc == b"VP8X":
            if not payload:
                raise ValueError("invalid WebP (empty VP8X chunk)")
            flags = payload[0]
            if not keep_color:
                flags &= ~0x20  # ICC profile
            if inject:
                flags |= 0x08   # EXIF present
            else:
                flags &= ~0x08
            flags &= ~0x04      # XMP
            payload = bytes([flags]) + payload[1:]

        chunks.append(fourcc + struct.pack("<I", len(payload)) + payload + pad)

    if inject:
        payload = exif_prefix + build_minimal_tiff(orientation)
        pad = b"\x00" if len(payload) & 1 else b""
        chunks.append(b"EXIF" + struct.pack("<I", len(payload)) + payload + pad)

    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def optimize_webp(data: bytes, keep_color: bool) -> tuple[bytes | None, str | None]:
    # Only safe for static lossless WebP (VP8L); never re-encode lossy WebP.
    if not has_chunk(data, b"VP8L") or has_chunk(data, b"ANMF"):
        return None, None
    try:
        from PIL import Image
    except Exception:
        return None, None

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            kwargs = {"lossless": True, "quality": 100, "method": 6}
            if keep_color and im.info.get("icc_profile"):
                kwargs["icc_profile"] = im.info["icc_profile"]
            exif = im.getexif()
            if exif.get(ORIENTATION_TAG):
                kwargs["exif"] = exif.tobytes()
            out = io.BytesIO()
            im.save(out, "WEBP", **kwargs)
        candidate = out.getvalue()
    except Exception:
        return None, None
    if len(candidate) < len(data):
        return candidate, "cwebp-lossless"
    return None, None
