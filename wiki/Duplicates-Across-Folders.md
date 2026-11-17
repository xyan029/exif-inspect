# Duplicates Across Folders

```bash
exif-inspect dupes evidence/ downloads/       # folders are searched recursively
exif-inspect dupes --distance 4 evidence/     # stricter grouping
exif-inspect dupes --json evidence/ > groups.json
```

Case work piles up copies of the same picture. `dupes` groups them and names the likely original in each group, so that the other tools are run on the right file.

## How files are grouped

- **Exact duplicates** share a SHA-256; they are grouped whatever their names and folders.
- **Near-duplicates** share a perceptual hash. The picture is shrunk to 32x32 grey, its 2-D DCT taken, and the 64 lowest frequencies apart from the mean are thresholded at their median into 64 bits (pHash). Re-saving at another quality, resizing, mild colour changes and small watermarks move only a few bits; different pictures differ in about half. The default allows 10 bits.
- A second hash from the gradient between neighbouring pixels of a 9x8 thumbnail (dHash) must agree within 16 bits as well. It keeps smooth pictures with similar layouts, two skies or two documents, from falling into one group on pHash alone.
- Both hashes are taken at the four rotations and the closest pair counts, so a turned copy is found and its rotation is printed.

Crops are not duplicates by these hashes, since the hash describes the whole frame: a crop that keeps most of the frame may still group, a tight crop will not. [[Comparing Two Versions]] handles a pair that may be crop and source.

## The likely original

Within a group, files are ranked by the most pixels, then the higher JPEG quality, then the most EXIF tags, then the earliest modification time. The first is marked with a star. The ranking can be fooled by an upscaled copy or by metadata copied across; the `compare` tool on the top two gives the reasons in full.

## Output

```
scanned 11 file(s): 3 group(s) of duplicates, 2 single(s), 0 unreadable

== group 1: 5 files, within 10 hash bits
   * coffee.jpg
       JPEG 600x400, q92, 2 EXIF tags, 78 KB; likely original
     coffee_wm.jpg
       JPEG 600x400, q90, 0 EXIF tags, 71 KB; distance 0/0
     coffee_rot.jpg
       JPEG 400x600, q85, 0 EXIF tags, 55 KB; distance 0/0, turned 270
     sub/coffee_small.jpg
       JPEG 600x400, q75, 0 EXIF tags, 40 KB; distance 0/0
     sub/coffee_thumb.png
       PNG 300x200, -, 0 EXIF tags, 108 KB; distance 0/0

== group 2: 2 files, identical bytes
   * astronaut.jpg
       JPEG 512x512, q92, 2 EXIF tags, 73 KB; likely original
     sub/IMG_0001.jpg
       JPEG 512x512, q92, 2 EXIF tags, 73 KB; same bytes
```

`distance 0/0` is the pHash and dHash distance to the group's original. `--json` writes the same groups as JSON for a spreadsheet or a case file. Files that cannot be opened are listed at the end rather than stopping the scan.
