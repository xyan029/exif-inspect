"""Simple forensic heuristics over extracted metadata.

These are hints for a human examiner. Metadata is easy to edit or strip,
so every finding here is a lead to follow up, never a conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass

from .extract import ImageReport

# Lower-case substrings of Software values written by common editors.
EDITORS = (
    "photoshop", "lightroom", "gimp", "affinity", "pixelmator", "snapseed",
    "picsart", "canva", "facetune", "meitu", "photoscape", "paint.net",
    "darktable", "rawtherapee", "capture one",
)


@dataclass
class Finding:
    level: str   # "info" or "flag"
    code: str    # stable machine-readable id
    message: str


def run_checks(report: ImageReport) -> list[Finding]:
    findings: list[Finding] = []

    if report.error:
        return [Finding("flag", "unreadable", f"Could not read file: {report.error}")]

    if not report.has_exif:
        findings.append(Finding(
            "info", "no_exif",
            "No EXIF metadata. Typical after screenshots, messaging apps or "
            "social-media re-encoding; it can also mean metadata was stripped on purpose.",
        ))
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
        findings.append(Finding(
            "info", "no_camera",
            "No camera Make/Model recorded.",
        ))

    if report.gps_decimal:
        lat, lon = report.gps_decimal
        findings.append(Finding(
            "flag", "gps_present",
            f"GPS coordinates embedded: {lat}, {lon}. "
            "Check before sharing this file publicly.",
        ))

    return findings
