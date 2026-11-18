# exif-inspect

[![tests](https://github.com/xyan029/exif-inspect/actions/workflows/tests.yml/badge.svg)](https://github.com/xyan029/exif-inspect/actions/workflows/tests.yml)

A small command-line tool for first-pass image forensics. The [wiki](https://github.com/xyan029/exif-inspect/wiki) explains how to examine a photo with it, method by method, and what each result can and cannot show; the pages live in [`wiki/`](wiki/) and are published from there. It prints the EXIF metadata of images, reads the XMP editing history and Camera Raw settings, detects C2PA Content Credentials and AI-generation labels, points out details worth a closer look, and draws error level analysis (ELA) heatmaps for JPEGs, finds the earlier JPEG qualities an image or a pasted region passed through (JPEG ghosts), reads an earlier save's quantisation table from the coefficients and maps the blocks that were compressed only once, checks that objects agree on where the light came from, maps where the traces of demosaicing are missing, maps the sensor noise and the regions whose noise does not match, finds the traces of resizing and turning in the whole picture or in one region, finds data hidden after the picture, in its lowest bits or in its JPEG coefficients, relates two versions of a picture, groups duplicates across folders, finds copy-move clones, compares the EXIF thumbnail with the main image, and matches photos to the camera that took them by sensor noise.

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
| `gain_map_misfit` | The HDR gain map no longer fits the picture: the image was resized after capture and the gain map kept |
| `gain_map_missing` | EXIF names an iPhone 12 or later but the file has no HDR gain map, which editors such as Snapseed and Meitu drop |
| `xmp_editor` | The XMP packet names an image editor as creator tool or in its history |
| `xmp_history` | `xmpMM:History` lists the saves and conversions the file went through, with software and time |
| `xmp_derived` | The file was derived from another document (`xmpMM:DerivedFrom`, or the document ID differs from the original one) |
| `xmp_raw` | Developed from a raw file by Camera Raw or Lightroom, with the main development settings |
| `xmp_retouch` | Camera Raw's healing or clone brush was used: the number of areas painted over |
| `xmp_local_adjust` | Local adjustments (masks, gradients, brush) recorded in the Camera Raw settings |
| `xmp_crop` | Cropped in Camera Raw, with the share of the frame that was kept |
| `xmp_ai` | The software, credit or source type in XMP names a generative AI tool |
| `xmp_date_mismatch` | `xmp:CreateDate` differs from EXIF `DateTimeOriginal`: one of them was rewritten |
| `trailing_data` | Bytes follow the end of the picture (after JPEG EOI, PNG IEND, the WebP RIFF size or the GIF trailer), with a guess at what they are: an archive, another image, text |
| `trailing_image` | A second JPEG after the first, announced by an MPF segment (gain map or extra frame) |
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
exif-inspect dq photo.jpg              # an earlier JPEG save's table, and blocks compressed only once
exif-inspect resample photo.png        # traces of resizing and turning, by region
exif-inspect xmp photo.jpg             # XMP editing history and Camera Raw settings
exif-inspect stego photo.png           # bytes after the picture, LSB and JPEG-coefficient steganalysis
exif-inspect compare a.jpg b.jpg       # how two versions of a picture relate
exif-inspect dupes folder/             # duplicates and near-duplicates, with the likely original
exif-inspect report photo.jpg          # everything above in one HTML page
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

## JPEG ghosts

```bash
exif-inspect ghost photo.jpg            # earlier JPEG qualities, whole image and by region
exif-inspect ghost --grid photo.jpg     # also try the 63 other offsets of the 8x8 grid (slow)
exif-inspect ghost --curve photo.jpg    # print the re-save error at every quality
```

Re-saving a JPEG at the quality it was last saved at changes it very little, and the same holds for any quality it passed through earlier: the error between the picture and a re-save dips at every quality in its history. Farid ([Exposing digital forgeries from JPEG ghosts](https://farid.berkeley.edu/downloads/publications/tifs09.pdf), 2009) named such a dip a JPEG ghost. The tool re-saves the image at every quality from 50 to 100 and measures the squared difference in 16-pixel blocks, after removing the mean of every 8x8 cell (its rounding flips back and forth in smooth areas and would look like ghosts). The curves are read in two ways:

- **Whole image.** A dip in the mean curve at least 4 below the current quality and at least 15% deep means the file was saved as a JPEG at about that quality before (untouched test files never produced a dip deeper than 6%). The curve runs from quality 40 so that a dip near 50 still has a shoulder on its left; the region search below uses 50 to 100. A dip at a quality whose tables divide those of a lower dip (80 echoes 60, 75 echoes 50, 87 echoes 75) is dropped as an echo.
- **By region.** Each block's curve is scaled to 0-1 and compared with the median curve of the picture. Blocks that turn at some quality and sit 0.35 below the rest there carry a ghost the rest lacks; blocks that rise 0.35 above the rest show the rebound between an earlier quality and the current one. Six or more connected blocks of either kind are reported with their quality and a box, and the picture is written with the region tinted cyan. Blocks with too little detail to change under any re-save are left out, and the report says what share of the picture could be judged.

The current quality is read from the quantization tables when they are libjpeg's, and taken from the lowest point of the curve otherwise. A PNG has no current quality, so any dip in it reports a JPEG save in its past.

```
== twice.jpg
   saved at quality 90
   re-save error by quality 50-100:
   ██▇▆▆▅▄▃▃▂▂▂▂▃▃▃▃▄▄▄▅▅▅▅▅▄▄▃▃▂▂▂▂▃▃▂▁▁▂▁▁▁▁▁▁▁▁▁▁▁▁
   50        60        70        80        90      100
   ! whole image carries a ghost at quality 60 (dip 48%): it was saved as a JPEG at about that quality before
```

**Grid alignment.** A ghost appears only when the 8x8 grid of the re-save lines up with the grid of the earlier compression. A pasted patch lands on the grid in one case out of 64, so `--grid` repeats the search at the other 63 offsets with a coarse set of qualities, refines the best one, and reports the offset. It costs about 12 times the plain run (3 seconds against 40 for a 1000-pixel picture), and it finds less than the plain search does on the grid: the save on the host's grid blurs the earlier quantisation, so an off-grid ghost is faint and only the stronger ones stand out. Ghosts also need the earlier quality to be lower than the current one: a patch from a better JPEG leaves none, which is the case `ela` covers.

**Evaluation**, on 20 scikit-image sample pictures (300 to 1000 pixels wide):

| Test | Result |
|---|---|
| 80 untouched files (each picture saved once at 70, 80, 90 and 95) | nothing reported, apart from one real ghost (below) |
| 28 files saved at a lower quality first (60 then 90, 72 then 85, 80 then 95, 55 then 70) | 25 report the earlier quality within 1; the 3 missed were saved at 55 then 70, where the curve flattens instead of dipping |
| 7 files saved at 90 then 70 | nothing reported, as expected: the earlier quality was higher |
| 120 pictures with a 128x96 patch pasted on the grid from a copy at 55 to 80, saved 10 to 35 points higher | 93 found at the right place with the quality within 3; 4 more at the right place with the quality 4 to 6 points low (patches from 60 in a picture saved at 75, where the ghost sits close to the current quality); 23 missed, 12 of them in the clock and colour wheel pictures whose centres have too little detail to judge |
| 10 pictures with the same patch pasted 3 pixels off the grid, with `--grid` | 3 found at the right place, offset and quality; nothing found in the other 7 |
| 5 untouched files with `--grid` | nothing reported |

No test reported a region in a wrong place. The one report among the untouched files was the `coins` picture at quality 95, and its ghost is real: the PNG in the collection shows a dip at quality 85 on its own (error 0.11 against 4.1 at quality 90), so it was a JPEG saved at quality 85 before it became a PNG. Repeated saves at the same quality, saves through other encoders' tables (Apple ImageIO, WeChat), resizing and cropping all move or blur the dips, so a ghost names a quality only approximately and a missing ghost says little.

## Double quantisation

```bash
exif-inspect dq photo.jpg             # an earlier save's table, and the blocks that never saw it
```

A JPEG save divides every DCT coefficient by a step from its quantisation table and rounds. A second save with other steps rounds the rounded values again, and the two grids beat against each other: some values of the final coefficient collect several of the first save's values and others none, so its histogram shows periodic peaks and gaps (Lukas and Fridrich, Estimation of primary quantization matrix in double compressed JPEG images, 2003). The pattern depends on the first step, which makes the first save's table readable from the file. A block pasted or painted in after the first save was rounded only once, and its coefficients fall where a twice-rounded block's rarely do (Lin, He, Tang and Tang, [Fast, automatic and fine-grained tampered JPEG image detection via DCT coefficient analysis](https://doi.org/10.1016/j.patcog.2009.03.019), 2009; Bianchi and Piva, [Image forgery localization via block-grained analysis of JPEG artifacts](https://doi.org/10.1109/TIFS.2012.2187516), 2012).

The tool reads the quantised coefficients straight from the file, with the decoder `stego` uses, and works on the first 15 AC coefficients of the luma channel.

- **The first table.** To judge a candidate first step the tool needs the coefficients as they were before any rounding. Decoding the picture, cropping four pixels off the top and left and taking the DCT again breaks the block grid and gives a good estimate of them. Rounding those with a candidate step and then with the file's own predicts the histogram a double save would give; rounding them once predicts a single save. Every step from 1 to 48 is tried, in a mixture that lets up to half of the blocks be rounded once. A coefficient counts when its best step gains 0.03 nats per coefficient over a single save and 1.5 times what the model leaves unexplained, and three coefficients make a file that was saved before.
- **Its quality.** With six or more coefficients, the libjpeg quality whose table explains the histograms as well as free steps do (97% of the gain) is named. In a picture with little texture neighbouring steps can give the same histogram, and the tool then names a range of qualities.
- **By block.** Each 8x8 block gets the log likelihood ratio of its coefficients under the two predictions, averaged over the blocks within two of it. A ratio of 4 or more reads as compressed once; 16 connected blocks are reported as a region and tinted red. Blocks with fewer than two non-zero coefficients, with a channel at black or white (clipping between the saves follows no rounding model) or hanging over the edge of the picture are not judged.

A picture with a 128x96 patch of another pasted in between a save at quality 70 and one at 90:

```
== splice.jpg
   ! saved as a JPEG before, with another table: 15 of the first 15 coefficients show double quantisation
     first save's step for each (now) : 1: 7 (2), 2: 7 (2), 3: 8 (3), 4: 7 (2), 5: 6 (2), 6: 10 (3), 7: 8 (3), 8: 8 (3), 9: 8 (3), 10: 11 (4), 11: 10 (3), 12: 10 (3), 13: 11 (4), 14: 14 (5), 15: 25 (8)
     libjpeg's table at quality 70 explains them
   checkable : 98% of blocks have detail and no clipped pixel
   ! region around (56, 88, 208, 208) was compressed only once (5.9% of checkable blocks read that way): pasted, painted or filled in after the first save
   output : splice_dq.png (red = compressed once, dimmed = cannot judge)
```

The pattern is strong when the second save is the finer one, weak when it is coarser, and absent when the two saves used the same table or when the picture was cropped off the grid or resized in between. Progressive JPEGs are skipped, since the decoder reads baseline files only, and so is anything that is not a JPEG.

**Evaluation**, on 12 scikit-image sample pictures: the four with no JPEG in their past at full size (cameraman, moon, brick, gravel) and eight halved by box averaging, which takes an old block grid apart.

| Test | Result |
|---|---|
| saved once at 98, 95, 90, 80 or 70 (60 files) | none read as saved before |
| saved twice with the second save finer: 50 then 75, 60 then 80, 70 then 90, 75 then 85, 85 then 95, 90 then 95 (72 files) | all 72 read as saved twice, with the first quality inside the range named (one quality for 70, 85 and 90; up to 3 wide for 50); no region flagged |
| 80 then 85 (12 files) | 8 read as saved twice, first quality right; no region flagged |
| the same table twice, or the second save coarser: 85 then 85, 85 then 75, 90 then 75 (36 files) | 2 of the 85 then 75 files read as saved twice; nothing else |

A patch of another sample pasted in at a random place off the grid between the two saves; found means a region was reported with at least half of its blocks on the patch:

| Saves | 128x96 patch, of 11 | 64x64 patch, of 12 | `ghost` on the 128x96 files |
|---|---|---|---|
| 50 then 75 | 11 | not run | 0 |
| 60 then 80 | 11 | 12 | 4 |
| 70 then 90 | 11 | 11 | 0 |
| 75 then 85 | 10 | 10 | 3 |
| 80 then 85 | 3 | not run | 1 |
| 85 then 95 | 11 | 11 | 0 |
| 90 then 95 | 11 | 10 | 0 |
| 85 then 85, 85 then 75, 90 then 75 | 0 | not run | 0 |

`dq` reported no region off its patch in any of these files. The region covers a median of 70% to 92% of the patch for the pairs found throughout, and less where the patch is smooth or clipped. `ghost` is on the table because the two tools answer opposite cases. Here the picture has the JPEG past and the patch has none, which a ghost search has little to hold on to. With the roles swapped, a 128x96 patch from a quality 60 JPEG pasted on the grid into a picture with no JPEG past and saved once at 90 or 95, `ghost` finds 8 and 7 of 11 and `dq` reports nothing in any of the 22 files, since the picture as a whole was compressed once.

Real files: of the 89 JPEGs in the exif-samples collection, 23 have fewer than 500 blocks and are skipped. Of the other 66, 65 show no earlier table, as files straight from a camera should. The one that does carries `PMB Service Uploader` in its `Software` tag, Sony's upload tool, so it was indeed saved again. Opening the 62 clean ones of at least 256 pixels and saving them again, with and without a 96x96 patch of another photo pasted in:

| Second save | read as saved twice | region flagged, nothing pasted | patch found |
|---|---|---|---|
| quality 98 | 59 of 62 | 0 | 56 |
| quality 95 | 55 | 0 | 40 |
| quality 90 | 33 | 0 | 18 |
| quality 85 | 16 with a patch | not run | 12 |

No region was reported off its patch here either. Cameras save at high quality with tables of their own, so a second save has to be finer still to leave a clear pattern, and the rate falls as the second quality does. A 16-megapixel file takes about twelve seconds, nearly all of it spent reading the coefficients; larger files are read from the top down to 200 000 blocks.

Six of the 16 sample pictures carry an earlier table themselves when saved once at 98: coins (quality 85, the value `ghost` reads from it too), the scanned page (an encoder that is not libjpeg), retina, the Hubble field, rocket and grass. Grass shows it on three high coefficients only, with the steps of quality 90, and the pixels of the PNG confirm it: the coefficient at zigzag position 15 lies on average 0.05 of a step from a multiple of 8, where a picture with no JPEG past would give 0.25, and it does so only on the picture's own grid. Synthetic drawings with flat colours and hard edges are a poor fit for the model: the colour wheel sample, saved twice, is read as such for 3 of the 6 pairs with a finer second save, and never has a region flagged.

## Lighting direction

```bash
exif-inspect light photo.jpg                                   # every usable outline
exif-inspect light --box 120,80,260,300 --box 400,90,520,310 photo.jpg   # one object per box
```

Along the outline where an object hides what is behind it, the surface turns away from the camera, so its normal lies in the image plane and points straight out of the outline. Under one distant light and a matte surface the brightness just inside the outline is a linear function of that normal, `I = R (N . L) + A`, with `L` the light direction projected onto the picture, `R` the reflectance and `A` the ambient light (Johnson and Farid, [Exposing digital forgeries by detecting inconsistencies in lighting](https://farid.berkeley.edu/downloads/publications/acm05.pdf), 2005). A least-squares fit along one outline gives `L` for that object. Everything in a real photograph shares one light, so an object whose outline says otherwise was lit somewhere else.

The tool finds outlines on its own. Edge pixels (Sobel gradient, thinned, hysteresis thresholds) are grouped into 8-connected chains; each chain's normals are turned to point away from its centre, the brightness 2.5 to 4.5 pixels inside is sampled, and the fit is kept when the chain covers at least 100 degrees of normal directions, the model explains at least 70% of the brightness variation, the shading swings by at least 10 grey levels and the direction is determined within 20 degrees. Directions are given in degrees, 0 for light from the right, 90 from above, 180 from the left, 270 from below.

- **Without boxes**, every usable outline counts. When outlines carrying at least 60% of the evidence agree within 35 degrees, that is the scene's light, and a well-determined outline (fit 0.8 or better, within 8 degrees) more than 35 degrees off it is flagged. When they do not agree, the tool says so and flags nothing.
- **With `--box`**, each box stands for one object and the best outline inside it (most pixels, weighted by fit) gives that object's direction. This is the mode for a real question such as whether two people in one picture were lit alike: pick the boxes around them.

The picture written next to the input draws each used outline with an arrow toward its light, green when it agrees, red when it does not, and a yellow arrow in the corner for the common direction.

```
== spliced.jpg
   outlines : 4 usable of 8 edge chains
   - (227, 40, 291, 111): lit from 40 degrees (the upper right), +-1, fit 0.95
   - (105, 110, 191, 209): lit from 40 degrees (the upper right), +-1, fit 0.95
   ! (300, 155, 375, 241): lit from 220 degrees (the lower left), +-1, fit 0.95
   - (228, 225, 286, 290): lit from 40 degrees (the upper right), +-1, fit 0.95
   light from 40 degrees (the upper right), outlines agree within +-0
   ! outline at (300, 155, 375, 241) is lit from 220 degrees, 180 off the rest: check whether it belongs to this scene
```

**Evaluation.** On rendered matte spheres (light 30 degrees above the image plane, sensor noise, JPEG quality 90), the estimate is close to exact: over 12 directions and three backgrounds the median error was 0.4 degrees and the largest 1.7. In a scene of four spheres, one lit from 220 degrees among three lit from 40, the odd sphere is flagged with the others agreeing within a degree, and a sphere only 60 degrees off is flagged as well. Four spheres each lit from a different side give no consensus and no flag.

Real photographs are harder, and the limit is choosing outlines, not the fit. In the scikit-image sample photos the automatic pass kept 6 to 9 outlines per picture, but most belonged to helmet rims, folds, lips and edges between two materials rather than to the silhouette of a convex object, and their directions scattered: the astronaut, the cat and the motorcycle gave no consensus, which is the right answer for that evidence. The method's authors chose outlines by hand, and `--box` is the equivalent here. Several lights, a nearby light, shiny surfaces, cast shadows and a change of material along the outline all break the model, so a flagged object is a lead to examine with the other tools, not a verdict.

## Demosaicing traces (CFA)

```bash
exif-inspect cfa photo.png            # writes photo_cfa.png when a region lacks the trace
```

A camera sensor records one colour per pixel through a Bayer filter, half of them green on a checkerboard, and the demosaicing step fills in the other half of the green channel from its neighbours. Those filled-in pixels are predicted far better by their neighbours than the measured ones, so the two lattices of the checkerboard have very different prediction residuals (Ferrara, Bianchi, De Rosa and Piva, [Image forgery localization via fine-grained analysis of CFA artifacts](https://ieeexplore.ieee.org/document/6210378), 2012). The tool predicts every green pixel from its four neighbours, squares the residual, and in 32-pixel blocks takes the log ratio `L` of its mean on the two lattices. Clipped pixels and their neighbours are left out.

- **Whole picture.** The median `L` over blocks with enough residual gives the trace: *strong* when at least 0.5 in size, *weak* when small but of the same sign in at least 75% of the blocks (a sign test at 5 standard deviations), *none* otherwise. Its sign names the interpolated lattice. A picture that was resized, rotated or heavily compressed after demosaicing, or that never went through it (a rendering, a screenshot, a generated image), shows none.
- **By region.** JPEG and sensor noise add the same error to both lattices, so `L` shrinks toward zero where the picture's own residual is small. Rather than one threshold, the tool learns from the picture how `L` depends on residual power (the median in each of 8 power bins, kept monotone) and flags blocks whose `L` is below 0.15 while sitting more than 3 spreads under what their power predicts. Blocks whose predicted `L` is under 0.5 cannot be judged. Four or more connected flagged blocks are reported as a region and tinted red in the output picture; a region pasted from another source, resized, generated, or shifted by an odd number of pixels against the grid lacks the trace or carries it the other way round.

**Evaluation**, on 9 scikit-image sample pictures halved in size (which removes whatever traces they had), sampled through an RGGB mosaic and demosaiced bilinearly:

| Test | Result |
|---|---|
| untouched, saved as PNG and at JPEG quality 95, 90, 80, 70 (45 files) | no region flagged; the trace reads strong in every PNG and at quality 95, weak down to quality 70 or 80, none in the two smoothest pictures at 80 and below |
| the same pictures before demosaicing (4 files) | none |
| a 128x96 patch pasted from the picture before demosaicing, from a copy shifted one pixel sideways, or from a copy resized 1.3 times (27 forgeries per quality) | located in 22 of 27 as PNG, 20 of 27 at quality 95, 11 of 27 at quality 90; the retina picture is too smooth to judge anywhere |

Real camera files are another matter. Of the 89 JPEGs in the [exif-samples](https://github.com/ianare/exif-samples) collection, 85 show no trace at all, including recent phones saved at quality 95: noise reduction, sharpening, multi-frame processing and JPEG itself wipe out the lattice difference. Three show a weak one, and one, a Canon IXUS 40 photo re-saved by Windows Photo Gallery near quality 96, shows a strong trace with one flagged region of five blocks at the edge of a blown-out white band, which is a false alarm. So a missing trace says nothing on its own, and the tool is for pictures that kept theirs: raw conversions exported as PNG, TIFF or high-quality JPEG, scans, and lab images. Where the trace is there, a region without it is hard to explain innocently.

## Noise level

```bash
exif-inspect noise photo.jpg          # writes photo_noise.png when a region stands out
```

A sensor adds noise to every pixel it records, and in one exposure that noise has one level, rising gently with brightness. A region pasted in from another photograph brings its own level, and a region that was smoothed, painted or filled by software has almost none (Mahdian and Saic, [Using noise inconsistencies for blind image forensics](https://doi.org/10.1016/j.imavis.2009.02.001), 2009). The tool measures the noise in 32-pixel blocks and compares each block with the blocks it should resemble.

- **Measuring.** The noise of a block is the smallest eigenvalue of the covariance of its 5x5 patches that lie on flat pixels: the picture's own content fills the leading components, noise alone reaches every component, so the smallest one is the noise floor (Pyatykh, Hesser and Zheng, 2013, with the weak-texture patch selection of Liu, Tanaka and Okutomi, 2013). Flat pixels and the block's texture are measured on a copy blurred three times, where noise is almost gone, so that a noisy region is not mistaken for texture. A block with fewer than 150 flat patches is not judged.
- **Comparing.** Some texture leaks into any local estimate and sensor noise grows with brightness, so the level an untouched block should show is learned from the picture itself: the median over blocks of similar texture (6 bins, interpolated) and, when there are enough blocks, similar brightness (3 bins). The busiest tenth of the blocks is left out, since the curve has nothing beyond them. A block is flagged when it differs from its prediction by a factor of 1.8, by 1.5 grey levels, and by three times the picture's own scatter. Six or more connected flagged blocks are reported as a region, red when noisier and blue when smoother than its peers.

The report also gives the picture's noise level, taken over its quietest third, and whether it rises with brightness.

**Evaluation**, on 10 scikit-image sample pictures with Gaussian noise of 4 grey levels added, then a 128x96 patch given noise of its own:

| Test | PNG | JPEG 95 | JPEG 90 | JPEG 80 |
|---|---|---|---|---|
| untouched (10 pictures per quality): regions flagged | 0 | 0 | 0 | 0 |
| patch with noise 12, located | 7 of 10 | 6 | 7 | 6 |
| patch with noise 8, located | 3 of 10 | 4 | 5 | 1 |
| patch with no noise (smoothed), located | 3 of 10 | 4 | 0 | 0 |

A noisier patch is found in most pictures and survives compression, because the extra noise is larger than what JPEG removes. A smoothed patch is found only where the picture around it is quiet: in textured content the estimate's floor of 2 to 4 grey levels hides the difference, and from JPEG quality 90 down the picture's own noise is mostly gone too, so the picture is as smooth as the patch.

On 63 untouched camera JPEGs from the exif-samples collection (300 pixels or more on each side), 7 had a region flagged: grainy pictures from an old Nikon compact where leaves and sky came out smoother than their peers, and a dark corner of foliage in a phone HDR picture that came out noisier. In-camera noise reduction works hardest on smooth areas and leaves dark busy ones alone, so a real photograph is not perfectly even either, and a flagged region is a lead to check with the other tools rather than a verdict. The noise of a region that was blurred, cloned over or filled by AI is often the only pixel-level trace left, which is what the tool is for.

## Resizing and turning

```bash
exif-inspect resample photo.png       # writes photo_resample.png when a trace is found
```

Enlarging, shrinking or turning a picture computes every new pixel from a few old ones. How much a new pixel owes to its neighbours depends on where it falls between the old ones, and that position repeats: after enlarging by 1.25, every fifth pixel sits exactly on an old one. The picture then carries a periodic pattern in how well each pixel is predicted by its neighbours (Popescu and Farid, [Exposing digital forgeries by detecting traces of resampling](https://doi.org/10.1109/TSP.2004.839932), 2005), and a fixed predictor is enough to show it as sharp peaks in the spectrum of the prediction error (Kirchner, [Fast and reliable resampling detection by spectral analysis of fixed linear predictor residue](https://doi.org/10.1145/1411328.1411333), 2008). An object scaled to fit before it is pasted in carries the pattern on its own, so the tool reports where the trace is as well as whether it is there.

- **Peaks.** Each pixel is predicted from its eight neighbours and the size of the error is kept. Its spectrum is averaged over half-overlapping tiles, 256 pixels wide where the picture has 30 of them and 128 or 64 otherwise, and every frequency is compared with the median of its surroundings, of its row and of its column. A peak counts when it stands 2 times above that and 8 spreads of the spectrum's own scatter, and lies at least 0.07 cycles per pixel from zero. Cloth, brick and print are periodic too, so where the picture's own spectrum rises, the peak in the error must be 4 times higher still.
- **Reading.** A peak at frequency `f` on an axis comes from enlarging by `1/(1-f)` or `1/f`, from reducing to `1/(1+f)`, or, when `f` is small, from a turn by `asin(f)`; all of them are printed. A peak off the axes that fits a pure turn is read as its angle.
- **JPEG blocks.** A picture resized after a JPEG save keeps that save's 8-pixel blocks, stretched by the factor. They show as a comb, peaks at two or more multiples of one frequency, and the comb gives the factor directly: blocks 10.0 pixels wide mean 1.25x. Reductions leave little interpolation trace, and the comb is how most of them are found.
- **By region.** The peaks' height is measured in every 64-pixel tile against the tile's own spectrum, and smoothed by a median over the tile and its neighbours. When half of the tiles carry the trace it belongs to the picture; otherwise six or more connected tiles are reported as a region and tinted red. A faint trace everywhere is told from a strong one in one place by a sign test on the other tiles, since chance lifts a frequency above its surroundings in half of them. When the whole picture's spectrum shows nothing, each tile votes for its own strongest frequency, and a connected group voting alike is tested as a region of its own; only peaks on the axes are accepted there, because an oblique peak in one region is usually a texture.

Three things are out of reach by design. Multiples of 1/8 belong to the file's own JPEG grid and are never counted, which hides the factors whose trace lands there (1.14, 1.33, 1.6, 2.67, 4, 8). At exactly 2x both new pixels of each pair sit a quarter step from an old one, are interpolated alike, and nothing repeats. Factors within 7% of 1 and turns under 4 degrees fall below the lowest frequency read.

A 512-pixel picture with a 224x192 patch of another picture, enlarged 1.5 times, pasted at (216, 304):

```
== splice.png
   checkable : 100% of the picture has enough detail to judge
   ! resampling trace around (192, 256, 480, 512) only (20% of checkable tiles carry it clearly): that area was resized or turned on its own, as an object scaled to fit before pasting is
     peak at 0.334 cycles per pixel down, 14.3 times its surroundings: enlarged 1.50x (or 3.00x), or reduced to 0.75x
     peak at 0.332 cycles per pixel across, 10.6 times its surroundings: enlarged 1.50x (or 3.01x), or reduced to 0.75x
   output : splice_resample.png (red = carries the trace, dimmed = cannot judge, white frame = area analysed)
```

**Evaluation.** Of 16 scikit-image sample pictures as distributed, three show a trace: the astronaut across the whole picture (peaks at 0.176 on both axes, 11 times their surroundings, which reads as 1.21x), the scanned text (0.352), and one region of the coffee cup. Their history is unknown, so they are left out, and the other 13 (191 to 1411 pixels on the short side) are the untouched set. Saved as PNG and as JPEG at quality 95, 90, 80 and 70, none of the 65 files is flagged.

The whole picture resized with bicubic interpolation, or turned and cropped to its centre; found, and in brackets how many of those carry the true factor or angle among their readings:

| Operation | PNG | JPEG 95 | JPEG 90 |
|---|---|---|---|
| enlarged 1.1x | 11 of 13 (11) | 6 (4) | 3 (1) |
| enlarged 1.25x | 12 (12) | 9 (7) | 6 (2) |
| enlarged 1.5x | 12 (12) | 9 (8) | 6 (3) |
| enlarged 1.7x | 12 (12) | 8 (6) | 7 (5) |
| enlarged 2x | 4 (0) | 1 (0) | 2 (0) |
| enlarged 3x | 12 (12) | 8 (5) | 8 (1) |
| reduced to 0.75x | 5 (5) | 0 | 0 |
| reduced to 0.5x | 0 of 12 | 0 | 0 |
| turned 5 degrees | 5 of 12 (2) | 2 (1) | 1 (0) |
| turned 10 degrees | 6 (3) | 1 (1) | 1 (0) |
| turned 20 degrees | 6 (4) | 0 | 1 (0) |
| turned 35 degrees | 10 (9) | 7 (7) | 3 (3) |
| turned 45 degrees | 10 (10) | 8 (7) | 3 (3) |

The picture missed at every enlargement is the colour wheel, a smooth drawing with almost no prediction error to analyse; at 1.1x the scanned page, 191 pixels high, is missed as well. The four found at 2x were JPEGs before: their blocks, now 16 pixels wide, put peaks at odd multiples of 1/16, and the comb's first tooth at 1/16 is below the lowest frequency read, so the factor is not recovered. Three of the five reductions found are read from the comb, and two from an interpolation peak at 1/3 that Pillow's filtered reduction left, strong in the moon picture and faint in the cameraman. Small turns are the weak spot: their peaks are a few times lower than those of a resize in these pictures.

A patch of another sample picture, resized or turned, pasted in at a random place; located means reported as a region that covers at least half of the patch within a box at most four times its area:

| Patch | 128x96, PNG | 128x96, JPEG 95 | 224x192, PNG | 224x192, JPEG 95 |
|---|---|---|---|---|
| enlarged 1.25x | 2 of 12 | 0 | 5 of 9 | 0 |
| enlarged 1.5x | 4 | 0 | 8 | 0 |
| enlarged 3x | 3 | 0 | 5 | 0 |
| reduced to 0.75x | 0 | 0 | 1 | 0 |
| turned 20 degrees | 1 | 0 | 1 | 0 |

No forgery was flagged anywhere other than at its patch. A pasted region is found when it is large, enlarged and the file was saved without loss; one JPEG save at quality 95 after the paste is enough to hide it. That limits the tool to PNG and TIFF exports, screenshots, scans and figures, which is where composites often end up.

Real files, the 65 JPEGs of the [exif-samples](https://github.com/ianare/exif-samples) collection that are at least 128 pixels on each side: 63 show no trace. The other two are the two frames of one iPhone 6 HDR shot, with a faint trace across the picture and a peak at 0.493 on both axes, which reads as 1.97x or 2.03x. The picture is soft in the way a digitally zoomed phone picture is, and a camera that zooms, corrects its lens or aligns frames does resample; the tool cannot tell that from an enlargement made afterwards.

The 63 clean ones resized afterwards, which is what happens to most pictures on their way to a slide or a web page; found, and in brackets with the true factor among the readings, then with the comb giving it:

| Resized | PNG | JPEG 95 | JPEG 90 |
|---|---|---|---|
| 1.25x | 61 of 63 (61, comb 42) | 52 (32, comb 3) | 33 (23, comb 0) |
| 1.5x | 61 (61, comb 38) | 57 (35, comb 1) | 52 (14, comb 0) |
| 0.8x | 31 (28, comb 28) | 2 (1, comb 1) | 1 (0) |
| 0.5x | 3 of 61 (0) | 1 (0) | 0 |

Real pictures do better than the samples after a JPEG save because the first save's blocks add their own peaks, and the reading suffers for the same reason: a tooth of the comb read alone gives a wrong factor, so after a lossy save the verdict that the picture was resized is more reliable than the factor printed with it. Halving turns 8-pixel blocks into 4-pixel ones, which land on the grid and stay hidden.

## XMP editing history

```bash
exif-inspect xmp photo.jpg            # creator tool, dates, document IDs, history, Camera Raw settings
exif-inspect xmp --raw photo.jpg      # the XML packet itself
```

Adobe's tools, and many others, keep an XML packet (XMP) in the file next to EXIF, and it says far more about editing than EXIF does. `xmp:CreatorTool` names the last program that wrote it. `xmpMM:History` gets an event on every save in Photoshop and Lightroom, with the software, the time and what changed, and `xmpMM:DerivedFrom` points at the document the file was made from. Lightroom and Camera Raw store every development setting: the raw file name, the sliders, the crop rectangle, each local adjustment, and each place where the healing or clone brush was used (`crs:RetouchAreas`). Generators and AI editors leave their name there too (Adobe Firefly in the history, the IPTC digital source type, "Made with Google AI" as credit).

The packet is read from JPEG APP1 segments, including Adobe's extended-XMP continuation for packets over 64 KB, from PNG `iTXt` chunks and WebP `XMP` chunks, and from Pillow's metadata for other formats. It is parsed with the standard library, capped at 8 MB, into properties keyed by the usual prefixes (`xmp`, `xmpMM`, `crs`, `photoshop`, `dc`, `Iptc4xmpExt`, ...). The main report shows one summary line and the `xmp_*` findings listed above; the subcommand prints the whole history and the Camera Raw settings that differ from their defaults.

```
== edited.jpg
   packet : 2561 bytes in JPEG APP1 segment; namespaces crs, dc, photoshop, xmp, xmpMM
   creator tool : Adobe Photoshop Lightroom Classic 13.0 (Macintosh)
   create date  : 2024-05-04T10:22:31
   modify date  : 2024-05-06T18:40:12+02:00
   derived from : xmp.did:0000
   history (2 steps, oldest first):
     (no time)  derived  converted from image/x-nikon-nef to image/jpeg, saved to new location
     2024-05-06 18:40:12+02:00  saved  Adobe Photoshop Lightroom Classic 13.0 (Macintosh)  changed /
   camera raw:
     raw file        DSC_0421.NEF
     Exposure2012    +0.65
     Highlights2012  -40
     crop            left 0.1, top 0.05, right 0.9, bottom 0.95
     retouch         3 healed or cloned area(s)
   ! Camera Raw healing or clone brush applied in 3 place(s): parts of the picture were painted over.
   ! Cropped in Camera Raw to 80% x 90% of the original frame (left 0.10, top 0.05).
```

XMP is plain text that anyone can edit, and most messaging apps and social platforms strip it, so what it says is a lead and what it does not say means nothing. A retouch entry is as close as metadata gets to naming the spot that was changed: Lightroom records the position and size of every healed area, so the packet printed with `--raw` shows where to look with `ela`, `noise` and `copymove`.

## HTML report

```bash
exif-inspect report photo.jpg                       # writes photo_report.html
exif-inspect report --skip light,copymove photo.jpg # leave tools out
exif-inspect report --grid -o out.html photo.jpg    # slower ghost search, chosen output path
```

One page with everything: the picture, the findings (flagged ones first), the metadata report, and a section for each tool in turn, `ela`, `ghost`, `dq`, `cfa`, `noise`, `resample`, `light`, `copymove`, `thumbnail` (when the file has one) and `xmp`, each with the map it drew and the text it printed, and the time it took. Pictures are embedded in the page, downscaled to 1400 pixels, so the file stands alone and can be sent on; a report on a 12-megapixel photo is a few hundred kilobytes. A tool that fails on a file gets a red heading and its error, and the rest of the page is still written. `prnu` is not included, since it needs a camera fingerprint.

## Hidden data

```bash
exif-inspect stego photo.png              # trailer and LSB steganalysis, writes photo_lsb.png
exif-inspect stego photo.jpg              # trailer and the JSteg / F5 tests on the DCT coefficients
exif-inspect stego --extract photo.jpg    # also saves the bytes after the picture as photo_trailer.bin
```

Three places to hide things in an image file. The simplest is after the picture: every viewer stops reading a JPEG at its EOI marker, a PNG at its IEND chunk, a WebP at the end of its RIFF container and a GIF at its trailer byte, so whatever follows rides along unseen. The main report checks every file for this (`trailing_data`), says how many bytes there are and what they look like (ZIP and other archives, another image, PDF, executables, XML, plain text), and `stego --extract` saves them. A second JPEG that an MPF segment announces, such as an iPhone's gain map, is reported as expected (`trailing_image`).

The other place is inside the pixels. Replacing the least significant bit of each sample changes the picture by at most one grey level, invisibly, but it changes the statistics of the sample values. For lossless pictures (PNG, BMP, TIFF, GIF; JPEG stores no samples) the subcommand gives three views:

- **The LSB planes as a picture.** A natural image's lowest bit still follows the content: smooth areas show contour-like bands, textures show structure. An embedded payload is pure noise. The picture written next to the input shows the lowest bit of each channel side by side.
- **RS analysis** (Fridrich, Goljan and Du, 2001). Pixels are taken in groups of four; flipping the lowest bits of two of them by the mask makes some groups noisier (regular) and some smoother (singular), and in a clean picture the counts barely depend on the direction of the flip. A payload erodes that asymmetry in a known way, and the quadratic Fridrich derived turns the four counts into an estimate of how many samples carry random bits. The estimate is reported per channel and averaged; from 5% up a payload is flagged.
- **The chi-square attack** (Westfeld and Pfitzmann, 1999) over consecutive blocks of 4096 samples in scan order. Embedding evens out the counts of each pair of values 2k and 2k+1, so a payload written from the top of the picture shows as a run of blocks where the test fires. The run is reported as a share of the scan and, when it agrees with the RS estimate, the payload is placed "from the start of the picture".

**Evaluation**, on 12 scikit-image sample pictures with random bits written into the lowest bits:

| Payload | RS estimate |
|---|---|
| none | 0.00 to 0.02 |
| 10% of the samples, at random | 0.08 to 0.12 |
| 50% of the samples, at random | 0.46 to 0.52 |
| every sample | 0.94 to 1.00 |

The chi-square attack on its own is not trusted for a verdict: on these pictures, several of which were JPEGs once, it reads clean pictures as largely embedded, which is its known weakness on noisy or previously compressed content. It only adds the position of a payload that RS has already found.

Steganography that keeps the statistics (matrix embedding, adaptive schemes such as HUGO or S-UNIWARD) and steganography in JPEG coefficients (JSteg, F5, OutGuess, Steghide) are not covered: RS sees plain LSB replacement, the kind most "hide a message in a picture" tools use.

The third place is the JPEG itself. A JPEG stores no pixels, only quantised DCT coefficients, and that is where JPEG steganography writes. The tool has its own baseline JPEG decoder (`jpegdct`) that reads the coefficients exactly as the file holds them, and runs two tests on the luma AC coefficients:

- **JSteg-style LSB replacement** (JSteg, early OutGuess, most homemade tools) flips the lowest bit of coefficients other than 0 and 1. In a clean picture there are far more 2s than 3s and far more -2s than -1s; embedding evens each pair out. Westfeld's chi-square test runs over blocks of 10 000 coefficients in scan order and reports the share of the stream that reads as evened out and how much of it sits in one run from the start, which is where a payload written from the top of the picture lands. Clean pictures read 0% throughout; from 10% up the file is flagged.
- **F5-style shrinkage** (F5, and matrix embedding in general) decrements the absolute value of coefficients instead, which raises the count of 0s and lowers 1s and 2s. Fridrich's calibration estimates how many coefficients were changed: decompress, crop four pixels off the left and top so the block grid moves, recompress with the file's own quantisation tables, and compare the low-frequency histograms with what this content would have clean. The estimate is printed; from 15% it is noted and from 30% flagged. It is a lead rather than a verdict: a few heavily processed phone HDR pictures read 20-35% with nothing in them, and F5 at a 30% change rate reads 20-40%.

Progressive and arithmetic-coded JPEGs are not read. Adaptive schemes designed against these statistics (nsF5 at low rates, J-UNIWARD) are not detected.

**Evaluation**, on eight scikit-image sample pictures saved at quality 80 and nine camera JPEGs:

| File | LSB share | F5 estimate |
|---|---|---|
| clean | 0% | 0 to 12% (one phone HDR picture 20%) |
| JSteg, random bits in the first 30% of the usable coefficients | 23 to 44%, all in one run from the start | |
| JSteg, every usable coefficient | 59 to 93% | |
| F5-like, 10% of the non-zero coefficients changed | 0% | 0 to 21% |
| F5-like, 30% changed | 0% | 18 to 43% |

## Comparing two versions

```bash
exif-inspect compare original.jpg forwarded.jpg       # writes original_vs_forwarded.png
```

Given two files, the smaller scene is searched for inside the larger one at every position, at scales down to a fifth of the frame and at the four rotations, with the normalised cross-correlation that also places the EXIF thumbnail. The best placement gives the crop box and the scale factor between the two. The pair is then aligned at full resolution, brightness and contrast matched, and compared in 16-pixel blocks: blocks that differ by more than four spreads of the block differences and at least 10 grey levels are the edits between the versions, reported as boxes in the container's coordinates and drawn in orange.

```
== parent.jpg  vs  child.jpg
   * format        A: JPEG 600x400                     B: JPEG 330x240
   * jpeg quality  A: 92                               B: 78
   * camera        A: Canon EOS R6                     B: -
   match : B is a crop of A: box (120, 60, 560, 380), 59% of the frame, scaled 0.75x (down); correlation 0.992
   ! changed : 2 region(s) differ beyond resampling and compression noise (4.8% of the shared area):
       (440, 220, 520, 332) in A
   parent : A came first: A shows more of the scene; B covers 59% of it
```

Which file is the parent is read from the evidence in order: the one whose frame contains the other; else the one with more pixels; else the one saved at higher JPEG quality; else the one with more of its metadata left; else the one modified earlier. The reason is printed, since each can be forged: a crop can be upscaled past the original, and metadata can be copied across. Placements covering under a quarter of the frame need a correlation of 0.75 instead of 0.6 and a side of at least 40 pixels at analysis size, because a small patch of noise matches anywhere. Two pictures that place with a correlation of 0.97 or better, at the same size and with no changed region, are reported as the same picture.

## Duplicates across folders

```bash
exif-inspect dupes evidence/ downloads/       # folders are searched recursively
exif-inspect dupes --distance 4 evidence/     # stricter grouping
exif-inspect dupes --json evidence/ > groups.json
```

Case work piles up copies: the same photo saved at three qualities, resized for a message, rotated by a viewer, duplicated byte for byte across folders. `dupes` groups them. Exact duplicates share a SHA-256. Near-duplicates share a perceptual hash: the picture is shrunk to 32x32 grey, its 2-D DCT taken, and the 64 lowest frequencies apart from the mean are thresholded at their median into 64 bits (pHash). Re-saving, resizing, mild colour changes and watermarks move only a few bits; different pictures differ in about half. A second hash from the gradient between neighbouring pixels of a 9x8 thumbnail (dHash) must agree within 16 bits before two files are grouped, which keeps smooth pictures with similar layouts apart. Both hashes are taken at the four rotations and the closest pair counts, so a turned copy is found and its rotation reported.

```
scanned 11 file(s): 3 group(s) of duplicates, 2 single(s), 0 unreadable

== group 1: 5 files, within 10 hash bits
   * coffee.jpg
       JPEG 600x400, q92, 2 EXIF tags, 78 KB; likely original
     coffee_rot.jpg
       JPEG 400x600, q85, 0 EXIF tags, 55 KB; distance 0/0, turned 270
     sub/coffee_thumb.png
       PNG 300x200, -, 0 EXIF tags, 108 KB; distance 0/0
```

Within a group the likely original is the file with the most pixels, then the higher JPEG quality, then the most EXIF tags, then the earliest modification time. Crops are not duplicates by these hashes, since the hash describes the whole frame; `compare` handles a pair that may be crop and source. A scan reads every file once and compares hashes pairwise, so a few thousand files take seconds.

## Cases behind the checks

Each of these checks came from a file met in practice:

- **`extension_mismatch`**: a screenshot forwarded through a messaging app arrived as a PNG named `.jpg`.
- **`dimension_mismatch`**: a phone screenshot cropped to 796 px wide still carried EXIF saying 1206 px, the phone's full screen width.
- **`c2pa_manifest`**: an image downloaded straight from ChatGPT had no EXIF but held C2PA credentials in a PNG `caBX` chunk. Before v0.3.0 the tool reported only "No EXIF metadata".
- **`copymove --keypoints`**: in a red-team test, a chalkboard sign was copied, shrunk to about a third and pasted elsewhere in the same street photo. Block matching found nothing, even with `--min-pairs 50`. `ela` still showed the paste as a bright rectangle with hard edges, because the resized patch had a different compression history from its surroundings. With `--keypoints` the same photo is caught: the pasted copy, about 40 by 60 pixels, is reported at scale 0.36 of the original sign with 18 matching keypoints.
- **`prnu locate`**: an iPhone photo edited with Photos' Clean Up was caught only by the IPTC AI label in its metadata. The removed areas had almost no sensor pattern even in the untouched original, so their loss was not detectable (see [Finding replaced areas](#camera-identification-prnu)).

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

## HDR gain map

iPhones from the iPhone 12 on, and Ultra HDR cameras such as the Pixel, save a small grayscale gain map with each photo, which HDR displays use to brighten the highlights. A JPEG keeps it as a second image listed in its MPF (APP2) segment; a HEIC keeps it as an auxiliary image. `exif-inspect` reports its size and maker and checks that it still fits the picture. Apple writes it at exactly half the image size.

An editor has three choices: rewrite the gain map along with the picture, drop it, or leave the old one in place. The last would preserve whatever was removed. One iPhone 16 Pro photo of a pill bottle on a counter was edited in four apps, each removing the bottle and its shadow:

| Editor | Gain map | Image size | Other traces |
|---|---|---|---|
| none (original) | 2142x2856, half size | 4284x5712 | |
| Apple Photos Clean Up | rewritten, bottle gone | 4284x5712 | IPTC `compositeWithTrainedAlgorithmicMedia` |
| Google Photos Magic Eraser | rewritten, bottle gone, **left at 2142x2856** | 3144x4192 | IPTC source type, "Edited with Google AI" |
| Snapseed 4.2 | dropped | 4284x5712 | `Software` tag, MakerNote dropped |
| Meitu 12.22 | dropped | 4284x5712 | `Software` tag, MakerNote dropped |

None of the four left a stale gain map. Google Photos shrinks the picture and keeps a gain map sized for the original (scale 0.68), which `gain_map_misfit` catches without relying on its label. A file whose EXIF names a recent iPhone but has no gain map gets an informational `gain_map_missing`; photos taken with HDR off, or converted by other software, lack one too.

The Clean Up photo from the "Finding replaced areas" case below also had its gain map rewritten in exactly the two edited areas and left untouched elsewhere. Comparing the gain map with the picture in 32-pixel blocks did not locate the edit without the original: the most anomalous blocks fell in the edited areas no more often than chance.

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

The analysis uses a 2048-pixel centre crop (or the largest square that fits), so a fingerprint only fits photos taken at the same resolution. PCE grows with the number of pixels compared; `--crop` trades speed for strength. The tool reads PCE of 60 or more as the same camera, 20 to 60 as inconclusive, and below 20 as no evidence. A different camera inside the same phone reached 21.5 in the test below, so an inconclusive score says nothing about which way the answer leans.

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

**A phone.** An iPhone 16 Pro, HEIC, 21 shots of a plain wall with the main 1x camera set to 24 megapixels. In the dim room the phone saved 11 of them at 12 megapixels instead, so two fingerprints were built, each over the largest centred square (`--crop 0`):

| Fingerprint | Tested on | PCE |
|---|---|---|
| main camera, 24 MP (10 shots) | five everyday 1x photos, 24 MP | 937 to 1449 |
| main camera, 12 MP (11 shots) | three photos from the 0.5x ultrawide camera of the same phone, 12 MP | 12.8 to 21.5 |

The main camera's pattern is strong once the fingerprint and the photos share one resolution, stronger than the Nikons' at their smaller crop. The ultrawide camera has its own sensor and stays far below 60. Its scores still sit a little above those between separate Nikons (up to 14.9), which fits processing shared by the lenses of one phone; with a weaker fingerprint such scores could reach the inconclusive band.

An earlier attempt pooled the 24 and 12 megapixel shots into one fingerprint over a 2048-pixel crop and scored the same 1x photos at only 43 to 118. Two differences in pixel mapping blur each other, which is why `prnu build` now keeps a single resolution.

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

**A real AI removal.** On the same iPhone, Photos' Clean Up removed two objects from a 24-megapixel photo: a crumpled white plastic bag, now filled with the wooden surface around it, and a strip along the bright edge of a book. The edited JPEG was checked against the 24 MP fingerprint, and `prnu locate` marked nothing. With the untouched HEIC as ground truth, the pattern strength in each area (correlation of `W` with `I*K` divided by its expected value, over 64-pixel tiles) was:

| Area | original photo | after Clean Up | whole photo |
|---|---|---|---|
| plastic bag | 0.007 | 0.029 | about 0.04 |
| book edge | 0.007 | -0.012 | |

Both areas carried about a sixth of the photo's usual pattern before anything was edited, probably because the phone's multi-frame processing smeared the sensor pattern on these bright, glossy surfaces. Removing them took away little that could be missed. The filled bag area even scores higher than before, possibly because the fill borrows the noise texture of the real pixels around it. Making the prediction depend on brightness, since this phone's pattern is relatively stronger in dark areas, did not help: the untouched original then showed deficits as strong as the edited photo's. The edit was given away only by its metadata, where Photos writes the IPTC source type `compositeWithTrainedAlgorithmicMedia` (reported as `ai_composite_marker`). PRNU localisation needs the replaced area to have had a clear pattern to begin with.

Phones add their own difficulties. Each lens of a multi-camera phone has its own sensor and fingerprint. Multi-frame modes (night mode, HDR merges) and digital stabilisation shift pixels between frames and blur the pattern, and binned 48-megapixel sensors output processed 12- or 24-megapixel images. Build the fingerprint from photos of the same lens, resolution and mode as the questioned photo. Phones also switch resolution on their own (an iPhone set to 24 megapixels falls back to 12 in dim light), so `prnu build` keeps only the most common resolution and lists the photos it skipped, and `prnu match` and `prnu locate` refuse a photo whose resolution differs from the fingerprint's.

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

- Full IPTC metadata
- C2PA signature validation

## License

MIT
