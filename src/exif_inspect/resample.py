"""Traces of resizing and turning, for the whole picture and by region.

Enlarging, shrinking or turning a picture computes every new pixel from a
few old ones. How much a new pixel owes to its neighbours depends on where
it falls between the old ones, and that position repeats: after enlarging
by 1.25, every fifth pixel sits exactly on an old one. The picture
therefore carries a hidden periodic pattern in how well each pixel is
predicted by its neighbours (Popescu and Farid, "Exposing digital
forgeries by detecting traces of resampling", 2005). Kirchner ("Fast and
reliable resampling detection by spectral analysis of fixed linear
predictor residue", 2008) showed that a fixed predictor is enough: the
pattern shows as sharp peaks in the spectrum of the prediction residual.

The tool predicts every pixel from its eight neighbours, takes the size of
the residual, and averages its spectrum over tiles. A frequency that
stands far above its surroundings, and that the picture's own spectrum
does not explain, is a resampling peak; its position gives the factor or
the angle, up to the aliases listed with it. The same frequencies are then
measured in 64-pixel tiles, which shows whether the whole picture carries
the trace or one region of it, such as an object scaled to fit before it
was pasted in. A region too small to show in the whole picture's spectrum
is searched for separately, by letting each tile vote for its own
strongest frequency.

A picture resized after a JPEG save also keeps that save's 8-pixel blocks,
stretched by the factor. They show as a comb of peaks and give the factor
exactly, including for reductions, which leave little interpolation trace.

A JPEG save after the resizing weakens all of this and hides a pasted
region even at quality 95, a 2x enlargement leaves no trace of this kind,
and the JPEG grid of the file itself puts peaks at multiples of 1/8, which
hide the factors that land there. A picture without a trace may still
have been resized.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image, ImageDraw

TILE = 64               # tile for the map, in pixels; tiles overlap by half
COARSE = (256, 128, 64)  # tile sizes tried for the whole-picture spectrum, largest first
MIN_COARSE = 30         # tiles the whole-picture spectrum must average over
MIN_FREQ = 0.07         # peaks closer to zero frequency than this are picture content
RATIO = 2.0             # a peak must stand this many times above its surroundings
Z_PEAK = 8.0            # and this many spreads of the spectrum's own scatter
CONTENT = 1.6           # where the picture's own spectrum rises more than this,
OVER_CONTENT = 4.0      # the residual's peak must be this many times higher still
GRID = 2.5              # height of a peak at a multiple of 1/8 that shows the JPEG grid
MAX_PEAKS = 6           # peaks kept for the report
VOTE = 8.0              # height of a tile's best bin over its background for the tile to vote
Z_LOCAL = 4.0           # spreads a region's peak must reach in the region's own spectrum
LOCAL = 3.0             # tile strength that counts as carrying the trace
WHOLE = 0.5             # share of judged tiles carrying it that makes it the whole picture's
LEAN = 0.6              # or this share of the other tiles rising at the strongest peak,
Z_LEAN = 4.0            # that many standard deviations above the half that chance gives
MIN_TILES = 6           # connected tiles that make a region
MIN_ENERGY = 0.05       # mean residual a tile needs to be judged
MAX_SIDE = 4000         # larger pictures are analysed on a centre crop of this side


@dataclass
class Peak:
    fx: float                        # horizontal frequency, cycles per pixel, 0 to 0.5
    fy: float                        # vertical frequency, -0.5 to 0.5
    ratio: float                     # height over the surrounding spectrum
    z: float                         # the same in spreads of the spectrum
    reading: str = ""                # the resizing or turn that puts a peak there


@dataclass
class ResampleResult:
    verdict: str                     # "none", "whole", "region" or "scattered"
    peaks: list[Peak]
    jpeg_grid: bool                  # the 8-pixel JPEG grid shows; its frequencies are never counted
    stretched: tuple[float, float] | None    # width of an earlier save's JPEG blocks, and the factor
    judged_fraction: float
    carrying_fraction: float         # share of judged tiles that carry the trace
    leaning_fraction: float          # share of the other tiles that rise at the strongest peak
    box: tuple[int, int, int, int] | None
    tile: int
    step: int
    offset: tuple[int, int]
    strength: np.ndarray = field(repr=False)    # tile grid of the trace's strength
    judged: np.ndarray = field(repr=False)
    carrying: np.ndarray = field(repr=False)
    flagged: np.ndarray = field(repr=False)     # tiles of the reported region
    size: tuple[int, int] = (0, 0)
    rgb: np.ndarray | None = field(default=None, repr=False)


def residual(gray: np.ndarray) -> np.ndarray:
    """Size of each pixel's error against Kirchner's fixed predictor, zero on the border."""
    g = gray.astype(np.float32)
    out = np.zeros_like(g)
    cross = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    diag = g[:-2, :-2] + g[:-2, 2:] + g[2:, :-2] + g[2:, 2:]
    out[1:-1, 1:-1] = np.abs(g[1:-1, 1:-1] - 0.5 * cross + 0.25 * diag)
    return out


def _window(tile: int) -> np.ndarray:
    win = np.hanning(tile).astype(np.float32)
    return win[:, None] * win[None, :]


def _power(tiles: np.ndarray, win: np.ndarray) -> np.ndarray:
    f = np.fft.rfft2((tiles - tiles.mean(axis=(1, 2), keepdims=True)) * win)
    return (f.real ** 2 + f.imag ** 2).astype(np.float32)


def tile_spectra(a: np.ndarray, tile: int = TILE) -> np.ndarray:
    """Power spectrum of every half-overlapping tile, Hann-windowed: (rows, cols, tile, tile // 2 + 1)."""
    view = sliding_window_view(a, (tile, tile))[::tile // 2, ::tile // 2]
    win = _window(tile)
    out = np.empty(view.shape[:2] + (tile, tile // 2 + 1), dtype=np.float32)
    for i in range(view.shape[0]):               # row by row keeps the memory flat
        out[i] = _power(view[i], win)
    return out


def mean_spectrum(a: np.ndarray, tile: int, mask: np.ndarray) -> np.ndarray:
    """Average spectrum of the tiles in `mask`, each scaled to unit energy so that none dominates."""
    view = sliding_window_view(a, (tile, tile))[::tile // 2, ::tile // 2]
    win = _window(tile)
    total = np.zeros((tile, tile // 2 + 1), dtype=np.float64)
    for i in np.nonzero(mask.any(axis=1))[0]:
        power = _power(view[i][mask[i]], win)
        total += (power / (power.sum(axis=(1, 2), keepdims=True) + 1e-12)).sum(axis=0)
    return (total / max(1, int(mask.sum()))).astype(np.float32)


def tile_mean(a: np.ndarray, tile: int) -> np.ndarray:
    """Mean of every half-overlapping tile, from the means of the half-tile blocks in it."""
    half = tile // 2
    bh, bw = a.shape[0] // half, a.shape[1] // half
    blocks = a[:bh * half, :bw * half].reshape(bh, half, bw, half).mean(axis=(1, 3))
    return (blocks[:-1, :-1] + blocks[1:, :-1] + blocks[:-1, 1:] + blocks[1:, 1:]) / 4


def _background(spec: np.ndarray, k: int = 5) -> np.ndarray:
    """What a bin would hold without a peak: the largest of three medians around it.

    The median of the square around a bin removes a peak and keeps a ridge,
    and edges in the picture draw ridges along both axes, so the medians
    along the bin's row and column are taken as well.
    """
    n, m = spec.shape
    pad = np.pad(spec, ((k, k), (k, k)), mode="wrap")
    square = np.median(sliding_window_view(pad, (2 * k + 1, 2 * k + 1)).reshape(n, m, -1), axis=2)
    k2 = 2 * k
    along_row = np.median(sliding_window_view(np.pad(spec, ((0, 0), (k2, k2)), mode="reflect"),
                                              2 * k2 + 1, axis=1), axis=2)
    along_col = np.median(sliding_window_view(np.pad(spec, ((k2, k2), (0, 0)), mode="wrap"),
                                              2 * k2 + 1, axis=0), axis=2)
    return np.maximum(np.maximum(square, along_row), along_col) + 1e-12


def _refine(ratio: np.ndarray, i: int, j: int) -> tuple[float, float]:
    """Sub-bin position of a peak from a parabola through its neighbours, in bins."""
    n, m = ratio.shape

    def shift(lo: float, mid: float, hi: float) -> float:
        lo, mid, hi = (math.log(max(v, 1e-9)) for v in (lo, mid, hi))
        den = lo - 2 * mid + hi
        return 0.0 if den >= 0 else max(-0.5, min(0.5, 0.5 * (lo - hi) / den))

    di = shift(ratio[(i - 1) % n, j], ratio[i, j], ratio[(i + 1) % n, j])
    dj = shift(ratio[i, j - 1], ratio[i, j], ratio[i, j + 1]) if 0 < j < m - 1 else 0.0
    return i + di, j + dj


def _turn(fx: float, fy: float) -> float | None:
    """The angle of a pure turn that puts a peak here, if there is one.

    A turn by t carries the old pixel grid to the frequencies (cos t, sin t)
    and (-sin t, cos t), which the new grid sees modulo 1.
    """
    best = None
    for m in (-1, 0, 1):
        for n in (-1, 0, 1):
            u, v = fx + m, fy + n
            off = abs(math.hypot(u, v) - 1)
            if off < 0.02 and (best is None or off < best[0]):
                angle = abs(math.degrees(math.atan2(v, u))) % 90
                best = (off, min(angle, 90 - angle))
    return best[1] if best else None


def reading(fx: float, fy: float, tile: int = TILE) -> str:
    """The resizing factors or the turn that put a peak at this frequency."""
    tol = 0.75 / tile
    ax, ay = abs(fx), abs(fy)
    on_axis = ax < tol or ay < tol
    turn = _turn(fx, fy)
    if not on_axis and turn is not None and turn > 0.5:
        return f"turned by {turn:.1f} degrees"
    if on_axis or abs(ax - ay) < tol:
        f = max(ax, ay)
        text = f"enlarged {1 / (1 - f):.2f}x (or {1 / f:.2f}x), or reduced to {1 / (1 + f):.2f}x"
        if on_axis and f <= 0.26:
            text += f", or turned by {math.degrees(math.asin(f)):.1f} degrees"
        return text
    return "resampled with a turn and a change of size, or sheared"


def stretched_grid(peaks: list[Peak], tol: float = 0.006) -> tuple[float, float] | None:
    """Width of an earlier JPEG save's blocks and the factor it implies, from a comb of peaks.

    A picture resized after a JPEG save still carries that save's 8-pixel
    blocks, now 8 times the factor wide. They show as peaks at two or more
    multiples of one frequency on the axes.
    """
    freqs = sorted({max(abs(p.fx), abs(p.fy)) for p in peaks
                    if min(abs(p.fx), abs(p.fy)) < tol})
    for f0 in freqs:
        harmonics = {k for f in freqs for k in (1, 2, 3, 4) if abs(f - k * f0) < tol}
        if len(harmonics) >= 2:
            return round(1 / f0, 1), round(1 / (8 * f0), 2)
    return None


def _grid_distance(fy: np.ndarray, fx: np.ndarray) -> np.ndarray:
    """Distance of each frequency from the nearest multiple of 1/8 on both axes."""
    dy = np.abs((fy * 8 + 0.5) % 1 - 0.5) / 8
    dx = np.abs((fx * 8 + 0.5) % 1 - 0.5) / 8
    return np.hypot(dy, dx)


def find_peaks(spec: np.ndarray, own: np.ndarray, z_min: float = Z_PEAK) -> tuple[list[Peak], bool]:
    """Peaks of the residual spectrum that the picture's own spectrum does not explain.

    Multiples of 1/8 on both axes belong to the 8-pixel JPEG grid and are
    never counted. The picture's content rides on each grid peak as
    sidebands, so with the grid showing a candidate must also stand above
    the grid peak nearest to it.
    """
    ratio = spec / _background(spec)
    content = own / _background(own)
    n, m = ratio.shape
    e = n // 8
    fy = np.fft.fftfreq(n)[:, None]
    fx = np.fft.rfftfreq(n)[None, :]
    radius = np.hypot(fy, fx)
    on_grid = (_grid_distance(fy, fx) < 1.5 / n) & (radius > 0.5 / n)
    valid = (radius >= MIN_FREQ) & ~on_grid

    # Height of the spectrum at every grid point, kept where it is a peak of its own.
    grid_peak = np.zeros((9, 5))
    for a in range(-4, 5):
        for b in range(5):
            if a == 0 and b == 0:
                continue
            i, j = (a * e) % n, b * e
            around = ratio[[(i - 1) % n, i, (i + 1) % n]][:, max(0, j - 1):j + 2]
            if ratio[i, j] >= around.max():
                grid_peak[a + 4, b] = ratio[i, j]
    grid = bool(grid_peak.max() >= GRID)

    logs = np.log(ratio[valid])
    spread = 1.4826 * float(np.median(np.abs(logs - np.median(logs)))) + 1e-6
    peaks: list[Peak] = []
    order = np.argsort(np.where(valid, ratio, 0).ravel())[::-1]
    for flat in order[:200]:
        i, j = divmod(int(flat), m)
        r = float(ratio[i, j])
        if r < RATIO:
            break
        if not valid[i, j] or math.log(r) / spread < z_min:
            continue
        if content[i, j] > CONTENT and r < OVER_CONTENT * content[i, j]:
            continue                                  # a periodic pattern in the picture itself
        if (j == 0 or j == m - 1) and fy[i, 0] < 0 and i != n // 2:
            continue                                  # mirror image of the bin at +fy
        if grid and r <= grid_peak[int(round(fy[i, 0] * 8)) + 4, int(round(fx[0, j] * 8))]:
            continue                                  # a sideband of the JPEG grid
        ri, rj = _refine(ratio, i, j)
        py = (ri / n + 0.5) % 1 - 0.5
        px = rj / n
        if any(min(math.hypot(px - p.fx, py - p.fy), math.hypot(px + p.fx, py + p.fy)) < 2.5 / n
               for p in peaks):
            continue                                  # shoulder of a peak already kept, or its mirror
        peaks.append(Peak(round(px, 4), round(py, 4), round(r, 1),
                          round(math.log(r) / spread, 1), reading(px, py, n)))
        if len(peaks) == MAX_PEAKS:
            break
    return peaks, grid


def coarse_peaks(gray: np.ndarray, res: np.ndarray) -> tuple[list[Peak], bool]:
    """Peaks of the whole picture's averaged spectrum, on the largest tiles it has enough of.

    A resampling trace is a pure tone, so larger tiles raise it further
    above the background; enough of them must remain to average the
    background's scatter away.
    """
    h, w = gray.shape
    size = COARSE[-1]
    for cand in COARSE:
        half = cand // 2
        if min(h, w) >= cand and ((h - cand) // half + 1) * ((w - cand) // half + 1) >= MIN_COARSE:
            size = cand
            break
    usable = tile_mean(res, size) >= MIN_ENERGY
    if not usable.any():
        return [], False
    return find_peaks(mean_spectrum(res, size, usable), mean_spectrum(gray, size, usable))


def local_peaks(spectra: np.ndarray, judged: np.ndarray, res: np.ndarray,
                gray: np.ndarray) -> list[Peak]:
    """Peaks carried by one region only, too small to show in the whole picture's spectrum.

    Each tile's spectrum, averaged with its eight neighbours, is divided by
    its own coarse background, and the tile votes for its highest bin. A
    connected group of tiles voting for the same bin is a region with a
    periodic residual of its own; its averaged spectrum is then put
    through the same tests as the whole picture's.
    """
    gh, gw, n, m = spectra.shape
    q = 4                                        # background: median of each 4x4 block of bins
    fy = np.fft.fftfreq(n)[:, None]
    fx = np.fft.rfftfreq(n)[None, :m - 1]
    skip = (np.hypot(fy, fx) < MIN_FREQ) | (_grid_distance(fy, fx) < 1.5 / n)
    weight = judged.astype(np.float32)
    best = np.zeros((gh, gw), dtype=np.int64)
    height = np.zeros((gh, gw), dtype=np.float32)
    count = np.zeros((gh, gw), dtype=np.float32)
    for i in range(gh):                          # one row of tiles at a time keeps the memory flat
        acc = np.zeros((gw, n, m - 1), dtype=np.float32)
        for y in range(max(0, i - 1), min(gh, i + 2)):
            row = spectra[y, :, :, :m - 1]
            row = row / (row.sum(axis=(1, 2), keepdims=True) + 1e-12) * weight[y, :, None, None]
            for dx in (-1, 0, 1):
                src = slice(max(0, dx), gw + min(0, dx))
                dst = slice(max(0, -dx), gw + min(0, -dx))
                acc[dst] += row[src]
                count[i, dst] += weight[y, src]
        blocks = acc.reshape(gw, n // q, q, (m - 1) // q, q).transpose(0, 1, 3, 2, 4)
        bg = np.median(blocks.reshape(gw, n // q, (m - 1) // q, q * q), axis=3)
        quot = acc / (np.repeat(np.repeat(bg, q, axis=1), q, axis=2) + 1e-12)
        quot[:, skip] = 0
        flat = quot.reshape(gw, -1)
        best[i] = flat.argmax(axis=1)
        height[i] = flat[np.arange(gw), best[i]]
    voters = judged & (count >= 4) & (height >= VOTE)
    if voters.sum() < MIN_TILES:
        return []

    # The bin with the most votes, counting a vote for a neighbouring bin as well.
    ci, cj = np.divmod(best, m - 1)
    votes = np.zeros((n, m - 1))
    np.add.at(votes, (ci[voters], cj[voters]), 1)
    near = sum(np.roll(np.roll(votes, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    ti, tj = np.unravel_index(int(near.argmax()), votes.shape)
    same = voters & (np.abs((ci - ti + n // 2) % n - n // 2) <= 1) & (np.abs(cj - tj) <= 1)
    if same.sum() < MIN_TILES:
        return []
    region = _largest_component(same)
    if region.sum() < MIN_TILES:
        return []
    peaks, _ = find_peaks(mean_spectrum(res, n, region), mean_spectrum(gray, n, region),
                          z_min=Z_LOCAL)
    # Resizing puts its peaks on the axes. An oblique peak that only one
    # region carries is nearly always a texture there: cloth, hatching, a fence.
    peaks = [p for p in peaks if min(abs(p.fx), abs(p.fy)) < 0.75 / n]
    voted = any(abs(p.fx - tj / n) <= 2 / n and abs((p.fy - ti / n + 0.5) % 1 - 0.5) <= 2 / n
                for p in peaks)
    return peaks if voted else []


def tile_strength(spectra: np.ndarray, peaks: list[Peak]) -> np.ndarray:
    """Per tile: the peaks' height over the tile's own spectrum around them, averaged."""
    gh, gw, n, m = spectra.shape
    out = np.zeros((gh, gw), dtype=np.float32)
    for p in peaks:
        i, j = int(round(p.fy * n)) % n, min(m - 1, int(round(p.fx * n)))
        rows = [(i + d) % n for d in range(-4, 5)]
        cols = [c for c in range(j - 4, j + 5) if 0 <= c < m]
        ring = spectra[:, :, rows][:, :, :, cols].reshape(gh, gw, -1)
        out += spectra[:, :, i, j] / (np.median(ring, axis=2) + 1e-12)
    return out / max(1, len(peaks))


def _smooth(a: np.ndarray, judged: np.ndarray) -> np.ndarray:
    """Median over each tile and its eight neighbours, counting only judged tiles.

    A median keeps the edge of a region where it is: a tile outside it has
    too few neighbours inside to be lifted, which a mean would do.
    """
    pad = np.pad(np.where(judged, a, np.nan).astype(np.float32), 1, constant_values=np.nan)
    stack = np.stack([pad[dy:dy + a.shape[0], dx:dx + a.shape[1]]
                      for dy in range(3) for dx in range(3)])
    with warnings.catch_warnings():              # a tile with no judged neighbour gives NaN
        warnings.simplefilter("ignore", RuntimeWarning)
        out = np.nanmedian(stack, axis=0)
    return np.where(np.isnan(out), 0, out).astype(np.float32)


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    pad = np.pad(mask, r)
    out = np.zeros_like(mask)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            out |= pad[dy:dy + mask.shape[0], dx:dx + mask.shape[1]]
    return out


def _largest_component(mask: np.ndarray) -> np.ndarray:
    from .prnu import _largest_component as largest
    return largest(mask)


def analyze(path: str | Path) -> ResampleResult:
    """Look for resampling peaks and map which part of the picture carries them."""
    tile, step = TILE, TILE // 2
    with Image.open(path) as img:
        rgb = np.asarray(img.convert("RGB"))
    h, w = rgb.shape[:2]
    if min(h, w) < 2 * tile:
        raise ValueError(f"picture smaller than {2 * tile} pixels on a side")
    oy = max(0, (h - MAX_SIDE) // 2) & ~7        # multiples of 8 keep the JPEG grid in place
    ox = max(0, (w - MAX_SIDE) // 2) & ~7
    crop = rgb[oy:oy + MAX_SIDE, ox:ox + MAX_SIDE]
    gray = np.asarray(Image.fromarray(crop).convert("L"), dtype=np.float32)

    res = residual(gray)
    judged = tile_mean(res, tile) >= MIN_ENERGY
    empty = np.zeros(judged.shape, dtype=bool)
    strength = np.zeros(judged.shape, dtype=np.float32)
    if not judged.any():
        return ResampleResult("none", [], False, None, 0.0, 0.0, 0.5, None, tile, step, (ox, oy),
                              strength, judged, empty, empty, (w, h), rgb)

    spectra = tile_spectra(res, tile)
    peaks, grid = coarse_peaks(gray, res)
    if not peaks:
        peaks = local_peaks(spectra, judged, res, gray)
    carrying, region, box, verdict, share, lean = empty, empty, None, "none", 0.0, 0.5
    if peaks:
        strength = _smooth(tile_strength(spectra, peaks), judged)
        carrying = judged & (strength >= LOCAL)
        share = float(carrying.sum() / judged.sum())
        verdict = "whole" if share >= WHOLE else "scattered"
        if verdict != "whole" and carrying.any():
            comp = _largest_component(carrying)
            if comp.sum() >= MIN_TILES:
                verdict, region = "region", comp
        if verdict != "whole":
            # A faint trace lifts the strongest peak above its surroundings in more than
            # half of the tiles; chance gives exactly half. Tiles near the region are left out.
            rest = judged & ~_dilate(region, 2)
            if rest.sum() >= 20:
                lean = float((tile_strength(spectra, peaks[:1])[rest] > 1).mean())
                if lean >= LEAN and (lean - 0.5) * 2 * math.sqrt(rest.sum() / 4) >= Z_LEAN:
                    verdict, region = "whole", empty
        if verdict == "region":
            ii, jj = np.nonzero(region)
            box = (int(ox + jj.min() * step), int(oy + ii.min() * step),
                   int(ox + jj.max() * step + tile), int(oy + ii.max() * step + tile))
    return ResampleResult(verdict, peaks, grid, stretched_grid(peaks),
                          round(float(judged.mean()), 3), round(share, 3), round(lean, 3),
                          box, tile, step, (ox, oy), strength, judged, carrying, region, (w, h), rgb)


def render(res: ResampleResult) -> Image.Image:
    """The picture dimmed where it cannot be judged, red where the trace is carried."""
    base = res.rgb.astype(np.float64)
    out = base * 0.55
    ox, oy = res.offset
    gh, gw = res.judged.shape
    t, s = res.tile, res.step
    ah, aw = (gh - 1) * s + t, (gw - 1) * s + t
    judged = np.zeros((ah, aw), dtype=bool)
    marked = np.zeros((ah, aw), dtype=bool)
    shown = res.flagged if res.verdict == "region" else res.carrying
    q = (t - s) // 2                              # each tile paints its centre, so tiles do not smear
    for i, j in zip(*np.nonzero(res.judged)):
        judged[i * s:i * s + t, j * s:j * s + t] = True
    for i, j in zip(*np.nonzero(shown)):
        marked[i * s + q:i * s + t - q, j * s + q:j * s + t - q] = True
    area = out[oy:oy + ah, ox:ox + aw]
    src = base[oy:oy + ah, ox:ox + aw]
    area[judged] = src[judged]
    area[marked] = src[marked] * 0.35 + np.array([255.0, 40.0, 40.0]) * 0.65
    out[oy:oy + ah, ox:ox + aw] = area
    pic = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pic)
    lw = max(2, pic.width // 400)
    d.rectangle((ox, oy, ox + aw - 1, oy + ah - 1), outline=(255, 255, 255), width=lw)
    if res.box:
        d.rectangle(res.box, outline=(255, 220, 0), width=lw)
    return pic
