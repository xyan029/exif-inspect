# ELA and JPEG Ghosts

Three methods ask whether every part of a JPEG went through the same compression history. ELA shows it as a picture to read by eye; ghosts turn it into numbers, naming the earlier quality; double quantisation reads the earlier table from the file's own coefficients and marks the blocks that never saw it.

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

## Double quantisation

```bash
exif-inspect dq photo.jpg               # photo_dq.png when a region was compressed once
```

A JPEG save divides every DCT coefficient by a step and rounds. A second save with other steps rounds the rounded values again, and the histogram of each coefficient shows periodic peaks and gaps that depend on the first step (Lukas and Fridrich, 2003). A block pasted or painted in after the first save was rounded once and lacks the pattern (Lin et al., 2009; Bianchi and Piva, 2012).

- **The first table.** The picture is decoded, cropped by four pixels and transformed again, which estimates the coefficients before any rounding. Rounding those twice, with a candidate first step and then the file's own, predicts a double save; rounding them once predicts a single one. The step that explains the file's histogram best is kept for each of the first 15 AC coefficients, and three coefficients with a clear gain make a file that was saved before.
- **Its quality.** With six or more coefficients the libjpeg quality whose table explains the histograms is named, as a range when the picture has too little texture to tell neighbouring steps apart. Another encoder's table is reported as such.
- **By block.** Each 8x8 block gets the log likelihood ratio of the two predictions, averaged with the blocks around it. Sixteen connected blocks that read as compressed once are a region, red in the output, as long as the blocks around them read plainly as compressed twice. Flat blocks, blocks with a clipped channel and blocks over the picture's edge are not judged.

```
   ! saved as a JPEG before, with another table: 15 of the first 15 coefficients show double quantisation
     libjpeg's table at quality 70 explains them
   ! region around (56, 88, 208, 208) was compressed only once (5.9% of checkable blocks read that way): pasted, painted or filled in after the first save
```

**When to use which.** `dq` answers the case where the picture has a JPEG past and the edit has none: a camera JPEG opened, changed and saved again. `ghost` answers the opposite one, a patch with a low-quality past in a picture without it. On 11 forgeries of the first kind `dq` found 10 or 11 and `ghost` 0 to 4, depending on the qualities; on 11 of the second kind `ghost` found 8 and `dq` none.

**What it needs.** A second save finer than the first. The same table twice, a coarser second save, and cropping or resizing between the saves leave no pattern. Baseline and progressive JPEGs are read alike. A progressive file whose last passes are missing, an arithmetic-coded one and other formats are skipped.

**How it is fooled.** Tried on the test forgeries, each step added between the paste and the last save:

| Step | File still read as saved twice | Patch still found |
|---|---|---|
| one pixel cropped, or a resize of 5% | never | never |
| eight pixels cropped | always | always |
| one more save at the first quality | always | never |
| patch cut from a JPEG of the first quality, pasted on its grid | always | never |
| noise of sigma 1 | nearly always | yes after a first save at 60 or 70, 2 of 11 after 85 |
| noise of sigma 3 | 8 or 10 of 11 after 70 or 60, never after 85 | never |
| blur of radius 0.8, or a 3x3 median | 1 to 3 of 11 | never |
| dithered coefficients (Stamm and Liu, 2011) | 0 to 4 of 11 | never |

A clean reading is therefore weak evidence against a forger who knows the method. The README has the full table.

**A region that follows the edges.** Blur or sharpening over the whole picture between the saves changes strong edges more than flat areas, and the blocks along them can read as compressed once while the rest still reads as twice. The tool fits how far such an edit smeared the pattern and reports a region only against a clear background, which removes most of these (59 of 252 test files before, 5 after; 47 of 310 camera files before, 19 after). When it sees the smear and no clear background it prints `no region reported` with the share of blocks that read as compressed once. A reported region that traces the high-contrast outlines of the picture deserves a look at whether the whole picture was filtered.

**Numbers.** On 12 scikit-image pictures: none of 60 files saved once read as saved before; all 72 saved twice with a finer second save were read as such with the first quality in the range named, and none had a region flagged; a 128x96 patch pasted between the saves was found in 10 or 11 of 11 for six pairs of qualities from 50 then 75 to 90 then 95, and in none for 80 then 85, where the two tables are too close. On 62 real camera JPEGs saved again at 98, 95 and 90, a pasted 96x96 patch was found in 56, 37 and 17, and no region was flagged when nothing was pasted.
