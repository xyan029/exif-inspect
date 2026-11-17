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

    g = report.gain_map
    if g is not None:
        sx, sy = g.scale
        scale = f"{sx:.2f}" if abs(sx - sy) < 0.01 else f"{sx:.2f}x{sy:.2f}"
        print(f"   gain map : {g.size[0]}x{g.size[1]} ({g.kind}, {g.container}), scale {scale}")

    t = report.trailer
    if t is not None:
        print(f"   trailer : {t.size} bytes after the image, {t.kind}")

    x = report.xmp
    if x.present:
        parts = [f"{x.size} bytes in {x.container}"]
        if x.creator_tool:
            parts.append(f"written by {x.creator_tool}")
        if x.history:
            parts.append(f"{len(x.history)} history step(s)")
        print(f"   xmp : {', '.join(parts)}")

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
                   help=f"side of the centre crop analysed, in pixels; 0 for the largest "
                        f"centred square (default: {prnu.CROP})")
    b.add_argument("images", nargs="+",
                   help="photos from one camera; 20 or more flat, bright shots work best")
    m = sub.add_parser("match", help="test photos against a fingerprint")
    m.add_argument("fingerprint", help="fingerprint file from 'prnu build'")
    m.add_argument("images", nargs="+", help="photos to test")
    loc = sub.add_parser("locate", help="map which parts of a photo lack the camera's pattern")
    loc.add_argument("fingerprint", help="fingerprint file from 'prnu build'")
    loc.add_argument("image", help="photo to examine")
    loc.add_argument("-o", "--output", help="output PNG (default: <name>_prnu.png)")
    loc.add_argument("--block", type=int,
                     help="block size in pixels (default: a quarter of the fingerprint crop)")
    args = parser.parse_args(argv)

    if not prnu.available():
        print('PRNU needs PyWavelets: pip install "exif-inspect[prnu]"')
        return 2

    if args.action == "build":
        fp = prnu.build(args.images, crop=args.crop)
        fp.save(args.output)
        print(f"fingerprint from {fp.images} of {len(args.images)} images "
              f"({fp.source[0]}x{fp.source[1]}), {fp.crop}x{fp.crop} centre crop -> {args.output}")
        for path, reason in fp.skipped:
            print(f"   skipped {path}: {reason}")
        return 0

    fp = prnu.Fingerprint.load(args.fingerprint)
    if args.action == "locate":
        from pathlib import Path

        src = Path(args.image)
        try:
            res = prnu.locate(fp, src, block=args.block)
        except Exception as exc:
            print(f"== {src}\n   error  : {exc}")
            return 1
        out = Path(args.output) if args.output else src.with_name(f"{src.stem}_prnu.png")
        prnu.render_locate(src, res).save(out)
        print(f"== {src}")
        print(f"   PCE {res.pce:.1f} over the whole crop, {res.block}-pixel blocks")
        print(f"   pattern checkable in {res.reliable_map.mean():.0%} of the crop "
              "(dark, clipped and busy areas cannot be judged)")
        if res.box:
            print(f"   ! pattern missing around {res.box}: that area may have been "
                  "replaced after capture")
        else:
            print("   no area found where the pattern is missing")
        print(f"   output : {out} (red = pattern missing, dimmed = cannot judge, "
              "white frame = area analysed)")
        return 0

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
            verdict = "inconclusive"
        else:
            verdict = "no evidence of the same camera"
        turn = f", image turned {result.rotation} degrees" if result.rotation else ""
        print(f"== {image}\n   PCE {result.pce:.1f}{turn}: {verdict}")
    return exit_code


