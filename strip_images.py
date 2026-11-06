#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""strip_images - losslessly strip metadata from PNG, JPEG, WebP and GIF files.

Removes all non-essential chunks/markers (EXIF, text, gamma, color profiles,
comments, APP segments, etc.) while preserving the exact pixel data.

Usage:
    python3 strip_images.py file.png file.jpg file.webp file.gif ...
    python3 strip_images.py -r ./photos
    python3 strip_images.py -r ./photos -o ./stripped
    python3 strip_images.py -r ./photos -o ./stripped --passthrough
    python3 strip_images.py --verify file.png
    python3 strip_images.py --skip-clean -r ./photos
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zlib

__version__ = "1.5.0"

PNG_SIG = b"\x89PNG\r\n\x1a\n"

# Chunks required to reconstruct the image itself.
PNG_STRUCTURAL = {
    b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS",
    b"acTL", b"fcTL", b"fdAT",  # APNG animation
}
# Chunks that affect color rendering (kept only with --keep-color).
PNG_COLOR = {b"cHRM", b"gAMA", b"iCCP", b"sRGB", b"cICP", b"mDCv", b"cLLi"}

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

# WebP chunks required for decoding.
WEBP_KEEP_CHUNKS = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF"}

# GIF application extensions that affect playback/rendering.
GIF_KEEP_APPS = {b"NETSCAPE2.0", b"ANIMEXTS1.0"}
GIF_APP_ICC = b"ICCRGBG1012"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

ORIENTATION_TAG = 0x0112


# ---------------------------------------------------------------------------
# Orientation helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Core stripping
# ---------------------------------------------------------------------------

def strip_png(data: bytes, keep_color: bool = False,
              keep_orientation: bool = True) -> bytes:
    if data[:8] != PNG_SIG:
        raise ValueError("not a PNG file")

    keep = set(PNG_STRUCTURAL)
    if keep_color:
        keep |= PNG_COLOR

    out = bytearray(PNG_SIG)
    pos = 8
    n = len(data)
    first_type = None
    last_type = None
    orientation = None

    while pos < n:
        if pos + 12 > n:
            raise ValueError("truncated PNG (incomplete chunk header)")
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        total = 12 + length
        if pos + total > n:
            raise ValueError("truncated PNG (incomplete chunk data)")
        if first_type is None:
            first_type = ctype
        last_type = ctype
        if ctype == b"eXIf" and orientation is None:
            orientation = tiff_orientation(data[pos + 8:pos + 8 + length])
        if ctype in keep:
            out.extend(data[pos:pos + total])
        pos += total

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


def strip_webp(data: bytes, keep_color: bool = False,
               keep_orientation: bool = True) -> bytes:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("not a WebP file")

    # First pass: parse chunks and look for an EXIF orientation.
    parsed = []
    pos = 12
    n = len(data)
    has_image = False
    has_vp8x = False
    orientation = None
    exif_prefix = b""

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

        payload = data[payload_start:payload_end]
        pad = data[payload_end:padded_end]
        pos = padded_end
        parsed.append((fourcc, payload, pad))

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


def _gif_subblocks_end(data: bytes, pos: int) -> int:
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
                pos = _gif_subblocks_end(data, pos + 2)
                continue

            if label == 0xFF:  # application extension
                if pos + 3 > len(data):
                    raise ValueError("truncated GIF application extension")
                block_size = data[pos + 2]
                app_end = pos + 3 + block_size
                if app_end > len(data):
                    raise ValueError("truncated GIF application extension")
                app_id = data[pos + 3:app_end]
                end = _gif_subblocks_end(data, app_end)
                keep = app_id in GIF_KEEP_APPS
                if keep_color and app_id == GIF_APP_ICC:
                    keep = True
                if keep:
                    out += data[pos:end]
                pos = end
                continue

            # Graphic control, plain text, and unknown extensions are kept.
            end = _gif_subblocks_end(data, pos + 2)
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
            end = _gif_subblocks_end(data, q)
            out += data[pos:end]
            pos = end
            continue

        raise ValueError("invalid GIF block")

    if not found_trailer:
        raise ValueError("invalid/truncated GIF (missing trailer)")
    return bytes(out)


