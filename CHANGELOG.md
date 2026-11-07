# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [2.1.1] - 2026-11-07

### Fixed
- Windows CI: the deduplication regression test now creates its metadata
  difference with modification time, since Windows cannot represent POSIX
  permission modes. Mode-based coverage remains a separate Linux/macOS test.

## [2.1.0] - 2026-11-07

### Added
- Installable Python package with `pip`, `pipx`, and `git+https` support.
  Installing provides the `jxlerate` and `jxlerate-gui` commands.
- Optional `images` extra for Pillow-dependent features (`--verify`, lossy
  re-encoding, WebP optimization).
- GitHub Actions CI that tests Linux, macOS, and Windows on Python 3.10–3.13
  and verifies the built wheel installs and runs in a clean environment.
- README installation, upgrade, and platform-specific Tkinter instructions.

### Changed
- The GUI now imports the core module normally instead of loading it by file
  path, so it works from both a source checkout and an installed package.

## [2.0.1] - 2026-11-06

### Fixed
- Prevented manifests and output files from colliding with each other or
  overwriting a different selected input.
- Lossless optimizers preserve EXIF orientation; verification now checks
  preserved orientation and requested ICC/color metadata as well as pixels.
- Lossy JPEG/WebP re-encoding retains ICC profiles when `--keep-color` is set.
- Lossy mode now errors clearly when Pillow is missing, the input format is
  unsupported, or an animated image cannot be re-encoded.
- Manifest verification rejects malformed, empty, and failed/dry-run manifests
  rather than reporting a false clean result.
- Savings totals now include only successful writes (or planned dry runs).
- Deduplication only hardlinks files with matching mode, ownership,
  modification time, and extended attributes.
- Added regression tests for orientation/color preservation, manifest safety,
  output collisions, lossy-mode validation, and dedupe metadata handling.

## [2.0.0] - 2026-11-05

### Changed
- Renamed the project from `strip-images` to **JXLerate** to reflect its broader
  scope (metadata stripping, lossless optimization, JPEG XL archiving,
  deduplication, and integrity manifests).
- CLI command is now `jxlerate`; GUI command is `jxlerate-gui`.
- Configuration moved to `~/.config/jxlerate/config.json` and is migrated
  automatically from the old location.
- The old `strip-images` / `strip-images-gui` commands and desktop entry were
  removed.

## [1.6.0] - 2026-11-05

### Added
- `--manifest FILE` writes a CSV of source/output paths, sizes, SHA-256,
  tool, and status for every processed file.
- `--verify-manifest FILE` re-hashes recorded outputs and fails on any
  size/hash mismatch or missing file.
- `--dedupe` finds exact duplicate images and replaces them with hardlinks
  (same filesystem), with `--dry-run` reporting.

## [1.5.0] - 2026-11-05

### Added
- `--to-jxl` lossless JPEG XL transcode:
  - JPEG is recompressed losslessly and verified **bit-for-bit** by
    reconstructing with `djxl`.
  - PNG/WebP/GIF are transcoded mathematically losslessly and verified by
    decoded-pixel comparison.
  - WebP is decoded to PNG first because `cjxl` has no WebP reader.
  - Conversion only happens when the `.jxl` is smaller; originals are kept by
    default.
- `--replace` to delete originals after a verified in-place conversion.
- GUI: **Convert to JPEG XL** and **Replace originals with .jxl** controls.

## [1.4.0] - 2026-11-05

### Added
- `--optimize` for pixel-lossless recompression: PNG via `oxipng`/`optipng`,
  JPEG via `jpegtran`, and lossless WebP re-encoding.
- `--max-effort` (zopfli) and `--progressive` JPEG options.
- `--lossy-quality N` for explicit, off-by-default JPEG/WebP re-encoding.
  Lossy mode changes pixels, warns, and disables exact verification.
- GUI controls for lossless optimize, max effort, progressive JPEG, and an
  off-by-default lossy mode with a quality spinbox and confirmation dialog.

## [1.3.1] - 2026-11-05

### Changed
- Relicensed the project under the MIT License.
- Added SPDX license headers to both source files.

## [1.3.0] - 2026-11-05