def ghost_main(argv: list[str]) -> int:
    from pathlib import Path

    from . import ghost

    parser = argparse.ArgumentParser(
        prog="exif-inspect ghost",
        description="Look for JPEG ghosts: the quality an image, or a pasted part of it, "
                    "was saved at before its last save.",
    )
    parser.add_argument("images", nargs="+", help="image files")
    parser.add_argument("-o", "--output",
                        help="output PNG (single image only; default: <name>_ghost.png, "
                             "written when a ghost region is found)")
    parser.add_argument("--grid", action="store_true",
                        help="also try the 63 other offsets of the 8x8 JPEG grid, which "
                             "finds pasted patches that do not sit on the grid (64x slower)")
    parser.add_argument("--block", type=int, default=ghost.BLOCK,
                        help=f"analysis block in pixels, a multiple of 8 (default: {ghost.BLOCK})")
    parser.add_argument("--curve", action="store_true",
                        help="print the re-save error at every quality")
    args = parser.parse_args(argv)
    if args.output and len(args.images) > 1:
        parser.error("--output works with a single image")
    if args.block < 8 or args.block % 8:
        parser.error("--block must be a multiple of 8")

    exit_code = 0
    for image in args.images:
        src = Path(image)
        print(f"== {src}")
        try:
            res = ghost.analyze(src, block=args.block, grid=args.grid)
        except Exception as exc:
            print(f"   error  : {exc}\n")
            exit_code = 1
            continue
        if res.current_quality is None:
            print(f"   source : {res.source_format}, not a JPEG; a ghost would show an earlier "
                  "JPEG save")
        else:
            print(f"   saved at quality {res.current_quality}")
        bars, axis = ghost.sparkline(res)
        print(f"   re-save error by quality {res.qualities[0]}-{res.qualities[-1]}:")
        print(f"   {bars}\n   {axis}")
        if args.curve:
            for q, v in zip(res.qualities, res.curve):
                print(f"     q{q:<3d} {v}")
        for q, depth in res.global_ghosts:
            print(f"   ! whole image carries a ghost at quality {q} (dip {depth:.0%}): "
                  f"it was saved as a JPEG at about that quality before")
        if res.ghost_quality is not None and res.box is not None:
            where = ("most of the picture" if res.ghost_fraction >= 0.5
                     else f"{res.ghost_blocks} blocks around {res.box}")
            off = f", grid offset {res.shift}" if res.shift != (0, 0) else ""
            print(f"   ! ghost at quality {res.ghost_quality} in {where}{off}: that area came "
                  f"from a JPEG saved at about quality {res.ghost_quality}")
            out = Path(args.output) if args.output else src.with_name(f"{src.stem}_ghost.png")
            ghost.render(res).save(out)
            print(f"   output : {out} (cyan = ghost region)")
        elif not res.global_ghosts:
            hint = "" if res.searched_grid else "; a patch off the 8x8 grid needs --grid"
            print(f"   no ghost found{hint}")
        print(f"   judged : {res.judged_fraction:.0%} of the picture has enough detail "
              "to show a ghost\n")
    return exit_code


def light_main(argv: list[str]) -> int:
    from pathlib import Path

    from . import lighting

    parser = argparse.ArgumentParser(
        prog="exif-inspect light",
        description="Estimate the light direction from object outlines and report "
                    "objects lit from somewhere else.",
    )
    parser.add_argument("images", nargs="+", help="image files")
    parser.add_argument("-o", "--output",
                        help="output PNG (single image only; default: <name>_light.png)")
    parser.add_argument("--box", action="append", metavar="X0,Y0,X1,Y1",
                        help="pixel box around one object; repeat for each object to compare "
                             "(single image only). Without boxes every usable outline counts.")
    args = parser.parse_args(argv)
    if (args.output or args.box) and len(args.images) > 1:
        parser.error("--output and --box work with a single image")
    boxes = []
    for text in args.box or []:
        try:
            x0, y0, x1, y1 = (int(v) for v in text.split(","))
        except ValueError:
            parser.error(f"--box needs four integers: {text!r}")
        boxes.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    exit_code = 0
    for image in args.images:
        src = Path(image)
        print(f"== {src}")
        try:
            res = lighting.analyze(src, boxes or None)
        except Exception as exc:
            print(f"   error  : {exc}\n")
            exit_code = 1
            continue
        if boxes:
            found = {c.region for c in res.contours}
            for i, box in enumerate(boxes):
                c = next((c for c in res.contours if c.region == i), None)
                if c is None:
                    print(f"   box {i + 1} {box}: no usable outline")
                else:
                    mark = "!" if c.outlier else "-"
                    print(f"   {mark} box {i + 1} {box}: lit from {c.angle:.0f} degrees "
                          f"({lighting.compass(c.angle)}), +-{c.sigma:.0f}, fit {c.fit:.2f}")
            if len(found) < 2:
                print("   fewer than two boxes gave an outline, so nothing to compare")
        else:
            print(f"   outlines : {len(res.contours)} usable of {res.chains} edge chains")
            for c in res.contours:
                mark = "!" if c.outlier else "-"
                print(f"   {mark} {c.box}: lit from {c.angle:.0f} degrees "
                      f"({lighting.compass(c.angle)}), +-{c.sigma:.0f}, fit {c.fit:.2f}")
        if not res.contours:
            print("   no outline curves enough, with a clear shading, to fix a direction\n")
            continue
        if res.consensus:
            print(f"   light from {res.direction:.0f} degrees ({lighting.compass(res.direction)}), "
                  f"outlines agree within +-{res.spread:.0f}")
            for c in res.outliers:
                print(f"   ! outline at {c.box} is lit from {c.angle:.0f} degrees, "
                      f"{c.deviation:.0f} off the rest: check whether it belongs to this scene")
        elif len(res.contours) > 1:
            print("   outlines disagree with each other; no common direction. Pick the objects "
                  "to compare with --box")
        out = Path(args.output) if args.output else src.with_name(f"{src.stem}_light.png")
        lighting.render(res).save(out)
        print(f"   output : {out} (arrows point toward the light; red = disagrees, "
              "yellow = common direction)\n")
    return exit_code


