"""JPEG ghosts: the quality an image, or part of it, was saved at before.

Re-saving a JPEG at the quality it was last saved at changes it very little,
and the same holds for any earlier quality it passed through: the error
between the image and a re-save dips at each quality in its history. Farid
("Exposing digital forgeries from JPEG ghosts", 2009) called such a dip a
JPEG ghost. A region pasted in from a JPEG of lower quality keeps its own
ghost, so a dip that appears in one area but not in the rest of the picture
marks a splice, and a dip across the whole picture below the current
quality shows that the file was compressed at least twice.

A ghost only shows when the 8x8 block grid of the re-save lines up with the
grid of the earlier compression. A pasted patch usually lands off the grid,
so the grid search tries all 64 offsets. Ghosts also need the earlier
quality to be lower than the current one; a patch from a better JPEG leaves
none, and error level analysis is the tool for that case.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .imagefile import opened

QUALITIES = range(50, 101)       # qualities the region search works on
EXTRA_LOW = 10                   # the whole-image curve starts this much lower, so a
                                 # dip near 50 still has a left shoulder to stand out from
BLOCK = 16                       # analysis block, a multiple of the JPEG 8x8 grid
MARGIN = 4                       # a ghost must sit this far below the current quality
WINDOW = 6                       # qualities on each side a dip is measured against
CONTRAST = 0.35                  # normalised error difference from the rest of the picture
DIP = 0.03                       # and the block's own curve must turn there
MIN_BLOCKS = 6                   # connected ghost blocks needed to report a region
FLAT = 2.0                       # blocks whose error never exceeds this cannot be judged
GLOBAL_DEPTH = 0.15              # relative depth of a dip in the whole-image curve


@dataclass
class GhostResult:
    qualities: list[int]
    curve: list[float]                      # mean squared error per quality, whole image
    current_quality: int | None             # quality the file is saved at (None: not a JPEG)
    global_ghosts: list[tuple[int, float]]  # (quality, relative depth) dips across the image
    ghost_quality: int | None               # earlier quality of the strongest local ghost
    ghost_blocks: int                       # blocks that carry that ghost
    ghost_fraction: float                   # share of judged blocks carrying it
    box: tuple[int, int, int, int] | None   # ghost region, image pixels
    shift: tuple[int, int]                  # grid offset the ghost was found at
    block: int
    judged_fraction: float                  # share of blocks with enough detail to judge
    flagged: np.ndarray | None = None       # block grid of the ghost region
    searched_grid: bool = False
    source_format: str | None = None
    rgb: np.ndarray | None = field(default=None, repr=False)


def _resave(img: Image.Image, quality: int) -> np.ndarray:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return np.asarray(Image.open(buf).convert("RGB"), dtype=np.float32)


def _block_mean(a: np.ndarray, size: int) -> np.ndarray:
    h, w = a.shape
    gh, gw = h // size, w // size
    return a[:gh * size, :gw * size].reshape(gh, size, gw, size).mean(axis=(1, 3))


def _remove_dc(diff: np.ndarray) -> np.ndarray:
    """Subtract the mean of every 8x8 cell from a difference image.

    A smooth block changes under a re-save mostly through the rounding of its
    DC coefficient, which flips back and forth as the quality changes and
    would look like a ghost. Ghosts live in the AC detail, so the DC part goes.
    """
    h, w, c = diff.shape
    gh, gw = h // 8, w // 8
    cells = diff[:gh * 8, :gw * 8].reshape(gh, 8, gw, 8, c)
    cells = cells - cells.mean(axis=(1, 3), keepdims=True)
    out = np.zeros_like(diff)
    out[:gh * 8, :gw * 8] = cells.reshape(gh * 8, gw * 8, c)
    return out


def error_curves(rgb: np.ndarray, qualities, block: int = BLOCK,
                 shift: tuple[int, int] = (0, 0)) -> np.ndarray:
    """Mean squared re-save error per quality and block, shape (Q, rows, cols).

    shift = (x, y) drops that many pixels from the left and top first, which
    moves the 8x8 grid of the re-save against the image.
    """
    sx, sy = shift
    sub = rgb[sy:, sx:]
    img = Image.fromarray(sub)
    base = sub.astype(np.float32)
    out = []
    for q in qualities:
        d = _remove_dc(base - _resave(img, q))
        out.append(_block_mean((d ** 2).mean(axis=2), block))
    return np.stack(out)


def _dips(values: np.ndarray, window: int) -> np.ndarray:
    """How far each sample sits below the higher of its two neighbourhoods.

    The quality axis comes first. A positive result at q means the error is
    lower at q than it gets within `window` samples on both sides, which is
    what a ghost looks like; a monotone curve gives nothing positive.
    """
    n = values.shape[0]
    out = np.full(values.shape, -np.inf, dtype=np.float32)
    for i in range(1, n - 1):
        left = values[max(0, i - window):i].max(axis=0)
        right = values[i + 1:i + 1 + window].max(axis=0)
        out[i] = np.minimum(left, right) - values[i]
    return out


def _normalise(curves: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = curves.min(axis=0), curves.max(axis=0)
    judged = hi > FLAT
    return (curves - lo) / np.maximum(hi - lo, 1e-6), judged


def _harmonic(qa: int, qb: int) -> bool:
    """True when the tables at qb divide those at qa, so a ghost at qa echoes at qb.

    Quality 80 uses half the steps of quality 60, and 75 half those of 50;
    an image quantised at the coarse steps also sits on the fine ones, so the
    error dips again there. Most entries of the luma table must match, within
    rounding, for a factor of 2 or 3.
    """
    if qa >= qb:
        return False
    from .quant import STD_LUMA, scaled_table
    a = np.array(scaled_table(STD_LUMA, qa))
    b = np.array(scaled_table(STD_LUMA, qb))
    return any(np.mean(np.abs(a - k * b) <= 1) >= 0.5 for k in (2, 3))


def _global_ghosts(curve: np.ndarray, qualities: list[int], limit: int,
                   window: int) -> list[tuple[int, float]]:
    found = []
    for i, q in enumerate(qualities):
        lo, hi = max(0, i - window), min(len(curve), i + window + 1)
        if q > limit or i in (0, len(curve) - 1) or curve[i] > curve[lo:hi].min():
            continue
        left, right = curve[lo:i].max(), curve[i + 1:hi].max()
        # Depth relative to the lower of the two neighbouring peaks. Near the
        # start of the range the left side is cut short, so only the right counts.
        peak = right if i < window else min(left, right)
        depth = (peak - curve[i]) / peak if peak > 0 else 0.0
        if depth >= GLOBAL_DEPTH and (i < window or curve[i] < min(left, right)):
            found.append((q, round(float(depth), 2)))
    return [(q, d) for q, d in found if not any(_harmonic(qa, q) for qa, _ in found)]


def _local_ghosts(curves: np.ndarray, qualities: list[int], limit: int, window: int,
                  contrast: float = CONTRAST):
    """Blocks whose error curve departs from the rest of the picture.

    Returns the block grid flagged at each quality, shape (Q, rows, cols),
    and the sign of each departure: -1 where the block dips below the
    others (a ghost at that quality), +1 where it bumps above them (the
    rebound between an earlier quality and the current one).
    """
    norm, judged = _normalise(curves)
    allowed = np.array([q <= limit for q in qualities])
    if not judged.any() or not allowed.any():
        return np.zeros(curves.shape, bool), np.zeros(len(qualities), int)
    median = np.median(norm[:, judged], axis=1)[:, None, None]
    low = (_dips(norm, window) > DIP) & (median - norm > contrast)
    high = (_dips(-norm, window) > DIP) & (norm - median > contrast)
    low &= judged & allowed[:, None, None]
    high &= judged & allowed[:, None, None]
    sign = np.where(high.sum(axis=(1, 2)) > low.sum(axis=(1, 2)), 1, -1)
    flagged = np.where(sign[:, None, None] > 0, high, low)
    return flagged, sign


def _largest_component(mask: np.ndarray) -> np.ndarray:
    from .prnu import _largest_component as largest
    return largest(mask)


def _pick(flagged: np.ndarray, qualities: list[int]) -> int | None:
    """Index of the quality with the most ghost blocks, preferring a fundamental to its echo."""
    counts = flagged.sum(axis=(1, 2))
    if counts.max() < MIN_BLOCKS:
        return None
    best = int(np.argmax(counts))
    for i, q in enumerate(qualities):
        if counts[i] >= counts[best] / 2 and _harmonic(q, qualities[best]):
            return i
    return best


def current_quality(img: Image.Image, curve: np.ndarray, qualities: list[int]) -> int | None:
    """The quality the file is saved at: exact for libjpeg tables, else the curve's minimum."""
    if img.format not in ("JPEG", "MPO"):
        return None
    from .quant import analyze as quant
    info = quant(img)
    if info is not None and info.standard and info.quality in qualities:
        return info.quality
    return qualities[int(np.argmin(curve))]


