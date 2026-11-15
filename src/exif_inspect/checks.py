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


def run_checks(report: ImageReport) -> list[Finding]:
    if report.error:
        return [Finding("flag", "unreadable", f"Could not read file: {report.error}")]

    findings: list[Finding] = []
    for check in (_extension_check, _dimension_check):
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

    return findings
