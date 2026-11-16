# HTML Report

```bash
exif-inspect report photo.jpg                        # writes photo_report.html
exif-inspect report --skip light,copymove photo.jpg  # leave tools out
exif-inspect report --grid -o out.html photo.jpg     # slower ghost search, chosen output path
```

One page with everything: the picture, the findings (flagged ones first), the metadata report, and then a section for each tool in turn, `ela`, `ghost`, `cfa`, `noise`, `light`, `copymove`, `thumbnail` (when the file has one) and `xmp`, each with the map it drew, the text it printed and the time it took.

- Pictures are embedded in the page, downscaled to 1400 pixels, so the file stands alone and can be sent on. A report on a 12-megapixel photo is a few hundred kilobytes.
- Each tool runs through its own command-line entry, so the page shows exactly what the command prints. A tool that fails on a file gets a red heading with its error, and the rest of the page is still written.
- `prnu` is not included, since it needs a camera fingerprint; run it separately and attach its pictures.
- Plain HTML and CSS, no scripts, so it opens anywhere and prints cleanly.

The page is a starting point for notes, not the notes themselves: add what each positive means for the file at hand and why each negative was or was not informative ([[Limits and False Positives]]).
