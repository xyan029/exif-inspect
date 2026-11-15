# exif-inspect

[![tests](https://github.com/xyan029/exif-inspect/actions/workflows/tests.yml/badge.svg)](https://github.com/xyan029/exif-inspect/actions/workflows/tests.yml)

A small command-line tool for first-pass image forensics. It prints the EXIF metadata of images, detects C2PA Content Credentials and AI-generation labels, points out details worth a closer look, and draws error level analysis (ELA) heatmaps for JPEGs and finds copy-move clones.

## What it reports

For each file:

- **SHA-256 hash** of the raw bytes, for chain-of-custody notes
- **Format, size and colour mode**
- **Key EXIF tags**: camera make and model, software, timestamps, lens and exposure settings, GPS (use `--all` to list every tag)
- **GPS coordinates** converted to decimal degrees
- **Findings**, a short list of hints:

| Code | Meaning |
|---|---|
| `editor_software` | The `Software` tag names a known image editor (Photoshop, GIMP, Snapseed, Meitu, …) |
| `time_mismatch` | `DateTime` (last save) differs from `DateTimeOriginal` (capture) |
| `gps_present` | Location is embedded in the file |
| `no_camera` | EXIF exists but records no camera make or model |
| `no_exif` | The file carries no EXIF at all |
| `extension_mismatch` | The file name says one format and the content is another (a PNG named `.jpg`) |
| `dimension_mismatch` | The width and height recorded in EXIF differ from the real image size, a sign of cropping or resizing after capture |
| `c2pa_manifest` | C2PA Content Credentials are embedded (PNG `caBX` chunk, JPEG APP11 segment or WebP `C2PA` chunk) |
| `ai_generated_marker` | The IPTC digital source type is `trainedAlgorithmicMedia` (made by generative AI) |
| `ai_composite_marker` | The IPTC digital source type is `compositeWithTrainedAlgorithmicMedia` (AI combined with other content) |
| `jpeg_tables` | Which JPEG quantization tables the encoder used: standard libjpeg tables with the exact quality, or a custom table with its fingerprint |
| `makernote_missing` | EXIF names a camera but carries no MakerNote, the vendor block every camera and phone writes |
| `tables_contradict_exif` | EXIF names a camera, but the quantization tables belong to other software (WeChat, Apple ImageIO, standard libjpeg) |
| `gps_timezone_mismatch` | The capture time zone is more than 3.5 hours from what the GPS longitude implies |
| `gps_time_mismatch` | The GPS fix time (UTC) is more than an hour from the capture time |
| `gps_incomplete` | An iPhone photo whose GPS block lacks the fix time and positioning error iPhones record |
| `unreadable` | The file could not be opened as an image |

### Provenance signals

C2PA Content Credentials are signed records that some cameras, editors and AI image generators attach to a file. `exif-inspect` reports where a manifest sits, a best-effort claim generator name and the IPTC digital source type. It does not validate the signature, so use a C2PA verifier to confirm who signed the manifest. Invisible pixel watermarks such as SynthID need the vendor's own detector.

These records live in the file container and disappear on a screenshot, most messaging apps and most social platforms. A missing manifest therefore says nothing about whether an image is AI-generated.

Metadata is easy to edit, copy or strip, so each finding is a lead for further analysis. A clean result says little about whether the pixels were altered; pixel-level methods such as error level analysis cover that side.

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/xyan029/exif-inspect.git
cd exif-inspect
pip install -e .
```

For iPhone HEIC photos, add the optional HEIC support:

```bash
pip install -e ".[heic]"
```

## Usage

```bash
exif-inspect photo.jpg                 # key tags and findings
exif-inspect --all photo.jpg           # every tag
exif-inspect --json *.jpg > out.json   # machine-readable output
```

Example output:

```
== photo.jpg
   sha256 : 16bf0df35c860e7792bd04135fd5a3a324b45427db4ca98245c4452d30d8ce5d
   format : JPEG  640x480  RGB
   [Image]
     Make                   Canon
     Model                  EOS R6
     Software               Adobe Photoshop 25.0
     DateTime               2026:01:02 10:00:00
   [Exif]
     DateTimeOriginal       2026:01:01 09:00:00
   GPS (decimal) : 43.66, -79.39
   Findings:
     ! Software tag names an image editor: 'Adobe Photoshop 25.0'.
     ! DateTime (2026:01:02 10:00:00) differs from DateTimeOriginal (2026:01:01 09:00:00); the file may have been saved again after capture.
     ! GPS coordinates embedded: 43.66, -79.39. Check before sharing this file publicly.
```

The exit code is `1` when any file could not be read, `0` otherwise.

## Error level analysis (ELA)

```bash
exif-inspect ela photo.jpg                    # writes photo_ela.png
exif-inspect ela --side-by-side photo.jpg     # original and heatmap in one image
exif-inspect ela -q 95 --gray -o out.png photo.jpg
```

| Option | Meaning |
|---|---|
| `-q`, `--quality` | JPEG quality used for the re-save (default 90) |
| `-o`, `--output` | Output PNG path (single image only) |
| `--scale` | Fixed amplification; by default the 99.5th percentile of the error maps to full brightness |
| `--gray` | Grayscale heatmap |
| `--side-by-side` | Original on the left, heatmap on the right |

**How it works.** Each JPEG save discards some information, and repeated saves discard less and less. ELA re-saves the image once at a fixed quality and measures how much every pixel changes. Areas that share the image's compression history change by similar amounts; an area pasted in from another source often changes more and shows up brighter.

**Reading the heatmap.** Edges and fine texture always show higher error, while smooth areas such as sky stay dark. Compare regions of similar texture and look for one that stands out from its neighbours. The signal weakens or disappears when an image has been re-saved many times, saved at very high quality, converted from PNG, screenshotted, or recompressed by a social-media platform, so ELA works as a screening aid alongside other evidence.

### JPEG quantization tables

Each JPEG stores the quantization tables its encoder used. Encoders built on libjpeg (Pillow, GIMP, many web services and apps) scale one standard pair of tables by a 1-100 quality setting, so `exif-inspect` reports the exact quality for those files. Other software ships its own tables; for these the tool prints a 12-character fingerprint and the closest libjpeg quality. A fingerprint names the program that saved the file last, so a photo re-saved by an editor or a messaging app carries that program's tables in place of the camera's.

Known fingerprints so far, each measured from a sample:

| Fingerprint | Encoder |
|---|---|
| `39d931620c0c` | Apple ImageIO, quality 70 |
| `efd739c2599e` | Apple ImageIO, quality 72 |
| `1ac983e0179e` | Apple ImageIO, quality 75 |
| `21e9f0213f64` | Apple ImageIO, quality 78 |
| `c0b709b1210a` | Apple ImageIO, quality 80 |
| `9433a0dd45b0` | Apple ImageIO, quality 85 |
| `5719fc272f79` | Apple ImageIO, quality 90 |
| `86df6d4b4b47` | Apple ImageIO, quality 95 |

The Apple entries come from `sips -s format jpeg -s formatOptions N` on macOS; a screenshot cropped on an iPhone matched the quality 90 entry. At quality 100 every encoder writes all-ones tables, so those files cannot be told apart. Images saved to the iPhone photo library from Xiaohongshu, Reddit and Xianyu all carried the quality 75 tables: the fingerprint identifies the encoder that wrote the file, and several apps can share it. Visible watermarks or the image content tell those apps apart.

WeChat, tested by sending the same Apple quality 90 JPEG from an iPhone to the desktop app:

| Mode | Result |
|---|---|
| Without "Original" | Re-encoded with standard libjpeg tables at quality 76, EXIF stripped |
| With "Original" | Passed through unchanged (still Apple ImageIO quality 90) |

Standard tables at quality 76 therefore get a WeChat hint. Any libjpeg encoder can choose the same quality, so treat it as a lead.

### Limits of metadata checks

The consistency checks catch careless edits: a camera added to a WeChat image, coordinates moved to another continent, a GPS block typed in by hand. A careful forger who changes only the latitude and longitude, inside the same time zone, leaves every other field consistent, and no metadata check can tell. In a red-team test a GPS edit of that kind passed all of the checks above. Settling such a case takes evidence outside the file: what the picture shows, neighbouring photos from the same device, or upload records.

## Cases behind the checks

Each check added in v0.3.0 came from a file met in practice:

- **`extension_mismatch`**: a screenshot forwarded through a messaging app arrived as a PNG named `.jpg`.
- **`dimension_mismatch`**: a phone screenshot cropped to 796 px wide still carried EXIF saying 1206 px, the phone's full screen width.
- **`c2pa_manifest`**: an image downloaded straight from ChatGPT had no EXIF but held C2PA credentials in a PNG `caBX` chunk. Before v0.3.0 the tool reported only "No EXIF metadata".

## Copy-move detection

```bash
exif-inspect copymove photo.jpg     # writes photo_copymove.png when clones are found
```

A copy-move forgery pastes part of an image elsewhere in the same image, for example to duplicate a person or to cover an object with nearby grass. The detector slides a 16-pixel block over a downscaled copy of the image, describes each block by a 4x4 grid of mean intensities, sorts the descriptors so similar blocks sit next to each other, and votes on the shift between matching blocks that lie far apart. A shift backed by at least 200 block pairs is reported, with the source region outlined in orange and the copy in cyan.

Flat areas such as sky are skipped. Repetitive textures (railings, tiles, windows) match at many different shifts and are filtered out for that reason. Small clones, clones that were rotated or rescaled before pasting, and heavy recompression can escape detection.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests build their own sample images, so no real photos are stored in the repository.

## Roadmap

- JPEG quantisation table fingerprinting
- Full XMP and IPTC metadata
- C2PA signature validation

## License

MIT
