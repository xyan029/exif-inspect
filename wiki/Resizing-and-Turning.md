# Resizing and Turning

Whether the picture, or one region of it, was enlarged, reduced or turned. An object pasted into a picture is usually scaled to fit first, and that scaling leaves a pattern the rest of the picture lacks.

```bash
exif-inspect resample photo.png       # photo_resample.png when a trace is found
```

## What it looks for

Resizing or turning computes every new pixel from a few old ones. How much a new pixel owes to its neighbours depends on where it falls between the old ones, and that position repeats: after enlarging by 1.25, every fifth pixel sits exactly on an old one. The picture then carries a periodic pattern in how well each pixel is predicted by its neighbours (Popescu and Farid, 2005), which a fixed predictor shows as sharp peaks in the spectrum of the prediction error (Kirchner, 2008).

- **Peaks.** The error of predicting each pixel from its eight neighbours is averaged as a spectrum over tiles. A frequency counts as a peak when it stands 2 times above its surroundings and 8 spreads of the spectrum's scatter, at least 0.07 cycles per pixel from zero. Where the picture's own spectrum rises too (cloth, brick, print), the peak in the error must be 4 times higher still.
- **Reading.** A peak at `f` on an axis comes from enlarging by `1/(1-f)` or `1/f`, from reducing to `1/(1+f)`, or from a small turn by `asin(f)`. All of them are printed; the method cannot choose among them. A peak off the axes that fits a pure turn is read as its angle.
- **JPEG blocks.** A picture resized after a JPEG save keeps that save's 8-pixel blocks, stretched by the factor. They show as a comb of peaks at multiples of one frequency, and the comb gives the factor directly: blocks 10.0 pixels wide mean 1.25x. Most reductions are found this way.
- **By region.** The peaks are measured again in every 64-pixel tile. Half of the tiles carrying the trace makes it the picture's; six connected tiles make a region, red in the output. A faint trace everywhere is told from a strong one in one place by a sign test on the other tiles. When the whole picture shows nothing, tiles vote for their own strongest frequency and a connected group voting alike is tested as a region of its own.

## Reading the output

```
   ! resampling trace around (192, 256, 480, 512) only (20% of checkable tiles carry it clearly): that area was resized or turned on its own, as an object scaled to fit before pasting is
     peak at 0.334 cycles per pixel down, 14.3 times its surroundings: enlarged 1.50x (or 3.00x), or reduced to 0.75x
```

| Line | Meaning |
|---|---|
| `resampling trace across the picture` | The whole picture was resized or turned. On its own this is ordinary: most pictures on the web were resized. |
| `faint resampling trace across the picture` | The same, weak in each tile and consistent over the picture. Camera zoom, lens correction and frame alignment do this too. |
| `resampling trace around (...) only` | One region carries it and the rest does not. This is the finding to follow up. |
| `the peaks form a comb` | The picture was a JPEG before it was resized, and the factor printed here is the one to trust. |
| `jpeg grid : the 8-pixel JPEG grid shows` | The file was a JPEG at its current size. Peaks at multiples of 1/8 are skipped. |
| `no resampling trace` | Nothing found, which after a JPEG save says little. |

## What it cannot see

- **A JPEG save after the resizing.** In the small sample pictures about two thirds of whole-picture traces survive quality 95 and about half survive 90, often without a readable factor; a pasted region is lost at 95.
- **Exactly 2x.** Both new pixels of each pair sit a quarter step from an old one and are interpolated alike, so nothing repeats.
- **Factors on the JPEG grid.** 1.14, 1.33, 1.6, 2.67, 4 and 8 put their trace on a multiple of 1/8, which is never counted.
- **Nearly nothing.** Factors within 7% of 1 and turns under 4 degrees fall below the lowest frequency read.
- **Well-filtered reductions** of pictures that were never JPEGs.
- **Smooth drawings**, which have almost no prediction error to analyse.

## How well it works

On 13 scikit-image sample pictures with no trace of their own, none flagged in 65 untouched files (PNG and JPEG 95 to 70).

| Whole picture | PNG | JPEG 95 | JPEG 90 |
|---|---|---|---|
| enlarged 1.25x | 12 of 13 | 9 | 6 |
| enlarged 1.5x | 12 | 9 | 6 |
| enlarged 2x | 4 | 1 | 2 |
| reduced to 0.75x | 5 | 0 | 0 |
| turned 10 degrees | 6 of 12 | 1 | 1 |
| turned 45 degrees | 10 | 8 | 3 |

| Pasted patch, PNG | 128x96 | 224x192 |
|---|---|---|
| enlarged 1.25x, located | 2 of 12 | 5 of 9 |
| enlarged 1.5x, located | 4 | 8 |
| enlarged 3x, located | 3 | 5 |
| turned 20 degrees, located | 1 | 1 |

Saved as JPEG at quality 95 after the paste, no patch is located. No forgery was flagged anywhere other than at its patch.

On 65 real camera JPEGs, 63 show no trace. The other two are the frames of one iPhone 6 HDR shot with a faint trace at 0.493 (1.97x or 2.03x), a soft picture of the kind digital zoom makes. Those 63 resized by 1.25 and saved as PNG: 61 found, 42 with the comb giving 1.25; reduced to 0.8: 31 found, 28 by the comb; resized by 1.5 and saved again at quality 90: 52 found, with the true factor among the readings in 14. After a lossy save, trust the verdict more than the factor.