def cfa_main(argv: list[str]) -> int:
    from pathlib import Path

    from . import cfa

    parser = argparse.ArgumentParser(
        prog="exif-inspect cfa",
        description="Look for the traces that demosaicing leaves in the green channel, "
                    "and for regions that lack them.",
    )
    parser.add_argument("images", nargs="+", help="image files")
    parser.add_argument("-o", "--output",
                        help="output PNG (single image only; default: <name>_cfa.png, written "
                             "when a region lacks the trace)")
    parser.add_argument("--block", type=int, default=cfa.BLOCK,
                        help=f"analysis block in pixels, even (default: {cfa.BLOCK})")
    args = parser.parse_args(argv)
    if args.output and len(args.images) > 1:
        parser.error("--output works with a single image")
    if args.block < 8 or args.block % 2:
        parser.error("--block must be an even number of at least 8")

    exit_code = 0
    for image in args.images:
        src = Path(image)
        print(f"== {src}")
        try:
            res = cfa.analyze(src, block=args.block)
        except Exception as exc:
            print(f"   error  : {exc}\n")
            exit_code = 1
            continue
        if res.trace == "none":
            print(f"   no demosaicing trace (lattice ratio {res.strength:+.2f}, "
                  f"{res.agreement:.0%} of blocks agree): the picture was resized, "
                  "recompressed, processed or generated after any demosaicing, or never "
                  "went through it. Most camera and phone JPEGs show none.\n")
            continue
        print(f"   demosaicing trace : {res.trace} (lattice ratio {res.strength:+.2f}, "
              f"{res.agreement:.0%} of blocks agree), interpolated green pixels at {res.lattice}")
        print(f"   checkable : {res.judged_fraction:.0%} of the picture carries enough of the "
              "trace to judge a block")
        if res.box:
            print(f"   ! trace missing around {res.box} ({res.flagged_fraction:.0%} of checkable "
                  "blocks): that area was pasted, resized, shifted or generated after the "
                  "rest was demosaiced")
            out = Path(args.output) if args.output else src.with_name(f"{src.stem}_cfa.png")
            cfa.render(res).save(out)
            print(f"   output : {out} (red = trace missing, dimmed = cannot judge, "
                  "white frame = area analysed)")
        else:
            print("   no region lacks the trace")
        print()
    return exit_code


