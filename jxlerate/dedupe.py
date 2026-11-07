# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Safe hardlink deduplication."""

from __future__ import annotations

import os
import stat
import sys

from .fs import sha256_file


def hardlink_metadata_signature(path: str):
    """Metadata that would become shared if two paths were hardlinked."""
    st = os.stat(path)
    xattrs = ()
    if hasattr(os, "listxattr") and hasattr(os, "getxattr"):
        try:
            xattrs = tuple((name, os.getxattr(path, name))
                           for name in sorted(os.listxattr(path)))
        except OSError:
            return None
    return (stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid,
            st.st_mtime_ns, xattrs)


def dedupe_entries(entries, dry_run: bool = False) -> tuple[int, int, int]:
    """Hardlink duplicate files. Returns (links, saved_bytes, failures)."""
    by_hash = {}
    links = 0
    saved = 0
    failures = 0

    for fp, _ in entries:
        if os.path.islink(fp):
            continue  # never replace a symlink with a hardlink
        try:
            size = os.path.getsize(fp)
            digest = sha256_file(fp)
            metadata = hardlink_metadata_signature(fp)
        except Exception as e:
            print(f"FAIL {fp}: {e}", file=sys.stderr)
            failures += 1
            continue

        if metadata is None:
            print(f"SKIP {fp}: cannot inspect xattrs safely for hardlinking")
            continue

        keepers = by_hash.setdefault(digest, {})
        keeper = keepers.get(metadata)
        if keeper is None:
            if keepers:
                print(f"SKIP {fp}: identical content but file metadata differs; "
                      "hardlinking would merge that metadata")
            keepers[metadata] = fp
            continue

        try:
            if os.path.samefile(keeper, fp):
                continue
        except Exception:
            pass

        if dry_run:
            print(f"DUP {fp} == {keeper} (would save {size} bytes)")
            links += 1
            saved += size
            continue

        try:
            folder = os.path.dirname(os.path.abspath(fp)) or "."
            tmp = os.path.join(folder, f".dedupe-{os.getpid()}-{links}")
            os.link(keeper, tmp)
            os.replace(tmp, fp)
            print(f"LINK {fp} -> {keeper} (saved {size} bytes)")
            links += 1
            saved += size
        except Exception as e:
            print(f"FAIL {fp}: {e}", file=sys.stderr)
            failures += 1

    return links, saved, failures
