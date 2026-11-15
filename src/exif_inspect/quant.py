"""JPEG quantization table analysis.

Every JPEG stores the tables its encoder used to throw away detail. Encoders
built on libjpeg scale one standard pair of tables by a 1-100 "quality"
setting, so those files reveal the exact quality used. Other software
(Apple ImageIO, Photoshop, many phone cameras and apps) ships its own
tables, which act as a fingerprint of the program that saved the file last.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from PIL import Image

# Standard tables from the JPEG spec (Annex K), as used by libjpeg.
# Natural (row-major) order, which is how Pillow exposes them.
STD_LUMA = [
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
]
STD_CHROMA = [
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
] + [99] * 32

SUBSAMPLING = {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}

# Fingerprints of non-standard tables, filled from samples we collect.
# Maps fingerprint -> description of the software and setting.
# Each entry was measured from a real sample; see README for how.
KNOWN_TABLES: dict[str, str] = {
    # macOS `sips -s formatOptions N` (Apple ImageIO). An iPhone-cropped
    # screenshot matched the 90 entry, so iOS appears to share these tables.
    "39d931620c0c": "Apple ImageIO at quality 70",
    "c0b709b1210a": "Apple ImageIO at quality 80",
    "5719fc272f79": "Apple ImageIO at quality 90",
    "86df6d4b4b47": "Apple ImageIO at quality 95",
}


def scaled_table(base: list[int], quality: int) -> list[int]:
    """libjpeg's quality scaling (jcparam.c), baseline-limited to 1..255."""
    scale = 5000 // quality if quality < 50 else 200 - 2 * quality
    return [min(255, max(1, (v * scale + 50) // 100)) for v in base]


def fingerprint(tables: dict[int, list[int]]) -> str:
    raw = b"".join(bytes(min(v, 255) for v in tables[k]) for k in sorted(tables))
    return hashlib.sha256(raw).hexdigest()[:12]


@dataclass
class QuantInfo:
    tables: int
    standard: bool                 # exact libjpeg table at some quality
    quality: int | None            # exact quality when standard
    closest_quality: int           # best-fitting libjpeg quality
    mean_abs_diff: float           # distance to that fit (0 when standard)
    subsampling: str | None
    fingerprint: str
    known_as: str | None


def _distance(actual: dict[int, list[int]], quality: int) -> float:
    refs = [scaled_table(STD_LUMA, quality), scaled_table(STD_CHROMA, quality)]
    total, count = 0, 0
    for idx in sorted(actual)[:2]:
        ref = refs[min(idx, 1)]
        total += sum(abs(a - r) for a, r in zip(actual[idx], ref))
        count += 64
    return total / count


def analyze(img: Image.Image) -> QuantInfo | None:
    """Return quantization details for a JPEG, or None for other formats."""
    tables = getattr(img, "quantization", None)
    if not tables:
        return None
    tables = {k: list(v) for k, v in tables.items()}

    best_q, best_d = 1, float("inf")
    for q in range(1, 101):
        d = _distance(tables, q)
        if d < best_d:
            best_q, best_d = q, d

    sampling = None
    try:
        from PIL import JpegImagePlugin
        sampling = SUBSAMPLING.get(JpegImagePlugin.get_sampling(img))
    except Exception:
        pass

    fp = fingerprint(tables)
    standard = best_d == 0
    return QuantInfo(
        tables=len(tables),
        standard=standard,
        quality=best_q if standard else None,
        closest_quality=best_q,
        mean_abs_diff=round(best_d, 2),
        subsampling=sampling,
        fingerprint=fp,
        known_as=KNOWN_TABLES.get(fp),
    )
