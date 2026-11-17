"""Steganalysis of JPEG coefficients: JSteg-style LSB replacement and F5-style shrinkage.

JPEG steganography writes into the quantised DCT coefficients. Two
families leave two different statistical traces:

- **LSB replacement** (JSteg, early OutGuess, many homemade tools) flips the
  lowest bit of non-zero coefficients other than 1. Each pair of values
  (2k, 2k+1) that differ only in that bit drifts toward equal counts, which
  the chi-square attack of Westfeld and Pfitzmann (1999) measures, here on
  the pairs (2,3), (4,5), ... and (-2,-1), (-4,-3), ... of the luma AC
  coefficients. Run in scan order over blocks of coefficients, a payload
  written from the top of the picture shows as the stretch where the
  test fires.
- **F5** (and matrix embedding in general) decrements the absolute value of
  coefficients instead, which lowers the histogram at 1 and 2 relative to
  0. Fridrich, Goljan and Hogea (2002) estimate the fraction of changed
  coefficients by calibration: decompress, crop four pixels off the left
  and top so that the block grid moves, recompress with the same
  quantisation tables, and read the histogram a clean picture of this
  content would have. The ratio reported here follows their estimator on
  the (0,1), (1,0) and (1,1) modes.

Both tests need the file's own coefficients, so they run on baseline JPEGs
that the jpegdct module can read. The LSB test is sharp: clean pictures
read 0% throughout and a payload shows block by block. The calibrated
estimate is a lead rather than a verdict: it reads 10-35% on some phone
HDR output that holds nothing, and 20-40% on pictures where F5 really
changed 30% of the coefficients. Modern adaptive schemes (J-UNIWARD,
nsF5 with optimal coding at low rates) are designed to defeat these
statistics and are not detected.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import jpegdct
from .hidden import _chi2_sf

MAX_BLOCKS = 60000       # luma blocks decoded by default (about 3.8 megapixels)
CHI_BLOCK = 10000        # AC coefficients per chi-square block
CHI_MIN = 2000           # smallest stream tested at all
CHI_THRESHOLD = 0.5      # Westfeld's p from which a block counts as evened out
LSB_FLAG = 0.1           # share of the coefficient stream reading as LSB-embedded to flag
F5_MODES = (1, 2, 3, 4, 5, 6, 7, 8, 9)   # zigzag indices of the low-frequency modes used
F5_MIN_BLOCKS = 1000     # fewer luma blocks than this and the calibrated estimate is noise
F5_NOTE = 0.15           # calibrated change ratio worth a look
F5_FLAG = 0.3            # and from which F5-style embedding is reported


@dataclass
class JpegStego:
    blocks: int                       # luma blocks examined
    nonzero_ac: int                   # non-zero AC coefficients among them
    usable: int                       # coefficients JSteg could use (not 0 or 1)
    lsb_embedded: float               # share of the stream whose pairs read as evened out
    lsb_prefix: float                 # share at the start of the stream, in one run
    lsb_probabilities: list[float] = field(default_factory=list)
    f5_ratio: float | None = None     # Fridrich's calibrated estimate of changed coefficients
    histogram: dict[int, int] = field(default_factory=dict)      # value -> count, |value| <= 8
    calibrated: dict[int, int] = field(default_factory=dict)
    progressive: bool = False
    error: str | None = None


def _ac_stream(jd: jpegdct.JpegData) -> np.ndarray:
    return jd.luma.coeffs[:, :, 1:].reshape(-1)


def _pair_chi(values: np.ndarray) -> float:
    """Probability that the LSB pairs of coefficients (excluding 0 and 1) are evened out."""
    v = values[(values != 0) & (values != 1)]
    if len(v) < 200:
        return 0.0
    v = v[np.abs(v) <= 64]
    # Map each value to its pair: JSteg pairs are (2k, 2k+1) for positive, (-2k, -2k+1) for negative.
    pair = np.where(v > 0, v // 2, -((-v + 1) // 2))             # 2,3 -> 1 ; -2,-1 -> -1
    lsb = v & 1
    keys, inv = np.unique(pair, return_inverse=True)
    even = np.bincount(inv, weights=(lsb == 0), minlength=len(keys))
    odd = np.bincount(inv, weights=(lsb == 1), minlength=len(keys))
    expected = (even + odd) / 2
    keep = expected >= 4
    if keep.sum() < 2:
        return 0.0
    chi = float(((even[keep] - expected[keep]) ** 2 / expected[keep]).sum())
    return _chi2_sf(chi, int(keep.sum()) - 1)


def lsb_attack(stream: np.ndarray, block: int = CHI_BLOCK) -> tuple[list[float], float, float]:
    """Westfeld's test per block of the stream: the share evened out, and the share in one run from the start.

    A clean picture has far more 2s than 3s (and -2s than -1s) in every block, so p is
    about 0 throughout. Where random bits were written the pairs balance and p is
    high; a payload written from the start of the picture fills a prefix of blocks.
    """
    probs = []
    n = len(stream)
    if n < CHI_MIN:
        return probs, 0.0, 0.0
    block = min(block, n)
    embedded, prefix, open_run = 0, 0, True
    for start in range(0, n - block + 1, block):
        p = _pair_chi(stream[start:start + block])
        probs.append(round(p, 3))
        if p >= CHI_THRESHOLD:
            embedded += block
            if open_run:
                prefix += block
        else:
            open_run = False
    return probs, embedded / n, prefix / n


def _mode_hist(jd: jpegdct.JpegData, zz_index: int, upto: int = 3) -> np.ndarray:
    vals = np.abs(jd.luma.coeffs[:, :, zz_index]).reshape(-1)
    return np.bincount(np.minimum(vals, upto), minlength=upto + 1).astype(np.float64)


def calibrate(data: bytes, jd: jpegdct.JpegData) -> jpegdct.JpegData | None:
    """The same picture cropped four pixels left and top and recompressed with the same tables."""
    with Image.open(io.BytesIO(data)) as img:
        rgb = img.convert("RGB")
    if rgb.width < 24 or rgb.height < 24:
        return None
    cropped = rgb.crop((4, 4, rgb.width, rgb.height))
    tables = [list(int(v) for v in jd.qtables[k]) for k in sorted(jd.qtables)]
    # Pillow wants natural-order tables; ours are zigzag.
    natural = []
    for t in tables:
        nat = [0] * 64
        for zz, pos in enumerate(jpegdct.ZIGZAG):
            nat[pos] = t[zz]
        natural.append(nat)
    luma = jd.luma
    sub = {(1, 1): 0, (2, 1): 1, (2, 2): 2}.get((luma.h, luma.v), 0)
    buf = io.BytesIO()
    cropped.save(buf, "JPEG", qtables=natural, subsampling=sub)
    try:
        return jpegdct.read(buf.getvalue(), max_blocks=luma.coeffs.shape[0] * luma.coeffs.shape[1])
    except jpegdct.Unsupported:
        return None


def f5_estimate(jd: jpegdct.JpegData, cal: jpegdct.JpegData) -> float:
    """Fridrich's estimate of the fraction of non-zero AC coefficients F5 changed."""
    num = den = 0.0
    # Fridrich used the modes (0,1), (1,0) and (1,1); the first nine AC modes steady
    # the estimate on pictures of a few thousand blocks.
    for zz in F5_MODES:
        h = _mode_hist(jd, zz)
        hc = _mode_hist(cal, zz)
        # Scale the calibrated histogram to the same number of blocks.
        hc *= h.sum() / max(hc.sum(), 1)
        num += hc[1] * (h[0] - hc[0]) + (h[1] - hc[1]) * (hc[2] - hc[1])
        den += hc[1] ** 2 + (hc[2] - hc[1]) ** 2
    if den <= 0:
        return 0.0
    return float(min(1.0, max(0.0, num / den)))


