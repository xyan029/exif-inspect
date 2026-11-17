# Comparing Two Versions

```bash
exif-inspect compare original.jpg forwarded.jpg       # writes original_vs_forwarded.png
```

The common case: you hold two files that show the same scene, an original and a forwarded copy, a full frame and a crop, a photo and the version that appeared online, and you want to know how they relate.

## Placement

The smaller scene is searched for inside the larger one at every position, at scales down to a fifth of the frame and at the four rotations, by normalised cross-correlation (the same search that places the EXIF thumbnail, see [[Thumbnail and Gain Map]]). The best placement gives the crop box, in the container's pixels, and the scale factor between the two: 0.75 means the other picture has three quarters of the container's pixels per unit of scene, so it was downscaled; 2.0 means upscaled.

A placement covering under a quarter of the frame needs a correlation of 0.75 instead of 0.6 and a side of at least 40 pixels at analysis size. Small patches of noise correlate with anything, and the first version of the tool "found" a 2% crop of a cat in a picture of a coffee cup.

## Changes

The pair is aligned at full resolution, brightness and contrast matched, and compared in 16-pixel blocks. Blocks that differ by more than four spreads of the block differences and at least 10 grey levels are the edits between the versions: painted areas, removed objects, added text. They are reported as boxes in the container's coordinates and drawn in orange on the output picture, next to the other version and a difference map.

Resampling and recompression leave a uniform haze over the difference map; an edit is a region that stands out of it. A re-save at a much lower quality or a heavy filter raises the haze everywhere and can hide small edits.

## Which came first

The parent is read from the evidence in order, and the reason is printed:

1. the one whose frame contains the other (a crop cannot show more than its source);
2. else the one with more pixels;
3. else the one saved at higher JPEG quality;
4. else the one with more of its metadata left;
5. else the one modified earlier.

Each can be forged on its own: a crop can be upscaled past the original, metadata can be copied across. Two files that agree on several points are harder to fake than one. Two pictures that place with a correlation of 0.97 or better, at the same size and with no changed region, are reported as the same picture within compression noise; the order then rests on quality and metadata alone.

## Example

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

The child was cut from the parent, had a spoon painted over, was shrunk to three quarters and saved at quality 78 without metadata. All four steps are in the output.