def analyze(path: str | Path, qualities=QUALITIES, block: int = BLOCK,
            grid: bool = False) -> GhostResult:
    """Run the ghost analysis on one image file."""
    qualities = list(qualities)
    extra = [q for q in range(qualities[0] - EXTRA_LOW, qualities[0]) if q >= 1]
    with opened(path) as img:
        fmt = img.format
        rgb = np.asarray(img.convert("RGB"))
        if rgb.shape[0] < 2 * block + 8 or rgb.shape[1] < 2 * block + 8:
            raise ValueError("image too small for ghost analysis")
        curves = error_curves(rgb, extra + qualities, block)
        curve = curves.mean(axis=(1, 2))
        current = current_quality(img, curve, extra + qualities)
    limit = (current if current is not None else 100) - MARGIN
    glob = _global_ghosts(curve, extra + qualities, limit, WINDOW)
    curves = curves[len(extra):]

    flagged, sign = _local_ghosts(curves, qualities, limit, WINDOW)
    best = (int(flagged.sum(axis=(1, 2)).max()), (0, 0), qualities, curves, flagged, sign)
    if grid:
        # A ghost off the grid is faint, because the save on the other grid
        # blurred it; the offset with the most ghost blocks wins.
        coarse = list(range(qualities[0], limit + 1, 5))
        if len(coarse) >= 3:
            for sy in range(8):
                for sx in range(8):
                    if (sx, sy) == (0, 0):
                        continue
                    c = error_curves(rgb, coarse, block, (sx, sy))
                    f, s = _local_ghosts(c, coarse, limit, 1)
                    count = int(f.sum(axis=(1, 2)).max())
                    if count > best[0]:
                        best = (count, (sx, sy), coarse, c, f, s)
        if best[1] != (0, 0):
            # Refine the coarse hit with every quality at that offset.
            shift = best[1]
            c = error_curves(rgb, qualities, block, shift)
            f, s = _local_ghosts(c, qualities, limit, WINDOW)
            best = (int(f.sum(axis=(1, 2)).max()), shift, qualities, c, f, s)
    _, shift, qs, c, flagged, sign = best
    _, judged = _normalise(c)

    ghost_q, box, region, count, fraction = None, None, None, 0, 0.0
    qi = _pick(flagged, qs)
    if qi is not None:
        whole = flagged[qi].sum() >= judged.sum() / 2
        region = flagged[qi] if whole else _largest_component(flagged[qi])
        count = int(region.sum())
        if count >= MIN_BLOCKS:
            fraction = float(flagged[qi].sum() / max(1, judged.sum()))
            if sign[qi] < 0:
                ghost_q = qs[qi]
            else:
                # The bump marks the rebound; the earlier quality is where the
                # flagged blocks' own curve is lowest below it.
                norm, _ = _normalise(c)
                below = [i for i, q in enumerate(qs) if q < qs[qi]]
                mean = norm[:, region].mean(axis=1)
                ghost_q = qs[min(below, key=lambda i: mean[i])] if below else qs[qi]
            ii, jj = np.nonzero(region)
            sx, sy = shift
            box = (int(sx + jj.min() * block), int(sy + ii.min() * block),
                   int(sx + (jj.max() + 1) * block), int(sy + (ii.max() + 1) * block))
        else:
            region, count = None, 0

    return GhostResult(qualities=extra + qualities, curve=[round(float(v), 3) for v in curve],
                       current_quality=current, global_ghosts=glob, ghost_quality=ghost_q,
                       ghost_blocks=count, ghost_fraction=round(fraction, 3), box=box,
                       shift=shift, block=block, judged_fraction=round(float(judged.mean()), 3),
                       flagged=region, searched_grid=grid, source_format=fmt, rgb=rgb)


