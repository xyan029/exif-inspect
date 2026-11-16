"""Local noise level, and regions whose noise does not match the rest.

A sensor adds noise to every pixel it records, and in one exposure that
noise has one level, rising gently with brightness. A region pasted in
from another photograph brings its own level, and a region that was
smoothed, painted or filled by software has almost none. Comparing the
noise block by block therefore shows seams that the eye cannot see
(Mahdian and Saic, "Using noise inconsistencies for blind image
forensics", 2009).

The noise in each 32-pixel block is the smallest eigenvalue of the
covariance of its 5x5 patches that lie on flat pixels: the picture's
content lives in the leading components, noise alone reaches every
component, so the smallest one is the noise floor (Pyatykh, Hesser and
Zheng, 2013; weak-texture patch selection after Liu, Tanaka and Okutomi,
2013). Noise depends on brightness and some texture still leaks in, so
the level an untouched block like this one should show is learned from
the picture itself, by texture and brightness, and a block well above or
below that prediction is flagged. Busy blocks without enough flat
patches cannot be judged.

JPEG compression removes part of the noise and adds its own blocking, so
the levels of a compressed file are not the sensor's; a difference between
regions survives moderate compression, a weak one does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

BLOCK = 32              # analysis block in pixels
PATCH = 5               # patch side for the covariance estimate
MIN_PATCHES = 150       # weak-texture patches a block needs for an estimate
MIN_FLAT = 0.1          # share of a block's pixels that must pass the edge test
FLAT_GRAD = 6.0         # gradient of the blurred picture below which a pixel counts as flat
RATIO = 1.8             # a block must differ from its prediction by this factor
TEXTURE_BINS = 6        # texture bins for the expected level; texture leaks into any estimate
BRIGHT_BINS = 3         # brightness bins inside each texture bin, when there are enough blocks
MIN_DIFF = 1.5          # and by this many grey levels of sigma
Z_FLAG = 3.0            # and by this many spreads of the picture's own scatter
MIN_BLOCKS = 6          # connected flagged blocks that make a region
BUSY = 0.9              # blocks above this texture quantile are too busy to judge
BINS = 8                # brightness bins for the noise-versus-brightness curve
MAX_SIDE = 4000         # larger pictures are analysed on a centre crop of this side

# Immerkaer's mask: zero response to constant and linear ramps, 6 sigma for white noise.
_MASK = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)
_MASK_GAIN = 6.0
_MAD_TO_SIGMA = 1 / 0.6745


@dataclass
class NoiseResult:
    level: float                     # median noise sigma over the quietest third of judged blocks
    rising: bool                     # whether the level grows with brightness
    judged_fraction: float
    box: tuple[int, int, int, int] | None
    kind: str | None                 # "noisier" or "smoother" than the rest
    ratio: float | None              # the region's noise over its predicted level
    block: int
    offset: tuple[int, int]
    sigma: np.ndarray = field(repr=False)       # block grid of noise sigma
    texture: np.ndarray = field(repr=False)     # block grid of content gradient, noise removed
    expected: np.ndarray = field(repr=False)
    judged: np.ndarray = field(repr=False)
    noisier: np.ndarray = field(repr=False)     # all flagged blocks of each kind
    smoother: np.ndarray = field(repr=False)
    flagged: np.ndarray = field(repr=False)     # blocks of the reported region
    size: tuple[int, int] = (0, 0)
    rgb: np.ndarray | None = field(default=None, repr=False)


def highpass(gray: np.ndarray) -> np.ndarray:
    """Absolute response of the Laplacian mask, zero on the border."""
    g = gray.astype(np.float32)
    out = np.zeros_like(g)
    out[1:-1, 1:-1] = np.abs(
        g[:-2, :-2] - 2 * g[:-2, 1:-1] + g[:-2, 2:]
        - 2 * g[1:-1, :-2] + 4 * g[1:-1, 1:-1] - 2 * g[1:-1, 2:]
        + g[2:, :-2] - 2 * g[2:, 1:-1] + g[2:, 2:])
    return out


def _gradient(gray: np.ndarray) -> np.ndarray:
    g = gray.astype(np.float32)
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = (g[:, 2:] - g[:, :-2]) / 2
    gy[1:-1, :] = (g[2:, :] - g[:-2, :]) / 2
    return np.hypot(gx, gy)


def _blur(a: np.ndarray) -> np.ndarray:
    k = np.array([1, 4, 6, 4, 1], dtype=np.float32) / 16
    p = np.pad(a, 2, mode="edge")
    rows = sum(k[i] * p[:, i:i + a.shape[1]] for i in range(5))
    return sum(k[i] * rows[i:i + a.shape[0], :] for i in range(5))


def block_sigma(gray: np.ndarray, block: int = BLOCK):
    """Per block: noise sigma, the share of flat pixels, the mean brightness, the texture.

    Every PATCH x PATCH window inside the block is a vector; the smallest
    eigenvalue of their covariance is the noise variance, because the
    picture's own content occupies the leading components and only noise
    spreads over all of them (Pyatykh, Hesser and Zheng, 2013). Texture
    therefore leaks far less than into a high-pass residual. The share of
    flat pixels (gradient below three times a first Immerkaer estimate plus
    a margin) is still reported, since a block with no quiet pixels at all
    is better left unjudged.
    """
    g = gray.astype(np.float32)
    h, w = g.shape
    gh, gw = h // block, w // block
    g = g[:gh * block, :gw * block]
    hp = highpass(g)
    rough = float(np.median(hp[1:-1, 1:-1])) * _MAD_TO_SIGMA / _MASK_GAIN
    # Edges and texture are measured on a copy blurred three times, where
    # noise is almost gone (its standard deviation falls to a fiftieth), so
    # that a noisier region is neither written off as all edges nor sorted
    # into a texture class of its own.
    smooth_grad = _gradient(_blur(_blur(_blur(g))))
    flat = smooth_grad < FLAT_GRAD + 0.3 * rough
    share = flat.reshape(gh, block, gw, block).mean(axis=(1, 3)).astype(np.float32)
    bright = g.reshape(gh, block, gw, block).mean(axis=(1, 3))
    # Content strength with the noise blurred away: what leaks into the estimate.
    texture = smooth_grad.reshape(gh, block, gw, block).mean(axis=(1, 3))

    n = block - PATCH + 1
    sigma = np.zeros((gh, gw), dtype=np.float32)
    dim = PATCH * PATCH
    for i in range(gh):
        rows = g[i * block:(i + 1) * block]
        frows = flat[i * block:(i + 1) * block]
        for j in range(gw):
            cell = rows[:, j * block:(j + 1) * block]
            patches = np.lib.stride_tricks.sliding_window_view(cell, (PATCH, PATCH))
            x = patches.reshape(n * n, dim).astype(np.float64)
            # Weak-texture patches only: an edge inside a patch would raise
            # every eigenvalue, so patches that are nearly all flat pixels
            # carry the estimate (Liu, Tanaka and Okutomi, 2013).
            fcount = np.lib.stride_tricks.sliding_window_view(
                frows[:, j * block:(j + 1) * block], (PATCH, PATCH)).reshape(n * n, dim).sum(axis=1)
            weak = fcount >= dim - 2
            if weak.sum() < MIN_PATCHES:
                continue
            x = x[weak]
            x = x - x.mean(axis=0)
            cov = x.T @ x / (len(x) - 1)
            lam = np.linalg.eigvalsh(cov)[0]
            sigma[i, j] = np.sqrt(max(lam, 0.0))
    return sigma, share, bright, texture


def expected_sigma(sigma: np.ndarray, bright: np.ndarray, texture: np.ndarray,
                   usable: np.ndarray) -> np.ndarray:
    """Noise an untouched block like this one should show.

    Texture leaks into every local noise estimate and sensor noise grows with
    brightness, so blocks are compared with blocks of similar texture and,
    when there are enough of them, similar brightness: the median of each
    bin, with the texture bins interpolated so that neighbouring blocks do
    not jump between bins.
    """
    out = np.zeros_like(sigma)
    n = int(usable.sum())
    if n == 0:
        return out
    if n < 4 * TEXTURE_BINS:
        out[usable] = np.median(sigma[usable])
        return out
    nb = BRIGHT_BINS if n >= 12 * TEXTURE_BINS * BRIGHT_BINS // 2 else 1
    b_edges = np.quantile(bright[usable], np.linspace(0, 1, nb + 1))
    for bi in range(nb):
        in_b = usable & (bright >= b_edges[bi]) & (bright <= b_edges[bi + 1])
        if in_b.sum() < 2 * TEXTURE_BINS:
            in_b = usable
        t_edges = np.quantile(texture[in_b], np.linspace(0, 1, TEXTURE_BINS + 1))
        centres, medians = [], []
        for lo, hi in zip(t_edges[:-1], t_edges[1:]):
            sel = in_b & (texture >= lo) & (texture <= hi)
            if sel.sum() >= 3:
                centres.append(float(np.median(texture[sel])))
                medians.append(float(np.median(sigma[sel])))
        target = usable & (bright >= b_edges[bi]) & (bright <= b_edges[bi + 1])
        if len(centres) >= 2:
            out[target] = np.interp(texture[target], centres, medians)
        else:
            out[target] = np.median(sigma[in_b])
    return out


def _largest_component(mask: np.ndarray) -> np.ndarray:
    from .prnu import _largest_component as largest
    return largest(mask)


def analyze(path: str | Path, block: int = BLOCK) -> NoiseResult:
    """Map the noise level and find regions whose noise does not fit the picture."""
    with Image.open(path) as img:
        rgb = np.asarray(img.convert("RGB"))
    h, w = rgb.shape[:2]
    oy, ox = max(0, (h - MAX_SIDE) // 2), max(0, (w - MAX_SIDE) // 2)
    crop = rgb[oy:oy + MAX_SIDE, ox:ox + MAX_SIDE]
    gray = np.asarray(Image.fromarray(crop).convert("L"), dtype=np.float32)
    sigma, share, bright, texture = block_sigma(gray, block)
    usable = (share >= MIN_FLAT) & (sigma > 0) & (bright > 8) & (bright < 247)

    expected = expected_sigma(sigma, bright, texture, usable)
    judged = usable & (expected > 0)
    if judged.any():
        # The busiest blocks leak the most texture into the estimate, and the
        # texture curve has nothing beyond them to compare with.
        judged &= texture <= np.quantile(texture[judged], BUSY)
    logr = np.zeros_like(sigma)
    logr[judged] = np.log(sigma[judged] / expected[judged])
    spread = 1.4826 * float(np.median(np.abs(logr[judged] - np.median(logr[judged])))) + 1e-6 \
        if judged.any() else 1.0
    limit = max(np.log(RATIO), Z_FLAG * spread)
    apart = np.abs(sigma - expected) >= MIN_DIFF
    noisier = judged & apart & (logr > limit)
    smoother = judged & apart & (logr < -limit)

    level = 0.0
    if judged.any():
        quiet = judged & (texture <= np.quantile(texture[judged], 1 / 3))
        level = float(np.median(sigma[quiet if quiet.any() else judged]))
    rising = False
    if judged.sum() >= 2 * BINS:
        dark = judged & (bright <= np.median(bright[judged]))
        light = judged & ~dark
        if dark.any() and light.any():
            rising = np.median(sigma[light]) > 1.3 * np.median(sigma[dark])

    region, box, kind, ratio = np.zeros_like(judged), None, None, None
    best = None
    for name, mask in (("noisier", noisier), ("smoother", smoother)):
        if mask.any():
            comp = _largest_component(mask)
            if comp.sum() >= MIN_BLOCKS and (best is None or comp.sum() > best[1].sum()):
                best = (name, comp)
    if best is not None:
        kind, region = best
        ii, jj = np.nonzero(region)
        box = (int(ox + jj.min() * block), int(oy + ii.min() * block),
               int(ox + (jj.max() + 1) * block), int(oy + (ii.max() + 1) * block))
        ratio = round(float(np.exp(np.median(logr[region]))), 2)
    return NoiseResult(round(level, 2), rising, round(float(judged.mean()), 3), box, kind, ratio,
                       block, (ox, oy), sigma, texture, expected, judged, noisier, smoother,
                       region, (w, h), rgb)


def render(res: NoiseResult) -> Image.Image:
    """The picture dimmed where it cannot be judged, red where noisier, blue where smoother."""
    base = res.rgb.astype(np.float64)
    out = base * 0.55
    ox, oy = res.offset
    b = res.block
    gh, gw = res.sigma.shape
    region = out[oy:oy + gh * b, ox:ox + gw * b]
    src = base[oy:oy + gh * b, ox:ox + gw * b]
    judged = np.kron(res.judged, np.ones((b, b), dtype=bool))
    region[judged] = src[judged]
    for mask, colour in ((res.noisier, (255.0, 40.0, 40.0)), (res.smoother, (40.0, 120.0, 255.0))):
        px = np.kron(mask, np.ones((b, b), dtype=bool))
        region[px] = src[px] * 0.35 + np.array(colour) * 0.65
    out[oy:oy + gh * b, ox:ox + gw * b] = region
    pic = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pic)
    lw = max(2, pic.width // 400)
    d.rectangle((ox, oy, ox + gw * b - 1, oy + gh * b - 1), outline=(255, 255, 255), width=lw)
    if res.box:
        d.rectangle(res.box, outline=(255, 220, 0), width=lw)
    return pic
