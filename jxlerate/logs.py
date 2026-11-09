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
from collections.abc import Callable
from urllib.parse import quote, unquote

from .fs import canonical_path, write_file_atomic

LOG_EXTENSIONS = frozenset({".html", ".json", ".txt"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

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
# same log file.  The reentrant lock lets a caller hold it across discovery
# and patching while helpers re-enter for each individual file.
_PATCH_LOCK = threading.RLock()


def _export_files_parent(path: str) -> str | None:
    """Return the export folder for a ``*.html_Files`` tree.

    ``path`` may be the files folder itself or a nested input inside it.  The
    returned parent is where the chat HTML lives, so log discovery can find it
    even when the user only selected the media folder.
    """
    current = path
    while True:
        name = os.path.basename(current)
        if name.lower().endswith(".html_files"):
            parent = os.path.dirname(current)
            if parent and parent != current:
                return parent
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def log_search_roots(inputs, recursive: bool):
    """Return ``[(directory, recurse), ...]`` for log discovery.

    Directory inputs are scanned recursively when ``--recursive`` is set.
    Explicit file inputs only contribute their containing folder, without
    recursing into unrelated subfolders.

    Inputs inside a DiscordChatExporter ``*.html_Files`` folder also contribute
    the parent export folder (non-recursively), because the chat log is a
    sibling of the media folder rather than inside it.
    """
    roots = {}
    for path in inputs:
        absolute = os.path.abspath(os.fspath(path))
        if os.path.isdir(absolute):
            root, recurse = absolute, bool(recursive)
            export_parent = _export_files_parent(absolute)
        elif os.path.isfile(absolute):
            root, recurse = os.path.dirname(absolute), False
            export_parent = _export_files_parent(root)
        else:
            continue
        roots[root] = roots.get(root, False) or recurse
        if export_parent and export_parent not in roots:
            roots[export_parent] = False

    selected = []
    keys = set()
    for root, recurse in roots.items():
        key = canonical_path(root)
        if key in keys:
            continue
        keys.add(key)
        selected.append((root, recurse))
    return selected


def _iter_files(roots, extensions):
    """Yield files under ``roots`` whose suffix is in ``extensions``."""
    seen = set()
    for root, recursive in roots:
        if not os.path.isdir(root):
            continue
        if recursive:
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    suffix = os.path.splitext(name)[1].lower()
                    if suffix not in extensions:
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
                    if suffix not in extensions:
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


def _iter_log_files(roots):
    """Yield candidate log paths, skipping duplicates across roots."""
    yield from _iter_files(roots, LOG_EXTENSIONS)


# Boundaries keep matches from starting or ending inside a longer filename.
# A preceding percent-encoded tag (``%20`` or ``%u2019``) is also accepted as
# a delimiter, because DiscordChatExporter encodes the surrounding path that
# way.  The lookbehinds are fixed-width so they remain valid in Python's
# regex engine.
_LEADING_BOUNDARY = (
    r"(?:(?<![A-Za-z0-9_.-])"
    r"|(?<=%u[0-9A-Fa-f]{4})"
    r"|(?<=%[0-9A-Fa-f]{2}))"
)
_TRAILING_BOUNDARY = (
    r"(?![A-Za-z0-9_.-]|%[0-9A-Fa-f]{2}|%u[0-9A-Fa-f]{4})"
)

# Broad reference scan used by the reconciliation pass.  The token excludes
# whitespace, quotes, HTML delimiters, query/fragment markers, and separators
# so a match is just the image path (the surrounding ``src=``/quotes stay
# untouched).
_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?P<ref>[^\s\"'<>?#=&(),;]+?\.(?:jpe?g|png|webp|gif))"
    + _TRAILING_BOUNDARY,
    re.IGNORECASE)
_JS_ESCAPE = re.compile(r"%u([0-9A-Fa-f]{4})")
_HASH_SUFFIX = re.compile(r"-[a-fA-F0-9]{8,}$")


def _filename_char_pattern(char: str) -> str:
    """Return a regex atom that matches one character of a disk filename.

    Literal text is preferred, but percent-encoded spellings are accepted for
    characters DiscordChatExporter encodes.  ``%`` also matches ``%25`` so
    double-encoded names such as ``My%2520File`` can match a disk file named
    ``My%20File``.  Non-ASCII characters additionally accept the JavaScript
    ``%uXXXX`` form (for example ``%u2019``).
    """
    escaped = re.escape(char)
    if char == " ":
        return r"(?: |%20)"
    if char == "%":
        return r"(?:%|%25)"
    if char.isascii() and (char.isalnum() or char in "_-~"):
        return escaped

    encoded = quote(char, safe="")
    alternatives = [escaped]
    if encoded != char:
        alternatives.append(re.escape(encoded))
    if not char.isascii() and ord(char) <= 0xFFFF:
        alternatives.append(re.escape(f"%u{ord(char):04X}"))
    if len(alternatives) == 1:
        return escaped
    return "(?:" + "|".join(alternatives) + ")"


def _filename_pattern(name: str) -> re.Pattern | None:
    """Match an isolated asset basename with optional Discord-style hash.

    Directory components are discarded before compilation, so encoded parent
    folders can never prevent a match.  DiscordChatExporter appends a short hex
    tracking hash to references (``IMG_7320-5eb75cfcf2d6578914.jpg``) while the
    file on disk keeps its clean name; the pattern therefore matches the base
    name, an optional ``-<hex hash>`` group, and the original extension.

    The lookarounds reject word characters, dots, and hyphens so that
    ``cat.png`` does not match ``copycat.png``, ``my-cat.png``, or
    ``cat.png.bak``.  Characters such as ``/``, ``\\``, quotes, commas, ``?``,
    ``#`` and ``)`` are valid boundaries, as are encoded punctuation tags.

    Bare names without an extension return ``None``: matching those would
    over-match unrelated text.
    """
    name = os.path.basename(os.fspath(name))
    base_name, old_ext = os.path.splitext(name)
    if not old_ext:
        return None

    stem = "".join(_filename_char_pattern(char) for char in base_name)
    return re.compile(
        _LEADING_BOUNDARY
        + r"(?P<stem>" + stem + r")"
        + r"(?P<hash>-[a-fA-F0-9]{8,})?"
        + re.escape(old_ext)
        + _TRAILING_BOUNDARY,
        re.IGNORECASE)


def _replacement(old_name: str,
                 new_name: str) -> Callable[[re.Match], str]:
    """Keep any captured hash and encoded stem, swapping only the extension."""
    old_base, _ = os.path.splitext(old_name)
    new_base, new_ext = os.path.splitext(new_name)

    def replace(match: re.Match) -> str:
        stem = match.group("stem")
        # Preserve the reference's encoded spelling when the basename itself
        # is unchanged.  Plain names still use the real new base, which keeps
        # case and --suffix behavior identical to v2.4.1.
        base = stem if ("%" in stem and new_base == old_base) else new_base
        return base + (match.group("hash") or "") + new_ext

    return replace


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


def _rewrite_log_file(path: str, transform: Callable[[str], str]) -> bool:
    """Apply ``transform`` and write atomically when the text changes."""
    with _PATCH_LOCK:
        with open(path, "rb") as handle:
            raw = handle.read()

        decoded = _decode(raw)
        if decoded is None:
            return False
        text, encoding, bom = decoded

        updated = transform(text)
        if updated == text:
            return False

        write_file_atomic(
            path, bom + updated.encode(encoding), preserve_mode=True)
        return True


def _patch_file(path: str, pattern: re.Pattern,
                replace: Callable[[re.Match], str]) -> bool:
    return _rewrite_log_file(
        path, lambda text: pattern.sub(replace, text))


def patch_archive_logs(roots, old_filename: str,
                       new_filename: str) -> tuple[int, int]:
    """Replace whole-filename references to an image in nearby text logs.

    ``roots`` is an iterable of ``(directory, recurse)`` pairs from
    :func:`log_search_roots`.  Returns ``(patched, failed)`` counts.  Matching
    is case-insensitive, accepts an optional Discord-style trailing hex hash,
    and tolerates percent-encoded characters inside the basename.  The hash and
    any encoded spelling are preserved verbatim; only the extension changes.
    The replacement base is the real new filename, which keeps references
    correct on case-sensitive filesystems.
    """
    old_name = os.path.basename(old_filename)
    new_name = os.path.basename(new_filename)
    if not old_name or not new_name or old_name == new_name:
        return 0, 0

    pattern = _filename_pattern(old_name)
    if pattern is None:
        return 0, 0

    replace = _replacement(old_name, new_name)
    patched = 0
    failed = 0
    with _PATCH_LOCK:
        paths = list(_iter_log_files(roots))
        for path in paths:
            try:
                if _patch_file(path, pattern, replace):
                    patched += 1
            except Exception:
                failed += 1
    return patched, failed


def _decode_reference(text: str) -> str:
    """Decode percent escapes and JavaScript ``%uXXXX`` escapes."""
    return unquote(_JS_ESCAPE.sub(
        lambda match: chr(int(match.group(1), 16)), text))


def _resolved_jxl_paths(target: str, log_path: str):
    """Yield canonical candidate paths for ``target`` relative to the log."""
    log_dir = os.path.dirname(os.path.abspath(log_path))
    if os.path.isabs(target):
        yield canonical_path(target)
        return

    yield canonical_path(os.path.join(log_dir, target))

    # A chat log moved inside its own ``*_Files`` folder still carries paths
    # such as ``Export.html_Files/name.jpg``.  Try those relative to the log
    # as well.
    prefix = os.path.basename(log_dir) + "/"
    if target.startswith(prefix):
        yield canonical_path(os.path.join(log_dir, target[len(prefix):]))


def _reconcile_reference(match: re.Match, log_path: str,
                         jxl_paths: set) -> str:
    """Swap the extension when the matching ``.jxl`` file already exists."""
    ref = match.group("ref")
    if "://" in ref or ref.startswith("//"):
        return ref

    decoded = _decode_reference(ref)
    stem, ext = os.path.splitext(decoded)
    if ext.lower() not in IMAGE_EXTENSIONS:
        return ref

    targets = [stem]
    clean = _HASH_SUFFIX.sub("", stem)
    if clean != stem:
        targets.append(clean)

    for base in targets:
        for candidate in _resolved_jxl_paths(base + ".jxl", log_path):
            if candidate in jxl_paths:
                return ref[:-len(ext)] + ".jxl"
    return ref


def patch_existing_jxl_logs(roots, dry_run: bool = False) -> tuple[int, int]:
    """Patch references whose matching ``.jxl`` file already exists on disk.

    Unlike :func:`patch_archive_logs`, this does not need the original image or
    a conversion in the current batch.  It lets ``--patch-logs`` repair HTML
    after images were converted by an earlier run, were skipped by
    ``--skip-existing``, or no longer exist because ``--replace`` removed them.

    Returns ``(patched_log_files, failed_log_files)``.  Remote URLs, missing
    targets, and references whose basename does not resolve to an existing
    ``.jxl`` file are left untouched.
    """
    jxl_paths = {
        canonical_path(path)
        for path in _iter_files(roots, frozenset({".jxl"}))
    }
    if not jxl_paths:
        return 0, 0

    patched = 0
    failed = 0
    with _PATCH_LOCK:
        paths = list(_iter_log_files(roots))
        for path in paths:
            try:
                if dry_run:
                    with open(path, "rb") as handle:
                        decoded = _decode(handle.read())
                    if decoded is None:
                        continue
                    updated = _REFERENCE_PATTERN.sub(
                        lambda match, log_path=path: _reconcile_reference(
                            match, log_path, jxl_paths),
                        decoded[0])
                    if updated != decoded[0]:
                        patched += 1
                elif _rewrite_log_file(
                        path,
                        lambda text, log_path=path: _REFERENCE_PATTERN.sub(
                            lambda match: _reconcile_reference(
                                match, log_path, jxl_paths),
                            text)):
                    patched += 1
            except Exception:
                if not dry_run:
                    failed += 1
    return patched, failed