def strip_bytes(data: bytes, ext: str, keep_color: bool = False,
                keep_orientation: bool = True) -> bytes:
    ext = ext.lower()
    if ext == ".png":
        return strip_png(data, keep_color, keep_orientation)
    if ext in (".jpg", ".jpeg"):
        return strip_jpeg(data, keep_color, keep_orientation)
    if ext == ".webp":
        return strip_webp(data, keep_color, keep_orientation)
    if ext == ".gif":
        return strip_gif(data, keep_color, keep_orientation)
    raise ValueError(f"unsupported file type: {ext}")


def strip_file(path: str, keep_color: bool = False,
               keep_orientation: bool = True) -> tuple[bytes, int, bool]:
    """Return (stripped_bytes, original_size, changed)."""
    with open(path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(path)[1]
    new = strip_bytes(data, ext, keep_color, keep_orientation)
    return new, len(data), (new != data)


# ---------------------------------------------------------------------------
# Optional recompression / transcoding
# ---------------------------------------------------------------------------

def _run_tool(cmd, timeout):
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=timeout)
        return True
    except Exception:
        return False


def _png_has_chunk(data: bytes, ctype: bytes) -> bool:
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        if data[pos + 4:pos + 8] == ctype:
            return True
        pos += 12 + length
    return False


def _webp_has_chunk(data: bytes, fourcc: bytes) -> bool:
    pos = 12
    while pos + 8 <= len(data):
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if data[pos:pos + 4] == fourcc:
            return True
        pos += 8 + size + (size & 1)
    return False


def _optimize_png(data: bytes, max_effort: bool) -> tuple[bytes | None, str | None]:
    best = data
    tool = None
    timeout = 600 if max_effort else 120
    is_apng = _png_has_chunk(data, b"acTL")

    with tempfile.TemporaryDirectory(prefix="strip-opt-") as td:
        oxipng = shutil.which("oxipng")
        if oxipng:
            path = os.path.join(td, "image.png")
            with open(path, "wb") as f:
                f.write(data)
            cmd = [oxipng, "-o", "6" if max_effort else "4",
                   "--strip", "safe", "--quiet"]
            if max_effort:
                cmd.append("--zopfli")
            cmd.append(path)
            if _run_tool(cmd, timeout):
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
            if _run_tool(cmd, timeout):
                with open(path, "rb") as f:
                    candidate = f.read()
                if len(candidate) < len(best):
                    best, tool = candidate, "optipng"

    return (best, tool) if tool else (None, None)


def _optimize_jpeg(data: bytes, progressive: bool) -> tuple[bytes | None, str | None]:
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
        if _run_tool(cmd, 120):
            with open(dst, "rb") as f:
                candidate = f.read()
            if len(candidate) < len(data):
                return candidate, "jpegtran"
    return None, None


def _optimize_webp(data: bytes, keep_color: bool) -> tuple[bytes | None, str | None]:
    # Only safe for static lossless WebP (VP8L); never re-encode lossy WebP.
    if not _webp_has_chunk(data, b"VP8L") or _webp_has_chunk(data, b"ANMF"):
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
            out = io.BytesIO()
            im.save(out, "WEBP", **kwargs)
        candidate = out.getvalue()
    except Exception:
        return None, None
    if len(candidate) < len(data):
        return candidate, "cwebp-lossless"
    return None, None


def optimize_bytes(data: bytes, ext: str, keep_color: bool = False,
                   max_effort: bool = False,
                   progressive: bool = False) -> tuple[bytes | None, str | None]:
    """Return (smaller_lossless_bytes, tool_name) or (None, None)."""
    ext = ext.lower()
    if ext == ".png":
        return _optimize_png(data, max_effort)
    if ext in (".jpg", ".jpeg"):
        return _optimize_jpeg(data, progressive)
    if ext == ".webp":
        return _optimize_webp(data, keep_color)
    return None, None


