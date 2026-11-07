# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""JPEG XL archival transcoding."""

from __future__ import annotations

import io
import os
import shutil
import tempfile

from .formats import is_animated, strip_bytes
from .formats.exif import ORIENTATION_TAG
from .imaging import verify_image_bytes
from .tools import run_tool


def transcode_jxl(data: bytes, ext: str,
                  max_effort: bool, keep_color: bool = False,
                  keep_orientation: bool = True) -> tuple[bytes | None, str]:
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
        if not run_tool(cmd, 900):
            return None, "cjxl failed"

        try:
            with open(dst, "rb") as f:
                jxl = f.read()
        except OSError:
            return None, "cjxl produced no output"

        # Verify before accepting the result.
        if ext in (".jpg", ".jpeg"):
            recon = os.path.join(td, "recon.jpg")
            if not run_tool([djxl, dst, recon], 300):
                return None, "djxl verification failed"
            with open(recon, "rb") as f:
                if f.read() != data:
                    return None, "JPEG reconstruction mismatch"
        else:
            recon = os.path.join(td, "recon.png")
            if not run_tool([djxl, dst, recon], 300):
                return None, "djxl verification failed"
            with open(recon, "rb") as f:
                if not verify_image_bytes(data, f.read(), keep_color,
                                          keep_orientation):
                    return None, "pixel/orientation/color verification failed"

        return jxl, "cjxl"


def to_jxl_file(path: str, keep_color: bool = False,
                keep_orientation: bool = True,
                max_effort: bool = False) -> tuple[bytes | None, int, str]:
    """Strip metadata and transcode to JXL. Returns (jxl, original_size, note)."""
    with open(path, "rb") as f:
        original = f.read()
    ext = os.path.splitext(path)[1]
    data = strip_bytes(original, ext, keep_color, keep_orientation)

    if ext.lower() not in (".jpg", ".jpeg") and is_animated(data, ext):
        return None, len(original), "animated input not supported for JXL"

    jxl, note = transcode_jxl(
        data, ext, max_effort, keep_color, keep_orientation)
    if jxl is None:
        return None, len(original), note
    if len(jxl) >= len(data):
        return None, len(original), f"not smaller ({len(jxl)} >= {len(data)})"
    return jxl, len(original), note
