"""Simple forensic heuristics over extracted metadata.

These are hints for a human examiner. Metadata is easy to edit or strip,
so every finding here is a lead to follow up, never a conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .extract import ImageReport

# Lower-case substrings of Software values written by common editors.
EDITORS = (
    "photoshop", "lightroom", "gimp", "affinity", "pixelmator", "snapseed",
    "picsart", "canva", "facetune", "meitu", "photoscape", "paint.net",
    "darktable", "rawtherapee", "capture one",
)

# File extension -> formats Pillow reports for that kind of file.
EXPECTED_FORMATS = {
    ".jpg": {"JPEG", "MPO"}, ".jpeg": {"JPEG", "MPO"}, ".jpe": {"JPEG", "MPO"},
    ".png": {"PNG"},
    ".webp": {"WEBP"},
    ".gif": {"GIF"},
    ".bmp": {"BMP"},
    ".tif": {"TIFF"}, ".tiff": {"TIFF"},
    ".heic": {"HEIF"}, ".heif": {"HEIF"},
}


@dataclass
class Finding:
    level: str   # "info" or "flag"
    code: str    # stable machine-readable id
    message: str


def _extension_check(report: ImageReport) -> Finding | None:
    ext = Path(report.path).suffix.lower()
    expected = EXPECTED_FORMATS.get(ext)
    if expected and report.format and report.format not in expected:
        return Finding(
            "flag", "extension_mismatch",
            f"File is named {ext} but its content is {report.format}; "
            "it was renamed or saved by a tool that kept the old name.",
        )
    return None


def _dimension_check(report: ImageReport) -> Finding | None:
    exif = report.groups.get("Exif", {})
    try:
        ew = int(float(exif["ExifImageWidth"]))
        eh = int(float(exif["ExifImageHeight"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not report.size or (ew, eh) in (report.size, report.size[::-1]):
        return None
    w, h = report.size
    return Finding(
        "flag", "dimension_mismatch",
        f"EXIF records {ew}x{eh} but the image is {w}x{h}; "
        "it was likely cropped or resized after the EXIF was written.",
    )


def _quant_check(report: ImageReport) -> Finding | None:
    q = report.jpeg_quant
    if q is None:
        return None
    if q.standard:
        hint = (f" Consistent with {q.known_as}." if q.known_as
                else " Typical of Pillow, GIMP and many web services and apps.")
        return Finding(
            "info", "jpeg_tables",
            f"Standard libjpeg quantization tables at quality {q.quality}.{hint}",
        )
    if q.known_as:
        return Finding("info", "jpeg_tables",
                       f"Quantization tables match {q.known_as} (fingerprint {q.fingerprint}).")
    return Finding(
        "info", "jpeg_tables",
        f"Custom quantization tables (fingerprint {q.fingerprint}), closest to "
        f"libjpeg quality {q.closest_quality}; the encoder was not plain libjpeg.",
    )


def _provenance_checks(report: ImageReport) -> list[Finding]:
    prov = report.provenance
    out: list[Finding] = []
    if prov.c2pa:
        who = f" Claim generator: {prov.generator}." if prov.generator else ""
        out.append(Finding(
            "flag", "c2pa_manifest",
            f"C2PA Content Credentials present ({prov.container}).{who} "
            "Check them with a C2PA verifier for the signer and edit history.",
        ))
    if prov.digital_source_type == "trainedAlgorithmicMedia":
        out.append(Finding(
            "flag", "ai_generated_marker",
            "IPTC digital source type says this media was created by a "
            "generative AI model (trainedAlgorithmicMedia).",
        ))
    elif prov.digital_source_type == "compositeWithTrainedAlgorithmicMedia":
        out.append(Finding(
            "flag", "ai_composite_marker",
            "IPTC digital source type says this media combines AI-generated "
            "and other content (compositeWithTrainedAlgorithmicMedia).",
        ))
    return out


CAMERA_BRANDS = (
    "canon", "nikon", "sony", "fujifilm", "olympus", "om digital", "panasonic",
    "leica", "pentax", "ricoh", "hasselblad", "sigma",
)



def _parse_offset(text: str) -> float | None:
    """'-04:00' -> -4.0"""
    try:
        sign = -1 if text.strip().startswith("-") else 1
        hours, minutes = text.strip().lstrip("+-").split(":")[:2]
        return sign * (int(hours) + int(minutes) / 60)
    except (ValueError, AttributeError):
        return None


def _gps_checks(report: ImageReport) -> list[Finding]:
    """Look for GPS data that disagrees with the rest of the EXIF."""
    if not report.gps_decimal:
        return []
    image = report.groups.get("Image", {})
    exif = report.groups.get("Exif", {})
    gps = report.groups.get("GPS", {})
    lat, lon = report.gps_decimal
    out: list[Finding] = []

    offset = _parse_offset(str(exif.get("OffsetTimeOriginal", "")))
    if offset is not None:
        # Clock offset vs. the longitude's solar offset, wrapping at the date line.
        diff = abs(offset - lon / 15) % 24
        diff = min(diff, 24 - diff)
        if diff > 3.5:
            out.append(Finding(
                "flag", "gps_timezone_mismatch",
                f"Capture time zone is UTC{exif.get('OffsetTimeOriginal')} but the GPS "
                f"longitude {lon} sits about {lon / 15:+.1f} h from UTC; the location "
                "or the time zone was changed.",
            ))

        from datetime import datetime, timedelta
        try:
            local = datetime.strptime(str(exif["DateTimeOriginal"]), "%Y:%m:%d %H:%M:%S")
            h, m, sec = (float(x) for x in gps["GPSTimeStamp"])
            gps_utc = datetime.strptime(str(gps["GPSDateStamp"]), "%Y:%m:%d") + timedelta(
                hours=h, minutes=m, seconds=sec)
            gap = abs((local - timedelta(hours=offset) - gps_utc).total_seconds())
            if gap > 3600:
                out.append(Finding(
                    "flag", "gps_time_mismatch",
                    f"GPS fix time ({gps['GPSDateStamp']} {h:02.0f}:{m:02.0f}:{sec:04.1f} UTC) "
                    f"is {gap / 3600:.1f} h away from the capture time; one of them was edited.",
                ))
        except (KeyError, TypeError, ValueError):
            pass

    make = str(image.get("Make", "")).lower()
    model = str(image.get("Model", "")).lower()
    if "apple" in make and "iphone" in model:
        missing = [k for k in ("GPSTimeStamp", "GPSHPositioningError") if k not in gps]
        if len(missing) == 2:
            out.append(Finding(
                "flag", "gps_incomplete",
                "EXIF names an iPhone, but the GPS block lacks the fix time and "
                "positioning error that iPhones record; the coordinates were likely "
                "written by hand.",
            ))
    return out


def _consistency_checks(report: ImageReport) -> list[Finding]:
    """Cross-check camera claims in EXIF against how the file was encoded."""
    image = report.groups.get("Image", {})
    exif = report.groups.get("Exif", {})
    make = str(image.get("Make", "")).strip()
    if not make:
        return []
    model = str(image.get("Model", "")).strip()
    camera = f"{make} {model}".strip()
    brand = make.lower()
    out: list[Finding] = []

    if "MakerNote" not in exif:
        out.append(Finding(
            "flag", "makernote_missing",
            f"EXIF names a {camera} but has no MakerNote. Cameras and phones write "
            "one, so the camera tags were added afterwards or software rewrote the "
            "file and dropped it.",
        ))

    q = report.jpeg_quant
    if q is None:
        return out
    known = q.known_as or ""
    if "WeChat" in known:
        out.append(Finding(
            "flag", "tables_contradict_exif",
            f"EXIF names a {camera}, but the quantization tables match WeChat "
            "compression, which strips EXIF. The camera tags were added after the "
            "image passed through WeChat.",
        ))
    elif known.startswith("Apple ImageIO") and "apple" not in brand:
        out.append(Finding(
            "flag", "tables_contradict_exif",
            f"EXIF names a {camera}, but the file was written by Apple's encoder "
            f"({known}). A {make} camera writes its own tables.",
        ))
    elif q.standard and (any(b in brand for b in CAMERA_BRANDS) or "apple" in brand):
        out.append(Finding(
            "flag", "tables_contradict_exif",
            f"EXIF names a {camera}, but the file uses standard libjpeg tables at "
            f"quality {q.quality}, typical of software re-saving. The camera's own "
            "encoder uses different tables.",
        ))
    return out


def run_checks(report: ImageReport) -> list[Finding]:
    if report.error:
        return [Finding("flag", "unreadable", f"Could not read file: {report.error}")]

    findings: list[Finding] = []
    for check in (_extension_check, _dimension_check, _quant_check):
        result = check(report)
        if result:
            findings.append(result)
    findings.extend(_provenance_checks(report))

    if not report.has_exif:
        if report.provenance.found:
            message = "No EXIF metadata."
        else:
            message = (
                "No EXIF metadata. Typical after screenshots, messaging apps or "
                "social-media re-encoding; it can also mean metadata was stripped "
                "on purpose."
            )
        findings.append(Finding("info", "no_exif", message))
        return findings

    image = report.groups.get("Image", {})
    exif = report.groups.get("Exif", {})

    software = str(image.get("Software", "")).strip()
    if software:
        if any(name in software.lower() for name in EDITORS):
            findings.append(Finding(
                "flag", "editor_software",
                f"Software tag names an image editor: {software!r}.",
            ))
        else:
            findings.append(Finding("info", "software", f"Software tag: {software!r}."))

    modified = image.get("DateTime")
    original = exif.get("DateTimeOriginal")
    if modified and original and modified != original:
        findings.append(Finding(
            "flag", "time_mismatch",
            f"DateTime ({modified}) differs from DateTimeOriginal ({original}); "
            "the file may have been saved again after capture.",
        ))

    if not image.get("Make") and not image.get("Model"):
        findings.append(Finding("info", "no_camera", "No camera Make/Model recorded."))

    if report.gps_decimal:
        lat, lon = report.gps_decimal
        findings.append(Finding(
            "flag", "gps_present",
            f"GPS coordinates embedded: {lat}, {lon}. "
            "Check before sharing this file publicly.",
        ))

    findings.extend(_gps_checks(report))
    findings.extend(_consistency_checks(report))
    return findings