def _lossy_bytes(data: bytes, ext: str, quality: int,
                 keep_orientation: bool = True) -> tuple[bytes | None, str | None]:
    """Re-encode lossily (JPEG/WebP only). Pixels WILL change."""
    try:
        from PIL import Image
    except Exception:
        return None, None

    ext = ext.lower()
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if getattr(im, "is_animated", False):
                return None, None
            exif = im.getexif()
            keep_exif = bool(keep_orientation and exif.get(ORIENTATION_TAG))

            if ext in (".jpg", ".jpeg"):
                kwargs = {"quality": quality, "optimize": True, "progressive": True}
                if keep_exif:
                    kwargs["exif"] = exif.tobytes()
                out = io.BytesIO()
                im.save(out, "JPEG", **kwargs)
                return out.getvalue(), f"lossy-jpeg q{quality}"

            if ext == ".webp":
                kwargs = {"quality": quality, "method": 6}
                if keep_exif:
                    kwargs["exif"] = exif.tobytes()
                out = io.BytesIO()
                im.save(out, "WEBP", **kwargs)
                return out.getvalue(), f"lossy-webp q{quality}"
    except Exception:
        return None, None
    return None, None


def process_file(path: str, keep_color: bool = False,
                 keep_orientation: bool = True, optimize: bool = False,
                 max_effort: bool = False, progressive: bool = False,
                 lossy_quality: int | None = None) -> tuple[bytes, int, bool, str]:
    """Strip, optionally recompress, and return (data, original_size, changed, tool)."""
    with open(path, "rb") as f:
        original = f.read()
    ext = os.path.splitext(path)[1]
    data = strip_bytes(original, ext, keep_color, keep_orientation)
    tool = "metadata"

    if lossy_quality is not None:
        candidate, name = _lossy_bytes(data, ext, lossy_quality, keep_orientation)
        if candidate is not None and len(candidate) < len(data):
            data, tool = candidate, name
        elif optimize:
            candidate, name = optimize_bytes(
                data, ext, keep_color, max_effort, progressive)
            if candidate is not None and len(candidate) < len(data):
                data, tool = candidate, name
    elif optimize:
        candidate, name = optimize_bytes(
            data, ext, keep_color, max_effort, progressive)
        if candidate is not None and len(candidate) < len(data):
            data, tool = candidate, name

    return data, len(original), (data != original), tool


# ---------------------------------------------------------------------------
# JPEG XL archival transcode
# ---------------------------------------------------------------------------

def _is_animated(data: bytes, ext: str) -> bool:
    ext = ext.lower()
    if ext == ".png":
        return _png_has_chunk(data, b"acTL")
    if ext == ".webp":
        return _webp_has_chunk(data, b"ANMF")
    if ext == ".gif":
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                return bool(getattr(im, "is_animated", False))
        except Exception:
            return False
    return False


def _pixels_equal_bytes(a: bytes, b: bytes) -> bool:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(a)) as im1, Image.open(io.BytesIO(b)) as im2:
            return _frame_signatures(im1) == _frame_signatures(im2)
    except Exception:
        return False


