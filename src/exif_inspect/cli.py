"""Command-line entry point: exif-inspect IMAGE [IMAGE ...]"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import __version__
from .checks import run_checks
from .extract import read_image

# Tags shown in the default (non --all) view.
KEY_TAGS = {
    "Image": ["Make", "Model", "Software", "DateTime", "Artist", "Copyright"],
    "Exif": ["DateTimeOriginal", "DateTimeDigitized", "OffsetTimeOriginal",
             "LensModel", "ExposureTime", "FNumber", "ISOSpeedRatings",
             "FocalLength", "ExifImageWidth", "ExifImageHeight"],
    "GPS": ["GPSLatitudeRef", "GPSLatitude", "GPSLongitudeRef", "GPSLongitude",
            "GPSAltitude", "GPSDateStamp"],
}


def _print_text(report, findings, show_all: bool) -> None:
    print(f"== {report.path}")
    print(f"   sha256 : {report.sha256 or '-'}")
    if report.error:
        print(f"   error  : {report.error}")
    else:
        w, h = report.size or (0, 0)
        print(f"   format : {report.format}  {w}x{h}  {report.mode}")

    for group, tags in report.groups.items():
        if show_all:
            items = sorted(tags.items())
        else:
            items = [(k, tags[k]) for k in KEY_TAGS.get(group, []) if k in tags]
        if not items:
            continue
        print(f"   [{group}]")
        for key, value in items:
            print(f"     {key:<22} {value}")

    if report.gps_decimal:
        lat, lon = report.gps_decimal
        print(f"   GPS (decimal) : {lat}, {lon}")

    q = report.jpeg_quant
    if q is not None:
        kind = (f"libjpeg standard, quality {q.quality}" if q.standard
                else f"custom, closest libjpeg q{q.closest_quality} (off by {q.mean_abs_diff})")
        extra = f", {q.subsampling}" if q.subsampling else ""
        print(f"   jpeg tables : {kind}{extra}, fp {q.fingerprint}")

    t = report.thumbnail
    if t is not None:
        w, h = t.size
        if t.unrelated:
            state = "does not match the main image"
        elif t.cropped:
            state = f"shows a wider scene (main image covers {t.cover:.0%})"
        elif t.changed_box:
            state = f"differs around {t.changed_box}"
        elif t.partial:
            state = "shows the centre of the main image"
        else:
            state = "matches the main image"
        print(f"   thumbnail : {w}x{h}, {state}")

    prov = report.provenance
    if prov.found:
        parts = []
        if prov.c2pa:
            parts.append(f"C2PA in {prov.container}")
        if prov.generator:
            parts.append(f"generator {prov.generator}")
        if prov.digital_source_type:
            parts.append(f"source type {prov.digital_source_type}")
        print(f"   provenance : {'; '.join(parts)}")

    if findings:
        print("   Findings:")
        for f in findings:
            mark = "!" if f.level == "flag" else "-"
            print(f"     {mark} {f.message}")
    print()


def ela_main(argv: list[str]) -> int:
    # Imported here so the metadata command stays fast to start.
    from pathlib import Path

    from .ela import run_ela, side_by_side

    parser = argparse.ArgumentParser(
        prog="exif-inspect ela",
        description="Write an error level analysis (ELA) heatmap for each image.",
    )
    parser.add_argument("images", nargs="+", help="image files (JPEG works best)")
    parser.add_argument("-q", "--quality", type=int, default=90,
                        help="JPEG quality used for the re-save (default: 90)")
    parser.add_argument("-o", "--output",
                        help="output PNG path (single image only; "
                             "default: <name>_ela.png next to the input)")
    parser.add_argument("--scale", type=float,
                        help="fixed amplification factor (default: automatic)")
    parser.add_argument("--gray", action="store_true",
                        help="grayscale heatmap")
    parser.add_argument("--side-by-side", action="store_true",
                        help="put the original image left of the heatmap")
    args = parser.parse_args(argv)

    if not 1 <= args.quality <= 100:
        parser.error("--quality must be between 1 and 100")
    if args.output and len(args.images) > 1:
        parser.error("--output works with a single image")

    exit_code = 0
    for image in args.images:
        src = Path(image)
        try:
            result = run_ela(src, args.quality, args.scale, args.gray)
        except Exception as exc:  # unreadable or not an image
            print(f"== {src}\n   error  : {exc}\n")
            exit_code = 1
            continue

        out_img = side_by_side(src, result.heatmap) if args.side_by_side else result.heatmap
        out_path = Path(args.output) if args.output else src.with_name(f"{src.stem}_ela.png")
        out_img.save(out_path)

        print(f"== {src}")
        print(f"   output : {out_path}")
        print(f"   error  : mean {result.mean_error}, max {result.max_error}, "
              f"amplified x{result.scale}")
        if result.source_format != "JPEG":
            print(f"   note   : source is {result.source_format}; "
                  "ELA is designed for JPEG and may be uninformative here")
        print()
    return exit_code


def copymove_main(argv: list[str]) -> int:
    from pathlib import Path

    from .copymove import detect, draw

    parser = argparse.ArgumentParser(
        prog="exif-inspect copymove",
        description="Look for regions copied and pasted within the same image.",
    )
    parser.add_argument("images", nargs="+", help="image files")
    parser.add_argument("-o", "--output",
                        help="output PNG (single image only; default: <name>_copymove.png)")
    parser.add_argument("--min-pairs", type=int, default=200,
                        help="matching block pairs needed to report a clone (default: 200)")
    parser.add_argument("--keypoints", action="store_true",
                        help="match SIFT keypoints instead of blocks; finds clones that were "
                             "scaled or rotated (needs OpenCV: pip install \"exif-inspect[keypoints]\")")
    parser.add_argument("--size", type=int, default=None,
                        help="with --keypoints: longest side to analyse at, in pixels "
                             "(default: twice the image, kept within 1000-2400)")
    args = parser.parse_args(argv)
    if args.output and len(args.images) > 1:
        parser.error("--output works with a single image")
    if args.keypoints:
        return _keypoints_main(args)

    exit_code = 0
    for image in args.images:
        src = Path(image)
        try:
            result = detect(src, min_pairs=args.min_pairs)
        except Exception as exc:
            print(f"== {src}\n   error  : {exc}\n")
            exit_code = 1
            continue
        print(f"== {src}")
        if not result.clones:
            print("   no copy-move regions found\n")
            continue
        out = Path(args.output) if args.output else src.with_name(f"{src.stem}_copymove.png")
        draw(src, result).save(out)
        for n, c in enumerate(result.clones, 1):
            print(f"   clone {n}: shift {c.shift}, {c.pairs} matching block pairs")
            print(f"            source {c.source_box} -> copy {c.copy_box}")
        print(f"   output : {out} (orange = source, cyan = copy)\n")
    return exit_code


def _keypoints_main(args) -> int:
    from pathlib import Path

    from . import keypoints

    if not keypoints.available():
        print('keypoint mode needs OpenCV: pip install "exif-inspect[keypoints]"')
        return 2
    exit_code = 0
    for image in args.images:
        src = Path(image)
        try:
            result = keypoints.detect(src, size=args.size)
        except Exception as exc:
            print(f"== {src}\n   error  : {exc}\n")
            exit_code = 1
            continue
        print(f"== {src}")
        if not result.clones:
            print(f"   no copy-move regions found ({result.keypoints} keypoints checked)\n")
            continue
        out = Path(args.output) if args.output else src.with_name(f"{src.stem}_copymove.png")
        keypoints.draw(src, result).save(out)
        for n, c in enumerate(result.clones, 1):
            turn = f", rotated {abs(c.rotation):.0f} degrees" if abs(c.rotation) >= 1 else ""
            print(f"   clone {n}: scale {c.scale:.2f}{turn}, {c.matches} matching keypoints")
            print(f"            region A {c.box_a} -> region B {c.box_b}")
        print(f"   output : {out} (orange = A, cyan = B, the smaller copy is B)\n")
    return exit_code


def thumbnail_main(argv: list[str]) -> int:
    from pathlib import Path

    from .thumbnail import render

    parser = argparse.ArgumentParser(
        prog="exif-inspect thumbnail",
        description="Compare the EXIF thumbnail with the main image and save both side by side.",
    )
    parser.add_argument("images", nargs="+", help="image files")
    parser.add_argument("-o", "--output",
                        help="output PNG (single image only; default: <name>_thumbnail.png)")
    parser.add_argument("--extract", action="store_true",
                        help="also save the thumbnail as stored, as <name>_thumb.jpg")
    args = parser.parse_args(argv)
    if args.output and len(args.images) > 1:
        parser.error("--output works with a single image")

    exit_code = 0
    for image in args.images:
        src = Path(image)
        print(f"== {src}")
        try:
            rendered = render(src)
        except Exception as exc:
            print(f"   error  : {exc}\n")
            exit_code = 1
            continue
        if rendered is None:
            print("   no EXIF thumbnail\n")
            continue
        data, picture = rendered
        report = read_image(src)
        for f in run_checks(report):
            if f.code.startswith("thumbnail"):
                print(f"   ! {f.message}")
        out = Path(args.output) if args.output else src.with_name(f"{src.stem}_thumbnail.png")
        picture.save(out)
        print(f"   output : {out} (thumbnail | main image | difference)")
        if args.extract:
            raw = src.with_name(f"{src.stem}_thumb.jpg")
            raw.write_bytes(data)
            print(f"   thumb  : {raw}")
        print()
    return exit_code


def prnu_main(argv: list[str]) -> int:
    from . import prnu

    parser = argparse.ArgumentParser(
        prog="exif-inspect prnu",
        description="Identify the camera that took a photo from its sensor noise (PRNU).",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    b = sub.add_parser("build", help="estimate a camera fingerprint from photos it took")
    b.add_argument("-o", "--output", required=True, help="fingerprint file to write (.npz)")
    b.add_argument("--crop", type=int, default=prnu.CROP,
                   help=f"side of the centre crop analysed, in pixels (default: {prnu.CROP})")
    b.add_argument("images", nargs="+",
                   help="photos from one camera; 20 or more flat, bright shots work best")
    m = sub.add_parser("match", help="test photos against a fingerprint")
    m.add_argument("fingerprint", help="fingerprint file from 'prnu build'")
    m.add_argument("images", nargs="+", help="photos to test")
    args = parser.parse_args(argv)

    if not prnu.available():
        print('PRNU needs PyWavelets: pip install "exif-inspect[prnu]"')
        return 2

    if args.action == "build":
        fp = prnu.build(args.images, crop=args.crop)
        fp.save(args.output)
        print(f"fingerprint from {fp.images} of {len(args.images)} images, "
              f"{fp.crop}x{fp.crop} centre crop -> {args.output}")
        return 0

    fp = prnu.Fingerprint.load(args.fingerprint)
    exit_code = 0
    for image in args.images:
        try:
            result = prnu.match(fp, image)
        except Exception as exc:
            print(f"== {image}\n   error  : {exc}")
            exit_code = 1
            continue
        if result.pce >= prnu.PCE_THRESHOLD:
            verdict = "same camera"
        elif result.pce >= 20:
            verdict = "inconclusive, leaning towards the same camera"
        else:
            verdict = "no evidence of the same camera"
        turn = f", image turned {result.rotation} degrees" if result.rotation else ""
        print(f"== {image}\n   PCE {result.pce:.1f}{turn}: {verdict}")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "prnu":
        return prnu_main(argv[1:])
    if argv and argv[0] == "thumbnail":
        return thumbnail_main(argv[1:])
    if argv and argv[0] == "ela":
        return ela_main(argv[1:])
    if argv and argv[0] == "copymove":
        return copymove_main(argv[1:])

    parser = argparse.ArgumentParser(
        prog="exif-inspect",
        description="Print EXIF metadata and simple forensic hints for images.",
        epilog="Subcommands: 'exif-inspect ela IMAGE' writes an error level "
               "analysis heatmap (see 'exif-inspect ela --help').",
    )
    parser.add_argument("images", nargs="+", help="image files to inspect")
    parser.add_argument("-a", "--all", action="store_true",
                        help="show every tag instead of the key ones")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    results = []
    exit_code = 0
    for path in args.images:
        report = read_image(path)
        findings = run_checks(report)
        if report.error:
            exit_code = 1
        if args.json:
            results.append({**asdict(report),
                            "findings": [asdict(f) for f in findings]})
        else:
            _print_text(report, findings, args.all)

    if args.json:
        json.dump(results, sys.stdout, indent=2, ensure_ascii=False, default=str)
        print()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
