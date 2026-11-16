"""Traces of colour filter array (CFA) demosaicing, and where they are missing.

A camera sensor records one colour per pixel through a Bayer filter, half
of them green on a checkerboard. The demosaicing step fills in the other
half of the green channel from its neighbours. The filled-in pixels are
therefore predicted much better by their neighbours than the measured
ones, and the two lattices of the checkerboard have very different
prediction residuals (Ferrara, Bianchi, De Rosa and Piva, "Image forgery
localization via fine-grained analysis of CFA artifacts", 2012).

The tool predicts every green pixel from its four neighbours, squares the
residual, and compares its mean on the two lattices block by block. The
log ratio L is far from zero wherever the picture came straight from a
sensor through demosaicing, with a sign that says which lattice was
measured. A region pasted from another picture, resized, rotated,
generated, or shifted by an odd number of pixels has L near zero or of
the other sign. Blocks too flat to carry a residual cannot be judged.

Strong JPEG compression, noise reduction and resizing of the whole
picture weaken or erase the trace, so a picture without any trace is not
suspicious by itself; a region that lacks a trace the rest of the picture
carries is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

BLOCK = 32              # analysis block in pixels (even, so lattices balance)
MIN_RESIDUAL = 1.0      # mean squared residual a block needs to be judged at all
STRONG = 0.5            # |L| that counts as a clear trace
AGREE = 0.75            # share of blocks sharing one sign that counts as a weak trace
Z_AGREE = 5.0           # and the sign test must be this significant
FLAG = 0.15             # a block with L * sign(L_picture) below this lacks the trace
Z_FLAG = 3.0            # and must sit this many spreads below what its power predicts
MIN_BLOCKS = 4          # connected flagged blocks that make a region
BINS = 8                # power bins used to learn how L depends on residual power
MAX_SIDE = 4000         # larger pictures are analysed on a centre crop of this side


@dataclass
class CfaResult:
    strength: float                  # median L over usable blocks, signed
    agreement: float                 # share of usable blocks whose L has the majority sign
    trace: str                       # "strong", "weak" or "none"
    lattice: str                     # which green lattice was interpolated
    judged_fraction: float           # share of blocks where the trace should be visible
    flagged_fraction: float          # share of those lacking the trace
    box: tuple[int, int, int, int] | None
    block: int
    offset: tuple[int, int]
    lmap: np.ndarray = field(repr=False)        # block grid of L
    expected: np.ndarray = field(repr=False)    # L predicted from each block's power
    judged: np.ndarray = field(repr=False)
    flagged: np.ndarray = field(repr=False)     # blocks in the reported region
    size: tuple[int, int] = (0, 0)
    rgb: np.ndarray | None = field(default=None, repr=False)


def residual(green: np.ndarray) -> np.ndarray:
    """Green minus the mean of its four neighbours, zero on the border."""
    g = green.astype(np.float32)
    out = np.zeros_like(g)
    out[1:-1, 1:-1] = g[1:-1, 1:-1] - (g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]) / 4
    return out


def lattice_ratio(green: np.ndarray, block: int = BLOCK, valid: np.ndarray | None = None):
    """Per block: log ratio of mean squared residual on the two checkerboard lattices.

    Pixels where `valid` is False (clipped ones, whose residual says nothing)
    are left out of both means. Returns the L grid, the mean squared
    residual per block and the share of valid pixels per block. Lattice A
    holds pixels with x + y even.
    """
    e2 = residual(green) ** 2
    h, w = e2.shape
    gh, gw = h // block, w // block
    e2 = e2[:gh * block, :gw * block]
    ok = np.ones_like(e2) if valid is None else valid[:gh * block, :gw * block].astype(np.float32)
    yy, xx = np.mgrid[0:gh * block, 0:gw * block]
    a = ((xx + yy) % 2 == 0).astype(np.float32)
    cells = e2.reshape(gh, block, gw, block)
    ok = ok.reshape(gh, block, gw, block)
    mask = a.reshape(gh, block, gw, block) * ok
    other = (1 - a.reshape(gh, block, gw, block)) * ok
    va = (cells * mask).sum(axis=(1, 3)) / np.maximum(mask.sum(axis=(1, 3)), 1)
    vb = (cells * other).sum(axis=(1, 3)) / np.maximum(other.sum(axis=(1, 3)), 1)
    lmap = np.log((va + 1e-3) / (vb + 1e-3))
    return lmap, (va + vb) / 2, ok.mean(axis=(1, 3))


def _largest_component(mask: np.ndarray) -> np.ndarray:
    from .prnu import _largest_component as largest
    return largest(mask)


def expected_ratio(lmap: np.ndarray, power: np.ndarray, usable: np.ndarray) -> np.ndarray:
    """L predicted from residual power alone.

    JPEG quantisation and sensor noise add the same error to both lattices,
    so the ratio shrinks toward zero where the picture's own residual is
    small. The median L in each power bin, interpolated over log power,
    gives what an untouched block of that power should show.
    """
    out = np.zeros_like(lmap)
    if usable.sum() < 2 * BINS:
        out[usable] = np.median(lmap[usable]) if usable.any() else 0.0
        return out
    logp = np.log(power + 1e-6)
    edges = np.quantile(logp[usable], np.linspace(0, 1, BINS + 1))
    centres, medians = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = usable & (logp >= lo) & (logp <= hi)
        if sel.sum() >= 3:
            centres.append(float(np.median(logp[sel])))
            medians.append(float(np.median(lmap[sel])))
    # More residual power can only make the trace clearer, so the prediction
    # never drops with power. This also keeps a tampered region that happens
    # to fill the busiest bin from setting its own expectation.
    medians = list(np.maximum.accumulate(medians)) if medians else [0.0]
    centres = centres or [float(np.median(logp[usable]))]
    out[usable] = np.interp(logp[usable], centres, medians)
    return out


def analyze(path: str | Path, block: int = BLOCK) -> CfaResult:
    """Map where the picture carries demosaicing traces and where it does not."""
    with Image.open(path) as img:
        rgb = np.asarray(img.convert("RGB"))
    h, w = rgb.shape[:2]
    oy = max(0, (h - MAX_SIDE) // 2) & ~1     # even offsets keep the lattice parity
    ox = max(0, (w - MAX_SIDE) // 2) & ~1
    crop = rgb[oy:oy + MAX_SIDE, ox:ox + MAX_SIDE]
    green = crop[:, :, 1]
    # A clipped pixel and its neighbours carry no interpolation residual.
    clipped = (green >= 250) | (green <= 5)
    valid = ~clipped
    valid[1:] &= ~clipped[:-1]
    valid[:-1] &= ~clipped[1:]
    valid[:, 1:] &= ~clipped[:, :-1]
    valid[:, :-1] &= ~clipped[:, 1:]
    lmap, power, valid_frac = lattice_ratio(green, block, valid)
    usable = (power >= MIN_RESIDUAL) & (valid_frac >= 0.5)

    strength = float(np.median(lmap[usable])) if usable.any() else 0.0
    sign = 1.0 if strength >= 0 else -1.0
    n = int(usable.sum())
    agreement = float((np.sign(lmap[usable]) == sign).mean()) if n else 0.0
    z = (agreement * n - n / 2) / np.sqrt(n / 4) if n else 0.0
    if abs(strength) >= STRONG:
        trace = "strong"
    elif agreement >= AGREE and z >= Z_AGREE:
        trace = "weak"        # faint in every block, but the same way round almost everywhere
    else:
        trace = "none"
    lattice = ("x + y odd" if strength > 0 else "x + y even") if trace != "none" else "unknown"

    # Signed so that the picture's own trace is positive, then compared with
    # what each block's residual power predicts for an untouched block.
    signed = lmap * sign
    expected = expected_ratio(signed, power, usable)
    judged = usable & (expected >= STRONG)        # blocks where the trace should show
    resid = signed - expected
    spread = 1.4826 * float(np.median(np.abs(resid[usable] - np.median(resid[usable])))) + 1e-6 \
        if usable.any() else 1.0
    flagged_all = judged & (signed < FLAG) & (resid < -Z_FLAG * spread)
    region = np.zeros_like(judged)
    box = None
    flagged_fraction = float(flagged_all.sum() / max(1, judged.sum()))
    if flagged_all.any():
        region = _largest_component(flagged_all)
        if region.sum() < MIN_BLOCKS:
            region = np.zeros_like(judged)
        else:
            ii, jj = np.nonzero(region)
            box = (int(ox + jj.min() * block), int(oy + ii.min() * block),
                   int(ox + (jj.max() + 1) * block), int(oy + (ii.max() + 1) * block))
    return CfaResult(round(strength, 3), round(agreement, 3), trace, lattice,
                     round(float(judged.mean()), 3),
                     round(flagged_fraction, 3), box, block, (ox, oy), lmap, expected * sign,
                     judged, region, (w, h), rgb)


def render(res: CfaResult) -> Image.Image:
    """The picture dimmed where it cannot be judged, red where the trace is missing."""
    base = res.rgb.astype(np.float64)
    out = base * 0.55
    ox, oy = res.offset
    b = res.block
    gh, gw = res.lmap.shape
    judged = np.kron(res.judged, np.ones((b, b), dtype=bool))
    flagged = np.kron(res.flagged, np.ones((b, b), dtype=bool))
    region = out[oy:oy + gh * b, ox:ox + gw * b]
    src = base[oy:oy + gh * b, ox:ox + gw * b]
    region[judged] = src[judged]
    red = np.array([255.0, 40.0, 40.0])
    region[flagged] = src[flagged] * 0.35 + red * 0.65
    out[oy:oy + gh * b, ox:ox + gw * b] = region
    pic = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pic)
    lw = max(2, pic.width // 400)
    d.rectangle((ox, oy, ox + gw * b - 1, oy + gh * b - 1), outline=(255, 255, 255), width=lw)
    if res.box:
        d.rectangle(res.box, outline=(255, 220, 0), width=lw)
    return pic