def analyze(path: str | Path, max_blocks: int | None = MAX_BLOCKS) -> JpegStego:
    data = Path(path).read_bytes()
    try:
        jd = jpegdct.read(data, max_blocks=max_blocks)
    except jpegdct.Unsupported as exc:
        return JpegStego(0, 0, 0, 0.0, 0.0, progressive="progressive" in str(exc), error=str(exc))
    except Exception as exc:
        return JpegStego(0, 0, 0, 0.0, 0.0, error=f"cannot read coefficients: {exc}")
    stream = _ac_stream(jd)
    probs, embedded, prefix = lsb_attack(stream)
    nonzero = int((stream != 0).sum())
    usable = int(((stream != 0) & (stream != 1)).sum())
    hist = {int(v): int(c) for v, c in zip(*np.unique(stream[np.abs(stream) <= 8], return_counts=True))}
    res = JpegStego(int(jd.luma.coeffs.shape[0] * jd.luma.coeffs.shape[1]), nonzero, usable,
                    round(embedded, 3), round(prefix, 3), probs, histogram=hist)
    cal = None
    if res.blocks >= F5_MIN_BLOCKS:
        try:
            cal = calibrate(data, jd)
        except Exception:
            cal = None
    if cal is not None:
        res.f5_ratio = round(f5_estimate(jd, cal), 3)
        cs = _ac_stream(cal)
        res.calibrated = {int(v): int(c) for v, c in zip(*np.unique(cs[np.abs(cs) <= 8], return_counts=True))}
    return res


