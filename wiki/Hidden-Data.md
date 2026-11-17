# Hidden Data

```bash
exif-inspect stego photo.png              # trailer and LSB steganalysis, writes photo_lsb.png
exif-inspect stego --extract photo.jpg    # also saves the bytes after the picture
```

Two places to hide things in an image file.

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

## What it does not see

Steganography that keeps the statistics (matrix embedding, adaptive schemes such as HUGO or S-UNIWARD) and steganography in JPEG coefficients (JSteg, F5, OutGuess, Steghide) are out of reach of these tests. RS sees plain LSB replacement, which is what most "hide a message in a picture" tools do, and the trailer check sees anything appended, which is what most people who do not use a tool do.
