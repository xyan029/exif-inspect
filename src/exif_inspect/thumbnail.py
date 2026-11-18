"""Compare the EXIF thumbnail with the main image.

Cameras and phones store a small preview (often 160x120) inside EXIF, in the
second image directory (IFD1). Some editors crop, retouch or blur the main
image and leave that preview untouched, so the preview can still show what
was cut away or painted over. This module:

1. pulls the thumbnail JPEG out of the raw EXIF block,
2. searches the thumbnail for the main image at a range of sizes, turns and
   positions (normalised cross-correlation), which finds crops,
3. when the main image covers the whole thumbnail, compares the two block by
   block and marks blocks that differ much more than the rest.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .imagefile import opened

SIDE = 160          # working size of the longest thumbnail side
BLOCK = 8           # comparison block, in working pixels
CROP_COVER = 0.9    # matched area below this share of the thumbnail = crop
MIN_NCC = 0.6       # weaker matches are reported as "does not match"
CROP_NCC = 0.85     # a crop claim needs a closer match than a full-frame one
MIN_SCALE = 0.45    # smallest crop searched, as a share of the thumbnail width


@dataclass
class ThumbInfo:
    size: tuple[int, int]                 # thumbnail pixels as stored
    rotation: int = 0                     # degrees the thumbnail was turned to fit
    match: float = 0.0                    # correlation of the best placement, -1..1
    cover: float = 1.0                    # share of the thumbnail the main image fills
    crop_box: tuple[float, float, float, float] | None = None  # thumbnail fractions
    partial: bool = False                 # thumbnail shows only part of the main image
    changed: float = 0.0                  # share of blocks that differ
    changed_box: tuple[int, int, int, int] | None = None       # main-image pixels
    diff_map: np.ndarray | None = field(default=None, repr=False)

    @property
    def cropped(self) -> bool:
        return self.crop_box is not None

    @property
    def unrelated(self) -> bool:
        return self.match < MIN_NCC


def thumbnail_bytes(raw_exif: bytes | None) -> bytes | None:
    """Return the JPEG thumbnail stored in IFD1, or None."""
    if not raw_exif:
        return None
    tiff = raw_exif[6:] if raw_exif.startswith(b"Exif\x00\x00") else raw_exif
    if tiff[:2] == b"II":
        e = "<"
    elif tiff[:2] == b"MM":
        e = ">"
    else:
        return None
    try:
        (ifd0,) = struct.unpack(e + "I", tiff[4:8])
        (n,) = struct.unpack(e + "H", tiff[ifd0:ifd0 + 2])
        (ifd1,) = struct.unpack(e + "I", tiff[ifd0 + 2 + 12 * n:ifd0 + 6 + 12 * n])
        if not ifd1 or ifd1 + 2 > len(tiff):
            return None
        (n1,) = struct.unpack(e + "H", tiff[ifd1:ifd1 + 2])
        tags = {}
        for i in range(n1):
            entry = tiff[ifd1 + 2 + 12 * i:ifd1 + 14 + 12 * i]
            tag, typ, _count = struct.unpack(e + "HHI", entry[:8])
            value = struct.unpack(e + ("H" if typ == 3 else "I"), entry[8:10] if typ == 3 else entry[8:12])[0]
            tags[tag] = value
    except struct.error:
        return None
    start, length = tags.get(0x0201), tags.get(0x0202)
    if not start or not length:
        return None
    data = tiff[start:start + length]
    return data if data[:2] == b"\xff\xd8" else None


def _grey(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float64)


def _trim_bars(a: np.ndarray) -> np.ndarray:
    """Drop the black bars some cameras add to fit a fixed 160x120 frame."""
    def bar(line: np.ndarray) -> bool:
        return line.mean() < 16 and line.std() < 4
    top, bottom, left, right = 0, a.shape[0], 0, a.shape[1]
    while bottom - top > 8 and bar(a[top]):
        top += 1
    while bottom - top > 8 and bar(a[bottom - 1]):
        bottom -= 1
    while right - left > 8 and bar(a[top:bottom, left]):
        left += 1
    while right - left > 8 and bar(a[top:bottom, right - 1]):
        right -= 1
    return a[top:bottom, left:right]


def _ncc_map(image: np.ndarray, templ: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of templ at every position inside image."""
    th, tw = templ.shape
    t = templ - templ.mean()
    tn = np.sqrt((t ** 2).sum())
    if tn == 0:
        return np.full((image.shape[0] - th + 1, image.shape[1] - tw + 1), -1.0)
    H, W = image.shape
    fshape = (H + th, W + tw)
    corr = np.fft.irfft2(np.fft.rfft2(image, fshape) * np.fft.rfft2(t[::-1, ::-1], fshape), fshape)
    corr = corr[th - 1:H, tw - 1:W]
    ii = np.pad(image, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    ii2 = np.pad(image ** 2, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def box(s):
        return s[th:, tw:] - s[:-th, tw:] - s[th:, :-tw] + s[:-th, :-tw]
    n = th * tw
    var = box(ii2) - box(ii) ** 2 / n
    return corr / (np.sqrt(np.maximum(var, 1e-9)) * tn)


def _place(container: np.ndarray, templ_img: Image.Image, min_scale: float = MIN_SCALE):
    """Best (ncc, cover, box, texture) for templ_img placed inside container."""
    H, W = container.shape
    ratio = templ_img.width / templ_img.height
    best = (-1.0, 0.0, (0, 0, W, H), 0.0)
    full_w = min(W, H * ratio)
    for k in np.linspace(1.0, min_scale, 28):
        w = int(round(full_w * k))
        h = int(round(w / ratio))
        if w < 12 or h < 12 or w > W or h > H:
            continue
        templ = _grey(templ_img.resize((w, h), Image.LANCZOS))
        ncc = _ncc_map(container, templ)
        y, x = np.unravel_index(int(np.argmax(ncc)), ncc.shape)
        score = float(ncc[y, x])
        if score > best[0] + 0.02:   # prefer the larger placement on near ties
            best = (score, (w * h) / (W * H), (int(x), int(y), int(x) + w, int(y) + h),
                    float(templ.std()))
    return best


def compare(main_img: Image.Image, thumb_img: Image.Image) -> ThumbInfo:
    info = ThumbInfo(size=thumb_img.size)
    base = _trim_bars(_grey(thumb_img))
    scale = SIDE / max(base.shape)
    if scale < 1:
        base = _grey(Image.fromarray(base.astype(np.uint8)).resize(
            (max(1, round(base.shape[1] * scale)), max(1, round(base.shape[0] * scale))), Image.LANCZOS))
    small = main_img.convert("L")
    small.thumbnail((SIDE * 2, SIDE * 2), Image.LANCZOS)

    best = None
    for rot in (0, 90, 270, 180):
        t = np.rot90(base, k=rot // 90)
        ncc, cover, box, texture = _place(t, small)
        if best is None or ncc > best[0] + 0.02:
            best = (ncc, cover, box, texture, rot, t)
    ncc, cover, box, texture, rot, t = best
    info.rotation, info.match, info.cover = rot, round(ncc, 3), round(cover, 3)
    H, W = t.shape
    if ncc < MIN_NCC:
        # Some cameras store only the centre of the frame as the thumbnail:
        # look for the thumbnail inside the main image before calling them
        # unrelated.
        inner = Image.fromarray(np.clip(np.rot90(base, k=rot // 90), 0, 255).astype(np.uint8))
        r_ncc, *_ = _place(_grey(small), inner)
        if r_ncc >= MIN_NCC:
            info.match = round(r_ncc, 3)
            info.partial = True
        return info
    if cover < CROP_COVER:
        if ncc >= CROP_NCC and texture >= 12:
            x0, y0, x1, y1 = box
            info.crop_box = (x0 / W, y0 / H, x1 / W, y1 / H)
        return info

    # Same framing: compare block by block after matching brightness and contrast.
    x0, y0, x1, y1 = box
    region = t[y0:y1, x0:x1]
    m = _grey(small.resize((x1 - x0, y1 - y0), Image.LANCZOS))
    m = (m - m.mean()) / (m.std() + 1e-9) * region.std() + region.mean()
    blur = ImageFilter.GaussianBlur(1)
    a = _grey(Image.fromarray(np.clip(region, 0, 255).astype(np.uint8)).filter(blur))
    b = _grey(Image.fromarray(np.clip(m, 0, 255).astype(np.uint8)).filter(blur))
    diff = np.abs(a - b)
    gh, gw = diff.shape[0] // BLOCK, diff.shape[1] // BLOCK
    if gh == 0 or gw == 0:
        return info
    blocks = diff[:gh * BLOCK, :gw * BLOCK].reshape(gh, BLOCK, gw, BLOCK).mean(axis=(1, 3))
    med = float(np.median(blocks))
    mad = float(np.median(np.abs(blocks - med))) + 0.5
    hot = (blocks > max(14.0, med + 8 * mad))
    info.diff_map = diff
    info.changed = round(float(hot.mean()), 3)
    if hot.sum() >= 2:
        ys, xs = np.nonzero(hot)
        fx = main_img.width / (gw * BLOCK)
        fy = main_img.height / (gh * BLOCK)
        info.changed_box = (int(xs.min() * BLOCK * fx), int(ys.min() * BLOCK * fy),
                            int((xs.max() + 1) * BLOCK * fx), int((ys.max() + 1) * BLOCK * fy))
    return info


def analyze(img: Image.Image) -> ThumbInfo | None:
    """Thumbnail comparison for an open image, or None when it has no thumbnail."""
    data = thumbnail_bytes(img.info.get("exif"))
    if data is None:
        return None
    try:
        thumb = Image.open(io.BytesIO(data))
        thumb.load()
    except Exception:
        return None
    info = compare(img, thumb)
    info.diff_map = None   # keep reports small and JSON-friendly
    return info


def render(path: str | Path) -> tuple[Image.Image, Image.Image] | None:
    """(thumbnail JPEG as stored, side-by-side picture) or None."""
    with opened(path) as img:
        data = thumbnail_bytes(img.info.get("exif"))
        if data is None:
            return None
        thumb = Image.open(io.BytesIO(data))
        thumb.load()
        info = compare(img, thumb)
        main = img.convert("RGB")
    h = 360
    t = thumb.convert("RGB").rotate(info.rotation, expand=True)
    t = t.resize((round(t.width * h / t.height), h), Image.NEAREST)
    m = main.resize((round(main.width * h / main.height), h), Image.LANCZOS)
    d = ImageDraw.Draw(t)
    if info.crop_box:
        x0, y0, x1, y1 = info.crop_box
        d.rectangle((x0 * t.width, y0 * t.height, x1 * t.width, y1 * t.height),
                    outline=(0, 220, 255), width=3)
    panels = [t, m]
    if info.diff_map is not None:
        dm = np.clip(info.diff_map * 4, 0, 255).astype(np.uint8)
        heat = Image.fromarray(dm).resize((m.width, h), Image.NEAREST).convert("RGB")
        if info.changed_box:
            fx, fy = m.width / main.width, h / main.height
            x0, y0, x1, y1 = info.changed_box
            ImageDraw.Draw(heat).rectangle((x0 * fx, y0 * fy, x1 * fx, y1 * fy),
                                           outline=(255, 140, 0), width=3)
        panels.append(heat)
    gap = 8
    out = Image.new("RGB", (sum(p.width for p in panels) + gap * (len(panels) - 1), h), "white")
    x = 0
    for p in panels:
        out.paste(p, (x, 0))
        x += p.width + gap
    return data, out
