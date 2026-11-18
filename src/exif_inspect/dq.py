"""Double JPEG quantisation: the table of an earlier save, and the blocks that never saw it.

A JPEG save divides every DCT coefficient by a step from its quantisation
table and rounds. A second save with other steps rounds the already
rounded values again, and the two grids beat against each other: some
values of the final coefficient collect several of the first save's
values and others none, so its histogram shows periodic peaks and gaps
(Lukas and Fridrich, "Estimation of primary quantization matrix in double
compressed JPEG images", 2003; Popescu and Farid, 2004). The pattern
depends on the first step alone, which makes it readable: the tool tries
every first step and keeps the one that explains the histogram best.

To judge a candidate step it needs the coefficients as they were before
any rounding. Decoding the picture, cropping four pixels off the top and
left and taking the DCT again breaks the block grid and gives a good
estimate of them (Fridrich's calibration). Rounding those twice, with the
candidate step and then the file's own, predicts the histogram a double
save would give; rounding them once predicts a single save.

The same two predictions judge each 8x8 block (Lin, He, Tang and Tang,
"Fast, automatic and fine-grained tampered JPEG image detection via DCT
coefficient analysis", 2009; Bianchi and Piva, 2012). A block pasted or
painted in after the first save was rounded only once, and its
coefficients fall where a twice-rounded block's rarely do. Summing the
log likelihood ratio over the low frequencies gives a map of the blocks
that were compressed once in a picture that was compressed twice.

The pattern is strong when the second save is the finer one, weak when
it is coarser, and absent when the two tables agree or when the picture
was cropped off the grid or resized between the saves. A file with no
pattern may still have been saved more than once.

Noise, sharpening or a filter over the whole picture between the saves
smears the pattern everywhere. The spread of that smear is fitted with
the step, and a region is reported only when the blocks outside it still
read plainly as compressed twice; a weakened pattern always has a
weakest stretch, and without those two checks it reads as a patch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import jpegdct
from .quant import STD_LUMA, scaled_table

FREQS = tuple(range(1, 16))   # coefficients used, in zigzag order: the first 15 AC
RANGE = 60                    # coefficient values beyond this are pooled
Q1_MAX = 48                   # largest first step tried
NOISE = 0.3                   # rounding of pixels between the saves, in coefficient units
SPREADS = (0.3, 0.6, 1.0, 1.5, 2.2, 3.0)   # wider spreads tried once a step is found: noise,
                              # sharpening or blur between the saves smears the pattern
SAMPLES = 60000               # calibrated blocks the predictions are drawn from
SHARES = (0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5)   # shares of blocks rounded once that are tried
GAIN = 0.03                   # nats per coefficient a first step must gain over a single save,
FIT = 1.5                     # this many times what the model leaves unexplained,
MIN_FILLED = 0.05             # on a coefficient that is non-zero in this share of blocks
SMOOTH = 2                    # the ratio is averaged over blocks this far away on each side
QUALITY_FIT = 0.97            # share of the gain a libjpeg table must reach to be named
MIN_FREQS = 3                 # coefficients that must show the pattern,
MAP_FREQS = 6                 # and how many it takes before single blocks are judged
FLAG = 4.0                    # smoothed log likelihood ratio that reads as compressed once
BACKGROUND = -2.0             # median ratio the blocks outside a region must stay under: a
                              # region is reported against a picture that reads as compressed twice
CLIP = 1                      # pixels this close to black or white count as clipped
MIN_NONZERO = 2               # non-zero coefficients a block needs to be judged
MIN_BLOCKS = 16               # connected flagged blocks that make a region
MAX_BLOCKS = 200000           # luma blocks decoded (12.8 megapixels), from the top of the picture

# The 8x8 DCT as JPEG defines it.
_K = np.arange(8)[:, None]
_DCT = np.cos(np.pi * (2 * np.arange(8)[None, :] + 1) * _K / 16) * 0.5
_DCT[0] /= np.sqrt(2)


@dataclass
class Step:
    index: int                       # zigzag index of the coefficient
    current: int                     # the file's quantisation step there
    first: int                       # the earlier save's step
    gain: float                      # nats per coefficient over a single save
    once: float = 0.0                # share of blocks this coefficient puts at a single save
    left: float = 0.0                # nats per coefficient the fitted model leaves unexplained


@dataclass
class DqResult:
    verdict: str                     # "none", "double", "region" or "skipped"
    note: str                        # why the file was skipped, when it was
    steps: list[Step]
    first_quality: tuple[int, int] | None    # libjpeg qualities whose table fits the first steps
    judged_fraction: float
    single_fraction: float           # share of judged blocks that read as compressed once
    box: tuple[int, int, int, int] | None
    covered: float                   # share of the picture's blocks that were decoded
    ratio: np.ndarray | None = field(default=None, repr=False)     # block grid, smoothed
    judged: np.ndarray | None = field(default=None, repr=False)
    flagged: np.ndarray | None = field(default=None, repr=False)   # blocks of the reported region
    size: tuple[int, int] = (0, 0)
    rgb: np.ndarray | None = field(default=None, repr=False)
    smeared: bool = False            # blocks read as compressed once, with no clear background


def block_dct(pixels: np.ndarray) -> np.ndarray:
    """Unquantised DCT of every whole 8x8 block, zigzag order: (count, 64)."""
    h, w = pixels.shape
    bh, bw = h // 8, w // 8
    blocks = (pixels[:bh * 8, :bw * 8].astype(np.float64) - 128).reshape(bh, 8, bw, 8)
    coeffs = np.einsum("ki,aibj,lj->abkl", _DCT, blocks, _DCT).reshape(bh * bw, 64)
    return coeffs[:, jpegdct.ZIGZAG]


def _histogram(values: np.ndarray) -> np.ndarray:
    return np.bincount(np.clip(values, -RANGE, RANGE).astype(np.int64).ravel() + RANGE,
                       minlength=2 * RANGE + 1).astype(np.float64)


def _predict(rounded: np.ndarray) -> np.ndarray:
    """Probability of each coefficient value, with half a count added so that none is zero."""
    h = _histogram(rounded) + 0.5
    return h / h.sum()


def first_step(observed: np.ndarray, calibrated: np.ndarray, noise: np.ndarray, current: int):
    """The first step that explains one coefficient's histogram best.

    The histogram is modelled as a mixture: most blocks rounded twice, a
    share of them (pasted or painted later) rounded once. Returns the step,
    its gain in nats per coefficient over a single save (0 when no step
    gains anything), what the mixture still leaves unexplained, the share
    rounded once, the predicted distributions of a single and of that
    double save, and the gain of every step tried.
    """
    seen = _histogram(observed)
    seen /= seen.sum()
    filled = seen > 0
    single = _predict(np.round(calibrated / current))
    base = float((seen * np.log(single)).sum())
    gains = np.zeros(Q1_MAX + 1)
    best = (0, 0.0, 0.0, single)
    for step in range(1, Q1_MAX + 1):
        if step == current:
            continue
        double = _predict(np.round((np.round(calibrated / step) * step + noise) / current))
        for share in SHARES:
            gain = float((seen * np.log((1 - share) * double + share * single)).sum()) - base
            gains[step] = max(gains[step], gain)
            if gain > best[1]:
                best = (step, gain, share, double)
    step, gain, share, double = best
    if step:
        # An edit of the whole picture between the saves (noise, sharpening, a filter) moves
        # every coefficient a little before the second rounding. Fitting that spread keeps
        # such a picture from reading as a patchwork of blocks compressed once.
        rounded = np.round(calibrated / step) * step
        for spread in SPREADS[1:]:
            wide = _predict(np.round((rounded + noise * (spread / NOISE)) / current))
            for s in SHARES:
                g = float((seen * np.log((1 - s) * wide + s * single)).sum()) - base
                if g > gain:
                    gain, share, double = g, s, wide
    mix = (1 - share) * double + share * single
    left = float((seen[filled] * np.log(seen[filled] / mix[filled])).sum())
    return step, gain, left, share, single, double, gains


def libjpeg_quality(found: dict[int, np.ndarray]) -> tuple[int, int] | None:
    """The libjpeg qualities whose standard table explains the histograms as well as any steps.

    `found` maps a coefficient's zigzag index to the gain of every first
    step tried. Neighbouring steps can give the same histogram when the
    coefficient is small, so tables are compared by what they explain,
    which lets such steps tie. The range of qualities within 1% of the
    best is returned when the best reaches 97% of what free steps explain.
    """
    zigzag = np.array(STD_LUMA)[jpegdct.ZIGZAG]
    free = sum(float(g.max()) for g in found.values())
    totals = []
    for q in range(1, 101):
        total = 0.0
        for z, gains in found.items():
            step = scaled_table([int(zigzag[z])], q)[0]
            total += float(gains[step]) if step <= Q1_MAX else 0.0
        totals.append(total)
    best = max(totals)
    if free <= 0 or best < QUALITY_FIT * free:
        return None
    fits = [q + 1 for q, t in enumerate(totals) if t >= 0.99 * best]
    return fits[0], fits[-1]


def _smooth(a: np.ndarray, judged: np.ndarray, r: int = SMOOTH) -> np.ndarray:
    """Mean over the judged blocks within r of each block."""
    pa = np.pad(np.where(judged, a, 0.0), r)
    pw = np.pad(judged.astype(np.float64), r)
    span = range(2 * r + 1)
    num = sum(pa[dy:dy + a.shape[0], dx:dx + a.shape[1]] for dy in span for dx in span)
    den = sum(pw[dy:dy + a.shape[0], dx:dx + a.shape[1]] for dy in span for dx in span)
    return num / np.maximum(den, 1)


def _largest_component(mask: np.ndarray) -> np.ndarray:
    from .prnu import _largest_component as largest
    return largest(mask)


def _skipped(note: str) -> DqResult:
    return DqResult("skipped", note, [], None, 0.0, 0.0, None, 0.0)


def analyze(path: str | Path, max_blocks: int | None = MAX_BLOCKS) -> DqResult:
    """Estimate an earlier save's quantisation steps and map the blocks that lack them."""
    data = Path(path).read_bytes()
    if data[:2] != b"\xff\xd8":
        return _skipped("not a JPEG, so there are no quantised coefficients to read")
    try:
        jd = jpegdct.read(data, max_blocks=max_blocks)
    except jpegdct.Unsupported as exc:
        return _skipped(f"{exc}; only baseline JPEGs can be read")
    luma = jd.luma
    table = jd.qtables[luma.tq]
    rows = luma.blocks_h
    if rows * 8 < jd.height:                     # stopped at the cap: the last rows are part filled
        rows -= luma.v
    coeffs = luma.coeffs[:rows]
    covered = min(1.0, rows * 8 / jd.height)

    pixels = jpegdct.to_pixels(jd, 0)[:rows * 8]
    calibrated = block_dct(pixels[4:, 4:])
    rng = np.random.default_rng(0)
    if len(calibrated) > SAMPLES:
        calibrated = calibrated[rng.choice(len(calibrated), SAMPLES, replace=False)]
    if len(calibrated) < 500:
        return _skipped("fewer than 500 blocks, too few for a histogram")
    noise = rng.normal(0, NOISE, len(calibrated))

    steps: list[Step] = []
    found: dict[int, np.ndarray] = {}
    ratio = np.zeros(coeffs.shape[:2])
    for z in FREQS:
        current = int(table[z])
        observed = coeffs[:, :, z]
        step, gain, left, share, single, double, gains = first_step(
            observed, calibrated[:, z], noise, current)
        if gain < GAIN or gain < FIT * left or (observed != 0).mean() < MIN_FILLED:
            continue
        steps.append(Step(z, current, step, round(gain, 3), share, round(left, 4)))
        found[z] = gains
        idx = np.clip(observed, -RANGE, RANGE) + RANGE
        ratio += np.log(single[idx] / double[idx])

    with Image.open(path) as img:
        rgb = np.asarray(img.convert("RGB"))
    size = (rgb.shape[1], rgb.shape[0])
    if len(steps) < MIN_FREQS:
        return DqResult("none", "", [], None, 0.0, 0.0, None, round(covered, 3), size=size, rgb=rgb)

    used = [s.index for s in steps]
    judged = (coeffs[:, :, used] != 0).sum(axis=2) >= MIN_NONZERO
    # A block with a channel at black or white was clipped between the saves, which no
    # rounding model describes, and a block that hangs over the edge was padded.
    whole_h, whole_w = min(rows, size[1] // 8), min(luma.blocks_w, size[0] // 8)
    inside = rgb[:whole_h * 8, :whole_w * 8]
    clipped = ((inside <= CLIP) | (inside >= 255 - CLIP)).any(axis=2)
    clean = np.zeros_like(judged)
    clean[:whole_h, :whole_w] = ~clipped.reshape(whole_h, 8, whole_w, 8).any(axis=(1, 3))
    judged &= clean
    smooth = _smooth(ratio, judged)
    once = judged & (smooth >= FLAG)
    region = np.zeros_like(judged)
    box = None
    verdict = "double"
    smeared = False
    if once.any() and len(steps) >= MAP_FREQS:
        comp = _largest_component(once)
        if comp.sum() >= MIN_BLOCKS:
            # Noise, sharpening or a filter over the whole picture between the saves weakens
            # the pattern everywhere, and its weakest stretch then looks like a region. A
            # real one stands against blocks that still read plainly as compressed twice.
            rest = judged & ~comp
            if rest.any() and float(np.median(smooth[rest])) <= BACKGROUND:
                verdict, region = "region", comp
                ii, jj = np.nonzero(comp)
                box = (int(jj.min() * 8), int(ii.min() * 8),
                       int(min(size[0], (jj.max() + 1) * 8)), int(min(size[1], (ii.max() + 1) * 8)))
            else:
                smeared = True
    return DqResult(verdict, "", steps, libjpeg_quality(found) if len(steps) >= MAP_FREQS else None,
                    round(float(judged.mean()), 3),
                    round(float(once.sum() / max(1, judged.sum())), 3),
                    box, round(covered, 3), smooth, judged, region, size, rgb, smeared)

def render(res: DqResult) -> Image.Image:
    """The picture dimmed where it cannot be judged, red where it was compressed once."""
    base = res.rgb.astype(np.float64)
    out = base * 0.55
    bh, bw = res.judged.shape
    h, w = min(bh * 8, base.shape[0]), min(bw * 8, base.shape[1])
    judged = np.kron(res.judged, np.ones((8, 8), dtype=bool))[:h, :w]
    flagged = np.kron(res.flagged, np.ones((8, 8), dtype=bool))[:h, :w]
    area = out[:h, :w]
    src = base[:h, :w]
    area[judged] = src[judged]
    area[flagged] = src[flagged] * 0.35 + np.array([255.0, 40.0, 40.0]) * 0.65
    pic = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pic)
    lw = max(2, pic.width // 400)
    d.rectangle((0, 0, w - 1, h - 1), outline=(255, 255, 255), width=lw)
    if res.box:
        d.rectangle(res.box, outline=(255, 220, 0), width=lw)
    return pic