def _transcode_jxl(data: bytes, ext: str,
                   max_effort: bool) -> tuple[bytes | None, str]:
    cjxl = shutil.which("cjxl")
    djxl = shutil.which("djxl")
    if not cjxl or not djxl:
        return None, "cjxl/djxl not installed"

    ext = ext.lower()
    with tempfile.TemporaryDirectory(prefix="strip-jxl-") as td:
        src = os.path.join(td, "in" + ext)
        dst = os.path.join(td, "out.jxl")
        with open(src, "wb") as f:
            f.write(data)

        # cjxl has no WebP reader; decode to PNG first.
        if ext == ".webp":
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    im.load()
                    kwargs = {}
                    if im.info.get("icc_profile"):
                        kwargs["icc_profile"] = im.info["icc_profile"]
                    exif = im.getexif()
                    if exif.get(ORIENTATION_TAG):
                        kwargs["exif"] = exif.tobytes()
                    src = os.path.join(td, "in.png")
                    im.save(src, "PNG", **kwargs)
            except Exception:
                return None, "WebP decode failed"

        cmd = [cjxl]
        if ext in (".jpg", ".jpeg"):
            cmd.append("--lossless_jpeg=1")
        else:
            cmd += ["-d", "0"]
        cmd += ["-e", "9" if max_effort else "7", src, dst]
        if not _run_tool(cmd, 900):
            return None, "cjxl failed"

        try:
            with open(dst, "rb") as f:
                jxl = f.read()
        except OSError:
            return None, "cjxl produced no output"

        # Verify before accepting the result.
        if ext in (".jpg", ".jpeg"):
            recon = os.path.join(td, "recon.jpg")
            if not _run_tool([djxl, dst, recon], 300):
                return None, "djxl verification failed"
            with open(recon, "rb") as f:
                if f.read() != data:
                    return None, "JPEG reconstruction mismatch"
        else:
            recon = os.path.join(td, "recon.png")
            if not _run_tool([djxl, dst, recon], 300):
                return None, "djxl verification failed"
            with open(recon, "rb") as f:
                if not _pixels_equal_bytes(data, f.read()):
                    return None, "pixel verification failed"

        return jxl, "cjxl"


def to_jxl_file(path: str, keep_color: bool = False,
                keep_orientation: bool = True,
                max_effort: bool = False) -> tuple[bytes | None, int, str]:
    """Strip metadata and transcode to JXL. Returns (jxl, original_size, note)."""
    with open(path, "rb") as f:
        original = f.read()
    ext = os.path.splitext(path)[1]
    data = strip_bytes(original, ext, keep_color, keep_orientation)

    if ext.lower() not in (".jpg", ".jpeg") and _is_animated(data, ext):
        return None, len(original), "animated input not supported for JXL"

    jxl, note = _transcode_jxl(data, ext, max_effort)
    if jxl is None:
        return None, len(original), note
    if len(jxl) >= len(data):
        return None, len(original), f"not smaller ({len(jxl)} >= {len(data)})"
    return jxl, len(original), note


def jxl_output_path(fp, base_dir, output_dir, suffix, prefix_roots):
    """Return the .jxl output path for an input file."""
    if output_dir:
        path = output_path_for(fp, base_dir, output_dir, suffix, prefix_roots)
    else:
        folder, name = os.path.split(fp)
        stem, _ = os.path.splitext(name)
        path = os.path.join(folder, stem + suffix + ".jxl")
    stem, _ = os.path.splitext(path)
    return stem + ".jxl"


# ---------------------------------------------------------------------------
# Verification / writing
# ---------------------------------------------------------------------------

def have_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def _frame_signatures(image) -> list:
    """Hash every frame so animated PNG/GIF/WebP files are fully verified."""
    import hashlib
    from PIL import ImageSequence

    signatures = []
    for frame in ImageSequence.Iterator(image):
        signatures.append((
            frame.mode,
            frame.size,
            hashlib.sha256(frame.tobytes()).hexdigest(),
        ))
    return signatures


def verify_lossless(orig_path: str, new_data: bytes) -> bool:
    """Return True only if the stripped bytes decode to identical pixels.

    Verifies every frame of animated files. Returns False (never silently
    True) on decode errors or mismatches. Raises RuntimeError if Pillow is
    unavailable.
    """
    try:
        from PIL import Image
    except Exception as exc:
        raise RuntimeError("Pillow is required for verification") from exc

    try:
        with Image.open(orig_path) as im:
            im.load()
            orig = _frame_signatures(im)
        with Image.open(io.BytesIO(new_data)) as im2:
            im2.load()
            new = _frame_signatures(im2)
    except Exception:
        return False
    return orig == new


