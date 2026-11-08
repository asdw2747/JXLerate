# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Command-line interface for JXLerate."""

from __future__ import annotations

import argparse
import shutil
import sys

from .core import RunOptions, run_batch
from .dedupe import dedupe_entries
from .fs import collect_file_entries
from .imaging import have_pillow
from .manifest import verify_manifest, write_manifest
from .version import __version__


class ConsoleReporter:
    """Print pipeline events to stdout/stderr with optional quiet mode."""

    def __init__(self, quiet: bool = False):
        self.quiet = quiet

    def start(self, total: int) -> None:
        pass

    def progress(self, done: int, total: int, name: str) -> None:
        pass

    def log(self, text: str) -> None:
        if not self.quiet:
            print(text)

    def error(self, text: str) -> None:
        print(text, file=sys.stderr)

    def notice(self, text: str) -> None:
        print(text, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="JXLerate - lossless image archiver and optimizer.")
    ap.add_argument("--version", action="version",
                    version=f"%(prog)s {__version__}")
    ap.add_argument("inputs", nargs="*", help="files or directories")
    ap.add_argument("-r", "--recursive", action="store_true", help="recurse into directories")
    ap.add_argument("-o", "--output-dir",
                    help="write stripped files into this directory (mirrors structure)")
    ap.add_argument("--suffix", default="",
                    help="append suffix to basename before extension (e.g. _stripped)")
    ap.add_argument("--backup", action="store_true", help="keep a .bak of the original")
    ap.add_argument("--dry-run", action="store_true", help="report only, don't write")
    ap.add_argument("--verify", action="store_true",
                    help="verify decoded pixels and preserved metadata (requires Pillow)")
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
    ap.add_argument("--patch-logs", action="store_true",
                    help="with --to-jxl --replace, update .html/.json/.txt image references")
    ap.add_argument("--manifest", metavar="FILE",
                    help="write a SHA-256 CSV manifest of results")
    ap.add_argument("--verify-manifest", metavar="FILE",
                    help="verify an existing manifest and exit")
    ap.add_argument("--dedupe", action="store_true",
                    help="hardlink byte-identical images with matching metadata and exit")
    ap.add_argument("--passthrough", action="store_true",
                    help="copy non-image files into the output folder too (requires -o)")
    ap.add_argument("--skip-clean", action="store_true",
                    help="do not write files that have nothing to strip")
    ap.add_argument("--only-if-smaller", action="store_true",
                    help="only write when the result is smaller (implies --skip-clean)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip planned outputs that already exist (resume -o/--suffix runs)")
    ap.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                    help="process up to N files in parallel (0 = all CPU cores; default 1)")
    ap.add_argument("-q", "--quiet", action="store_true", help="suppress per-file output")
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()

    if args.passthrough and not args.output_dir:
        ap.error("--passthrough requires --output-dir")
    if args.jobs < 0:
        ap.error("--jobs must be 0 or greater (0 = all CPU cores)")
    if args.verify and not have_pillow():
        ap.error("--verify requires Pillow (pip install Pillow)")
    if args.lossy_quality is not None:
        if not 1 <= args.lossy_quality <= 100:
            ap.error("--lossy-quality must be between 1 and 100")
        if not have_pillow():
            ap.error("--lossy-quality requires Pillow (pip install Pillow)")
        if args.verify:
            print("warning: --verify is ignored in lossy mode (pixels change).",
                  file=sys.stderr)
            args.verify = False
    if args.to_jxl and args.lossy_quality is not None:
        ap.error("--to-jxl cannot be combined with --lossy-quality")
    if args.replace and not args.to_jxl:
        ap.error("--replace requires --to-jxl")
    if args.patch_logs and not (args.to_jxl and args.replace):
        ap.error("--patch-logs requires --to-jxl and --replace")
    if args.replace and args.output_dir:
        ap.error("--replace cannot be combined with --output-dir")
    if args.to_jxl and not (shutil.which("cjxl") and shutil.which("djxl")):
        ap.error("--to-jxl requires cjxl and djxl (JPEG XL tools)")

    if args.verify_manifest:
        checked, failures = verify_manifest(args.verify_manifest)
        print(f"Manifest: {checked} file(s) checked, {failures} failure(s)",
              file=sys.stderr)
        sys.exit(1 if failures else 0)

    if not args.inputs:
        ap.error("the following arguments are required: inputs")

    if args.dedupe:
        if args.to_jxl or args.optimize or args.lossy_quality is not None \
                or args.output_dir or args.manifest or args.skip_existing:
            ap.error("--dedupe cannot be combined with processing options")
        print("Note: hardlinked paths share later content/metadata changes and "
              "access time; creating links changes ctime. Files with differing "
              "mode/owner/mtime/xattrs are skipped.", file=sys.stderr)
        entries = collect_file_entries(args.inputs, args.recursive)
        links, saved, failures = dedupe_entries(entries, args.dry_run)
        print(f"Dedupe: {links} hardlink(s), {saved} bytes saved, "
              f"{failures} failure(s)", file=sys.stderr)
        sys.exit(1 if failures else 0)

    options = RunOptions(
        inputs=tuple(args.inputs),
        recursive=args.recursive,
        output_dir=args.output_dir,
        suffix=args.suffix,
        backup=args.backup,
        verify=args.verify,
        keep_color=args.keep_color,
        keep_orientation=not args.strip_orientation,
        optimize=args.optimize,
        max_effort=args.max_effort,
        progressive=args.progressive,
        lossy_quality=args.lossy_quality,
        to_jxl=args.to_jxl,
        replace=args.replace,
        patch_logs=args.patch_logs,
        dry_run=args.dry_run,
        passthrough=args.passthrough,
        skip_clean=args.skip_clean,
        only_if_smaller=args.only_if_smaller,
        manifest=args.manifest,
        quiet=args.quiet,
        jobs=args.jobs,
        skip_existing=args.skip_existing,
    )

    summary = run_batch(options, ConsoleReporter(args.quiet))

    if summary.error:
        ap.error(summary.error)
    if summary.no_images:
        print("no image files found", file=sys.stderr)
        sys.exit(1)

    if args.manifest:
        try:
            write_manifest(args.manifest, summary.manifest_rows)
            print(f"Manifest: wrote {len(summary.manifest_rows)} row(s) to "
                  f"{args.manifest}", file=sys.stderr)
        except Exception as exc:
            print(f"MANIFEST FAIL {args.manifest}: {exc}", file=sys.stderr)
            summary.failed += 1

    print(f"\nDone: {summary.ok} ok, {summary.skipped} skipped, "
          f"{summary.failed} failed, total saved {summary.saved} bytes",
          file=sys.stderr)
    if summary.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