def noise_main(argv: list[str]) -> int:
    from pathlib import Path

    from . import noise

    parser = argparse.ArgumentParser(
        prog="exif-inspect noise",
        description="Map the sensor noise level and report regions whose noise does not "
                    "match the rest of the picture.",
    )
    parser.add_argument("images", nargs="+", help="image files")
    parser.add_argument("-o", "--output",
                        help="output PNG (single image only; default: <name>_noise.png, "
                             "written when a region is found)")
    parser.add_argument("--block", type=int, default=noise.BLOCK,
                        help=f"analysis block in pixels, a multiple of 8 (default: {noise.BLOCK})")
    args = parser.parse_args(argv)
    if args.output and len(args.images) > 1:
        parser.error("--output works with a single image")
    if args.block < 16 or args.block % 8:
        parser.error("--block must be a multiple of 8, at least 16")

    exit_code = 0
    for image in args.images:
        src = Path(image)
        print(f"== {src}")
        try:
            res = noise.analyze(src, block=args.block)
        except Exception as exc:
            print(f"   error  : {exc}\n")
            exit_code = 1
            continue
        if res.judged_fraction == 0:
            print("   no block is quiet enough to measure the noise\n")
            continue
        trend = "rising with brightness" if res.rising else "about the same at every brightness"
        print(f"   noise : about {res.level:.1f} grey levels in quiet areas, {trend}")
        print(f"   checkable : {res.judged_fraction:.0%} of the picture has flat patches to "
              "measure on")
        if res.box:
            what = ("has more noise than areas like it" if res.kind == "noisier"
                    else "has less noise than areas like it")
            print(f"   ! region around {res.box} {what} ({res.ratio:.2f} times the expected "
                  f"level): it may have been pasted in, smoothed, painted or filled")
            out = Path(args.output) if args.output else src.with_name(f"{src.stem}_noise.png")
            noise.render(res).save(out)
            print(f"   output : {out} (red = noisier, blue = smoother, dimmed = cannot judge)")
        else:
            print("   no region differs from areas of similar texture and brightness")
        print()
    return exit_code


def xmp_main(argv: list[str]) -> int:
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="exif-inspect xmp",
        description="Show the XMP packet of each image: creator tool, dates, document IDs, "
                    "the editing history and the Camera Raw settings.",
    )
    parser.add_argument("images", nargs="+", help="image files")
    parser.add_argument("--raw", action="store_true", help="print the XML packet itself")
    args = parser.parse_args(argv)

    exit_code = 0
    for image in args.images:
        src = Path(image)
        print(f"== {src}")
        report = read_image(src)
        if report.error:
            print(f"   error  : {report.error}\n")
            exit_code = 1
            continue
        x = report.xmp
        if not x.present:
            print("   no XMP packet\n")
            continue
        print(f"   packet : {x.size} bytes in {x.container}; namespaces {', '.join(x.namespaces)}")
        for label, value in (("creator tool", x.creator_tool), ("create date", x.create_date),
                             ("modify date", x.modify_date), ("metadata date", x.metadata_date),
                             ("document id", x.document_id),
                             ("original doc", x.original_document_id),
                             ("instance id", x.instance_id), ("derived from", x.derived_from),
                             ("credit", x.credit), ("rights", x.rights),
                             ("source type", x.digital_source_type)):
            if value:
                print(f"   {label:<13}: {value}")
        if x.history:
            print(f"   history ({len(x.history)} steps, oldest first):")
            for e in x.history:
                when = (e.when or "").replace("T", " ")
                bits = [when or "(no time)", e.action or "?"]
                if e.agent:
                    bits.append(e.agent)
                if e.changed:
                    bits.append(f"changed {e.changed}")
                if e.parameters:
                    bits.append(e.parameters)
                print("     " + "  ".join(bits))
        if x.raw_file or x.crs_settings or x.retouch_areas or x.local_adjustments or x.has_crop:
            print("   camera raw:")
            width = max([16] + [len(k) + 2 for k in x.crs_settings])
            if x.raw_file:
                print(f"     {'raw file':<{width}}{x.raw_file}")
            for key, value in x.crs_settings.items():
                print(f"     {key:<{width}}{value}")
            if x.has_crop and x.crop:
                print(f"     {'crop':<{width}}left {x.crop[0]}, top {x.crop[1]}, right {x.crop[2]}, "
                      f"bottom {x.crop[3]}")
            if x.retouch_areas:
                print(f"     {'retouch':<{width}}{x.retouch_areas} healed or cloned area(s)")
            if x.local_adjustments:
                print(f"     {'local':<{width}}{x.local_adjustments} local adjustment(s)")
        for f in run_checks(report):
            if f.code.startswith("xmp_"):
                print(f"   {'!' if f.level == 'flag' else '-'} {f.message}")
        if args.raw:
            print("   --- packet ---")
            print(x.raw.strip())
        print()
    return exit_code


