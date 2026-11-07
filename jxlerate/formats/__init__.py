# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Format dispatch for the supported lossless image types."""

from __future__ import annotations

import io

from .gif import strip_gif
from .jpeg import optimize_jpeg, strip_jpeg
from .png import has_chunk as _png_has_chunk
from .png import optimize_png, strip_png
from .webp import has_chunk as _webp_has_chunk
from .webp import optimize_webp, strip_webp

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


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


def optimize_bytes(data: bytes, ext: str, keep_color: bool = False,
                   max_effort: bool = False,
                   progressive: bool = False) -> tuple[bytes | None, str | None]:
    """Return (smaller_lossless_bytes, tool_name) or (None, None)."""
    ext = ext.lower()
    if ext == ".png":
        return optimize_png(data, max_effort)
    if ext in (".jpg", ".jpeg"):
        return optimize_jpeg(data, progressive)
    if ext == ".webp":
        return optimize_webp(data, keep_color)
    return None, None


def is_animated(data: bytes, ext: str) -> bool:
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
