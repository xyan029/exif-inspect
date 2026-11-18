"""Error level analysis (ELA) for JPEG images.

Re-saving a JPEG at a fixed quality changes regions that have already been
compressed many times only a little, while regions with a different
compression history (for example a pasted patch) change more. The per-pixel
difference, amplified and colour-mapped, is the ELA heatmap.

ELA is a screening aid. Edges and fine texture naturally show higher error,
and repeated re-saving, high-quality saves or format conversion weaken the
signal, so compare regions of similar texture when reading the map.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

from .imagefile import opened

# Anchor colours for a dark-to-bright heatmap (black -> purple -> orange -> yellow).
_ANCHORS = [
    (0.00, (0, 0, 4)),
    (0.25, (87, 16, 110)),
    (0.50, (188, 55, 84)),
    (0.75, (249, 142, 9)),
    (1.00, (252, 255, 164)),
]


def _build_palette() -> list[int]:
    """Linear interpolation between anchor colours, as a flat 256*3 palette."""
    palette: list[int] = []
    for i in range(256):
        t = i / 255
        for (t0, c0), (t1, c1) in zip(_ANCHORS, _ANCHORS[1:]):
            if t0 <= t <= t1:
                w = (t - t0) / (t1 - t0)
                palette.extend(round(a + (b - a) * w) for a, b in zip(c0, c1))
                break
    return palette


_PALETTE = _build_palette()


@dataclass
class ElaResult:
    heatmap: Image.Image   # colour (or grayscale) visualisation
    error: Image.Image     # raw per-pixel error, mode "L"
    mean_error: float
    max_error: int
    scale: float           # amplification applied to make the heatmap
    source_format: str | None


def _percentile_from_histogram(hist: list[int], pct: float) -> int:
    total = sum(hist)
    if total == 0:
        return 0
    target = total * pct / 100
    running = 0
    for value, count in enumerate(hist):
        running += count
        if running >= target:
            return value
    return len(hist) - 1


def error_level(img: Image.Image, quality: int = 90) -> Image.Image:
    """Per-pixel maximum absolute difference over RGB after one re-save."""
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    resaved = Image.open(buf).convert("RGB")
    r, g, b = ImageChops.difference(rgb, resaved).split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def run_ela(
    path: str | Path,
    quality: int = 90,
    scale: float | None = None,
    gray: bool = False,
) -> ElaResult:
    """Compute an ELA heatmap for one image file.

    scale=None picks an amplification that maps the 99.5th percentile of the
    error to full brightness, so a few extreme pixels do not wash out the map.
    """
    with opened(path) as img:
        fmt = img.format
        error = error_level(img, quality)

    hist = error.histogram()
    total = sum(hist) or 1
    mean_error = sum(v * c for v, c in enumerate(hist)) / total
    max_error = max((v for v, c in enumerate(hist) if c), default=0)

    if scale is None:
        ref = _percentile_from_histogram(hist, 99.5)
        scale = 255 / ref if ref > 0 else 1.0

    amplified = error.point(lambda v: min(255, round(v * scale)))
    if gray:
        heatmap = amplified
    else:
        heatmap = amplified.copy()
        heatmap.putpalette(_PALETTE)  # turns the "L" image into mode "P"
        heatmap = heatmap.convert("RGB")

    return ElaResult(heatmap, error, round(mean_error, 3), max_error, round(scale, 3), fmt)


def side_by_side(original_path: str | Path, heatmap: Image.Image) -> Image.Image:
    """Original on the left, heatmap on the right."""
    with opened(original_path) as img:
        left = img.convert("RGB")
    right = heatmap.convert("RGB").resize(left.size)
    canvas = Image.new("RGB", (left.width * 2, left.height), "white")
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width, 0))
    return canvas