### Added
- EXIF **Orientation is preserved by default** so portrait photos keep
  displaying correctly. Only a minimal ~26-byte TIFF block is written, and only
  when the orientation is 2–8; all other EXIF fields are still removed.
- Orientation preservation for JPEG (`APP1`), PNG/APNG (`eXIf`), and WebP
  (`EXIF`, with the VP8X flag updated).
- `--strip-orientation` to opt out, plus a **Preserve orientation** GUI
  checkbox (checked by default).
- GUI: determinate progress with the current filename, **Cancel** button, and
  **Open output folder** button.
- GUI: confirmation prompt before modifying originals in place.
- GUI: remembers all options, theme, and window size/position.
- GUI: file count label and status bar.
- GUI shortcuts: `Ctrl+O`, `Ctrl+Shift+O`, `Ctrl+Enter`, `Esc`.

## [1.2.0] - 2026-11-05

### Added
- Lossless **WebP** support: strips `EXIF`, `XMP `, and (optionally) `ICCP`
  chunks and updates the VP8X presence flags.
- Lossless **GIF** support: strips comment extensions and metadata-only
  application extensions (XMP, optionally ICC) while keeping animation and
  graphic-control data.
- `--skip-clean` to leave files that already have no metadata untouched.
- `--only-if-smaller` to write only when stripping actually reduces size.
- GUI checkboxes for **Skip unchanged files** and **Only write if smaller**.
- Multi-frame verification: `--verify` now checks every frame of animated
  PNG/WebP/GIF files, not just the first.

### Fixed
- Atomic writes no longer reset file permissions to `0600`; the original mode
  and timestamps are preserved.
- In-place processing of a symlink now updates the link target instead of
  replacing the symlink with a regular file.

## [1.1.0] - 2026-11-05

### Added
- Dark mode for the GUI, using a Breeze-inspired palette.
- Automatic theme detection (GNOME `color-scheme`, GTK `settings.ini`,
  or `GTK_THEME`), with an Auto/Light/Dark selector to override it.
- Live theme switching via the selector or `Ctrl+T`.
- The chosen theme is persisted in `~/.config/strip-images/config.json`.
- Clear checkmark/radio indicators by using themed classic Tk controls.

## [1.0.0] - 2026-11-05

First stable release. Losslessly strips metadata from PNG and JPEG files
without re-encoding the image pixels.

### Core
- PNG: keeps `IHDR`, `PLTE`, `IDAT`, `IEND`, `tRNS`, and APNG chunks
  (`acTL`, `fcTL`, `fdAT`); removes text, time, background, physical-size,
  and other auxiliary chunks.
- JPEG: keeps frame/table/scan markers and RST markers; removes EXIF, XMP,
  comments, JFIF, and ICC APP segments.
- Always preserves the Adobe APP14 marker because it can affect color decoding.
- Validates output structure: PNG must end with `IEND`, JPEG must end with
  `EOI`; truncated inputs are rejected instead of written.
- Outputs are written atomically (temp file + `os.replace`).

### CLI
- Files, folders, and recursive folder processing.
- `-o/--output-dir`, `--suffix`, `--backup`, `--dry-run`, `--verify`, `-q`.
- `--passthrough` copies non-image files into the output folder.
- `--keep-color` preserves color/rendering metadata.
- Collision-safe mirroring when multiple input folders are supplied.
- Non-zero exit status when any file fails.
- `--version` flag.

### GUI
- Tkinter front-end (`strip-images-gui`).
- File/folder picker, recursive processing, output folder, suffix, backups,
  dry-run, verification, color preservation, and passthrough.
- Live log with output paths and a completion summary.
- Thread-safe worker (options read on the main thread; completion reported
  through a queue).

### Fixed during development
- Verification no longer silently passes when output cannot be decoded.
- Arithmetic-coded JPEGs (SOF8) are no longer broken.
- Passthrough no longer writes during dry runs.
- Same-named files from different input folders no longer overwrite each other.

### Development history (pre-release)
1. CLI stripper for PNG chunks and JPEG markers.
2. Tkinter GUI, launcher, and desktop entry.
3. Output-path logging, completion dialog, and "no files" warning.
4. Passthrough of non-image files into the output folder.
5. Correctness hardening (strict verification, atomic writes, structure
   validation, `--keep-color`, multi-folder mirroring, GUI thread-safety).
