# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

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
