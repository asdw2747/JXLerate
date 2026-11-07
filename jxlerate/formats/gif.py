# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Lossless GIF metadata stripping."""

from __future__ import annotations

# GIF application extensions that affect playback/rendering.
GIF_KEEP_APPS = {b"NETSCAPE2.0", b"ANIMEXTS1.0"}
GIF_APP_ICC = b"ICCRGBG1012"


def _subblocks_end(data: bytes, pos: int) -> int:
    """Return the position just past a GIF sub-block chain."""
    while True:
        if pos >= len(data):
            raise ValueError("truncated GIF (incomplete sub-blocks)")
        size = data[pos]
        pos += 1
        if size == 0:
            return pos
        if pos + size > len(data):
            raise ValueError("truncated GIF (incomplete sub-block)")
        pos += size


def strip_gif(data: bytes, keep_color: bool = False,
              keep_orientation: bool = True) -> bytes:
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("not a GIF file")
    if len(data) < 13:
        raise ValueError("truncated GIF header")

    out = bytearray(data[:13])  # header + logical screen descriptor
    packed = data[10]
    pos = 13
    if packed & 0x80:  # global color table
        gct_size = 3 * (2 ** ((packed & 0x07) + 1))
        if pos + gct_size > len(data):
            raise ValueError("truncated GIF (incomplete global color table)")
        out += data[pos:pos + gct_size]
        pos += gct_size

    found_trailer = False
    while pos < len(data):
        introducer = data[pos]

        if introducer == 0x3B:  # trailer
            out.append(0x3B)
            found_trailer = True
            break

        if introducer == 0x21:  # extension
            if pos + 2 > len(data):
                raise ValueError("truncated GIF extension")
            label = data[pos + 1]

            if label == 0xFE:  # comment -> strip
                pos = _subblocks_end(data, pos + 2)
                continue

            if label == 0xFF:  # application extension
                if pos + 3 > len(data):
                    raise ValueError("truncated GIF application extension")
                block_size = data[pos + 2]
                app_end = pos + 3 + block_size
                if app_end > len(data):
                    raise ValueError("truncated GIF application extension")
                app_id = data[pos + 3:app_end]
                end = _subblocks_end(data, app_end)
                keep = app_id in GIF_KEEP_APPS
                if keep_color and app_id == GIF_APP_ICC:
                    keep = True
                if keep:
                    out += data[pos:end]
                pos = end
                continue

            # Graphic control, plain text, and unknown extensions are kept.
            end = _subblocks_end(data, pos + 2)
            out += data[pos:end]
            pos = end
            continue

        if introducer == 0x2C:  # image descriptor
            if pos + 10 > len(data):
                raise ValueError("truncated GIF image descriptor")
            packed_img = data[pos + 9]
            q = pos + 10
            if packed_img & 0x80:  # local color table
                lct_size = 3 * (2 ** ((packed_img & 0x07) + 1))
                if q + lct_size > len(data):
                    raise ValueError("truncated GIF (incomplete local color table)")
                q += lct_size
            if q >= len(data):
                raise ValueError("truncated GIF image data")
            q += 1  # LZW minimum code size
            end = _subblocks_end(data, q)
            out += data[pos:end]
            pos = end
            continue

        raise ValueError("invalid GIF block")

    if not found_trailer:
        raise ValueError("invalid/truncated GIF (missing trailer)")
    return bytes(out)
