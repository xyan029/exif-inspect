# Findings Reference

Every code the main report (`exif-inspect photo.jpg`) can print, grouped by what it reads. Flagged findings (`!`) are the ones to follow up; the rest are information. Codes are stable and appear unchanged in `--json` output.

## Container and format

| Code | Triggered when | Read it as |
|---|---|---|
| `unreadable` | Pillow cannot open the file | Not an image, a truncated download, or a HEIC without the `heic` extra |
| `extension_mismatch` | The name says `.jpg` but the content is PNG, or the like | Renamed, or saved by a tool that kept the old name; messaging apps do this to screenshots |
| `no_exif` | No EXIF block at all | The normal state after screenshots, messaging apps and social platforms; also deliberate stripping |
| `no_camera` | EXIF exists without Make or Model | Software wrote the EXIF, or the camera fields were removed |
| `jpeg_tables` | Always, for a JPEG | Standard libjpeg tables give the exact quality (Pillow, GIMP, web services); a custom table carries a fingerprint that names the encoder when it is known (Apple ImageIO at 70 to 95). Quality 76 standard tables are what WeChat writes without "Original" |
| `tables_contradict_exif` | EXIF names a camera but the tables belong to other software | The file was re-saved by software after the camera; the camera's own encoder uses its own tables |
| `makernote_missing` | EXIF names a camera but has no MakerNote | Cameras and phones always write one; it is dropped by most editors and never added by hand |
| `dimension_mismatch` | EXIF width and height differ from the real size | Cropped or resized after capture; rotation is accounted for |

## Times and places

| Code | Triggered when | Read it as |
|---|---|---|
| `time_mismatch` | `DateTime` differs from `DateTimeOriginal` | Saved again after capture; the gap says when |
| `gps_present` | Coordinates embedded | A privacy note, not a forensic one |
| `gps_timezone_mismatch` | Capture time zone more than 3.5 hours from what the longitude implies | Coordinates or time were edited, or the camera clock was set for another zone |
| `gps_time_mismatch` | GPS fix time (UTC) more than an hour from the capture time | Same as above; the GPS clock comes from satellites and is rarely wrong |
| `gps_incomplete` | An iPhone photo whose GPS block lacks the fix time and positioning error | A GPS block typed in or copied from another file; iPhones always write those fields |

A GPS edit within the same time zone passes all three checks. See [[Case Studies]].

## Software

| Code | Triggered when | Read it as |
|---|---|---|
| `editor_software` | `Software` names Photoshop, Lightroom, Fireworks, GIMP, Affinity, Pixelmator, Snapseed, PicsArt, Canva, Facetune, Meitu, PhotoScape, Paint.NET, darktable, RawTherapee or Capture One | Edited; the tag does not say what changed |
| `software` | Any other `Software` value | The last program that wrote the EXIF, often the camera firmware |

## Provenance

| Code | Triggered when | Read it as |
|---|---|---|
| `c2pa_manifest` | A C2PA manifest is embedded (PNG `caBX`, JPEG APP11, WebP `C2PA`) | Signed provenance from a camera, editor or generator; the claim generator name is shown but the signature is not verified here |
| `ai_generated_marker` | IPTC digital source type `trainedAlgorithmicMedia` | The producer labelled it as generated |
| `ai_composite_marker` | IPTC digital source type `compositeWithTrainedAlgorithmicMedia` | AI combined with other content: Photos' Clean Up, Google's Magic Eraser |

These labels disappear on a screenshot and on most platforms, so their absence means nothing.

## Thumbnail and gain map

| Code | Triggered when | Read it as |
|---|---|---|
| `thumbnail_crop` | The EXIF thumbnail shows a wider scene, the main image covering less than 90% of it | Cropped after capture, and the thumbnail was not regenerated |
| `thumbnail_edited` | Blocks of the main image differ far more from the thumbnail than the rest | Painted, cloned or pasted over after capture |
| `thumbnail_unrelated` | The thumbnail is another picture | The EXIF block was copied from another file |
| `thumbnail_rotated` | The thumbnail is turned relative to the image | Pixels rotated by software |
| `gain_map_misfit` | The HDR gain map's size no longer fits the picture | Resized after capture with the gain map kept (Google Photos does this) |
| `gain_map_missing` | EXIF names an iPhone 12 or later, no gain map | Dropped by editors such as Snapseed and Meitu; also HDR off or conversion by other software |

## XMP

| Code | Triggered when | Read it as |
|---|---|---|
| `xmp_editor` | Creator tool or a history agent names an editor | As `editor_software`, from the other metadata block |
| `xmp_history` | `xmpMM:History` has events | Each save with software and time, oldest first; Photoshop and Lightroom write one per save |
| `xmp_derived` | `DerivedFrom` present, or document ID differs from the original document ID | Made from another document, whose ID is shown |
| `xmp_raw` | `crs:RawFileName` present | Developed from a raw file; the main sliders are listed |
| `xmp_retouch` | `crs:RetouchAreas` or `RetouchInfo` present | Healing or clone brush used in that many places; positions are in the packet (`xmp --raw`) |
| `xmp_local_adjust` | Masks, gradients or brush corrections present | Local tone or colour changes, not content changes |
| `xmp_crop` | `crs:HasCrop` true | Cropped in Camera Raw; the share of the frame kept is shown |
| `xmp_ai` | Software, credit or source type names Firefly, "generative", Magic Eraser, Galaxy AI and the like | A generative tool touched the file |
| `xmp_date_mismatch` | `xmp:CreateDate` differs from EXIF `DateTimeOriginal` | One of the two was rewritten |
