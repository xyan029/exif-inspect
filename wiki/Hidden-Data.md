# Hidden Data

```bash
exif-inspect stego photo.png              # trailer and LSB steganalysis, writes photo_lsb.png
exif-inspect stego photo.jpg              # trailer and the JSteg / F5 tests on the DCT coefficients
exif-inspect stego --extract photo.jpg    # also saves the bytes after the picture
```

Three places to hide things in an image file.

## After the picture

Every viewer stops reading a JPEG at its EOI marker, a PNG at its IEND chunk, a WebP at the end of its RIFF container and a GIF at its trailer byte. Whatever follows rides along unseen: an archive, a second image, a text file. The main report checks every file (`trailing_data`), says how many bytes follow and what they look like (ZIP and other archives, another image, PDF, executables, XML, plain text), and `stego --extract` saves them for a closer look. A second JPEG that an MPF segment announces, such as an iPhone's gain map, is reported as expected (`trailing_image`) rather than flagged.

A trailer is not subtle once you look for it, which is why it works: nobody looks. A ZIP appended to a JPEG opens as a picture in a viewer and as an archive in an unzip tool, and survives any copy that does not re-encode the file. Re-encoding by a platform or a messaging app removes it.

## In the lowest bits

Replacing the least significant bit of each sample changes the picture by at most one grey level, invisibly, but it changes the statistics of the sample values. For lossless pictures (PNG, BMP, TIFF, GIF; JPEG stores no samples) the subcommand gives three views.

**The LSB planes as a picture.** The lowest bit of each channel drawn black and white, side by side. A natural image's lowest bit still follows the content: smooth areas show contour-like bands, textures show structure. An embedded payload is pure noise, and a payload written from the top of the picture shows as a noise band above a structured rest.

**RS analysis** (Fridrich, Goljan and Du, 2001). Pixels are taken in groups of four; flipping the lowest bits of two of them makes some groups noisier (regular) and some smoother (singular), and in a clean picture the counts barely depend on the direction of the flip. A payload erodes that asymmetry in a known way, and a quadratic turns the four counts into an estimate of how many samples carry random bits. Reported per channel and averaged; from 5% up a payload is flagged.

| Payload (12 sample pictures) | RS estimate |
|---|---|
| none | 0.00 to 0.02 |
| 10% of the samples, at random | 0.08 to 0.12 |
| 50% of the samples, at random | 0.46 to 0.52 |
| every sample | 0.94 to 1.00 |

**The chi-square attack** (Westfeld and Pfitzmann, 1999) over consecutive blocks of 4096 samples in scan order. Embedding evens out the counts of each pair of values 2k and 2k+1, so a payload written from the top shows as a run of blocks where the test fires. On its own the test is not trusted for a verdict: it reads many clean pictures as largely embedded, a known weakness on noisy or previously compressed content. It only adds the position of a payload that RS has already found.

## In the JPEG coefficients

A JPEG stores no pixels. It stores the quantised DCT coefficients of each 8x8 block, and JPEG steganography writes into those. The tool reads them with its own baseline decoder, exactly as the file holds them (Pillow only hands out pixels), and runs two tests on the luma AC coefficients. Progressive and arithmetic-coded files are not read; the subcommand says so.

**JSteg-style LSB replacement.** JSteg, early OutGuess and most homemade tools flip the lowest bit of coefficients other than 0 and 1. A clean picture has far more 2s than 3s and far more -2s than -1s, because the coefficient histogram falls off steeply from zero. Writing random bits evens each pair out. Westfeld's chi-square test runs over blocks of 10 000 coefficients in scan order and reports two shares of the stream: how much reads as evened out, and how much of that sits in one run from the start, which is where a payload written from the top of the picture lands. Clean pictures read 0% throughout, so from 10% up the file is flagged, and the prefix tells you roughly how long the message is.

```
   coeffs    : 3072 luma blocks read, 51210 non-zero AC coefficients, 24180 usable for JSteg
   lsb pairs : 31% of the coefficient stream reads as evened out; the first 31% in one run
   calibrated: F5-style change ratio about 9%
   ! LSB replacement in the coefficients likely (JSteg-style): 31% of the coefficient stream has evened-out value pairs, from the start of the picture (31% of the stream).
```

**F5-style shrinkage.** F5, and matrix embedding generally, does not flip bits. It decrements the absolute value of a coefficient when the bit is wrong, so the histogram gains 0s and loses 1s and 2s, and the pair test sees nothing. Fridrich, Goljan and Hogea estimate the change rate by calibration: decompress the picture, crop four pixels off the left and top so that the block grid no longer lines up with the old one, recompress with the file's own quantisation tables, and read the low-frequency histograms a clean picture of this content would have. The difference, over the first nine AC modes, gives the share of coefficients changed. Pictures under 1000 blocks skip it; the estimate is noise there.

| Picture (8 sample pictures at quality 80, 9 camera JPEGs) | LSB share | F5 estimate |
|---|---|---|
| clean | 0% | 0 to 12%, one phone HDR picture 20% |
| JSteg, random bits in the first 30% of the usable coefficients | 23 to 44%, in one run from the start | |
| JSteg, every usable coefficient | 59 to 93% | |
| F5-like, 10% of the non-zero coefficients changed | 0% | 0 to 21% |
| F5-like, 30% changed | 0% | 18 to 43% |

The LSB test is the firm one. The F5 estimate is a lead: it is printed, noted from 15% and flagged from 30%, and a heavily processed phone picture can sit in the noted band with nothing in it. When it is the only raised number, look at the file's origin before concluding anything.

## What it does not see

Steganography designed against these statistics is out of reach: matrix embedding at low rates, nsF5, and the adaptive schemes (HUGO, S-UNIWARD in pixels, J-UNIWARD in JPEG coefficients) that change the coefficients a detector least expects. Steghide and OutGuess 0.2 preserve the first-order histogram and pass the pair test. RS sees plain LSB replacement, which is what most "hide a message in a picture" tools do; the pair test sees JSteg-style replacement in a JPEG; the trailer check sees anything appended, which is what most people who do not use a tool do.
