# exif-inspect

[![tests](https://github.com/xyan029/exif-inspect/actions/workflows/tests.yml/badge.svg)](https://github.com/xyan029/exif-inspect/actions/workflows/tests.yml)

A small command-line tool for first-pass image forensics. It prints the EXIF metadata of images, detects C2PA Content Credentials and AI-generation labels, points out details worth a closer look, and draws error level analysis (ELA) heatmaps for JPEGs, finds copy-move clones, compares the EXIF thumbnail with the main image, and matches photos to the camera that took them by sensor noise.

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
| `thumbnail_crop` | The EXIF thumbnail shows a wider scene than the main image: the picture was cropped after capture |
| `thumbnail_edited` | Part of the main image differs from the EXIF thumbnail |
| `thumbnail_unrelated` | The EXIF thumbnail shows a different picture from the main image |
| `thumbnail_rotated` | The thumbnail is turned relative to the image (pixels rotated by software) |
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

Bright, saturated objects with hard edges can light up as strongly as a real paste. In a red-team test a building was copied, shrunk to 0.64 and pasted above a row of trees. ELA showed the pasted building as a bright rectangle, and showed an untouched traffic light against grey sky just as brightly: its yellow housing is a hard-edged rectangle and its colours suffer most from JPEG's chroma compression. `copymove --keypoints` reported only the building, with 181 matching keypoints and the scale of the copy. ELA points to places worth checking; a match of that kind is evidence that can be reproduced.

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

Each of these checks came from a file met in practice:

- **`extension_mismatch`**: a screenshot forwarded through a messaging app arrived as a PNG named `.jpg`.
- **`dimension_mismatch`**: a phone screenshot cropped to 796 px wide still carried EXIF saying 1206 px, the phone's full screen width.
- **`c2pa_manifest`**: an image downloaded straight from ChatGPT had no EXIF but held C2PA credentials in a PNG `caBX` chunk. Before v0.3.0 the tool reported only "No EXIF metadata".
- **`copymove --keypoints`**: in a red-team test, a chalkboard sign was copied, shrunk to about a third and pasted elsewhere in the same street photo. Block matching found nothing, even with `--min-pairs 50`. `ela` still showed the paste as a bright rectangle with hard edges, because the resized patch had a different compression history from its surroundings. With `--keypoints` the same photo is caught: the pasted copy, about 40 by 60 pixels, is reported at scale 0.36 of the original sign with 18 matching keypoints.

## EXIF thumbnail

```bash
exif-inspect thumbnail photo.jpg             # writes photo_thumbnail.png
exif-inspect thumbnail --extract photo.jpg   # also saves the thumbnail as photo_thumb.jpg
```

Cameras and phones store a small preview, usually 160 by 120 pixels, in the second image directory of EXIF (IFD1). Editors that crop or retouch a photo do not always regenerate it, so the preview can still show what was cut away or painted over. The tool:

1. reads the thumbnail JPEG from the raw EXIF block and drops the black bars some cameras add to fill a 4:3 frame,
2. searches the thumbnail for the main image at every position, at sizes down to 45% of its width and at four rotations, using normalised cross-correlation. A close match (0.85 or better) covering less than 90% of the thumbnail is reported as a crop,
3. when the main image fills the thumbnail, matches brightness and contrast, compares the two in 8-pixel blocks and reports blocks that differ far more than the rest.

The picture saved by `exif-inspect thumbnail` shows the thumbnail (enlarged, with the crop outlined in cyan), the main image, and a difference map with changed areas outlined in orange.

The thresholds were set on the 55 photos with thumbnails in the public [exif-samples](https://github.com/ianare/exif-samples) collection, from about 40 cameras and phones. On the 35 of them large enough to edit, none was flagged untouched, apart from one photo in that collection that really is a crop: its thumbnail shows the car park around the car that the main image frames. Pasted clones and painted areas covering about a tenth of the frame were found in 33 of 35 photos, crops to 70% and 50% in 33 and 32. Blurring is harder: a blur fine enough to hide a face often leaves little trace at thumbnail size, and it was found in 8 of 35.

A thumbnail only helps when the editor left it alone. Many apps rewrite or delete it, phones may store a larger preview elsewhere (HEIC files keep it outside EXIF), and anyone who knows about it can replace it.

## Camera identification (PRNU)

```bash
pip install -e ".[prnu]"                               # adds PyWavelets
exif-inspect prnu build -o myphone.npz flat/*.jpg      # fingerprint from 20+ photos
exif-inspect prnu match myphone.npz questioned/*.jpg   # PCE score per photo
```

Every pixel of an image sensor converts light with a slightly different efficiency. The differences are fixed for the life of the sensor and leave the same faint multiplicative pattern, the photo-response non-uniformity (PRNU), in every picture. It works as a fingerprint of one physical camera, telling apart two units of the same model. The implementation follows Lukas, Fridrich and Goljan (2006) and Chen et al. (2008):

1. **Noise residual**: the image minus a denoised copy (4-level `db4` wavelet transform, local Wiener filter on the detail bands). Row and column means are removed on each colour-filter subgrid and spectral peaks are flattened, which strips patterns that every camera of a model shares.
2. **Fingerprint**: from N photos of one camera, `K = sum(W_i * I_i) / sum(I_i^2)`, skipping clipped pixels. Bright, flat, slightly defocused shots (sky, a white wall) give the cleanest estimate.
3. **Match**: the residual of a questioned photo is correlated with `I * K` over all cyclic shifts, and the peak-to-correlation energy (PCE) measures how sharply it peaks at zero shift. The four rotations are tried, since phones store portrait shots turned.

The analysis uses a 2048-pixel centre crop (or the largest square that fits), so a fingerprint only fits photos taken at the same resolution. PCE grows with the number of pixels compared; `--crop` trades speed for strength. The tool reads PCE of 60 or more as the same camera, 20 to 60 as inconclusive, and below 20 as no evidence.

**Evaluation.** On the Dresden Image Database subset distributed with [prnu-python](https://github.com/polimi-ispl/prnu-python), with a 1024-pixel crop: six cameras (two units each of the Nikon D200, D70 and D70s), fingerprints from 10 flat-field photos each, and 20 natural photos tested against all six.

| | PCE, same camera | PCE, other cameras |
|---|---|---|
| median | 119 | 0.7 |
| range | 11 to 1257 | up to 14.9 |
| at or above 60 | 14 of 20 | 0 of 100 |

The highest score always belonged to the right camera, including against the second unit of the same model. Photos with little light or heavy texture gave weaker scores. Processing erodes the pattern quickly:

| Same 20 photos | median PCE | at or above 60 |
|---|---|---|
| as taken | 119 | 14 |
| re-saved at JPEG quality 76 (WeChat without "Original") | 43 | 7 |
| re-saved at quality 50 | 22 | 3 |
| resized to 90% | 0.9 | 0 |

**A phone.** An iPhone 16 Pro, main 1x camera, HEIC at the default 24 megapixels: a fingerprint from 21 shots of a plain wall, tested on five everyday 1x photos and on three control photos from a different camera.

| Crop | PCE, 1x photos | PCE, control photos |
|---|---|---|
| 1024 | 8 to 23 | 0.7 to 3.2 |
| 2048 | 43 to 118 (3 of 5 at or above 60) | 4.4 to 5.4 |

The phone's pattern is far weaker than a DSLR's at the same crop (Nikon median 119, phone 8 to 23), and it took four times the pixels to separate the two groups clearly. This is why the default crop is 2048.

**Finding replaced areas.**

```bash
exif-inspect prnu locate myphone.npz edited.jpg    # writes edited_prnu.png
```

An area pasted in, retouched or filled by AI object removal never passed through the sensor, so it lacks the pattern. `prnu locate` correlates the residual with `I * K` in overlapping blocks (a quarter of the crop wide by default) and compares each block with what the model `W = I*K + noise` predicts for it: `q * std(I*K) / std(W)`, where `q` is the median over all blocks. Blocks more than 3.5 spreads below their prediction are marked red. Dark, clipped and busy blocks, where even an untouched image would show little pattern, are dimmed as "cannot judge". The map covers only the analysed crop, so build the fingerprint with `--crop 0` to cover the largest centred square.

A first version fitted the prediction with a flexible regression on brightness and texture. It learned the tampered blocks' own texture and excused them, finding none. The one-parameter physical model, with a median that a minority of tampered blocks cannot pull, fixed that.

On the Dresden subset (1024 crop, 256-pixel blocks, fingerprints from 10 flat-field photos), a 384-pixel square was replaced where the clean photo's pattern was checkable:

| Replacement | found (over 30% of it marked) |
|---|---|
| the same area blurred, a stand-in for an AI fill | 13 of 16 |
| an area from another camera's photo | 2 of 16 |
| nothing (untouched photo) | false marks above 1% of the crop in 1 of 20 |

A splice of busy content from another camera is hard to tell from a busy area of this camera, because both predict little pattern. Smooth replacements, which is what object removal tends to produce, stand out.

Phones add their own difficulties. Each lens of a multi-camera phone has its own sensor and fingerprint. Multi-frame modes (night mode, HDR merges) and digital stabilisation shift pixels between frames and blur the pattern, and binned 48-megapixel sensors output processed 12- or 24-megapixel images. Build the fingerprint from photos of the same lens, resolution and mode as the questioned photo.

## Copy-move detection

```bash
exif-inspect copymove photo.jpg     # writes photo_copymove.png when clones are found
```

A copy-move forgery pastes part of an image elsewhere in the same image, for example to duplicate a person or to cover an object with nearby grass. The detector slides a 16-pixel block over a downscaled copy of the image, describes each block by a 4x4 grid of mean intensities, sorts the descriptors so similar blocks sit next to each other, and votes on the shift between matching blocks that lie far apart. A shift backed by at least 200 block pairs is reported, with the source region outlined in orange and the copy in cyan.

Flat areas such as sky are skipped. Repetitive textures (railings, tiles, windows) match at many different shifts and are filtered out for that reason. Small clones, clones that were rotated or rescaled before pasting, and heavy recompression can escape detection.

### Scaled and rotated clones

```bash
pip install -e ".[keypoints]"             # adds OpenCV
exif-inspect copymove --keypoints photo.jpg
```

Block matching compares pixels at one size and one angle, so a clone shrunk or turned before pasting slips past it. `--keypoints` matches SIFT keypoints instead, whose descriptors stay the same under scaling and rotation:

1. find SIFT keypoints on a grey copy of the image enlarged to twice its size, kept between 1000 and 2400 pixels on the longest side (`--size` changes this); enlarging gives a small, shrunken clone enough keypoints to match,
2. match every keypoint against all the others with approximate nearest-neighbour search (FLANN), keeping a match only when it is clearly better than the next best (the g2NN test),
3. fit a similarity transform (shift, scale and rotation) to groups of matches lying close together, and keep transforms backed by at least 8 matches,
4. drop transforms that look like a repeating pattern: keypoints with many near-identical twins, keypoints shared by three or more transforms, or two regions that overlap.

The report gives the scale and rotation from the larger region (A, orange) to the smaller one (B, cyan), with yellow lines joining the matched keypoints. Which region is the original cannot be told from the geometry alone.

On a test set of 40 forgeries made from scikit-image sample photos (a third of each photo pasted back at 1/3, 1/2 and 1.4 times the size, turned 30 degrees, or turned 90 degrees at 0.7 times), block matching found none and keypoint matching found 31. All eight misses on the smooth retina photo and the three clones shrunk to a third in the smallest photos (451 px wide) had too few keypoints to match. Of 18 untouched sample photos, the keypoint mode flagged two: a scanned page of text, where the same letters in the same font genuinely repeat, and a grass texture in which block matching also finds a repeated area. Text, logos and printed patterns can trigger it for that reason.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests build their own sample images, so no real photos are stored in the repository.

## Roadmap

- Full XMP and IPTC metadata
- C2PA signature validation

## License

MIT
