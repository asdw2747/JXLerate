# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Archive-aware patching of image references in text logs.

Only explicit ``--patch-logs`` runs reach this module.  The helpers are
deliberately conservative: they match whole filenames (never substrings),
preserve the file's encoding and BOM, and write atomically.
"""

from __future__ import annotations

import codecs
import os
import re
import threading

from .fs import canonical_path, write_file_atomic

LOG_EXTENSIONS = frozenset({".html", ".json", ".txt"})

# Longest BOMs must be checked first: the UTF-32-LE BOM starts with the
# UTF-16-LE BOM.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

# Serialize read-modify-write cycles: parallel --jobs workers may touch the
# same log file.
_PATCH_LOCK = threading.Lock()


def log_search_roots(inputs, recursive: bool):
    """Return ``[(directory, recurse), ...]`` for log discovery.

    Directory inputs are scanned recursively when ``--recursive`` is set.
    Explicit file inputs only contribute their containing folder, without
    recursing into unrelated subfolders.
    """
    roots = {}
    for path in inputs:
        absolute = os.path.abspath(os.fspath(path))
        if os.path.isdir(absolute):
            root, recurse = absolute, bool(recursive)
        elif os.path.isfile(absolute):
            root, recurse = os.path.dirname(absolute), False
        else:
            continue
        roots[root] = roots.get(root, False) or recurse

    selected = []
    keys = set()
    for root, recurse in roots.items():
        key = canonical_path(root)
        if key in keys:
            continue
        keys.add(key)
        selected.append((root, recurse))
    return selected


def _iter_log_files(roots):
    """Yield candidate log paths, skipping duplicates across roots."""
    seen = set()
    for root, recursive in roots:
        if not os.path.isdir(root):
            continue
        if recursive:
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    suffix = os.path.splitext(name)[1].lower()
                    if suffix not in LOG_EXTENSIONS:
                        continue
                    path = os.path.join(dirpath, name)
                    key = canonical_path(path)
                    if key not in seen:
                        seen.add(key)
                        yield path
        else:
            try:
                entries = os.scandir(root)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    suffix = os.path.splitext(entry.name)[1].lower()
                    if suffix not in LOG_EXTENSIONS:
                        continue
                    try:
                        if not entry.is_file():
                            continue
                    except OSError:
                        continue
                    key = canonical_path(entry.path)
                    if key not in seen:
                        seen.add(key)
                        yield entry.path


def _filename_pattern(name: str) -> re.Pattern:
    """Match a complete filename, not a substring of a longer name.

    The lookarounds exclude word characters, dots, and hyphens so that
    ``cat.png`` does not match ``copycat.png``, ``my-cat.png``, or
    ``cat.png.bak``.  Characters such as ``/``, ``\\``, quotes, commas,
    ``?``, ``#`` and ``)`` are valid boundaries.
    """
    return re.compile(
        r"(?<![A-Za-z0-9_.-])" + re.escape(name) + r"(?![A-Za-z0-9_.-])",
        re.IGNORECASE)


def _decode(raw: bytes):
    """Return ``(text, encoding, bom)`` or ``None`` for unknown encodings."""
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            try:
                return raw[len(bom):].decode(encoding), encoding, bom
            except UnicodeDecodeError:
                return None
    try:
        return raw.decode("utf-8"), "utf-8", b""
    except UnicodeDecodeError:
        return None


def _patch_file(path: str, pattern: re.Pattern, new_name: str) -> bool:
    with _PATCH_LOCK:
        with open(path, "rb") as handle:
            raw = handle.read()

        decoded = _decode(raw)
        if decoded is None:
            return False
        text, encoding, bom = decoded

        updated, count = pattern.subn(new_name, text)
        if not count or updated == text:
            return False

        write_file_atomic(
            path, bom + updated.encode(encoding), preserve_mode=True)
        return True


def patch_archive_logs(roots, old_filename: str,
                       new_filename: str) -> tuple[int, int]:
    """Replace whole-filename references to an image in nearby text logs.

    ``roots`` is an iterable of ``(directory, recurse)`` pairs from
    :func:`log_search_roots`.  Returns ``(patched, failed)`` counts.  Matching
    is case-insensitive; the replacement is the real new filename, which keeps
    references correct on case-sensitive filesystems.
    """
    old_name = os.path.basename(old_filename)
    new_name = os.path.basename(new_filename)
    if not old_name or not new_name or old_name == new_name:
        return 0, 0

    pattern = _filename_pattern(old_name)
    patched = 0
    failed = 0
    for path in _iter_log_files(roots):
        try:
            if _patch_file(path, pattern, new_name):
                patched += 1
        except Exception:
            failed += 1
    return patched, failed
