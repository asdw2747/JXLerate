# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Integrity manifest writing, row construction, and verification."""

from __future__ import annotations

import csv
import os
import sys

from .fs import sha256_file

MANIFEST_FIELDS = ["source", "output", "source_size", "output_size",
                   "sha256", "tool", "status"]


def manifest_relative(path: str, base_dir: str) -> str:
    """Return a manifest path relative to the manifest's own directory."""
    if not path:
        return ""
    return os.path.relpath(os.path.abspath(path), base_dir)


def manifest_row(source, output, source_size, output_size, digest,
                 tool, status, base_dir: str) -> list:
    return [
        manifest_relative(source, base_dir),
        manifest_relative(output, base_dir),
        source_size,
        output_size,
        digest,
        tool,
        status,
    ]


def write_manifest(path: str, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(MANIFEST_FIELDS)
        writer.writerows(rows)


def verify_manifest(path: str) -> tuple[int, int]:
    """Return (checked, failures)."""
    checked = 0
    failures = 0
    base = os.path.dirname(os.path.abspath(path))
    rows_seen = 0
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or any(
                    field not in reader.fieldnames for field in MANIFEST_FIELDS):
                print(f"INVALID MANIFEST {path}: missing or invalid header",
                      file=sys.stderr)
                return 0, 1

            for row in reader:
                rows_seen += 1
                if None in row:
                    print(f"INVALID MANIFEST {path}: malformed row {rows_seen + 1}",
                          file=sys.stderr)
                    failures += 1
                    continue

                status = row.get("status") or ""
                if status.startswith("skipped:"):
                    continue
                if status == "dry-run":
                    print(f"UNVERIFIED {row.get('source')}: dry-run output was not written",
                          file=sys.stderr)
                    failures += 1
                    continue
                if status in ("failed", "verify-failed"):
                    print(f"INVALID MANIFEST {path}: source recorded as {status}: "
                          f"{row.get('source')}", file=sys.stderr)
                    failures += 1
                    continue
                if status != "written":
                    print(f"INVALID MANIFEST {path}: unknown status {status!r}",
                          file=sys.stderr)
                    failures += 1
                    continue

                output = row.get("output") or ""
                digest = row.get("sha256") or ""
                output_size = row.get("output_size") or ""
                if (not output or len(digest) != 64 or
                        any(ch not in "0123456789abcdefABCDEF" for ch in digest) or
                        not output_size.isdecimal()):
                    print(f"INVALID MANIFEST {path}: incomplete row for "
                          f"{row.get('source')}", file=sys.stderr)
                    failures += 1
                    continue
                if not os.path.isabs(output):
                    output = os.path.join(base, output)
                checked += 1
                try:
                    size = os.path.getsize(output)
                    if size != int(output_size):
                        print(f"MISMATCH {output}: size {size}", file=sys.stderr)
                        failures += 1
                        continue
                    actual = sha256_file(output)
                    if actual != digest:
                        print(f"MISMATCH {output}: sha256", file=sys.stderr)
                        failures += 1
                except Exception as e:
                    print(f"MISSING {output}: {e}", file=sys.stderr)
                    failures += 1
    except Exception as e:
        print(f"INVALID MANIFEST {path}: {e}", file=sys.stderr)
        return checked, failures + 1

    if rows_seen == 0:
        print(f"INVALID MANIFEST {path}: contains no rows", file=sys.stderr)
        failures += 1
    return checked, failures
