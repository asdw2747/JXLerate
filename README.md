# strip-images

**Version 1.2.0**

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

The image is never re-encoded, so there is no quality loss. Outputs are
validated (PNG `IEND`, JPEG `EOI`, GIF trailer, WebP image data) and written
atomically so an interrupted run cannot corrupt the original. File permissions
and timestamps are preserved, and symlinks are followed rather than replaced.

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
- `--skip-clean` do not write files that already have no metadata to strip
- `--only-if-smaller` only write when the result is smaller (implies skip-clean)

Skipped files are not written to the output folder either, so with `-o` you
get only the files that actually changed.
- `--suffix S` append suffix to filenames
- `--backup` keep a `.bak` of the original (in-place mode)
- `--dry-run` report savings without writing
- `--verify` decode original and result with Pillow and compare pixels
  (all frames of animated files are checked)
- `-q, --quiet` suppress per-file output
- `--version` print the version number and exit

When several input folders are given, each folder keeps its own name in the
output tree so same-named files cannot overwrite each other.

## GUI

A simple Tkinter GUI is included:

```sh
strip-images-gui
```

It lets you pick files/folders, toggle options, and watch a log. No extra
dependencies beyond Tkinter (bundled with Python).

### Theme

The GUI has a **Theme: Auto / Light / Dark** selector (or press `Ctrl+T` to
cycle). Auto follows the desktop preference via GNOME `color-scheme`, then GTK
`~/.config/gtk-3.0/settings.ini`, then the `GTK_THEME` environment variable.
The choice is saved in `~/.config/strip-images/config.json`.

## Requirements

Python 3.8+. Pillow optional (only for `--verify`). No other dependencies.