def report_main(argv: list[str]) -> int:
    from pathlib import Path

    from . import report as report_mod

    parser = argparse.ArgumentParser(
        prog="exif-inspect report",
        description="Run every check on an image and write one self-contained HTML page "
                    "with the findings, the maps and each tool's output.",
    )
    parser.add_argument("images", nargs="+", help="image files")
    parser.add_argument("-o", "--output",
                        help="output HTML path (single image only; default: <name>_report.html)")
    parser.add_argument("--skip", default="",
                        help="comma-separated tools to leave out, from: " + ", ".join(report_mod.TOOLS))
    parser.add_argument("--grid", action="store_true",
                        help="pass --grid to the ghost search (64 times slower)")
    args = parser.parse_args(argv)
    if args.output and len(args.images) > 1:
        parser.error("--output works with a single image")
    skip = {t.strip() for t in args.skip.split(",") if t.strip()}
    unknown = skip - set(report_mod.TOOLS)
    if unknown:
        parser.error(f"unknown tool(s) in --skip: {', '.join(sorted(unknown))}")
    tools = tuple(t for t in report_mod.TOOLS if t not in skip)

    exit_code = 0
    for image in args.images:
        src = Path(image)
        built = report_mod.build(src, tools, grid=args.grid)
        out = Path(args.output) if args.output else src.with_name(f"{src.stem}_report.html")
        out.write_text(built.html, encoding="utf-8")
        failed = [s.title for s in built.sections if s.failed]
        print(f"== {src}")
        print(f"   report : {out} ({out.stat().st_size // 1024} KB, {built.flags} flagged finding(s), "
              f"{len(built.sections)} tool section(s))")
        if failed:
            print(f"   failed : {', '.join(failed)}")
            exit_code = 1
        if not built.sections:
            exit_code = 1
        print()
    return exit_code


def stego_main(argv: list[str]) -> int:
    from pathlib import Path

    from . import hidden

    parser = argparse.ArgumentParser(
        prog="exif-inspect stego",
        description="Look for data hidden in a file: bytes after the end of the picture, a "
                    "payload in the lowest bits of a lossless picture (LSB steganography), or "
                    "one in the DCT coefficients of a JPEG (JSteg-style LSB replacement, F5).",
    )
    parser.add_argument("images", nargs="+", help="image files")
    parser.add_argument("-o", "--output",
                        help="output PNG of the LSB planes (single image only; "
                             "default: <name>_lsb.png)")
    parser.add_argument("--extract", action="store_true",
                        help="also save the trailing bytes as <name>_trailer.bin")
    args = parser.parse_args(argv)
    if args.output and len(args.images) > 1:
        parser.error("--output works with a single image")

    exit_code = 0
    for image in args.images:
        src = Path(image)
        print(f"== {src}")
        report = read_image(src)
        if report.error:
            print(f"   error  : {report.error}\n")
            exit_code = 1
            continue
        t = report.trailer
        if t is None:
            print("   trailer : nothing after the end of the image")
        else:
            mark = "-" if t.expected else "!"
            print(f"   {mark} trailer : {t.size} bytes after the image at offset {t.offset}, "
                  f"{t.kind}; starts {t.sample!r}")
            if args.extract and not t.expected:
                out = src.with_name(f"{src.stem}_trailer.bin")
                out.write_bytes(src.read_bytes()[t.offset:])
                print(f"   saved   : {out}")
        if report.format in ("JPEG", "MPO"):
            from . import stegjpeg

            res = stegjpeg.analyze(src)
            if res.error:
                print(f"   coeffs    : {res.error}; the lowest-bit tests need pixel samples, "
                      "which a JPEG does not store\n")
                continue
            print(f"   coeffs    : {res.blocks} luma blocks read, {res.nonzero_ac} non-zero AC "
                  f"coefficients, {res.usable} usable for JSteg")
            print(f"   lsb pairs : {res.lsb_embedded:.0%} of the coefficient stream reads as "
                  f"evened out; the first {res.lsb_prefix:.0%} in one run")
            if res.f5_ratio is not None:
                print(f"   calibrated: F5-style change ratio about {res.f5_ratio:.0%}")
            v = stegjpeg.verdict(res)
            print(f"   {'!' if v.flag else '-'} {v.message}\n")
            continue
        try:
            res = hidden.analyze_lsb(src)
        except Exception as exc:
            print(f"   error  : {exc}\n")
            exit_code = 1
            continue
        print(f"   lsb     : {res.channels} channel(s), {res.samples} samples; ones {res.lsb_mean:.1%}, "
              f"entropy {res.lsb_entropy:.2f} bits per byte (8.00 is pure noise)")
        per = ", ".join(f"{r:.0%}" for r in res.rs_per_channel)
        print(f"   RS      : about {res.rs_rate:.0%} of the samples carry a random payload "
              f"(per channel {per})")
        prefix = res.chi_prefix / res.samples if res.samples else 0.0
        print(f"   chi-sq  : {res.chi_embedded:.0%} of the scan reads as embedded; the first "
              f"{prefix:.0%} in one run")
        verdict = hidden.verdict(res)
        print(f"   {'!' if verdict.flag else '-'} {verdict.message}")
        out = Path(args.output) if args.output else src.with_name(f"{src.stem}_lsb.png")
        res.planes.save(out)
        print(f"   output  : {out} (lowest bit of each channel, white = 1)\n")
    return exit_code


