#!/usr/bin/env python3
"""strip_images - losslessly strip metadata from PNG and JPEG files.

Removes all non-essential chunks/markers (EXIF, text, gamma, color profiles,
comments, APP segments, etc.) while preserving the exact pixel data.

Usage:
    python3 strip_images.py file.png file.jpg ...
    python3 strip_images.py -r ./photos
    python3 strip_images.py -r ./photos -o ./stripped
    python3 strip_images.py -r ./photos -o ./stripped --passthrough
    python3 strip_images.py --verify file.png
"""
import argparse
import io
import os
import shutil
import struct
import sys
import tempfile

__version__ = "1.0.0"

PNG_SIG = b"\x89PNG\r\n\x1a\n"

# Chunks required to reconstruct the image itself.
PNG_STRUCTURAL = {
    b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS",
    b"acTL", b"fcTL", b"fdAT",  # APNG animation
}
# Chunks that affect color rendering (kept only with --keep-color).
PNG_COLOR = {b"cHRM", b"gAMA", b"iCCP", b"sRGB", b"cICP", b"mDCv", b"cLLi"}

# Frame/table markers required to decode the image.
JPEG_KEEP_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC8,  # SOF0-8
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,              # SOF9-15
    0xC4,  # DHT
    0xDB,  # DQT
    0xCC,  # DAC
    0xDD,  # DRI
}
# APP14 (Adobe) can change how color channels are interpreted, so keep it.
JPEG_KEEP_APP = {0xEE}
# APP2 often carries an ICC profile; kept only with --keep-color.
JPEG_KEEP_APP_COLOR = {0xE2}

IMAGE_EXTS = {".png", ".jpg", ".jpeg"}


# ---------------------------------------------------------------------------
# Core stripping
# ---------------------------------------------------------------------------

def strip_png(data: bytes, keep_color: bool = False) -> bytes:
    if data[:8] != PNG_SIG:
        raise ValueError("not a PNG file")

    keep = set(PNG_STRUCTURAL)
    if keep_color:
        keep |= PNG_COLOR

    out = bytearray(PNG_SIG)
    pos = 8
    n = len(data)
    first_type = None
    last_type = None

    while pos < n:
        if pos + 12 > n:
            raise ValueError("truncated PNG (incomplete chunk header)")
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        total = 12 + length
        if pos + total > n:
            raise ValueError("truncated PNG (incomplete chunk data)")
        if first_type is None:
            first_type = ctype
        last_type = ctype
        if ctype in keep:
            out.extend(data[pos:pos + total])
        pos += total

    if first_type != b"IHDR":
        raise ValueError("invalid PNG (missing IHDR)")
    if last_type != b"IEND":
        raise ValueError("invalid/truncated PNG (missing IEND)")
    return bytes(out)


def strip_jpeg(data: bytes, keep_color: bool = False) -> bytes:
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG file")

    out = bytearray(b"\xff\xd8")
    i = 2
    n = len(data)
    found_eoi = False

    while i < n:
        # Find the next marker (skip stray bytes).
        if data[i] != 0xFF:
            j = data.find(b"\xff", i)
            if j == -1:
                break
            i = j

        # Skip fill bytes (0xFF padding).
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1

        if marker == 0x00:
            # Stuffed byte; should not appear outside entropy data.
            continue
        if marker == 0xD9:  # EOI
            out += b"\xff\xd9"
            found_eoi = True
            break
        if marker == 0xD8:  # SOI
            out += b"\xff\xd8"
            continue
        if 0xD0 <= marker <= 0xD7:  # RSTn (no length)
            out += bytes([0xFF, marker])
            continue
        if marker == 0x01:  # TEM (no length)
            out += bytes([0xFF, marker])
            continue

        # Marker with a length field.
        if i + 2 > n:
            break
        length = struct.unpack(">H", data[i:i + 2])[0]
        if length < 2 or i + length > n:
            break

        if marker == 0xDA:  # SOS -> keep, plus entropy-coded scan data
            out += bytes([0xFF, marker])
            out += data[i:i + length]
            i += length
            while i < n:
                j = data.find(b"\xff", i)
                if j == -1:
                    out += data[i:]
                    i = n
                    break
                out += data[i:j]
                k = j
                while k < n and data[k] == 0xFF:
                    k += 1
                if k >= n:
                    out += data[j:]
                    i = n
                    break
                if data[k] == 0x00:
                    # Stuffed byte: copy FF 00.
                    out += data[j:k + 1]
                    i = k + 1
                    continue
                if 0xD0 <= data[k] <= 0xD7:
                    # Restart marker inside the scan.
                    out += bytes([0xFF, data[k]])
                    i = k + 1
                    continue
                # Real marker ahead; hand back to the outer loop.
                i = j
                break
            continue

        keep = marker in JPEG_KEEP_MARKERS or marker in JPEG_KEEP_APP
        if keep_color and marker in JPEG_KEEP_APP_COLOR:
            keep = True
        if keep:
            out += bytes([0xFF, marker])
            out += data[i:i + length]
        i += length

    if not found_eoi:
        raise ValueError("invalid/truncated JPEG (missing EOI marker)")
    return bytes(out)


