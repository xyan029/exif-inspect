"""Two versions of a picture: how one sits inside the other, what changed, which came first.

Given two files, the smaller scene is searched for inside the larger one at
every position, at scales down to a fifth of the frame and at the four
rotations, with normalised cross-correlation (the same search that places
the EXIF thumbnail). The best placement gives the crop and the scale
factor between the two. The two are then aligned at full resolution and
compared block by block: regions that differ more than the resampling and
compression noise are the edits between the versions.

Which file is the parent is read from the evidence in order: the one
whose frame contains the other, else the one with more pixels, else the
one saved at higher JPEG quality, else the one with more of its metadata
left, else the one with the earlier modification time. Each reason is
reported, since a forger can upscale a crop or copy metadata across.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .imagefile import opened

from .extract import read_image
from .thumbnail import _ncc_map

ANALYSIS_SIDE = 600     # the container is searched at this size
MIN_SCALE = 0.2         # the other picture may cover as little as this of the container's width
MIN_NCC = 0.6           # below this the two pictures are not the same scene
MIN_NCC_SMALL = 0.75    # a placement covering under a quarter of the frame needs a closer match
MIN_SIDE = 40           # shorter side of the placed picture at analysis size, in pixels
SAME_NCC = 0.97         # above this, with full cover and equal size, they are the same picture
GOOD_NCC = 0.9          # a fit this close ends the search over orders and rotations
BLOCK = 16              # block size for the change map, in container pixels
CHANGE = 4.0            # a block changed when its mean difference exceeds this many spreads
MIN_DIFF = 10.0         # and at least this many grey levels
MIN_BLOCKS = 2          # connected changed blocks that make a region


@dataclass
class Placement:
    container: str                          # "a" or "b": the picture the other fits inside
    rotation: int                           # degrees the other was turned to fit
    box: tuple[int, int, int, int]          # where it sits, container pixels
    scale: float                            # other's pixels per container pixel (2 = upscaled twice)
    ncc: float
    cover: float                            # share of the container's area it covers


@dataclass
class CompareResult:
    a: str
    b: str
    size_a: tuple[int, int]
    size_b: tuple[int, int]
    placement: Placement | None
    same: bool                              # the same picture, within compression noise
    changes: list[tuple[int, int, int, int]]   # changed regions, container pixels
    changed_fraction: float
    parent: str | None                      # "a", "b" or None
    reasons: list[str] = field(default_factory=list)
    metadata: list[tuple[str, str, str]] = field(default_factory=list)   # (field, a, b)
    picture: Image.Image | None = field(default=None, repr=False)


def _grey(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float32)


def _shrink(img: Image.Image, side: int) -> tuple[Image.Image, float]:
    f = min(1.0, side / max(img.size))
    if f < 1.0:
        img = img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))), Image.LANCZOS)
    return img, f


def _search(container: np.ndarray, other: Image.Image, scales) -> tuple[float, tuple, float]:
    """Best (ncc, box, cover) for `other` placed inside `container` at the given width shares."""
    H, W = container.shape
    ratio = other.width / other.height
    full_w = min(W, H * ratio)
    best = (-1.0, (0, 0, W, H), 0.0)
    for k in scales:
        w = int(round(full_w * k))
        h = int(round(w / ratio))
        if w < 12 or h < 12 or w > W or h > H:
            continue
        if min(w, h) < MIN_SIDE:
            continue
        templ = _grey(other.resize((w, h), Image.LANCZOS))
        if templ.std() < 2.0:
            continue
        ncc = _ncc_map(container, templ)
        # Flat stretches of the container divide by next to nothing; a real match never exceeds 1.
        ncc = np.where(np.isfinite(ncc) & (ncc <= 1.0), ncc, -1.0)
        y, x = np.unravel_index(int(np.argmax(ncc)), ncc.shape)
        score = float(ncc[y, x])
        patch = container[y:y + h, x:x + w]
        if patch.std() < 2.0:
            continue
        cover = (w * h) / (W * H)
        if score < (MIN_NCC if cover >= 0.25 else MIN_NCC_SMALL):
            continue
        if score > best[0] + 0.01:
            best = (score, (int(x), int(y), int(x) + w, int(y) + h), cover)
    return best


def place(img_a: Image.Image, img_b: Image.Image) -> Placement | None:
    """Fit each picture inside the other at four rotations; keep the best."""
    best: tuple[float, Placement] | None = None
    for name, cont_img, other_img in (("a", img_a, img_b), ("b", img_b, img_a)):
        if best is not None and best[1].ncc >= GOOD_NCC:
            break                      # a close fit one way round settles it
        small, f = _shrink(cont_img, ANALYSIS_SIDE)
        cont = _grey(small)
        if cont.std() < 1.0:
            continue
        for rot in (0, 90, 180, 270):
            if best is not None and best[1].ncc >= GOOD_NCC:
                break
            other = other_img.rotate(rot, expand=True) if rot else other_img
            ncc, box, cover = _search(cont, other, np.linspace(1.0, MIN_SCALE, 33))
            if ncc <= 0:
                continue
            # Refine the scale around the hit.
            k0 = (box[2] - box[0]) / min(cont.shape[1], cont.shape[0] * other.width / other.height)
            fine = [k for k in np.linspace(k0 * 0.96, k0 * 1.04, 9) if MIN_SCALE * 0.9 <= k <= 1.0]
            ncc2, box2, cover2 = _search(cont, other, fine)
            if ncc2 > ncc:
                ncc, box, cover = ncc2, box2, cover2
            x0, y0, x1, y1 = (int(round(v / f)) for v in box)
            x1, y1 = min(x1, cont_img.width), min(y1, cont_img.height)
            scale = other.width / max(1, x1 - x0)
            cand = Placement(name, rot, (x0, y0, x1, y1), round(scale, 3), round(ncc, 3), round(cover, 3))
            key = ncc + 0.02 * cover            # on near ties prefer the fuller placement
            if best is None or key > best[0]:
                best = (key, cand)
    if best is None or best[1].ncc < MIN_NCC:
        return None
    return best[1]


def _changes(cont_img: Image.Image, other_img: Image.Image, p: Placement):
    """Blocks of the aligned pair that differ beyond the noise, as boxes in container pixels."""
    x0, y0, x1, y1 = p.box
    crop = cont_img.crop((x0, y0, x1, y1))
    other = other_img.rotate(p.rotation, expand=True) if p.rotation else other_img
    other = other.resize(crop.size, Image.LANCZOS)
    a = _grey(crop)
    b = _grey(other)
    # Match brightness and contrast, which editors and re-saves shift a little.
    if b.std() > 1e-6:
        b = (b - b.mean()) / b.std() * a.std() + a.mean()
    d = np.abs(a - b)
    h, w = d.shape
    gh, gw = h // BLOCK, w // BLOCK
    if gh == 0 or gw == 0:
        return [], 0.0, d
    blocks = d[:gh * BLOCK, :gw * BLOCK].reshape(gh, BLOCK, gw, BLOCK).mean(axis=(1, 3))
    med = float(np.median(blocks))
    spread = 1.4826 * float(np.median(np.abs(blocks - med))) + 1e-6
    flagged = (blocks > med + CHANGE * spread) & (blocks > MIN_DIFF)
    from .prnu import _largest_component
    boxes = []
    remaining = flagged.copy()
    while remaining.any():
        comp = _largest_component(remaining)
        if comp.sum() < MIN_BLOCKS:
            break
        ii, jj = np.nonzero(comp)
        boxes.append((x0 + int(jj.min()) * BLOCK, y0 + int(ii.min()) * BLOCK,
                      x0 + int(jj.max() + 1) * BLOCK, y0 + int(ii.max() + 1) * BLOCK))
        remaining &= ~comp
    return boxes, float(flagged.mean()), d


def _metadata(ra, rb) -> list[tuple[str, str, str]]:
    def field(r, group, key):
        return str(r.groups.get(group, {}).get(key, "")) if not r.error else ""
    rows = [
        ("format", f"{ra.format} {ra.size[0]}x{ra.size[1]}" if ra.size else "?",
         f"{rb.format} {rb.size[0]}x{rb.size[1]}" if rb.size else "?"),
        ("jpeg quality", _quality(ra), _quality(rb)),
        ("camera", f"{field(ra, 'Image', 'Make')} {field(ra, 'Image', 'Model')}".strip(),
         f"{field(rb, 'Image', 'Make')} {field(rb, 'Image', 'Model')}".strip()),
        ("software", field(ra, "Image", "Software"), field(rb, "Image", "Software")),
        ("captured", field(ra, "Exif", "DateTimeOriginal"), field(rb, "Exif", "DateTimeOriginal")),
        ("modified", field(ra, "Image", "DateTime"), field(rb, "Image", "DateTime")),
        ("thumbnail", "yes" if ra.thumbnail else "", "yes" if rb.thumbnail else ""),
        ("xmp", ra.xmp.creator_tool or ("yes" if ra.xmp.present else ""),
         rb.xmp.creator_tool or ("yes" if rb.xmp.present else "")),
        ("gps", "yes" if ra.gps_decimal else "", "yes" if rb.gps_decimal else ""),
    ]
    return [(k, va, vb) for k, va, vb in rows if va or vb]


def _quality(r) -> str:
    q = r.jpeg_quant
    if q is None:
        return ""
    return f"{q.quality}" if q.standard else f"~{q.closest_quality} ({q.known_as or 'custom'})"


def _parent(ra, rb, p: Placement | None, same_size: bool) -> tuple[str | None, list[str]]:
    reasons = []
    if p is not None and p.cover < 0.97:
        other = "b" if p.container == "a" else "a"
        reasons.append(f"{p.container.upper()} shows more of the scene; {other.upper()} covers "
                       f"{p.cover:.0%} of it")
        return p.container, reasons
    pa, pb = ra.size[0] * ra.size[1], rb.size[0] * rb.size[1]
    if pa != pb:
        parent = "a" if pa > pb else "b"
        reasons.append(f"{parent.upper()} has more pixels")
        return parent, reasons
    qa, qb = ra.jpeg_quant, rb.jpeg_quant
    if qa and qb and qa.closest_quality != qb.closest_quality:
        parent = "a" if qa.closest_quality > qb.closest_quality else "b"
        reasons.append(f"{parent.upper()} was saved at the higher JPEG quality "
                       f"({max(qa.closest_quality, qb.closest_quality)} against "
                       f"{min(qa.closest_quality, qb.closest_quality)})")
        return parent, reasons
    ma = sum(len(g) for g in ra.groups.values()) + (20 if ra.thumbnail else 0)
    mb = sum(len(g) for g in rb.groups.values()) + (20 if rb.thumbnail else 0)
    if ma != mb:
        parent = "a" if ma > mb else "b"
        reasons.append(f"{parent.upper()} kept more of its metadata")
        return parent, reasons
    da = str(ra.groups.get("Image", {}).get("DateTime", ""))
    db = str(rb.groups.get("Image", {}).get("DateTime", ""))
    if da and db and da != db:
        parent = "a" if da < db else "b"
        reasons.append(f"{parent.upper()} was modified earlier ({min(da, db)})")
        return parent, reasons
    reasons.append("nothing tells the two apart")
    return None, reasons


def compare(path_a: str | Path, path_b: str | Path) -> CompareResult:
    ra, rb = read_image(path_a), read_image(path_b)
    if ra.error:
        raise ValueError(f"{path_a}: {ra.error}")
    if rb.error:
        raise ValueError(f"{path_b}: {rb.error}")
    with opened(path_a) as ia, opened(path_b) as ib:
        img_a, img_b = ia.convert("RGB"), ib.convert("RGB")
    p = place(img_a, img_b)
    changes, fraction, diff = [], 0.0, None
    same = False
    if p is not None:
        cont, other = (img_a, img_b) if p.container == "a" else (img_b, img_a)
        changes, fraction, diff = _changes(cont, other, p)
        same_size = img_a.size == img_b.size or img_a.size == img_b.size[::-1]
        same = p.ncc >= SAME_NCC and p.cover >= 0.97 and same_size and not changes
    parent, reasons = (_parent(ra, rb, p, img_a.size == img_b.size) if p is not None else (None, []))
    if same:
        reasons = ["the pictures are the same within compression noise"] + reasons
    picture = _render(img_a, img_b, p, changes, diff) if p is not None else None
    return CompareResult(str(path_a), str(path_b), img_a.size, img_b.size, p, same, changes,
                         round(fraction, 3), parent, reasons, _metadata(ra, rb), picture)


def _render(img_a, img_b, p: Placement, changes, diff) -> Image.Image:
    """Container with the other's frame (cyan) and changed regions (orange), the other, the difference."""
    cont, other = (img_a, img_b) if p.container == "a" else (img_b, img_a)
    left = cont.copy()
    d = ImageDraw.Draw(left)
    lw = max(2, left.width // 400)
    d.rectangle(p.box, outline=(40, 220, 255), width=lw)
    for box in changes:
        d.rectangle(box, outline=(255, 140, 0), width=lw)
    height = min(left.height, 900)
    panels = [_fit(left, height), _fit(other, height)]
    if diff is not None:
        scale = min(1.0, 255.0 / max(1.0, float(np.percentile(diff, 99.5))))
        heat = Image.fromarray(np.clip(diff * scale, 0, 255).astype(np.uint8)).convert("RGB")
        panels.append(_fit(heat, height))
    width = sum(p_.width for p_ in panels) + 10 * (len(panels) - 1)
    out = Image.new("RGB", (width, height), "white")
    x = 0
    for panel in panels:
        out.paste(panel, (x, 0))
        x += panel.width + 10
    return out


def _fit(img: Image.Image, height: int) -> Image.Image:
    if img.height == height:
        return img
    return img.resize((max(1, round(img.width * height / img.height)), height), Image.LANCZOS)
