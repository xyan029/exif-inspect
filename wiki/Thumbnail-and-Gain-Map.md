# Thumbnail and Gain Map

Two small images that cameras store next to the picture and that editors often forget to update. When they are present, they are the most direct evidence of what the frame looked like at capture.

## EXIF thumbnail

```bash
exif-inspect thumbnail photo.jpg             # photo_thumbnail.png
exif-inspect thumbnail --extract photo.jpg   # also saves the thumbnail as photo_thumb.jpg
```

Cameras and phones store a small preview, usually 160 by 120 pixels, in the second image directory of EXIF (IFD1). Editors that crop or retouch do not always regenerate it, so the preview can still show what was cut away or painted over. The tool:

1. reads the thumbnail JPEG from the raw EXIF block and drops the black bars some cameras add to fill a 4:3 frame;
2. searches the thumbnail for the main image at every position, at sizes down to 45% of its width and at four rotations, by normalised cross-correlation. A close match (0.85 or better) covering less than 90% of the thumbnail is reported as a crop (`thumbnail_crop`);
3. when the main image fills the thumbnail, matches brightness and contrast, compares the two in 8-pixel blocks and reports blocks that differ far more than the rest (`thumbnail_edited`).

The picture written shows the thumbnail enlarged with the crop outlined in cyan, the main image, and a difference map with changed areas outlined in orange.

The thresholds were set on the 55 photos with thumbnails in the public exif-samples collection, from about 40 cameras and phones. On the 35 large enough to edit, none was flagged untouched, apart from one that really is a crop: its thumbnail shows the car park around the car the main image frames. Pasted clones and painted areas covering about a tenth of the frame were found in 33 of 35, crops to 70% and 50% in 33 and 32. Blurring is harder: a blur fine enough to hide a face often leaves little trace at thumbnail size, and it was found in 8 of 35.

A thumbnail only helps when the editor left it alone. Many apps rewrite or delete it, phones may store a larger preview elsewhere (HEIC keeps it outside EXIF), and anyone who knows about it can replace it (`thumbnail_unrelated` catches a thumbnail from another picture).

## HDR gain map

iPhones from the iPhone 12 on, and Ultra HDR cameras such as the Pixel, save a half-size grayscale gain map with each photo, which HDR displays use to brighten the highlights. A JPEG keeps it as a second image listed in its MPF segment; a HEIC keeps it as an auxiliary image. The main report prints its size and maker and checks that it still fits the picture.

An editor has three choices: rewrite the gain map along with the picture, drop it, or leave the old one in place. One iPhone 16 Pro photo of a pill bottle was edited in four apps, each removing the bottle:

| Editor | Gain map | Image size | Other traces |
|---|---|---|---|
| none (original) | 2142x2856, half size | 4284x5712 | |
| Apple Photos Clean Up | rewritten, bottle gone | 4284x5712 | IPTC `compositeWithTrainedAlgorithmicMedia` |
| Google Photos Magic Eraser | rewritten, left at 2142x2856 | 3144x4192 | IPTC source type, "Edited with Google AI" |
| Snapseed 4.2 | dropped | 4284x5712 | `Software` tag, MakerNote dropped |
| Meitu 12.22 | dropped | 4284x5712 | `Software` tag, MakerNote dropped |

None left a stale gain map. Google Photos shrinks the picture and keeps a gain map sized for the original, which `gain_map_misfit` catches without relying on the label. A file whose EXIF names a recent iPhone but has no gain map gets an informational `gain_map_missing`; photos taken with HDR off, or converted by other software, lack one too.