def compare_main(argv: list[str]) -> int:
    from pathlib import Path

    from . import compare as cmp

    parser = argparse.ArgumentParser(
        prog="exif-inspect compare",
        description="Relate two versions of a picture: where one sits inside the other, "
                    "what changed between them, and which is the parent.",
    )
    parser.add_argument("a", help="first image")
    parser.add_argument("b", help="second image")
    parser.add_argument("-o", "--output", help="output PNG (default: <a>_vs_<b>.png)")
    args = parser.parse_args(argv)
    a, b = Path(args.a), Path(args.b)
    print(f"== {a}  vs  {b}")
    try:
        res = cmp.compare(a, b)
    except Exception as exc:
        print(f"   error  : {exc}\n")
        return 1
    for key, va, vb in res.metadata:
        mark = " " if va == vb else "*"
        print(f"   {mark} {key:<13} A: {va or '-':<32} B: {vb or '-'}")
    p = res.placement
    if p is None:
        print("   the two pictures do not show the same scene (no placement above "
              f"correlation {cmp.MIN_NCC})\n")
        return 0
    other = "B" if p.container == "a" else "A"
    turn = f", turned {p.rotation} degrees" if p.rotation else ""
    if p.cover >= 0.97:
        frame = f"{other} shows the same framing as {p.container.upper()}"
    else:
        frame = (f"{other} is a crop of {p.container.upper()}: box {p.box}, "
                 f"{p.cover:.0%} of the frame")
    scale = ("at the same size" if abs(p.scale - 1) < 0.03 else
             f"scaled {p.scale:.2f}x ({'up' if p.scale > 1 else 'down'})")
    print(f"   match : {frame}, {scale}{turn}; correlation {p.ncc:.3f}")
    if res.same:
        print("   same  : the pictures are the same within compression noise")
    elif res.changes:
        print(f"   ! changed : {len(res.changes)} region(s) differ beyond resampling and compression "
              f"noise ({res.changed_fraction:.1%} of the shared area):")
        for box in res.changes:
            print(f"       {box} in {p.container.upper()}")
    else:
        print("   changed : no region differs beyond resampling and compression noise")
    if res.parent:
        print(f"   parent : {res.parent.upper()} came first: {'; '.join(res.reasons)}")
    else:
        print(f"   parent : undecided: {'; '.join(res.reasons)}")
    out = Path(args.output) if args.output else a.with_name(f"{a.stem}_vs_{b.stem}.png")
    res.picture.save(out)
    print(f"   output : {out} (container with the other's frame in cyan and changes in orange, "
          "the other picture, the difference)\n")
    return 0


