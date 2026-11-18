# Limits and False Positives

What each method needs, what destroys its signal, and what makes it say something when nothing happened. A negative result is only informative when the row says the method could have worked on that file.

## What breaks each method

| Method | Needs | Destroyed or weakened by | Typical false positive |
|---|---|---|---|
| Metadata findings | The original container | Screenshots, messaging apps, social platforms, "remove metadata" tools | A tag edited by hand to say the opposite |
| EXIF thumbnail | An editor that left the thumbnail alone | Apps that rewrite or drop it; HEIC keeps it elsewhere | A camera that pads the thumbnail with black bars (handled) |
| HDR gain map | iPhone 12 or later, Ultra HDR phones | Any editor that drops it | HDR turned off at capture |
| ELA | One JPEG history for the whole picture | Many re-saves, very high quality, PNG conversion, screenshots, platform recompression | Hard-edged, saturated objects (a traffic light lit up as much as a pasted building) |
| JPEG ghosts | An earlier quality lower than the current one, grid-aligned (or `--grid`) | Re-saves through non-libjpeg tables, resizing, cropping off the grid, strong compression | A quality that is an echo of a lower dip (80 after 60; handled) |
| Double quantisation | A JPEG, baseline or progressive, saved twice on the same grid, the second save finer | The same table twice, a coarser second save, a crop of one pixel or a resize of a few percent between the saves, noise or a filter between them, one more save at the first quality after the paste, a progressive file whose last passes are missing | A picture blurred or sharpened as a whole between the saves, where a region can follow the strong edges (19 of 310 re-saved camera files after such an edit; none in 366 files saved without one) |
| Copy-move (blocks) | Clone at the same size and angle, not flat | Scaling, rotation, heavy recompression, clones under 16 pixels | Repeating textures: railings, tiles, windows (filtered by shift voting) |
| Copy-move (keypoints) | Enough keypoints in the clone | Smooth content, clones under about 40 pixels in small pictures | Text, logos and printed patterns, where the same shapes really repeat |
| Demosaicing traces | A picture that kept its traces: raw conversions, PNG or TIFF, JPEG at 95 or better | Camera and phone pipelines (noise reduction, sharpening, multi-frame), JPEG below about 90, any resizing | A region next to blown-out highlights (one in 89 real files) |
| Noise level | Flat patches in the region, noise larger than what JPEG removed | JPEG below about 90 for faint differences; texture everywhere | In-camera noise reduction, which smooths skies more than foliage; dark corners of HDR phone pictures |
| Resizing and turning | A lossless file, or JPEG at 95 or better; detail in the region | A JPEG save after the resizing, exactly 2x, factors on the JPEG grid (1.33, 1.6, 4), factors within 7% of 1, a median filter or jittered sampling by a forger who knows the method | Camera zoom, lens correction and frame alignment, which resample too (one HDR phone shot in 65 real files); a regular pattern along one axis. Oblique patterns (cloth, screens, print rasters) are set aside with a note |
| Lighting | Outlines of convex, matte objects under one distant light | Several lights, a nearby light, shiny surfaces, cast shadows, a change of material along the outline | Rims, folds, lips and material edges picked as outlines (the automatic mode says "no consensus" rather than flagging) |
| PRNU | 20 or more flat shots from the same camera, lens, resolution and mode | Resizing (even to 90%), strong compression, multi-frame modes, a different lens of the same phone | A busy or dark region, where no pattern was ever visible (marked "cannot judge") |
| XMP | A packet the last program kept | Everything that strips metadata | Anything, since it is plain text that anyone can edit |
| Trailing data | The original file | Any re-encoding | None worth the name; an MPF second image is reported as expected |
| LSB steganalysis (RS) | A lossless picture with plain LSB replacement | JPEG (no samples), adaptive or matrix embedding, resaving the picture | Clean pictures stay under 3% |

## How much compression each pixel method survives

Measured on simulated forgeries in scikit-image sample pictures; details on each method's page.

| Method | PNG | JPEG 95 | JPEG 90 | JPEG 80 |
|---|---|---|---|---|
| Ghost of a pasted patch (on the grid) | 22 of 27 | 20 of 27 | 11 of 27 | not tested |
| Demosaicing trace missing in a patch | 22 of 27 | 20 of 27 | 11 of 27 | 0 |
| Double quantisation: 128x96 patch pasted between two saves, the second finer | not applicable | 11 of 11 (after 85) | 11 of 11 (after 70) | 11 of 11 (after 60) |
| Resampling: 224x192 patch enlarged 1.5x | 8 of 9 | 0 | not tested | not tested |
| Resampling: whole picture enlarged 1.5x | 12 of 13 | 9 of 13 | 6 of 13 | not tested |
| Noise: patch with three times the noise | 7 of 10 | 6 of 10 | 7 of 10 | 6 of 10 |
| Noise: smoothed patch | 3 of 10 | 4 of 10 | 0 | 0 |
| PRNU: same camera at PCE 60 or more (Dresden set) | 14 of 20 | - | re-saved at 76: 7 of 20 | re-saved at 50: 3 of 20 |

