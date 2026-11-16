# Metadata and XMP

```bash
exif-inspect photo.jpg            # key tags, provenance, thumbnail and gain map state, findings
exif-inspect --all photo.jpg      # every EXIF tag
exif-inspect xmp photo.jpg        # the XMP packet: history, Camera Raw settings
exif-inspect xmp --raw photo.jpg  # the XML itself
```

## What is read

- **EXIF**: IFD0 (make, model, software, dates), the Exif sub-IFD (capture time, lens, exposure, recorded size), GPS. Coordinates are converted to decimal degrees.
- **JPEG quantization tables**: fingerprinted and matched against libjpeg's scaled tables and a list of known encoders ([[Findings Reference]]).
- **MakerNote presence**, the **EXIF thumbnail** and the **HDR gain map** ([[Thumbnail and Gain Map]]).
- **Provenance**: C2PA manifests in PNG `caBX` chunks, JPEG APP11 segments and WebP `C2PA` chunks, with a best-effort claim generator name; IPTC digital source types.
- **XMP**: from JPEG APP1 segments (including Adobe's extended-XMP continuation for packets over 64 KB), PNG `iTXt` chunks, WebP `XMP` chunks, or Pillow's metadata for other formats.

## Reading an XMP packet

XMP says far more about editing than EXIF. `xmp:CreatorTool` names the last program that wrote it. Photoshop and Lightroom append an event to `xmpMM:History` on every save, with the software, the time and what changed. `xmpMM:DerivedFrom` points at the document the file was made from. Lightroom and Camera Raw store every development setting: the raw file name, the sliders, the crop rectangle, each local adjustment, and each place where the healing or clone brush was used.

```
== edited.jpg
   packet : 2561 bytes in JPEG APP1 segment; namespaces crs, dc, photoshop, xmp, xmpMM
   creator tool : Adobe Photoshop Lightroom Classic 13.0 (Macintosh)
   create date  : 2024-05-04T10:22:31
   derived from : xmp.did:0000
   history (2 steps, oldest first):
     (no time)  derived  converted from image/x-nikon-nef to image/jpeg
     2024-05-06 18:40:12+02:00  saved  Adobe Photoshop Lightroom Classic 13.0 (Macintosh)  changed /
   camera raw:
     raw file        DSC_0421.NEF
     Exposure2012    +0.65
     crop            left 0.1, top 0.05, right 0.9, bottom 0.95
     retouch         3 healed or cloned area(s)
   ! Camera Raw healing or clone brush applied in 3 place(s): parts of the picture were painted over.
```

The retouch entry is as close as metadata gets to naming the spot that was changed: `--raw` shows each area's position and size, which tells you where to look with `ela`, `noise` and `copymove`.

## How to weigh it

Metadata is plain text that anyone with a hex editor or exiftool can change, and most apps and platforms strip it. So:

- A finding is a lead. `editor_software` says an editor saved the file, not what it did; `xmp_retouch` says the healing brush was used, which may have removed a dust spot.
- Consistency between independent fields is stronger than any one field: an EXIF capture time, an XMP create date, a GPS fix time and a file system time that agree were probably not all edited.
- Absence means nothing. A forwarded picture has no EXIF, no XMP and no C2PA whatever its origin.
- A careful edit leaves clean metadata. In a red-team test, a GPS edit within the same time zone passed every check ([[Case Studies]]).
