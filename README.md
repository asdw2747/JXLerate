# JXLerate

**Version 2.4.1**

JXLerate is a lossless image archiver and optimizer. It removes unnecessary
metadata from `.png`, `.jpg`/`.jpeg`, `.webp`, and `.gif` files, can losslessly
recompress them, transcode to JPEG XL, deduplicate identical files, and write
integrity manifests — always keeping the actual image pixel data bit-for-bit
identical.

> Renamed from `strip-images` in v2.0.0. The old command names were removed.

## Install

JXLerate requires **Python 3.10 or newer**. Installing it provides two
commands: `jxlerate` (CLI) and `jxlerate-gui` (desktop app).

The recommended method is [pipx](https://pipx.pypa.io/), which keeps the tool
isolated from other Python projects:

```sh
pipx install "git+https://github.com/asdw2747/JXLerate.git"
```

Pillow is optional. It is needed for `--verify`, lossy re-encoding, and
lossless WebP optimization, and it lets verification confirm that retained ICC
and color metadata survived:

```sh
pipx inject jxlerate Pillow
```

To install from a local clone instead (useful for development):

```sh
git clone https://github.com/asdw2747/JXLerate.git
cd JXLerate
pipx install --editable .
pipx inject jxlerate Pillow
```

Or with plain pip:

```sh
python3 -m pip install "jxlerate[images] @ git+https://github.com/asdw2747/JXLerate.git"
```

From a clone, use `python3 -m pip install ".[images]"`.

### GUI requirements

The GUI uses Tkinter, which is part of the Python standard library but is
packaged separately on some Linux distributions:

```sh
# Debian / Ubuntu
sudo apt install python3-tk

# Fedora
sudo dnf install python3-tkinter

# Arch
sudo pacman -S tk
```

macOS and Windows builds from [python.org](https://www.python.org/) include
Tkinter. With Homebrew Python, install the matching `python-tk` formula.

### Upgrading and removing

```sh
pipx reinstall jxlerate   # re-fetch the latest version from git
pipx uninstall jxlerate
```

## What gets removed

- **PNG**: all chunks except `IHDR`, `PLTE`, `IDAT`, `IEND`, `tRNS`, and APNG
  animation chunks (`acTL`, `fcTL`, `fdAT`). Removes `tEXt`/`zTXt`/`iTXt`,
  `gAMA`, `cHRM`, `iCCP`, `sRGB`, `pHYs`, `bKGD`, `tIME`, etc.
- **JPEG**: all APP/COM segments (EXIF, ICC profiles, JFIF, comments, XMP, ...).
  Keeps SOF, DQT, DHT, DAC, DRI, SOS, RST and scan data untouched. The Adobe
  `APP14` marker is preserved because it can affect color decoding.
- **WebP**: removes `EXIF`, `XMP `, and (unless `--keep-color`) `ICCP` chunks,
  and clears the matching VP8X presence flags. Keeps `VP8 `, `VP8L`, `ALPH`,
  and animation chunks.
- **GIF**: removes comment extensions and metadata-only application extensions
  (XMP, and ICC unless `--keep-color`). Keeps graphic-control, plain-text,
  image, and animation-loop extensions.

With `--keep-color`, color/rendering metadata is preserved too (PNG
`cHRM`/`gAMA`/`iCCP`/`sRGB`/`cICP`, JPEG APP2 ICC profiles, WebP/GIF ICC).

## Orientation is preserved by default

Stripping EXIF normally removes the `Orientation` tag, which makes portrait
camera photos display rotated. This tool instead keeps a **minimal** orientation
block:

- Only written when the value is 2–8; orientation 1/absent adds nothing.
- ~26-byte TIFF block (~36 bytes in a JPEG APP1, including segment header).
- GPS, dates, camera info, comments, and all other EXIF fields are still removed.
- Applies to JPEG (`APP1`), PNG/APNG (`eXIf`), and WebP (`EXIF`).
- Disable with `--strip-orientation` (CLI) or the GUI's **Preserve orientation**
  checkbox.

By default the image is never re-encoded, so there is no quality loss. Outputs
are validated (PNG `IEND`, JPEG `EOI`, GIF trailer, WebP image data) and written
atomically so an interrupted run cannot corrupt the original. File permissions
and timestamps are preserved, and symlinks are followed rather than replaced.

## Saving more space (optional)

Metadata is usually a tiny fraction of an image, so the big savings come from
pixel-lossless recompression:

- `--optimize` — losslessly recompress the image:
  - PNG via `oxipng` (falls back to `optipng`); typically **10–20% smaller**.
  - JPEG via `jpegtran -optimize`; typically **5–8% smaller**.
  - Lossless WebP re-encoded when it is already lossless.
  - Only replaces the result when it is smaller; combine with `--verify`.
  - Retained orientation and requested color-profile metadata are preserved.
- `--max-effort` — slower/better PNG compression (zopfli; can take minutes).
- `--progressive` — write progressive JPEGs when optimizing (~10% on photos,
  compared with ~7% for baseline optimization).

### JPEG XL archival transcode (`--to-jxl`)

For much larger savings, `--to-jxl` converts to JPEG XL (requires `cjxl`/`djxl`):

- **JPEG → JXL**: lossless JPEG recompression; the original JPEG can be
  reconstructed **bit-for-bit** (`djxl`). Typical savings: **~20–25%**.
- **PNG/WebP/GIF → JXL**: mathematically lossless pixel transcode. Typical
  savings: **~35–45%** on PNG, ~10–15% on existing lossless WebP.
- Every conversion is verified before it is accepted (bit-exact for JPEG,
  decoded pixels for the others), and only kept when smaller.
- Originals are kept by default. Add `--replace` to delete them after a
  verified conversion (in-place mode only).
- Add `--patch-logs` with `--replace` to update whole-filename references to
  converted images inside `.html`, `.json`, and `.txt` files under the input
  folders. Only files with an actual match are rewritten (atomically), with
  their encoding, BOM, and line endings preserved; undecodable files are left
  untouched. If a log cannot be updated, the original image is kept.
- Discord/DiscordChatExporter references carry a trailing hex tracking hash
  (`IMG_7320-5eb75cfcf2d6578914.jpg`). Matching ignores that hash and preserves
  it verbatim, so only the extension changes:
  `IMG_7320-5eb75cfcf2d6578914.jxl`. A suffix is treated as a hash only when it
  is a hyphen plus at least eight hex characters.
- Animated inputs are skipped for now.
- JXL viewer support is still limited, but it is an ISO standard
  (ISO/IEC 18181) designed for archival use.

**Lossy mode (off by default, changes pixels):**

- `--lossy-quality N` re-encodes JPEG (and static WebP) at quality 1–100.
  This permanently discards detail. It is never used unless explicitly requested,
  and it disables `--verify` (which compares pixels exactly).
  With `--keep-color`, JPEG/WebP ICC profiles are retained. PNG/GIF are not
  lossily re-encoded; requesting lossy mode for them reports an error.

### Integrity manifest

- `--manifest FILE` writes a CSV with the source/output paths, sizes, SHA-256
  of the output, tool used, and status (`written`, `dry-run`, `skipped`, …).
- `--verify-manifest FILE` re-hashes every recorded output and exits non-zero
  on any size/hash mismatch or missing file.
- Manifest paths are checked before processing so a manifest cannot overwrite
  an input or planned output. Invalid/empty manifests fail verification.

This is useful for long-term archives: keep the manifest next to the images
and re-run `--verify-manifest` periodically.

### Deduplication

- `--dedupe -r DIR` hashes image files and replaces exact duplicates with
  **hardlinks** (same filesystem only), freeing duplicate storage instantly.
- `--dedupe --dry-run` reports what would be linked.
- Files are linked only when mode, owner/group, modification time, and extended
  attributes match. Hardlinked paths share later content/metadata changes;
  access time is shared and creating a link changes ctime.
- Symlinks are never replaced. Standalone action; cannot be combined with
  stripping/transcoding options.

External tools are optional: missing ones are skipped automatically.
Optimization uses `oxipng`/`optipng`, `jpegtran`, and Pillow; JXL uses
`cjxl`/`djxl`.

## Usage

```sh
jxlerate photo.png photo.jpg photo.webp animation.gif
jxlerate -r ./photos
jxlerate -r ./photos -o ./archive
jxlerate --optimize -r ./photos
jxlerate --to-jxl -r ./photos -o ./archive
jxlerate --to-jxl --replace --patch-logs -r ./photos
jxlerate --verify --backup file.png
jxlerate --skip-clean -r ./photos
jxlerate -r ./photos -o ./archive --jobs 4
jxlerate -r ./photos -o ./archive --skip-existing
```

Options:

- `-r, --recursive` recurse into directories
- `-o, --output-dir DIR` write stripped copies instead of overwriting
- `--passthrough` copy non-image files into the output folder too (requires -o)
- `--keep-color` keep color/rendering metadata (see above)
- `--strip-orientation` remove the Orientation tag too (not recommended)
- `--optimize` losslessly recompress for extra savings
- `--max-effort` slower, better PNG optimization (zopfli)
- `--progressive` write progressive JPEGs when optimizing
- `--lossy-quality N` LOSSY JPEG/WebP re-encode (off by default; changes pixels)
- `--to-jxl` losslessly transcode to JPEG XL (requires `cjxl`/`djxl`)
- `--replace` with `--to-jxl`, delete originals after verified conversion
- `--patch-logs` with `--to-jxl --replace`, update `.html`/`.json`/`.txt` image references
- `--manifest FILE` write a SHA-256 CSV manifest of results
- `--verify-manifest FILE` verify a manifest and exit
- `--dedupe` hardlink duplicate images and exit (use with `-r`)
- `--skip-clean` do not write files that already have no metadata to strip
- `--only-if-smaller` only write when the result is smaller (implies skip-clean)
- `--skip-existing` skip planned outputs that already exist (see below)
- `-j, --jobs N` process up to N files in parallel (default 1; `0` = all CPU cores)
- `--suffix S` append suffix to filenames
- `--backup` keep a `.bak` of the original (in-place mode)
- `--dry-run` report savings without writing
- `--verify` decode original and result with Pillow and compare frames, plus
  preserved orientation/color metadata (all frames of animated files are checked)
- `-q, --quiet` suppress per-file output
- `--version` print the version number and exit

Skipped files are not written to the output folder either, so with `-o` you
get only the files that actually changed. When several input folders are given,
each folder keeps its own name in the output tree so same-named files cannot
overwrite each other.
Explicit file inputs that map to the same output name are rejected before any
files are written.

### Parallel runs and resuming

- `-j, --jobs N` processes up to N files at once. The default is `1`, so
  resource use is unchanged unless requested; `--jobs 0` uses every CPU core.
  Log lines and manifest rows stay in the original input order, and output
  planning, collision checks, passthrough copying, and deduplication remain
  serial for safety. Each file is still written atomically.
- `--skip-existing` skips any planned destination that already exists. Re-run
  an interrupted `-o`/`--suffix` batch with the same command plus this flag to
  finish only the missing files. Because writes are atomic, an existing output
  is always complete. It trusts that the source did not change in the meantime;
  use `--manifest`/`--verify-manifest` when you need content verification.

## GUI

A Tkinter GUI is included:

```sh
jxlerate-gui
```

Features:

- Add files/folders, recursion, output folder, suffix, backups, dry-run,
  verification, color preservation, orientation preservation, passthrough,
  skip-clean, only-if-smaller, and skip-existing.
- **Parallel jobs** spinbox (`0` = all CPU cores) for faster batches.
- Lossless **Optimize** (with **Max effort** and **Progressive JPEG**), plus an
  off-by-default **Lossy re-encode** with a quality spinbox and confirmation.
- **Convert to JPEG XL** with an optional **Replace originals with .jxl**
  checkbox. Lossy and same-format optimize are disabled while JXL is selected.
  **Patch log references** is available once Replace is checked.
- Determinate progress with the current filename and a **Cancel** button.
- **Open** button to launch the output folder.
- Confirmation before overwriting originals in place.
- Remembers all options, the theme, and the window position/size.
- Shortcuts: `Ctrl+O` add files, `Ctrl+Shift+O` add folder, `Ctrl+Enter` run,
  `Esc` cancel, `Ctrl+T` cycle theme.

### Theme

The **Theme: Auto / Light / Dark** selector follows the desktop preference via
GNOME `color-scheme`, then GTK `~/.config/gtk-3.0/settings.ini`, then the
`GTK_THEME` environment variable. Settings are saved in
`~/.config/jxlerate/config.json` (migrated automatically from the old
`strip-images` config on first launch).

## Requirements

Python 3.10+. The core stripping tool has no third-party dependencies.

Optional, used only for the matching features:
- **Pillow** (`images` extra) — `--verify`, lossy mode, WebP optimization
- **oxipng** / **optipng** — PNG optimization
- **jpegtran** — JPEG optimization
- **cjxl** / **djxl** — JPEG XL conversion

## Project layout

The implementation is split into focused modules so format handling and the
shared batch pipeline stay independent:

```
jxlerate/
├── cli.py          command-line parsing and validation
├── core.py         RunOptions, RunSummary, shared run_batch() pipeline
├── formats/        PNG, JPEG, WebP, GIF, and EXIF handling
├── imaging.py      verification and Pillow-based re-encoding
├── jxl.py          JPEG XL transcoding
├── tools.py        external-tool invocation
├── fs.py           atomic writes, collection, output planning, safety checks
├── logs.py         archive-aware .html/.json/.txt reference patching
├── manifest.py     manifest rows, writing, and verification
├── dedupe.py       metadata-safe hardlink deduplication
└── gui/            Tkinter app, config, and thread-safe worker bridge
```

Both front-ends call `jxlerate.core.run_batch()`, and the GUI worker
communicates only through typed events on a queue. Existing flat imports such
as `import jxlerate; jxlerate.strip_png(...)` remain supported.

## Tests

Run the regression suite with:

```sh
python3 -m unittest discover -s tests -v
```

Image tests skip when Pillow is unavailable; PNG optimizer coverage also skips
when `oxipng` is not installed.

## License

MIT License — see [LICENSE](LICENSE).

Copyright (c) 2026 Prisma Chill.