def write_file_atomic(path: str, data: bytes, keep_stat_from: str | None = None) -> None:
    """Write data atomically, preserving mode/mtime and following symlinks.

    keep_stat_from is the original file whose permissions and timestamps
    should be carried over to the replacement.
    """
    # If path is a symlink, update its target instead of replacing the link.
    target = os.path.realpath(path) if os.path.islink(path) else path
    directory = os.path.dirname(os.path.abspath(target)) or "."
    os.makedirs(directory, exist_ok=True)

    source_stat = None
    if keep_stat_from:
        try:
            source_stat = os.stat(keep_stat_from)  # follows symlinks
        except OSError:
            source_stat = None

    fd, tmp = tempfile.mkstemp(prefix=".strip-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if source_stat is not None:
            os.chmod(tmp, stat.S_IMODE(source_stat.st_mode))
            os.utime(tmp, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# File collection / output paths
# ---------------------------------------------------------------------------

def _is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXTS


def _walk_pruned(root: str, exclude_dir):
    """os.walk that skips exclude_dir if it lies inside root."""
    for current, dirs, names in os.walk(root):
        if exclude_dir:
            dirs[:] = [d for d in dirs
                       if os.path.abspath(os.path.join(current, d)) != exclude_dir]
            if os.path.abspath(current) == exclude_dir:
                continue
        yield current, dirs, names


def collect_file_entries(inputs, recursive, exclude_dir=None):
    """Return [(image_path, base_dir_or_None), ...]."""
    entries = []
    for p in inputs:
        if os.path.isdir(p):
            base = os.path.abspath(p)
            if recursive:
                for root, _, names in _walk_pruned(p, exclude_dir):
                    for nm in names:
                        if _is_image(nm):
                            entries.append((os.path.join(root, nm), base))
            else:
                for nm in os.listdir(p):
                    fp = os.path.join(p, nm)
                    if os.path.isfile(fp) and _is_image(nm):
                        entries.append((fp, base))
        elif os.path.isfile(p):
            entries.append((p, None))
    return entries


def collect_files(inputs, recursive):
    """Backwards-compatible helper returning only image paths."""
    return [fp for fp, _ in collect_file_entries(inputs, recursive)]


def output_path_for(fp, base_dir, output_dir, suffix, prefix_roots):
    if base_dir is not None:
        rel = os.path.relpath(os.path.abspath(fp), base_dir)
        if prefix_roots:
            rel = os.path.join(os.path.basename(base_dir), rel)
    else:
        rel = os.path.basename(fp)

    if suffix:
        folder, name = os.path.split(rel)
        stem, ext = os.path.splitext(name)
        rel = os.path.join(folder, stem + suffix + ext)
    return os.path.join(output_dir, rel)


def collect_passthrough_entries(inputs, recursive, prefix_roots, exclude_dir=None):
    """Return [(src, relative_dest), ...] for every non-image file."""
    entries = []
    for p in inputs:
        if os.path.isdir(p):
            base = os.path.abspath(p)
            if recursive:
                for root, _, names in _walk_pruned(p, exclude_dir):
                    for nm in names:
                        src = os.path.join(root, nm)
                        if _is_image(nm):
                            continue
                        rel = os.path.relpath(src, base)
                        if prefix_roots:
                            rel = os.path.join(os.path.basename(base), rel)
                        entries.append((src, rel))
            else:
                for nm in os.listdir(p):
                    src = os.path.join(p, nm)
                    if not os.path.isfile(src) or _is_image(nm):
                        continue
                    rel = os.path.join(os.path.basename(base), nm) if prefix_roots else nm
                    entries.append((src, rel))
        elif os.path.isfile(p) and not _is_image(p):
            entries.append((p, os.path.basename(p)))
    return entries


def copy_passthrough(entries, output_dir):
    count = 0
    for src, rel in entries:
        dst = os.path.join(output_dir, rel)
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Losslessly strip metadata from PNG/JPEG/WebP/GIF files.")
    ap.add_argument("--version", action="version",
                    version=f"%(prog)s {__version__}")
    ap.add_argument("inputs", nargs="+", help="files or directories")
    ap.add_argument("-r", "--recursive", action="store_true", help="recurse into directories")
    ap.add_argument("-o", "--output-dir", help="write stripped files into this directory (mirrors structure)")
    ap.add_argument("--suffix", default="", help="append suffix to basename before extension (e.g. _stripped)")
    ap.add_argument("--backup", action="store_true", help="keep a .bak of the original")
    ap.add_argument("--dry-run", action="store_true", help="report only, don't write")
    ap.add_argument("--verify", action="store_true", help="verify pixel data is unchanged (requires Pillow)")
    ap.add_argument("--keep-color", action="store_true",
                    help="keep color metadata (PNG color chunks, JPEG/WebP/GIF ICC)")
    ap.add_argument("--strip-orientation", action="store_true",
                    help="also remove the EXIF Orientation tag (photos may display rotated)")
    ap.add_argument("--optimize", action="store_true",
                    help="losslessly recompress (oxipng/jpegtran/lossless WebP)")
    ap.add_argument("--max-effort", action="store_true",
                    help="slower but better PNG optimization (enables zopfli)")
    ap.add_argument("--progressive", action="store_true",
                    help="write progressive JPEGs when optimizing")
    ap.add_argument("--lossy-quality", type=int, metavar="N",
                    help="LOSSY re-encode JPEG/WebP at quality 1-100 (off by default)")
    ap.add_argument("--to-jxl", action="store_true",
                    help="losslessly transcode to JPEG XL (.jxl)")
    ap.add_argument("--replace", action="store_true",
                    help="with --to-jxl, delete originals after verified conversion")
    ap.add_argument("--passthrough", action="store_true",
                    help="copy non-image files into the output folder too (requires -o)")
    ap.add_argument("--skip-clean", action="store_true",
                    help="do not write files that have nothing to strip")
    ap.add_argument("--only-if-smaller", action="store_true",
                    help="only write when the result is smaller (implies --skip-clean)")
    ap.add_argument("-q", "--quiet", action="store_true", help="suppress per-file output")
    args = ap.parse_args()

    if args.passthrough and not args.output_dir:
        ap.error("--passthrough requires --output-dir")
    if args.verify and not have_pillow():
        ap.error("--verify requires Pillow (pip install Pillow)")
    if args.lossy_quality is not None:
        if not 1 <= args.lossy_quality <= 100:
            ap.error("--lossy-quality must be between 1 and 100")
        if args.verify:
            print("warning: --verify is ignored in lossy mode (pixels change).",
                  file=sys.stderr)
            args.verify = False
    if args.to_jxl and args.lossy_quality is not None:
        ap.error("--to-jxl cannot be combined with --lossy-quality")
    if args.replace and not args.to_jxl:
        ap.error("--replace requires --to-jxl")
    if args.replace and args.output_dir:
        ap.error("--replace cannot be combined with --output-dir")
    if args.to_jxl and not (shutil.which("cjxl") and shutil.which("djxl")):
        ap.error("--to-jxl requires cjxl and djxl (JPEG XL tools)")

    output_dir = os.path.abspath(args.output_dir) if args.output_dir else None
    exclude_dir = output_dir
    dir_inputs = [p for p in args.inputs if os.path.isdir(p)]
    prefix_roots = len(dir_inputs) > 1

    entries = collect_file_entries(args.inputs, args.recursive, exclude_dir)
    if not entries and not (args.passthrough and output_dir):
        print("no image files found", file=sys.stderr)
        sys.exit(1)

    passthrough_entries = []
    if args.passthrough and output_dir:
        passthrough_entries = collect_passthrough_entries(
            args.inputs, args.recursive, prefix_roots, exclude_dir)

    total_saved = 0
    ok = 0
    skipped = 0
    fail = 0

    for fp, base_dir in entries:
        if args.to_jxl:
            try:
                jxl, orig_size, note = to_jxl_file(
                    fp, args.keep_color, not args.strip_orientation,
                    args.max_effort)
            except Exception as e:
                print(f"FAIL {fp}: {e}", file=sys.stderr)
                fail += 1
                continue

            if jxl is None:
                if not args.quiet:
                    print(f"SKIP {fp}: {note}")
                skipped += 1
                continue

            out_path = jxl_output_path(
                fp, base_dir, output_dir, args.suffix, prefix_roots)
            if os.path.exists(out_path):
                if not args.quiet:
                    print(f"SKIP {fp}: {out_path} already exists")
                skipped += 1
                continue

            saved = orig_size - len(jxl)
            if args.dry_run:
                if not args.quiet:
                    print(f"{fp}: {orig_size} -> {len(jxl)} bytes "
                          f"(saved {saved}) [cjxl] -> {out_path}")
                total_saved += saved
                ok += 1
                continue

            try:
                write_file_atomic(out_path, jxl, keep_stat_from=fp)
                if args.replace:
                    if args.backup:
                        shutil.copy2(fp, fp + ".bak")
                    os.remove(fp)
            except Exception as e:
                print(f"WRITE FAIL {fp}: {e}", file=sys.stderr)
                fail += 1
                continue

            total_saved += saved
            if not args.quiet:
                print(f"{fp}: {orig_size} -> {len(jxl)} bytes "
                      f"(saved {saved}) [cjxl] -> {out_path}")
            ok += 1
            continue

        try:
            new_data, orig_size, changed, tool = process_file(
                fp, args.keep_color, not args.strip_orientation,
                args.optimize, args.max_effort, args.progressive,
                args.lossy_quality)
        except Exception as e:
            print(f"FAIL {fp}: {e}", file=sys.stderr)
            fail += 1
            continue

        saved = orig_size - len(new_data)

        skip_reason = None
        if args.only_if_smaller and (not changed or len(new_data) >= orig_size):
            skip_reason = "no smaller result"
        elif args.skip_clean and not changed:
            skip_reason = "already clean"
        if skip_reason:
            if not args.quiet:
                print(f"SKIP {fp}: {skip_reason}")
            skipped += 1
            continue

        if args.verify and not verify_lossless(fp, new_data):
            print(f"VERIFY FAILED {fp}: pixels differ or output is invalid!", file=sys.stderr)
            fail += 1
            continue

        total_saved += saved

        if output_dir:
            out_path = output_path_for(
                fp, base_dir, output_dir, args.suffix, prefix_roots)
        elif args.suffix:
            d = os.path.dirname(fp)
            name, ext = os.path.splitext(os.path.basename(fp))
            out_path = os.path.join(d, name + args.suffix + ext)
        else:
            out_path = fp

        if args.dry_run:
            if not args.quiet:
                print(f"{fp}: {orig_size} -> {len(new_data)} bytes "
                      f"(saved {saved}) [{tool}]")
            ok += 1
            continue

        try:
            if args.backup and out_path == fp:
                shutil.copy2(fp, fp + ".bak")
            write_file_atomic(out_path, new_data, keep_stat_from=fp)
        except Exception as e:
            print(f"WRITE FAIL {fp}: {e}", file=sys.stderr)
            fail += 1
            continue

        if not args.quiet:
            print(f"{fp}: {orig_size} -> {len(new_data)} bytes "
                  f"(saved {saved}) [{tool}] -> {out_path}")
        ok += 1

    if passthrough_entries:
        if args.dry_run:
            if not args.quiet:
                print(f"Passthrough: would copy {len(passthrough_entries)} non-image file(s).",
                      file=sys.stderr)
        else:
            try:
                n = copy_passthrough(passthrough_entries, output_dir)
                if not args.quiet:
                    print(f"Passthrough: copied {n} non-image file(s).", file=sys.stderr)
            except Exception as e:
                print(f"PASSTHROUGH FAIL: {e}", file=sys.stderr)
                fail += 1

    print(f"\nDone: {ok} ok, {skipped} skipped, {fail} failed, "
          f"total saved {total_saved} bytes", file=sys.stderr)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
