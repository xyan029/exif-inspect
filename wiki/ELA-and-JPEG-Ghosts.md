# ELA and JPEG Ghosts

Both methods ask whether every part of a JPEG went through the same compression history. ELA shows it as a picture to read by eye; ghosts turn it into numbers, naming the earlier quality.

## Error level analysis

```bash
exif-inspect ela photo.jpg                    # photo_ela.png
exif-inspect ela --side-by-side photo.jpg     # original and heatmap in one image
exif-inspect ela -q 95 --gray -o out.png photo.jpg
```

Each JPEG save discards some information, and repeated saves at the same quality discard less and less. ELA re-saves the image once at a fixed quality (90 by default) and maps how much every pixel changes. Areas that share the picture's history change by similar amounts; an area pasted in from another source often changes more and shows brighter. The 99.5th percentile of the error maps to full brightness unless `--scale` fixes the amplification.

**Reading the map.** Edges and fine texture always show higher error, smooth areas such as sky stay dark. Compare regions of similar texture and look for one that stands out from its neighbours. Bright, saturated objects with hard edges light up on their own: in a red-team test a building copied, shrunk to 0.64 and pasted above trees showed as a bright rectangle, and so did an untouched traffic light against grey sky. `copymove --keypoints` settled it by reporting only the building, with 181 matching keypoints and the scale of the copy. ELA points at places worth checking; a match of that kind is evidence that can be reproduced.

## JPEG ghosts

```bash
exif-inspect ghost photo.jpg            # earlier qualities, whole image and by region
exif-inspect ghost --grid photo.jpg     # also try the 63 other offsets of the 8x8 grid
exif-inspect ghost --curve photo.jpg    # print the re-save error at every quality
```

Re-saving a JPEG at the quality it was last saved at changes it very little, and the same holds for any quality it passed through earlier: the error between the picture and a re-save dips at every quality in its history (Farid, 2009). The tool re-saves the image at every quality from 40 to 100, measures the squared difference in 16-pixel blocks after removing the mean of every 8x8 cell, and reads the curves two ways:

- **Whole image.** A dip at least 4 below the current quality and at least 15% deep means the file was saved as a JPEG at about that quality before. A dip at a quality whose tables divide those of a lower dip (80 after 60, 75 after 50, 87 after 75) is dropped as an echo. A PNG has no current quality, so any dip reports a JPEG save in its past.
- **By region.** Each block's curve is scaled to 0 to 1 and compared with the median curve of the picture. Blocks that turn at some quality and sit 0.35 below the rest there carry a ghost the rest lacks; blocks that rise 0.35 above the rest show the rebound between an earlier quality and the current one. Six or more connected blocks are reported with the quality and a box, and the picture is written with the region tinted cyan.

```
== twice.jpg
   saved at quality 90
   re-save error by quality 40-100:
   ██▇▆▆▅▄▃▃▂▂▂▂▃▃▃▃▄▄▄▅▅▅▅▅▄▄▃▃▂▂▂▂▃▃▂▁▁▂▁▁▁▁▁▁▁▁▁▁▁▁
   ! whole image carries a ghost at quality 60 (dip 48%): it was saved as a JPEG at about that quality before
```

**Grid alignment.** A ghost appears only when the 8x8 grid of the re-save lines up with the grid of the earlier compression. A pasted patch lands on the grid in one case out of 64, so `--grid` repeats the search at the other 63 offsets. It costs about 12 times the plain run, and it finds less: the save on the host's grid blurs the earlier quantisation, so only strong off-grid ghosts stand out (3 of 10 in the test set). Ghosts also need the earlier quality to be lower than the current one; a patch from a better JPEG leaves none, which is ELA's case.

**Numbers.** On 20 scikit-image pictures: 80 untouched files gave no report apart from one real ghost (see [[Case Studies]], the coins picture); 25 of 28 files saved at a lower quality first reported it within 1; 93 of 120 pasted patches were located at the right place with the quality within 3, none in a wrong place.
