# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""JXLerate - lossless image archiver and optimizer.

The package is split into focused modules, but the historical flat-module
import surface remains available here for compatibility::

    import jxlerate
    jxlerate.strip_png(...)

New code should import from the specific module (for example
``jxlerate.formats.png`` or ``jxlerate.core``).
"""

from __future__ import annotations

from .version import __version__

# Format parsers and dispatch.
from .formats import (
    IMAGE_EXTS,
    is_animated as _is_animated,
    optimize_bytes,
    strip_bytes,
)
from .formats.exif import (
    ORIENTATION_TAG,
    build_minimal_tiff,
    jpeg_orientation_segment,
    png_orientation_chunk,
    tiff_orientation,
)
from .formats.gif import GIF_APP_ICC, GIF_KEEP_APPS, strip_gif
from .formats.jpeg import (
    JPEG_KEEP_APP,
    JPEG_KEEP_APP_COLOR,
    JPEG_KEEP_MARKERS,
    optimize_jpeg as _optimize_jpeg,
    strip_jpeg,
)
from .formats.png import (
    PNG_COLOR,
    PNG_SIG,
    PNG_STRUCTURAL,
    has_chunk as _png_has_chunk,
    optimize_png as _optimize_png,
    strip_png,
)
from .formats.webp import (
    WEBP_KEEP_CHUNKS,
    has_chunk as _webp_has_chunk,
    optimize_webp as _optimize_webp,
    strip_webp,
)

# Shared helpers.
from .tools import run_tool as _run_tool
from .imaging import (
    PIL_COLOR_INFO_KEYS,
    frame_signatures as _frame_signatures,
    have_pillow,
    lossy_bytes as _lossy_bytes,
    verify_image_bytes,
    verify_lossless,
)
from .jxl import (
    to_jxl_file,
    transcode_jxl as _transcode_jxl,
)
from .fs import (
    canonical_path,
    collect_file_entries,
    collect_passthrough_entries,
    copy_passthrough,
    jxl_output_path,
    output_path_for,
    plan_output_path,
    sha256_file,
    validate_manifest_location,
    validate_output_plan,
    write_file_atomic,
)
from .manifest import MANIFEST_FIELDS, write_manifest, verify_manifest
from .dedupe import (
    dedupe_entries,
    hardlink_metadata_signature as _hardlink_metadata_signature,
)
from .core import (
    FileResult,
    RunOptions,
    RunSummary,
    process_file,
    run_batch,
)

# Historical entry point.
from .cli import main

__all__ = [
    "__version__",
    "IMAGE_EXTS",
    "ORIENTATION_TAG",
    "PIL_COLOR_INFO_KEYS",
    "MANIFEST_FIELDS",
    "RunOptions",
    "FileResult",
    "RunSummary",
    "main",
    "run_batch",
    "process_file",
    "strip_bytes",
    "strip_png",
    "strip_jpeg",
    "strip_webp",
    "strip_gif",
    "optimize_bytes",
    "have_pillow",
    "verify_image_bytes",
    "verify_lossless",
    "to_jxl_file",
    "collect_file_entries",
    "collect_passthrough_entries",
    "copy_passthrough",
    "output_path_for",
    "jxl_output_path",
    "plan_output_path",
    "validate_output_plan",
    "validate_manifest_location",
    "write_file_atomic",
    "write_manifest",
    "verify_manifest",
    "dedupe_entries",
]
