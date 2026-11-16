# Noise and Demosaicing

Two maps of whether every region went through the same sensor and pipeline. Noise asks how much random variation each block carries; demosaicing asks whether the periodic trace of the colour filter array is still there.

## Noise level

```bash
exif-inspect noise photo.jpg          # photo_noise.png when a region stands out
```

A sensor adds noise to every pixel it records, with one level per exposure, rising gently with brightness. A region pasted in from another photograph brings its own level; a region smoothed, painted or filled by software has almost none (Mahdian and Saic, 2009).

- **Measuring.** The noise of a 32-pixel block is the smallest eigenvalue of the covariance of its 5x5 patches that lie on flat pixels: content fills the leading components, noise alone reaches every component (Pyatykh et al., 2013; Liu et al., 2013). Flat pixels and texture are measured on a copy blurred three times, where noise is almost gone, so that a noisy region is not mistaken for texture. Fewer than 150 flat patches and the block is not judged.
- **Comparing.** Texture leaks into any local estimate and noise grows with brightness, so each block is compared with the median of blocks of similar texture (6 bins) and brightness (3 bins). A block is flagged when it differs from that by a factor of 1.8, by 1.5 grey levels and by three times the picture's own scatter; six connected blocks make a region, red when noisier and blue when smoother.

| Simulated splices, noise 4 added to the picture | PNG | q95 | q90 | q80 |
|---|---|---|---|---|
| untouched, regions flagged (10 per quality) | 0 | 0 | 0 | 0 |
| patch with noise 12 located | 7/10 | 6 | 7 | 6 |
| patch with noise 8 located | 3/10 | 4 | 5 | 1 |
| smoothed patch located | 3/10 | 4 | 0 | 0 |

A noisier patch survives compression because the extra noise is larger than what JPEG removes. A smoothed patch is found only where the picture around it is quiet: in texture the estimate's floor of 2 to 4 grey levels hides the difference, and from quality 90 down the picture's own noise is mostly gone too. On 63 untouched camera JPEGs, 7 had a region flagged, skies and leaves that in-camera noise reduction had smoothed more than their peers and a dark foliage corner of an HDR phone picture, so a flagged region is a lead to check with the other tools.

## Demosaicing traces

```bash
exif-inspect cfa photo.png            # photo_cfa.png when a region lacks the trace
```

A sensor records one colour per pixel through a Bayer filter, half of them green on a checkerboard; demosaicing fills in the other half of the green channel from its neighbours. The filled-in pixels are predicted far better by their neighbours than the measured ones, so the two lattices of the checkerboard differ sharply in prediction residual (Ferrara et al., 2012). The tool takes the log ratio `L` of the residual on the two lattices in 32-pixel blocks, clipped pixels left out.

- **Whole picture.** The median `L` gives the trace: strong at 0.5 or more, weak when small but of the same sign in 75% of the blocks, none otherwise. Its sign names the interpolated lattice.
- **By region.** JPEG and sensor noise pull `L` toward zero in quiet blocks, so the expected `L` is learned from the picture as a monotone function of residual power; blocks below 0.15 and more than three spreads under their prediction are flagged, four connected ones make a region, red in the output. A region pasted from another source, resized, generated, or shifted by an odd number of pixels against the grid lacks the trace or carries it the other way round.

On simulated captures (pictures halved, sampled through an RGGB mosaic, demosaiced bilinearly): no region flagged in 45 untouched files; a pasted patch located in 22 of 27 as PNG, 20 of 27 at quality 95, 11 of 27 at quality 90.

**Real camera files are another matter.** Of 89 JPEGs in the exif-samples collection, 85 show no trace at all, including recent phones at quality 95: noise reduction, sharpening, multi-frame processing and JPEG itself wipe the lattice difference out. So a missing trace says nothing on its own, and the tool is for pictures that kept theirs: raw conversions exported as PNG, TIFF or high-quality JPEG, scans, lab images. Where the trace is there, a region without it is hard to explain innocently.
