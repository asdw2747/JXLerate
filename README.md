# strip-images

**Version 1.4.0**

Losslessly strip unnecessary metadata from `.png`, `.jpg`/`.jpeg`, `.webp`, and
`.gif` files while keeping the actual image pixel data bit-for-bit identical.

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
- `--max-effort` — slower/better PNG compression (zopfli; can take minutes).
- `--progressive` — write progressive JPEGs when optimizing (~10% on photos,
  compared with ~7% for baseline optimization).

**Lossy mode (off by default, changes pixels):**

- `--lossy-quality N` re-encodes JPEG (and static WebP) at quality 1–100.
  This permanently discards detail. It is never used unless explicitly requested,
  and it disables `--verify` (which compares pixels exactly).
  PNG/GIF are not lossily re-encoded.

External tools are optional: missing ones are skipped automatically. Only
`oxipng`/`optipng`, `jpegtran`, and Pillow are used for optimization.

## Install / use

```sh
strip-images photo.png photo.jpg photo.webp animation.gif
strip-images -r ./photos
strip-images -r ./photos -o ./stripped
strip-images --verify --backup file.png
strip-images --skip-clean -r ./photos
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
- `--skip-clean` do not write files that already have no metadata to strip
- `--only-if-smaller` only write when the result is smaller (implies skip-clean)
- `--suffix S` append suffix to filenames
- `--backup` keep a `.bak` of the original (in-place mode)
- `--dry-run` report savings without writing
- `--verify` decode original and result with Pillow and compare pixels
  (all frames of animated files are checked)
- `-q, --quiet` suppress per-file output
- `--version` print the version number and exit

Skipped files are not written to the output folder either, so with `-o` you
get only the files that actually changed. When several input folders are given,
each folder keeps its own name in the output tree so same-named files cannot
overwrite each other.

## GUI

A Tkinter GUI is included:

```sh
strip-images-gui
```

Features:

- Add files/folders, recursion, output folder, suffix, backups, dry-run,
  verification, color preservation, orientation preservation, passthrough,
  skip-clean, and only-if-smaller.
- Lossless **Optimize** (with **Max effort** and **Progressive JPEG**), plus an
  off-by-default **Lossy re-encode** with a quality spinbox and confirmation.
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
`~/.config/strip-images/config.json`.

## Requirements

Python 3.8+. Pillow optional (only for `--verify`). No other dependencies.

## License

MIT License — see [LICENSE](LICENSE).

Copyright (c) 2026 Prisma Chill.
