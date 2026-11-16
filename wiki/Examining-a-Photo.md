# Examining a Photo

A working order for one questioned file. Each step says what to run, what to look for, and when to stop.

## 1. Hash it and read the container

```bash
exif-inspect photo.jpg
```

Note the SHA-256 for your records. Then read the findings top to bottom:

- **Where has it been?** `no_exif` plus standard libjpeg tables at quality 75 or 76 says a platform or messaging app re-encoded it ([[Case Studies]]). Most of what follows will be weak on such a file; what remains is the pixel content and the EXIF thumbnail, if any.
- **Which program wrote it last?** `editor_software`, `xmp_editor`, `jpeg_tables` with a known fingerprint, `tables_contradict_exif`. A camera JPEG with Apple ImageIO or libjpeg tables was saved by software after the camera.
- **Is the story consistent?** `time_mismatch`, `xmp_date_mismatch`, `gps_timezone_mismatch`, `gps_time_mismatch`, `dimension_mismatch`, `makernote_missing`, `gain_map_missing`.
- **Does it say it was generated or retouched?** `c2pa_manifest`, `ai_generated_marker`, `ai_composite_marker`, `xmp_ai`, `xmp_retouch`, `xmp_history`.

If the metadata already answers your question, you are done. If it is clean, remember that a careful edit leaves clean metadata; go on.

## 2. Look at what the camera stored next to the picture

```bash
exif-inspect thumbnail photo.jpg
```

The EXIF thumbnail is a 160-pixel copy of the frame as it was at capture. `thumbnail_crop`, `thumbnail_edited` and `thumbnail_unrelated` compare it with the main image. Many editors leave it alone, so it is the most direct evidence there is when it is present. The HDR gain map plays the same role on recent iPhones and Pixels (`gain_map_misfit`).

## 3. Run everything once

```bash
exif-inspect report photo.jpg
```

Open the HTML page. The maps are easier to compare side by side than one at a time, and each section carries its own caveats. Then go to whichever map shows something.

## 4. Follow the map that lit up

| What you saw | Run next | Why |
|---|---|---|
| A rectangle brighter than its surroundings in the ELA map | `ghost photo.jpg` and, if nothing, `ghost --grid photo.jpg` | A ghost names the quality the patch was saved at before, which ELA cannot |
| Two areas that look the same | `copymove photo.jpg`, then `copymove --keypoints photo.jpg` | Block matching gives a shift backed by hundreds of pairs; keypoints find scaled or rotated copies |
| A smooth area with no noise in the noise map | `cfa photo.jpg`, `xmp photo.jpg` | A smoothed or filled area lacks the demosaicing trace too; Lightroom records its healing areas in XMP |
| An object that looks pasted in | `light --box ... --box ... photo.jpg` | Compare its light direction with another object's, boxes chosen by you |
| A region that does not match the camera | `prnu match` and `prnu locate` with a fingerprint of the claimed camera | The only method that ties pixels to one physical camera |

## 5. Write down what you can and cannot say

For each positive, record the command, the output picture and the numbers printed (shift and pairs for copy-move, quality for a ghost, ratio for noise, degrees for lighting, PCE for PRNU). For each negative, record why the method could not have seen anything on this file, from [[Limits and False Positives]]. A report that says "ELA showed nothing, on a file WeChat re-encoded at quality 76" is worth more than one that says "ELA showed nothing".