def render(res: GhostResult) -> Image.Image:
    """The picture dimmed, the ghost region tinted cyan and framed in yellow."""
    base = res.rgb.astype(np.float64)
    out = base * 0.55
    if res.flagged is not None:
        sx, sy = res.shift
        b = res.block
        gh, gw = res.flagged.shape
        mask = np.kron(res.flagged, np.ones((b, b), dtype=bool))
        region = out[sy:sy + gh * b, sx:sx + gw * b]
        src = base[sy:sy + gh * b, sx:sx + gw * b]
        cyan = np.array([40.0, 220.0, 255.0])
        region[mask] = src[mask] * 0.45 + cyan * 0.55
        out[sy:sy + gh * b, sx:sx + gw * b] = region
    pic = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    if res.box:
        d = ImageDraw.Draw(pic)
        d.rectangle(res.box, outline=(255, 220, 0), width=max(2, pic.width // 400))
    return pic


def sparkline(res: GhostResult) -> list[str]:
    """Two text lines: the whole-image error curve as bars, and the quality axis."""
    bars = "▁▂▃▄▅▆▇█"
    curve = np.array(res.curve)
    top = curve.max() or 1.0
    line = "".join(bars[min(7, int(v / top * 8))] for v in curve)
    axis = [" "] * len(res.qualities)
    for i, q in enumerate(res.qualities):
        if q % 10 == 0:
            label = str(q)
            start = min(i, len(axis) - len(label))     # keep the last label inside
            axis[start:start + len(label)] = label
    return [line, "".join(axis)]