@dataclass
class Verdict:
    flag: bool
    message: str


def verdict(res: JpegStego) -> Verdict:
    if res.error:
        return Verdict(False, f"Coefficient tests not run: {res.error}.")
    if res.lsb_embedded >= LSB_FLAG:
        where = f", from the start of the picture ({res.lsb_prefix:.0%} of the stream)" \
            if res.lsb_prefix >= 0.05 else ""
        return Verdict(True, f"LSB replacement in the coefficients likely (JSteg-style): "
                             f"{res.lsb_embedded:.0%} of the coefficient stream has evened-out "
                             f"value pairs{where}.")
    if res.f5_ratio is not None and res.f5_ratio >= F5_FLAG:
        return Verdict(True, f"F5-style embedding likely: the calibrated estimate is that about "
                             f"{res.f5_ratio:.0%} of the non-zero coefficients were changed.")
    if res.f5_ratio is not None and res.f5_ratio >= F5_NOTE:
        return Verdict(False, f"Calibrated F5 estimate of {res.f5_ratio:.0%} is raised but within what "
                              f"heavily processed camera output reads; no LSB replacement "
                              f"({res.lsb_embedded:.0%}).")
    if res.f5_ratio is None:
        return Verdict(False, f"No sign of LSB replacement in the coefficients ({res.lsb_embedded:.0%} "
                              f"of the stream); too few blocks for the calibrated F5 estimate.")
    return Verdict(False, f"No sign of coefficient steganography (LSB pairs {res.lsb_embedded:.0%}, "
                          f"F5 estimate {res.f5_ratio:.0%}).")


# --- embedding, for tests and demonstrations ---------------------------------

def embed_jsteg(data: bytes, bits: np.ndarray) -> bytes:
    """Write bits into the LSBs of usable luma AC coefficients, JSteg fashion, in scan order."""
    jd = jpegdct.read(data)
    stream = jd.luma.coeffs[:, :, 1:]
    flat = stream.reshape(-1)
    usable = np.nonzero((flat != 0) & (flat != 1))[0]
    n = min(len(bits), len(usable))
    idx = usable[:n]
    # Two's complement makes (v & ~1) | bit right for negative values too: -1 <-> -2.
    flat[idx] = (flat[idx] & ~1) | bits[:n].astype(np.int32)
    jd.luma.coeffs[:, :, 1:] = flat.reshape(stream.shape)
    return jpegdct.write(jd)


def embed_f5_like(data: bytes, ratio: float, seed: int = 0) -> bytes:
    """Decrement the absolute value of a share of the non-zero luma AC coefficients (F5's change)."""
    rng = np.random.default_rng(seed)
    jd = jpegdct.read(data)
    stream = jd.luma.coeffs[:, :, 1:]
    flat = stream.reshape(-1)
    nz = np.nonzero(flat)[0]
    pick = rng.choice(nz, int(len(nz) * ratio), replace=False)
    flat[pick] -= np.sign(flat[pick])
    jd.luma.coeffs[:, :, 1:] = flat.reshape(stream.shape)
    return jpegdct.write(jd)