def dupes_main(argv: list[str]) -> int:
    from . import dupes

    parser = argparse.ArgumentParser(
        prog="exif-inspect dupes",
        description="Find duplicate and near-duplicate pictures across folders and name the "
                    "likely original in each group.",
    )
    parser.add_argument("paths", nargs="+", help="folders (searched recursively) or files")
    parser.add_argument("--distance", type=int, default=dupes.DISTANCE,
                        help=f"perceptual-hash bits that may differ within a group "
                             f"(default: {dupes.DISTANCE}; 0 for near-identical pictures only)")
    parser.add_argument("--json", action="store_true", help="emit the groups as JSON")
    args = parser.parse_args(argv)
    if not 0 <= args.distance <= 32:
        parser.error("--distance must be between 0 and 32")

    res = dupes.find(args.paths, max_distance=args.distance)
    if args.json:
        import json
        from dataclasses import asdict
        out = {"scanned": res.scanned, "singles": res.singles, "unreadable": res.unreadable,
               "groups": [{"exact": g.exact, "best": g.best.path,
                           "members": [{**{k: v for k, v in asdict(m).items()
                                           if k not in ("phash", "dhash")},
                                        "distance": g.distances[m.path]} for m in g.members]}
                          for g in res.groups]}
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return 0
    print(f"scanned {res.scanned} file(s): {len(res.groups)} group(s) of duplicates, "
          f"{res.singles} single(s), {len(res.unreadable)} unreadable")
    for n, g in enumerate(res.groups, 1):
        kind = "identical bytes" if g.exact else f"within {args.distance} hash bits"
        print(f"\n== group {n}: {len(g.members)} files, {kind}")
        for m in g.members:
            dp, dd, rot = g.distances[m.path]
            star = "*" if m is g.best else " "
            q = f"q{m.quality}" if m.quality else "-"
            turn = f", turned {rot}" if rot else ""
            same = "same bytes" if m.sha256 == g.best.sha256 and m is not g.best else f"distance {dp}/{dd}{turn}"
            print(f"   {star} {m.path}")
            print(f"       {m.fmt} {m.size[0]}x{m.size[1]}, {q}, {m.tags} EXIF tags, "
                  f"{m.bytes // 1024} KB; {same if m is not g.best else 'likely original'}")
    for path, err in res.unreadable:
        print(f"\n   unreadable {path}: {err}")
    return 0


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
    if argv and argv[0] == "ghost":
        return ghost_main(argv[1:])
    if argv and argv[0] == "light":
        return light_main(argv[1:])
    if argv and argv[0] == "cfa":
        return cfa_main(argv[1:])
    if argv and argv[0] == "noise":
        return noise_main(argv[1:])
    if argv and argv[0] == "xmp":
        return xmp_main(argv[1:])
    if argv and argv[0] == "report":
        return report_main(argv[1:])
    if argv and argv[0] == "stego":
        return stego_main(argv[1:])
    if argv and argv[0] == "compare":
        return compare_main(argv[1:])
    if argv and argv[0] == "dupes":
        return dupes_main(argv[1:])

    parser = argparse.ArgumentParser(
        prog="exif-inspect",
        description="Print EXIF metadata and simple forensic hints for images.",
        epilog="Subcommands: ela (error level analysis), ghost (JPEG ghosts), light (lighting "
               "direction), cfa (demosaicing traces), noise (noise level), xmp (editing history), "
               "stego (hidden data), compare (two versions of a picture), dupes (duplicates across "
               "folders), copymove, thumbnail, prnu, "
               "report (everything as one HTML page); see 'exif-inspect <subcommand> --help'.",
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
            entry = {**asdict(report), "findings": [asdict(f) for f in findings]}
            entry["xmp"].pop("raw", None)
            results.append(entry)
        else:
            _print_text(report, findings, args.all)

    if args.json:
        json.dump(results, sys.stdout, indent=2, ensure_ascii=False, default=str)
        print()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
