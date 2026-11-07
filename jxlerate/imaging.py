# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Pixel/metadata verification and optional Pillow-based re-encoding."""

from __future__ import annotations

import hashlib
import io

from .formats.exif import ORIENTATION_TAG

PIL_COLOR_INFO_KEYS = (
    "icc_profile", "gamma", "chromaticity", "srgb", "cicp", "mdcv", "clli")


def have_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def frame_signatures(image) -> list:
    """Hash every frame so animated PNG/GIF/WebP files are fully verified."""
    from PIL import ImageSequence

    signatures = []
    for frame in ImageSequence.Iterator(image):
        # Optimizers may losslessly change PNG storage type (for example RGB
        # to palette). Compare decoded 8-bit color values, not Pillow's storage
        # mode. Keep higher-depth/other modes raw to avoid quantizing them.
        if frame.mode in ("1", "L", "P", "LA", "RGB", "RGBA"):
            pixels = frame.convert("RGBA")
            mode = "RGBA"
        else:
            pixels = frame
            mode = frame.mode
        signatures.append((
            mode,
            frame.size,
            hashlib.sha256(pixels.tobytes()).hexdigest(),
        ))
    return signatures


def verify_image_bytes(original_data: bytes, result_data: bytes,
                       keep_color: bool = False,
                       keep_orientation: bool = True) -> bool:
    """Compare decoded frames and explicitly preserved orientation/color data."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(original_data)) as original:
            original.load()
            original_frames = frame_signatures(original)
            original_orientation = original.getexif().get(ORIENTATION_TAG)
            if original_orientation not in range(2, 9):
                original_orientation = None
            original_metadata = {
                "orientation": original_orientation if keep_orientation else None,
                "color": {k: original.info[k] for k in
                          PIL_COLOR_INFO_KEYS
                          if keep_color and k in original.info},
            }
        with Image.open(io.BytesIO(result_data)) as result:
            result.load()
            result_frames = frame_signatures(result)
            result_orientation = result.getexif().get(ORIENTATION_TAG)
            if result_orientation not in range(2, 9):
                result_orientation = None
            if not keep_orientation:
                result_orientation = None
            result_color = {k: result.info[k] for k in
                            PIL_COLOR_INFO_KEYS
                            if keep_color and k in result.info}
            result_metadata = {"orientation": result_orientation,
                               "color": result_color}
        return (original_frames == result_frames and
                original_metadata["orientation"] == result_metadata["orientation"] and
                original_metadata["color"] == result_metadata["color"])
    except Exception:
        return False


def verify_lossless(orig_path: str, new_data: bytes, keep_color: bool = False,
                    keep_orientation: bool = True) -> bool:
    """Return True only if output frames and requested metadata are preserved.

    Verifies every frame of animated files. Returns False (never silently
    True) on decode errors or mismatches. Raises RuntimeError if Pillow is
    unavailable.
    """
    try:
        from PIL import Image  # noqa: F401
    except Exception as exc:
        raise RuntimeError("Pillow is required for verification") from exc

    try:
        with open(orig_path, "rb") as f:
            original_data = f.read()
    except Exception:
        return False
    return verify_image_bytes(original_data, new_data, keep_color, keep_orientation)


def lossy_bytes(data: bytes, ext: str, quality: int,
                keep_orientation: bool = True,
                keep_color: bool = False) -> tuple[bytes, str]:
    """Re-encode lossily (JPEG/WebP only). Pixels WILL change."""
    try:
        from PIL import Image
    except Exception as exc:
        raise RuntimeError("Pillow is required for lossy re-encoding") from exc

    ext = ext.lower()
    if ext not in (".jpg", ".jpeg", ".webp"):
        raise ValueError("lossy re-encoding supports JPEG and WebP only")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if getattr(im, "is_animated", False):
                raise ValueError("lossy re-encoding does not support animated images")
            exif = im.getexif()
            keep_exif = bool(keep_orientation and exif.get(ORIENTATION_TAG))
            icc_profile = im.info.get("icc_profile") if keep_color else None

            if ext in (".jpg", ".jpeg"):
                kwargs = {"quality": quality, "optimize": True, "progressive": True}
                if keep_exif:
                    kwargs["exif"] = exif.tobytes()
                if icc_profile:
                    kwargs["icc_profile"] = icc_profile
                out = io.BytesIO()
                im.save(out, "JPEG", **kwargs)
                return out.getvalue(), f"lossy-jpeg q{quality}"

            if ext == ".webp":
                kwargs = {"quality": quality, "method": 6}
                if keep_exif:
                    kwargs["exif"] = exif.tobytes()
                if icc_profile:
                    kwargs["icc_profile"] = icc_profile
                out = io.BytesIO()
                im.save(out, "WEBP", **kwargs)
                return out.getvalue(), f"lossy-webp q{quality}"
    except (RuntimeError, ValueError):
        raise
    except Exception as exc:
        raise RuntimeError(f"lossy re-encoding failed: {exc}") from exc
    raise ValueError("unsupported lossy image format")
