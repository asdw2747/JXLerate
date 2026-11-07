# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""EXIF/TIFF orientation parsing and minimal metadata builders."""

from __future__ import annotations

import struct
import zlib

ORIENTATION_TAG = 0x0112


def tiff_orientation(tiff: bytes):
    """Return the EXIF Orientation value (1-8) from a TIFF payload, or None."""
    if len(tiff) < 8:
        return None
    order = tiff[:2]
    if order == b"II":
        endian = "<"
    elif order == b"MM":
        endian = ">"
    else:
        return None
    if struct.unpack(endian + "H", tiff[2:4])[0] != 42:
        return None

    ifd = struct.unpack(endian + "I", tiff[4:8])[0]
    if ifd + 2 > len(tiff):
        return None
    count = struct.unpack(endian + "H", tiff[ifd:ifd + 2])[0]
    pos = ifd + 2
    for _ in range(count):
        if pos + 12 > len(tiff):
            return None
        tag, typ, n = struct.unpack(endian + "HHI", tiff[pos:pos + 8])
        if tag == ORIENTATION_TAG and typ == 3 and n >= 1:
            if n == 1:
                value = struct.unpack(endian + "H", tiff[pos + 8:pos + 10])[0]
            else:
                offset = struct.unpack(endian + "I", tiff[pos + 8:pos + 12])[0]
                if offset + 2 > len(tiff):
                    return None
                value = struct.unpack(endian + "H", tiff[offset:offset + 2])[0]
            return value if 1 <= value <= 8 else None
        pos += 12
    return None


def build_minimal_tiff(value: int) -> bytes:
    """Build a tiny little-endian TIFF block containing only Orientation."""
    out = bytearray(b"II")
    out += struct.pack("<H", 42)          # TIFF magic
    out += struct.pack("<I", 8)           # IFD0 offset
    out += struct.pack("<H", 1)           # one entry
    out += struct.pack("<HHI", ORIENTATION_TAG, 3, 1)
    out += struct.pack("<H", value) + b"\x00\x00"  # inline SHORT
    out += struct.pack("<I", 0)           # no next IFD
    return bytes(out)


def jpeg_orientation_segment(value: int) -> bytes:
    """Build a minimal EXIF APP1 segment carrying only Orientation."""
    payload = b"Exif\x00\x00" + build_minimal_tiff(value)
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def png_orientation_chunk(value: int) -> bytes:
    """Build a minimal PNG eXIf chunk carrying only Orientation."""
    data = build_minimal_tiff(value)
    body = b"eXIf" + data
    return (struct.pack(">I", len(data)) + body +
            struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))
