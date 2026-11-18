# Getting Started

## Install

Python 3.10 or newer.

```bash
git clone https://github.com/xyan029/exif-inspect.git
cd exif-inspect
pip install -e .
```

Optional extras, each for one feature:

| Extra | Adds | Needed by |
|---|---|---|
| `heic` | pillow-heif | iPhone HEIC and HEIF files |
| `keypoints` | OpenCV | `copymove --keypoints` (scaled or rotated clones) |
| `prnu` | PyWavelets | `prnu` (camera identification) |

```bash
pip install -e ".[heic,keypoints,prnu]"
```

A tagged release can be installed straight from GitHub:

```bash
pip install "exif-inspect[heic,keypoints,prnu] @ git+https://github.com/xyan029/exif-inspect.git@v0.6.0"
```

## First run

```bash
exif-inspect photo.jpg
```

prints the SHA-256 of the file, its format and size, the key EXIF tags, the JPEG quantization tables, the thumbnail and gain map state, one line about the XMP packet, any provenance signals, and then the **Findings**: a list of hints, each with a stable code. A `!` marks a finding worth following up, a `-` marks information.

```
== photo.jpg
   sha256 : 16bf0df3...
   format : JPEG  640x480  RGB
   [Image]
     Make                   Canon
     Model                  EOS R6
     Software               Adobe Photoshop 25.0
   ...
   Findings:
     ! Software tag names an image editor: 'Adobe Photoshop 25.0'.
     ! DateTime (2026:01:02 10:00:00) differs from DateTimeOriginal (2026:01:01 09:00:00); ...
```

Useful switches:

- `--all` lists every EXIF tag instead of the key ones.
- `--json` prints the same report as JSON, one object per file, for scripts and notes.
- Several files can be given at once. The exit code is 1 when any file could not be read.

## The subcommands

Each pixel method is a subcommand that writes a picture next to the input (`photo_ela.png`, `photo_ghost.png`, and so on) and prints what it found:

```bash
exif-inspect ela photo.jpg
exif-inspect ghost photo.jpg
exif-inspect dq photo.jpg
exif-inspect copymove photo.jpg
exif-inspect noise photo.jpg
exif-inspect cfa photo.jpg
exif-inspect resample photo.png
exif-inspect light photo.jpg
exif-inspect thumbnail photo.jpg
exif-inspect xmp photo.jpg
exif-inspect report photo.jpg     # all of the above on one HTML page
```

`exif-inspect <subcommand> --help` lists the options. `-o` sets the output path when there is one input file.

## Reading the output

Three habits save most misreadings:

1. **A finding is a lead.** `editor_software` means the Software tag names an editor; it does not say what was edited, and a tag can be typed in by hand.
2. **Silence proves nothing.** A screenshot, a messaging app or a social platform strips metadata and recompresses pixels, after which most methods see nothing. "No EXIF" and "no ghost" are the normal state of a forwarded picture.
3. **Compare like with like.** The pixel maps are read by comparing regions of similar texture and brightness. Edges, text and fine texture light up in every map; a smooth area that lights up next to smooth areas that do not is the signal.

[[Examining a Photo]] turns these into a working order.