def strip_file(path: str, keep_color: bool = False) -> tuple[bytes, int]:
    with open(path, "rb") as f:
        data = f.read()
    low = path.lower()
    if low.endswith(".png"):
        new = strip_png(data, keep_color)
    elif low.endswith(".jpg") or low.endswith(".jpeg"):
        new = strip_jpeg(data, keep_color)
    else:
        raise ValueError(f"unsupported file type: {path}")
    return new, len(data)


# ---------------------------------------------------------------------------
# Verification / writing
# ---------------------------------------------------------------------------

def have_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def verify_lossless(orig_path: str, new_data: bytes) -> bool:
    """Return True only if the stripped bytes decode to identical pixels.

    Returns False (never silently True) on decode errors or mismatches.
    Raises RuntimeError if Pillow is unavailable.
    """
    try:
        from PIL import Image
    except Exception as exc:
        raise RuntimeError("Pillow is required for verification") from exc

    try:
        with Image.open(orig_path) as im:
            im.load()
            orig = (im.mode, im.size, im.tobytes())
        with Image.open(io.BytesIO(new_data)) as im2:
            im2.load()
            new = (im2.mode, im2.size, im2.tobytes())
    except Exception:
        return False
    return orig == new


def write_file_atomic(path: str, data: bytes) -> None:
    """Write data to path atomically (temp file + os.replace)."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".strip-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# File collection / output paths
# ---------------------------------------------------------------------------

def _is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXTS


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
                        if _is_image(nm):
                            entries.append((os.path.join(root, nm), base))
            else:
                for nm in os.listdir(p):
                    fp = os.path.join(p, nm)
                    if os.path.isfile(fp) and _is_image(nm):
                        entries.append((fp, base))
        elif os.path.isfile(p):
            entries.append((p, None))
    return entries


def collect_files(inputs, recursive):
    """Backwards-compatible helper returning only image paths."""
    return [fp for fp, _ in collect_file_entries(inputs, recursive)]


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
                        if _is_image(nm):
                            continue
                        rel = os.path.relpath(src, base)
                        if prefix_roots:
                            rel = os.path.join(os.path.basename(base), rel)
                        entries.append((src, rel))
            else:
                for nm in os.listdir(p):
                    src = os.path.join(p, nm)
                    if not os.path.isfile(src) or _is_image(nm):
                        continue
                    rel = os.path.join(os.path.basename(base), nm) if prefix_roots else nm
                    entries.append((src, rel))
        elif os.path.isfile(p) and not _is_image(p):
            entries.append((p, os.path.basename(p)))
    return entries


def copy_passthrough(entries, output_dir):
    count = 0
    for src, rel in entries:
        dst = os.path.join(output_dir, rel)
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Losslessly strip metadata from PNG/JPEG files.")
    ap.add_argument("--version", action="version",
                    version=f"%(prog)s {__version__}")
    ap.add_argument("inputs", nargs="+", help="files or directories")
    ap.add_argument("-r", "--recursive", action="store_true", help="recurse into directories")
    ap.add_argument("-o", "--output-dir", help="write stripped files into this directory (mirrors structure)")
    ap.add_argument("--suffix", default="", help="append suffix to basename before extension (e.g. _stripped)")
    ap.add_argument("--backup", action="store_true", help="keep a .bak of the original")
    ap.add_argument("--dry-run", action="store_true", help="report only, don't write")
    ap.add_argument("--verify", action="store_true", help="verify pixel data is unchanged (requires Pillow)")
    ap.add_argument("--keep-color", action="store_true",
                    help="keep color/rendering metadata (iCCP/sRGB/gAMA/cHRM, JPEG ICC)")
    ap.add_argument("--passthrough", action="store_true",
                    help="copy non-image files into the output folder too (requires -o)")
    ap.add_argument("-q", "--quiet", action="store_true", help="suppress per-file output")
    args = ap.parse_args()

    if args.passthrough and not args.output_dir:
        ap.error("--passthrough requires --output-dir")
    if args.verify and not have_pillow():
        ap.error("--verify requires Pillow (pip install Pillow)")

    output_dir = os.path.abspath(args.output_dir) if args.output_dir else None
    exclude_dir = output_dir
    dir_inputs = [p for p in args.inputs if os.path.isdir(p)]
    prefix_roots = len(dir_inputs) > 1

    entries = collect_file_entries(args.inputs, args.recursive, exclude_dir)
    if not entries and not (args.passthrough and output_dir):
        print("no image files found", file=sys.stderr)
        sys.exit(1)

    passthrough_entries = []
    if args.passthrough and output_dir:
        passthrough_entries = collect_passthrough_entries(
            args.inputs, args.recursive, prefix_roots, exclude_dir)

    total_saved = 0
    ok = 0
    fail = 0

    for fp, base_dir in entries:
        try:
            new_data, orig_size = strip_file(fp, args.keep_color)
        except Exception as e:
            print(f"FAIL {fp}: {e}", file=sys.stderr)
            fail += 1
            continue

        if args.verify and not verify_lossless(fp, new_data):
            print(f"VERIFY FAILED {fp}: pixels differ or output is invalid!", file=sys.stderr)
            fail += 1
            continue

        saved = orig_size - len(new_data)
        total_saved += saved

        if output_dir:
            out_path = output_path_for(
                fp, base_dir, output_dir, args.suffix, prefix_roots)
        elif args.suffix:
            d = os.path.dirname(fp)
            name, ext = os.path.splitext(os.path.basename(fp))
            out_path = os.path.join(d, name + args.suffix + ext)
        else:
            out_path = fp

        if args.dry_run:
            if not args.quiet:
                print(f"{fp}: {orig_size} -> {len(new_data)} bytes (saved {saved})")
            ok += 1
            continue

        try:
            if args.backup and out_path == fp:
                shutil.copy2(fp, fp + ".bak")
            write_file_atomic(out_path, new_data)
        except Exception as e:
            print(f"WRITE FAIL {fp}: {e}", file=sys.stderr)
            fail += 1
            continue

        if not args.quiet:
            print(f"{fp}: {orig_size} -> {len(new_data)} bytes (saved {saved}) -> {out_path}")
        ok += 1

    if passthrough_entries:
        if args.dry_run:
            if not args.quiet:
                print(f"Passthrough: would copy {len(passthrough_entries)} non-image file(s).",
                      file=sys.stderr)
        else:
            try:
                n = copy_passthrough(passthrough_entries, output_dir)
                if not args.quiet:
                    print(f"Passthrough: copied {n} non-image file(s).", file=sys.stderr)
            except Exception as e:
                print(f"PASSTHROUGH FAIL: {e}", file=sys.stderr)
                fail += 1

    print(f"\nDone: {ok} ok, {fail} failed, total saved {total_saved} bytes",
          file=sys.stderr)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
