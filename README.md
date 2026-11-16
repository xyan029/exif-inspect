# exif-inspect

[![tests](https://github.com/xyan029/exif-inspect/actions/workflows/tests.yml/badge.svg)](https://github.com/xyan029/exif-inspect/actions/workflows/tests.yml)

A small command-line tool for first-pass image forensics. It prints the EXIF metadata of images, detects C2PA Content Credentials and AI-generation labels, points out details worth a closer look, and draws error level analysis (ELA) heatmaps for JPEGs, finds the earlier JPEG qualities an image or a pasted region passed through (JPEG ghosts), checks that objects agree on where the light came from, finds copy-move clones, compares the EXIF thumbnail with the main image, and matches photos to the camera that took them by sensor noise.

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

- Full XMP and IPTC metadata
- C2PA signature validation

## License

MIT