## Reading a negative

Before writing "no sign of editing", check:

1. Does the file still carry metadata? If not, every metadata check was blind.
2. What are the JPEG tables? Standard tables at 75 or 76 after a known camera mean a platform re-encoded it; ELA, ghost, CFA and faint noise differences were weakened or erased.
3. Was it resized? `dimension_mismatch`, `gain_map_misfit` or a size that is not the camera's say yes; PRNU and CFA are then gone. `resample` can confirm it on a lossless file.
4. Is the questioned region smooth? Copy-move, noise and lighting cannot judge flat areas, and CFA and PRNU mark them "cannot judge".

## Files written against the tool

The checks above assume the tool reads the file the way a viewer does. A file can be written so that it does otherwise, which defeats every check at once. Before the parser audit, one legal fill byte in front of a marker made `dq` and `stego` stop with an error and made the trailer check report nothing while a ZIP sat behind the picture. Leaving out the Huffman tables, spreading the components over several scans, or giving all components one id had the same effect on `dq`, and in a small file the last of these returned wrong coefficients without an error.

The reader now follows libjpeg's own rules for all of these, and for damaged data as well, and prints what it passed over:

| `structure :` line | Meaning |
|---|---|
| `N fill bytes in front of a marker` | Legal padding that encoders do not write. |
| `N stray bytes between segments` | Bytes that belong to no segment. libjpeg skips them with a warning. |
| `a Huffman table the scan needs is not in the file` | The standard table was assumed, as libjpeg does. Usual in frames cut from MJPEG video, unusual in a photograph. |
| `the components come in N scans` | Baseline, with one scan per component or group. Legal and rare. |
| `components share an id` | The frame header names two components alike. |
| `component N has more than one scan` | A later scan was laid over an earlier one. No encoder writes this. |
| `the scan data runs out before the picture is complete` | Truncated or damaged; the missing blocks were left empty, as a viewer shows them. |
| `N Huffman codes that no table holds` | Damaged data, read as zero the way libjpeg reads it. |
| `the progressive passes stop early: ...` | A progressive file that ends before its last pass, or whose last pass has a header and no data. It opens everywhere; some coefficients never arrived or lack their lowest bits. `dq` and `stego` skip it. |
| `after N scans the file breaks the format (...)` | The first scans are sound and something behind them is not. libjpeg refuses the whole file; a viewer that draws pass by pass shows what the first scans brought, and that is what was read. |
| `no scan carries component N` | The frame header names a component that never arrives. libjpeg shows it as flat grey. |

None of these lines appears for a file straight from a camera or an editor, so each one is worth a second look at who wrote the file.

Progressive JPEGs are read like baseline ones: on 88 real files rewritten as progressive, `dq` and `stego` print the same result for both forms. Still open: arithmetic-coded JPEGs are skipped with the reason stated, and so is a progressive file whose last pass was dropped, which costs the picture one bit per coefficient. No encoder writes either kind for a photograph, so such a skip on a file that should be an ordinary camera JPEG is itself a finding.

## Text, names and neighbours written against the tool

A file's author also chooses its metadata values, its name and, in a folder handed over as evidence, what lies next to it. What the tool does with each:

| The file carries | The tool |
|---|---|
| line breaks, escape sequences or bidirectional overrides in a tag, an XMP field or its name | prints them as `\n`, `\x1b`, `\u202e`; a value can no longer add lines that look like findings or erase lines above it |
| a number that is not finite, a date past year 9999, a string of hundreds of digits where a number belongs | leaves the check that needed it without a result; `--json` prints `null` |
| more than one XMP packet | shows the first, flags `xmp_multiple`, and flags an editor named in any of them |
| a PNG ancillary chunk with a wrong checksum or length, or text that expands past 1 MB | opens the picture as libpng viewers do and flags `png_faulty_chunks` |
| a GIF with appended data ending in `;`, a WebP whose header size covers appended bytes | reports the trailer |

And for the folder: a named pipe, a device or a directory under a picture's name is refused at once, a dangling link is listed as unreadable, and an output name that already exists as a symbolic link is replaced by a new file, so a planted link cannot point the tool's output at another file.

Still open: data hidden inside a container (a private PNG chunk in front of IEND, a JPEG comment segment) is not reported, pictures past Pillow's limit of about 179 megapixels are refused, and HEIC files were not part of the audit.
