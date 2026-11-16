# Copy-Move

```bash
exif-inspect copymove photo.jpg               # photo_copymove.png when clones are found
exif-inspect copymove --min-pairs 100 photo.jpg
exif-inspect copymove --keypoints photo.jpg   # needs the keypoints extra (OpenCV)
```

A copy-move forgery pastes part of an image elsewhere in the same image: a person duplicated, an object covered with nearby grass. Two detectors, for two kinds of clone.

## Block matching

Slides a 16-pixel block over a downscaled copy of the image, describes each block by a 4x4 grid of mean intensities, sorts the descriptors so that similar blocks sit next to each other, and votes on the shift between matching blocks that lie far apart. A shift backed by at least 200 block pairs (`--min-pairs`) is reported, the source outlined in orange and the copy in cyan.

Flat areas are skipped. Repetitive textures (railings, tiles, windows) match at many different shifts and are filtered out for that reason. Small clones, clones rotated or rescaled before pasting, and heavy recompression escape it.

## Keypoints

Block matching compares pixels at one size and one angle. `--keypoints` matches SIFT keypoints instead, whose descriptors stay the same under scaling and rotation:

1. keypoints are found on a grey copy enlarged to twice its size, kept between 1000 and 2400 pixels on the longest side (`--size`), so that a small shrunken clone has enough of them;
2. every keypoint is matched against all the others with approximate nearest-neighbour search, keeping a match only when it is clearly better than the next best;
3. a similarity transform (shift, scale, rotation) is fitted to groups of matches lying close together and kept when backed by at least 8 matches;
4. transforms that look like a repeating pattern are dropped: keypoints with many near-identical twins, keypoints shared by three or more transforms, overlapping regions.

The report gives the scale and rotation from the larger region (A, orange) to the smaller (B, cyan), with the matched keypoints joined. Which region is the original cannot be told from the geometry.

**Numbers.** On 40 forgeries made from scikit-image sample photos (a third of each photo pasted back at 1/3, 1/2 and 1.4 times the size, turned 30 degrees, or turned 90 degrees at 0.7 times), block matching found none and keypoint matching found 31; the misses were on the smooth retina photo and the smallest clones in the smallest pictures. Of 18 untouched photos, keypoint mode flagged two: a scanned page of text, where the same letters really repeat, and a grass texture.

**A case.** A chalkboard sign copied, shrunk to about a third and pasted elsewhere in a street photo: block matching found nothing even at `--min-pairs 50`, ELA showed the paste as a bright rectangle, and keypoint mode reported the copy, about 40 by 60 pixels, at scale 0.36 with 18 matching keypoints.
