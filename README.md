# strip-images

**Version 1.0.0**

Losslessly strip unnecessary metadata from `.png` and `.jpg`/`.jpeg` files while
keeping the actual image pixel data bit-for-bit identical.

## What gets removed

- **PNG**: all chunks except `IHDR`, `PLTE`, `IDAT`, `IEND`, `tRNS`, and APNG
  animation chunks (`acTL`, `fcTL`, `fdAT`). Removes `tEXt`/`zTXt`/`iTXt`,
  `gAMA`, `cHRM`, `iCCP`, `sRGB`, `pHYs`, `bKGD`, `tIME`, etc.
- **JPEG**: all APP/COM segments (EXIF, ICC profiles, JFIF, comments, XMP, ...).
  Keeps SOF, DQT, DHT, DAC, DRI, SOS, RST and scan data untouched. The Adobe
  `APP14` marker is preserved because it can affect color decoding.

With `--keep-color`, color/rendering metadata is preserved too (PNG
`cHRM`/`gAMA`/`iCCP`/`sRGB`/`cICP` and JPEG APP2 ICC profiles).

The image is never re-encoded, so there is no quality loss. Outputs are
validated (PNG must end with `IEND`, JPEG must end with `EOI`) and written
atomically so an interrupted run cannot corrupt the original.

## Install / use

```sh
strip-images photo.png photo.jpg
strip-images -r ./photos
strip-images -r ./photos -o ./stripped
strip-images --verify --backup file.png
```

Options:

- `-r, --recursive` recurse into directories
- `-o, --output-dir DIR` write stripped copies instead of overwriting
- `--passthrough` copy non-image files into the output folder too (requires -o)
- `--keep-color` keep color/rendering metadata (see above)
- `--suffix S` append suffix to filenames
- `--backup` keep a `.bak` of the original (in-place mode)
- `--dry-run` report savings without writing
- `--verify` decode original and result with Pillow and compare pixels
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

## Requirements

Python 3.8+. Pillow optional (only for `--verify`). No other dependencies.