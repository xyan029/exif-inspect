# exif-inspect

A command-line tool for first-pass image forensics. It reads what a file says about itself (EXIF, XMP, C2PA, JPEG tables, thumbnails, gain maps) and what the pixels say (error levels, JPEG ghosts, copy-move clones, demosaicing traces, noise levels, lighting direction, sensor noise), and it points out what deserves a closer look.

Every line it prints is a lead for a human examiner, not a verdict. Metadata is easy to edit or strip, and each pixel method has conditions under which it sees nothing. The wiki says what each method can and cannot show, so that you know how much weight a result carries.

## Start here

- [[Getting Started]]: install, the first run, how to read the output.
- [[Examining a Photo]]: which tool to run first, and what to do with what it says.
- [[Findings Reference]]: every finding code the main report can print, what triggers it and what it means.
- [[Limits and False Positives]]: what breaks each method, in one table.

## Methods

| Page | Commands | Question it answers |
|---|---|---|
| [[Metadata and XMP]] | `exif-inspect`, `exif-inspect xmp` | What does the file say about its own history? |
| [[ELA and JPEG Ghosts]] | `ela`, `ghost` | Was a region saved through a different JPEG history than the rest? |
| [[Copy-Move]] | `copymove` | Was part of the picture cloned from elsewhere in the same picture? |
| [[Noise and Demosaicing]] | `noise`, `cfa` | Did every region go through the same sensor and pipeline? |
| [[Lighting]] | `light` | Were the objects lit by the same light? |
| [[Thumbnail and Gain Map]] | `thumbnail`, main report | Does the small preview the camera stored still match the picture? |
| [[Camera Identification]] | `prnu` | Did this camera take this photo, and is any part of it foreign? |
| [[Hidden Data]] | `stego`, main report | Is something hidden after the picture, in its lowest bits or in its JPEG coefficients? |
| [[Comparing Two Versions]] | `compare` | How do two files of the same scene relate, and which came first? |
| [[Duplicates Across Folders]] | `dupes` | Which files in these folders are copies of each other, and which is the original? |
| [[HTML Report]] | `report` | Everything above on one page to send on. |

## Cases

[[Case Studies]] collects the files that shaped the checks: a WeChat re-encode, a GPS edit that passed every check, an AI object removal caught only by its metadata, a PNG in a public dataset that turned out to have been a JPEG.
