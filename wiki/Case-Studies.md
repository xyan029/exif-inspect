# Case Studies

Files met in practice, and what they taught the tool. Several checks exist because of one of these.

## A screenshot that became a `.jpg`

A screenshot forwarded through a messaging app arrived as a PNG named `.jpg`. Pillow reports the real format, and `extension_mismatch` says so. The same app had cropped it to 796 pixels wide while the EXIF still said 1206, the phone's full screen width: `dimension_mismatch`.

## What WeChat does to a photo

The same Apple quality 90 JPEG was sent from an iPhone to the desktop app twice. Without "Original" it arrived re-encoded with standard libjpeg tables at quality 76 and with EXIF stripped; with "Original" it passed through unchanged. Standard tables at quality 76 therefore get a WeChat hint in `jpeg_tables`. Any libjpeg encoder can choose the same quality, so it is a lead.

## Images saved from apps

Images saved to the iPhone photo library from Xiaohongshu, Reddit and Xianyu all carried the Apple ImageIO quality 75 tables: the fingerprint identifies the encoder that wrote the file, and several apps share it. Visible watermarks or the image content tell those apps apart.

## A ChatGPT image with no EXIF

An image downloaded straight from ChatGPT had no EXIF but held C2PA credentials in a PNG `caBX` chunk, with the generator `OpenAI Media Service API` and the source type `trainedAlgorithmicMedia`. Before the provenance scan the tool reported only "No EXIF metadata".

## A GPS edit that passed every check

In a red-team test the latitude and longitude of a photo were changed to another place in the same time zone. The time-zone check, the GPS clock check and the iPhone field check all passed, because nothing else in the file had changed. Settling such a case takes evidence outside the file: what the picture shows, neighbouring photos from the same device, upload records.

## A building pasted above the trees

A building was copied, shrunk to 0.64 and pasted above a row of trees. ELA showed the pasted building as a bright rectangle, and showed an untouched traffic light just as brightly, because its yellow housing is a hard-edged rectangle whose colours suffer most from JPEG's chroma compression. `copymove --keypoints` reported only the building, with 181 matching keypoints and the scale of the copy.

## A chalkboard sign shrunk to a third

A sign copied, shrunk to about a third and pasted elsewhere in a street photo. Block matching found nothing even at `--min-pairs 50`; ELA showed the paste as a bright rectangle; keypoint mode reported the copy, about 40 by 60 pixels, at scale 0.36 with 18 matching keypoints. This is why keypoint mode exists.

## Four apps remove a pill bottle

One iPhone 16 Pro photo edited in Apple Photos Clean Up, Google Photos Magic Eraser, Snapseed and Meitu. Each left a different trace: the IPTC composite label and a rewritten gain map, a shrunken picture with a gain map still sized for the original (`gain_map_misfit`), a `Software` tag with the MakerNote and gain map dropped. See [[Thumbnail and Gain Map]].

## An AI removal that PRNU could not see

Photos' Clean Up removed a plastic bag and a book edge from a 24-megapixel iPhone photo. `prnu locate` marked nothing. With the untouched HEIC as ground truth, both areas had carried only a sixth of the photo's usual sensor pattern before any edit, because the phone's multi-frame processing smears the pattern on bright glossy surfaces; removing them took away little that could be missed. The edit was given away only by `ai_composite_marker`. Metadata and pixel methods cover different failures, which is the reason to run both.

## A PNG that used to be a JPEG

`coins.png` from the scikit-image sample collection, used as an untouched control for the ghost evaluation, reported a ghost at quality 85. The PNG itself shows a dip at 85 with an error of 0.11 against 4.1 at quality 90: it had been a JPEG saved at quality 85 before it became a PNG. A control file is only a control if its own history is known.

## A texture that had been a JPEG at quality 90

`grass.png` from the same collection was meant as a host with no JPEG past for the double quantisation tests. Saved once at quality 98 it read as saved twice, on three of the highest coefficients only, with the steps of libjpeg's quality 90. The pixels of the PNG settle it without any model: its DCT coefficient at zigzag position 15 lies on average 0.05 of a step from a multiple of 8, where a picture with no JPEG past gives 0.25, and only on the picture's own block grid. The low coefficients show nothing because their steps of 2 and 3 are too close to the rounding of the pixels. `coins.png` reads as quality 85 with this method too, on all 15 coefficients, which agrees with its ghost.

## A photo re-saved by Windows Photo Gallery

A Canon IXUS 40 photo re-saved by Windows Photo Gallery near quality 96 was the one file in 89 real camera JPEGs that kept a strong demosaicing trace, and it also produced a five-block false region at the edge of a blown-out white band. A strong trace in a re-saved file says the re-save was gentle; the false region says what the CFA map's edge looks like.
