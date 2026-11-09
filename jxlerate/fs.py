# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Filesystem helpers: atomic writes, collection, planning, and safety."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import time

from .formats import IMAGE_EXTS


def is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXTS


def _replace_with_retry(source: str, destination: str) -> None:
    """Replace ``destination``, retrying transient Windows sharing errors."""
    attempts = 5 if os.name == "nt" else 1
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            transient = (
                os.name == "nt"
                and (isinstance(exc, PermissionError)
                     or getattr(exc, "winerror", None) in (5, 32, 33))
            )
            if not transient or attempt == attempts - 1:
                raise
            time.sleep(0.01 * (2 ** attempt))


def write_file_atomic(path: str, data: bytes,
                      keep_stat_from: str | None = None,
                      preserve_mode: bool = False) -> None:
    """Write data atomically, preserving mode/mtime and following symlinks.

    keep_stat_from is the original file whose permissions and timestamps
    should be carried over to the replacement.  preserve_mode keeps the
    destination's permission bits without carrying over its timestamps.
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
    elif preserve_mode:
        try:
            source_stat = os.stat(target)
        except OSError:
            source_stat = None

    fd, tmp = tempfile.mkstemp(prefix=".strip-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if source_stat is not None:
            os.chmod(tmp, stat.S_IMODE(source_stat.st_mode))
            if keep_stat_from:
                os.utime(
                    tmp,
                    ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
        _replace_with_retry(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
                        if is_image(nm):
                            entries.append((os.path.join(root, nm), base))
            else:
                for nm in os.listdir(p):
                    fp = os.path.join(p, nm)
                    if os.path.isfile(fp) and is_image(nm):
                        entries.append((fp, base))
        elif os.path.isfile(p) and is_image(p):
            entries.append((p, None))
    return entries


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
                        if is_image(nm):
                            continue
                        rel = os.path.relpath(src, base)
                        if prefix_roots:
                            rel = os.path.join(os.path.basename(base), rel)
                        entries.append((src, rel))
            else:
                for nm in os.listdir(p):
                    src = os.path.join(p, nm)
                    if not os.path.isfile(src) or is_image(nm):
                        continue
                    rel = os.path.join(os.path.basename(base), nm) if prefix_roots else nm
                    entries.append((src, rel))
        elif os.path.isfile(p) and not is_image(p):
            entries.append((p, os.path.basename(p)))
    return entries


def copy_passthrough(entries, output_dir, skip_existing=False):
    count = 0
    for src, rel in entries:
        dst = os.path.join(output_dir, rel)
        if canonical_path(src) == canonical_path(dst):
            continue
        if skip_existing and os.path.exists(dst):
            continue
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


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


def plan_output_path(fp, base_dir, output_dir, suffix, prefix_roots,
                     to_jxl: bool = False) -> str:
    """Return the destination path for one input, preserving CLI semantics."""
    if output_dir:
        path = output_path_for(fp, base_dir, output_dir, suffix, prefix_roots)
    elif to_jxl:
        folder, name = os.path.split(fp)
        stem, _ = os.path.splitext(name)
        path = os.path.join(folder, stem + suffix + ".jxl")
    elif suffix:
        name, ext = os.path.splitext(os.path.basename(fp))
        path = os.path.join(os.path.dirname(fp), name + suffix + ext)
    else:
        path = fp

    if to_jxl:
        stem, _ = os.path.splitext(path)
        return stem + ".jxl"
    return path


def jxl_output_path(fp, base_dir, output_dir, suffix, prefix_roots):
    """Backwards-compatible wrapper around :func:`plan_output_path`."""
    return plan_output_path(fp, base_dir, output_dir, suffix, prefix_roots,
                            to_jxl=True)



def canonical_path(path: str) -> str:
    """Normalize a path for collision checks, resolving existing symlinks."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def validate_output_plan(output_pairs, source_paths, manifest_path=None) -> None:
    """Fail before processing if outputs would collide with each other/inputs."""
    source_keys = {canonical_path(path) for path in source_paths}
    destinations = {}

    for source, destination in output_pairs:
        source_key = canonical_path(source)
        destination_key = canonical_path(destination)

        if destination_key in destinations:
            other = destinations[destination_key]
            raise ValueError(
                f"multiple inputs map to the same output: {other} and {source} "
                f"-> {destination}")
        destinations[destination_key] = source

        if destination_key in source_keys and destination_key != source_key:
            raise ValueError(
                f"output for {source} would overwrite a different input: {destination}")

    if manifest_path:
        manifest_key = canonical_path(manifest_path)
        if manifest_key in source_keys:
            raise ValueError(f"manifest path would overwrite an input: {manifest_path}")
        if manifest_key in destinations:
            raise ValueError(f"manifest path conflicts with an output: {manifest_path}")
        # realpath does not collapse hardlinks; opening the manifest for write
        # would truncate every hardlink to the same inode, including an input.
        for source in source_paths:
            try:
                if os.path.samefile(manifest_path, source):
                    raise ValueError(
                        f"manifest path is a hardlink to an input: {manifest_path}")
            except OSError:
                pass
        for _, destination in output_pairs:
            try:
                if os.path.samefile(manifest_path, destination):
                    raise ValueError(
                        f"manifest path is a hardlink to an output: {manifest_path}")
            except OSError:
                pass


def validate_manifest_location(manifest_path, inputs) -> None:
    """Do not overwrite an existing file inside an input tree as the manifest."""
    if manifest_path and os.path.isdir(manifest_path):
        raise ValueError(f"manifest path is a directory: {manifest_path}")
    if not manifest_path or not os.path.exists(manifest_path):
        return
    manifest_key = canonical_path(manifest_path)
    for path in inputs:
        if os.path.isfile(path) and canonical_path(path) == manifest_key:
            raise ValueError(f"manifest path would overwrite an input: {manifest_path}")
        if os.path.isdir(path):
            root = canonical_path(path)
            try:
                common = os.path.commonpath((root, manifest_key))
            except ValueError:
                continue
            if common == root:
                raise ValueError(
                    f"manifest path is inside an input folder and would overwrite "
                    f"an existing file: {manifest_path}")
