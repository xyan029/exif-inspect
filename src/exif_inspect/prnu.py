"""Camera identification from sensor noise (PRNU).

Every pixel of an image sensor converts light with a slightly different
efficiency. The differences are fixed for the life of the sensor, so they
leave the same faint multiplicative pattern, the photo-response
non-uniformity (PRNU), in every picture the camera takes. The method of
Lukas, Fridrich and Goljan (2006) and Chen et al. (2008):

1. Noise residual of an image: the image minus a denoised copy. Denoising
   uses a 4-level wavelet transform with a local Wiener filter. Row and
   column means are then removed on each colour-filter subgrid, and peaks
   in the spectrum are flattened; both strip patterns shared by every
   camera of a model (readout lines, demosaicing, JPEG blocks).
2. Fingerprint of a camera: from N images I_i with residuals W_i,
   K = sum(W_i * I_i) / sum(I_i ** 2), which is the maximum-likelihood
   estimate under the model W = I * K + noise. Flat, bright, unsaturated
   shots (sky, a white wall) give the cleanest estimate.
3. Test: correlate the residual of a questioned image with I * K over all
   cyclic shifts and measure how sharply the correlation peaks at zero shift
   with the peak-to-correlation energy (PCE). A PCE above 60 is the usual
   threshold for "taken by this camera".

The analysis uses a centre crop of the image so that every picture from the
same camera lines up pixel for pixel. Resized, heavily compressed or
digitally stabilised images lose most of the pattern.

locate() maps which parts of an image carry the pattern, which finds areas
replaced after capture (pasted, retouched or filled in by AI object removal).

Needs PyWavelets: pip install "exif-inspect[prnu]".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import pywt
except ImportError:  # pragma: no cover - exercised only without PyWavelets
    pywt = None

SIGMA = 5.0          # assumed std of the PRNU-bearing noise, 0-255 scale
LEVELS = 4
WAVELET = "db4"
CROP = 2048          # side of the centre crop analysed (0 or too large: the largest centred square)
PCE_THRESHOLD = 60.0
SATURATED = 250


def available() -> bool:
    return pywt is not None


def load(path: str | Path, crop: int = CROP) -> np.ndarray:
    """Centre crop of the image as float RGB, in sensor orientation.

    EXIF orientation is deliberately ignored: the stored pixel grid is the
    sensor grid. Portrait shots from a phone are often stored turned, which
    match() handles by trying the four rotations.
    """
    with Image.open(path) as img:
        arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]
    if crop <= 0 or min(h, w) < crop:      # 0: the largest centred square
        crop = min(h, w) - min(h, w) % 2
    y0, x0 = (h - crop) // 2, (w - crop) // 2
    return arr[y0:y0 + crop, x0:x0 + crop]


def _local_variance(c: np.ndarray, size: int) -> np.ndarray:
    pad = size // 2
    p = np.pad(c * c, pad, mode="reflect")
    ii = np.pad(p, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    s = ii[size:, size:] - ii[:-size, size:] - ii[size:, :-size] + ii[:-size, :-size]
    return s / (size * size)


def _wiener_detail(c: np.ndarray, sigma2: float) -> np.ndarray:
    """Noise part of a wavelet detail band (local Wiener, smallest variance of 4 windows)."""
    c = c.astype(np.float64)
    var = np.min([_local_variance(c, s) for s in (3, 5, 7, 9)], axis=0)
    var = np.maximum(var - sigma2, 0.0)
    return c * sigma2 / (var + sigma2)


def _zero_mean(w: np.ndarray) -> np.ndarray:
    """Remove row and column means on each of the four 2x2 colour-filter subgrids."""
    w = w.copy()
    for dy in (0, 1):
        for dx in (0, 1):
            sub = w[dy::2, dx::2]
            sub -= sub.mean(axis=0, keepdims=True)
            sub -= sub.mean(axis=1, keepdims=True)
    return w


def _gray(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.2989 + rgb[..., 1] * 0.5870 + rgb[..., 2] * 0.1140


def residual(rgb: np.ndarray) -> np.ndarray:
    """Noise residual of one image, one channel (luminance-weighted)."""
    if pywt is None:
        raise RuntimeError('PRNU needs PyWavelets: pip install "exif-inspect[prnu]"')
    sigma2 = SIGMA ** 2
    out = np.empty(rgb.shape, dtype=np.float32)
    for ch in range(3):
        coeffs = pywt.wavedec2(rgb[..., ch], WAVELET, level=LEVELS, mode="periodization")
        noise = [np.zeros_like(coeffs[0])]
        for bands in coeffs[1:]:
            noise.append(tuple(_wiener_detail(b, sigma2) for b in bands))
        out[..., ch] = _zero_mean(pywt.waverec2(noise, WAVELET, mode="periodization"))
    return _spectral_whiten(_gray(out))


def _spectral_whiten(k: np.ndarray) -> np.ndarray:
    """Divide out peaks in the magnitude spectrum, keeping the phase.

    The magnitude is compared with its local average; bins standing far
    above it (periodic artefacts) are scaled down to that average.
    """
    f = np.fft.fft2(k)
    mag = np.abs(f)
    size = 5
    pad = size // 2
    p = np.pad(mag, pad, mode="wrap")
    ii = np.pad(p, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    local = (ii[size:, size:] - ii[:-size, size:] - ii[size:, :-size] + ii[:-size, :-size]) / size ** 2
    scale = np.minimum(1.0, 2.0 * local / np.maximum(mag, 1e-12))
    return _zero_mean(np.real(np.fft.ifft2(f * scale)))


@dataclass
class Fingerprint:
    k: np.ndarray            # luminance fingerprint, crop x crop
    images: int
    crop: int
    source: tuple[int, int] | None = None     # (long side, short side) of the photos used
    skipped: list = field(default_factory=list, compare=False)   # (path, reason), not saved

    def save(self, path: str | Path) -> None:
        np.savez_compressed(path, k=self.k.astype(np.float32), images=self.images, crop=self.crop,
                            source=np.array(self.source or (0, 0)))

    @classmethod
    def load(cls, path: str | Path) -> "Fingerprint":
        d = np.load(path)
        source = tuple(int(v) for v in d["source"]) if "source" in d.files else None
        return cls(k=d["k"].astype(np.float64), images=int(d["images"]), crop=int(d["crop"]),
                   source=source if source and source[0] else None)


def _size(path) -> tuple[int, int]:
    """(long side, short side) of a stored image, read from its header."""
    with Image.open(path) as img:
        return max(img.size), min(img.size)


def build(paths, crop: int = CROP) -> Fingerprint:
    """Estimate a camera fingerprint from several images of the same camera.

    Only photos of the most common resolution are used: a phone that switches
    between, say, 24 and 12 megapixels maps sensor pixels to image pixels
    differently in each mode, and mixing them blurs the fingerprint.
    """
    paths = list(paths)
    sizes = {}
    for path in paths:
        try:
            sizes[path] = _size(path)
        except Exception:
            sizes[path] = None
    counts = {}
    for sz in sizes.values():
        if sz:
            counts[sz] = counts.get(sz, 0) + 1
    if not counts:
        raise ValueError("no readable images")
    source = max(counts, key=lambda sz: (counts[sz], sz[0] * sz[1]))
    skipped = []
    keep = []
    for path in paths:
        sz = sizes[path]
        if sz is None:
            skipped.append((str(path), "unreadable"))
        elif sz != source:
            skipped.append((str(path), f"{sz[0]}x{sz[1]}, the others are {source[0]}x{source[1]}"))
        else:
            keep.append(path)
    num = den = None
    used = 0
    shape = None
    for path in keep:
        rgb = load(path, crop)
        if shape is None:
            shape = rgb.shape
        if rgb.shape != shape:
            rgb = np.rot90(rgb)
            if rgb.shape != shape:
                skipped.append((str(path), "crop does not line up"))
                continue
        w = residual(rgb)
        lum = _gray(rgb)
        lum = np.where(lum >= SATURATED, 0.0, lum)   # clipped pixels carry no PRNU
        if num is None:
            num, den = np.zeros_like(w), np.zeros_like(w)
        num += w * lum
        den += lum * lum
        used += 1
    if not used:
        raise ValueError("no usable images")
    k = num / np.maximum(den, 1.0)
    return Fingerprint(k=_spectral_whiten(_zero_mean(k)), images=used, crop=shape[0],
                       source=source, skipped=skipped)


def _check_size(fp: Fingerprint, path) -> None:
    if fp.source and _size(path) != fp.source:
        long_side, short_side = _size(path)
        raise ValueError(f"photo is {long_side}x{short_side}, the fingerprint was built from "
                         f"{fp.source[0]}x{fp.source[1]} photos; resolutions must match")


def pce(a: np.ndarray, b: np.ndarray, exclude: int = 11) -> tuple[float, tuple[int, int]]:
    """Peak-to-correlation energy of the cyclic cross-correlation of a and b."""
    a = a - a.mean()
    b = b - b.mean()
    xc = np.real(np.fft.ifft2(np.fft.fft2(a) * np.conj(np.fft.fft2(b))))
    peak = xc[0, 0]
    yy, xx = np.unravel_index(int(np.argmax(np.abs(xc))), xc.shape)
    mask = np.ones(xc.shape, dtype=bool)
    r = exclude // 2
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            mask[dy % xc.shape[0], dx % xc.shape[1]] = False
    energy = np.mean(xc[mask] ** 2)
    sign = 1.0 if peak >= 0 else -1.0
    shift = (int(yy if yy <= xc.shape[0] // 2 else yy - xc.shape[0]),
             int(xx if xx <= xc.shape[1] // 2 else xx - xc.shape[1]))
    return float(sign * peak ** 2 / energy), shift


@dataclass
class Match:
    pce: float
    rotation: int        # degrees the image was turned to fit the fingerprint

    @property
    def same_camera(self) -> bool:
        return self.pce >= PCE_THRESHOLD


def match(fp: Fingerprint, path: str | Path) -> Match:
    """PCE between a fingerprint and an image, trying the four rotations."""
    _check_size(fp, path)
    rgb = load(path, fp.crop)
    if rgb.shape[0] != fp.crop:
        raise ValueError(f"image is smaller than the {fp.crop}-pixel fingerprint crop "
                         "(resized images cannot be matched)")
    w0, lum0 = residual(rgb), _gray(rgb)   # turning the residual = residual of the turned image
    best = Match(pce=-np.inf, rotation=0)
    for k in range(4):
        w, lum = np.rot90(w0, k), np.rot90(lum0, k)
        if w.shape != fp.k.shape:
            continue
        value, _ = pce(w, lum * fp.k)
        if value > best.pce:
            best = Match(pce=round(value, 1), rotation=90 * k)
    return best


# ---------------------------------------------------------------------------
# Localisation: which parts of an image lack the camera's pattern
# ---------------------------------------------------------------------------

@dataclass
class LocateResult:
    rotation: int
    pce: float
    block: int
    rho: np.ndarray          # correlation per block (grid)
    expected: np.ndarray     # predicted correlation per block
    z: np.ndarray            # (rho - expected) / spread
    reliable: np.ndarray     # blocks where the pattern should be visible
    flagged: np.ndarray      # reliable blocks with far too little pattern
    offset: tuple[int, int]  # crop position in the image, (x, y)
    box: tuple[int, int, int, int] | None   # largest flagged region, image pixels
    zmap: np.ndarray | None = None          # per-pixel score over the crop, stored orientation
    reliable_map: np.ndarray | None = None
    flag_map: np.ndarray | None = None


def _blocks(a: np.ndarray, size: int, step: int):
    h, w = a.shape
    ys = range(0, h - size + 1, step)
    xs = range(0, w - size + 1, step)
    return ys, xs


def locate(fp: "Fingerprint", path: str | Path, block: int | None = None,
           z_flag: float = -3.5, min_snr: float = 2.5) -> LocateResult:
    """Map where the fingerprint is present in an image.

    For each block the correlation between the noise residual W and I * K is
    compared with the correlation the model W = I*K + noise predicts for it,
    q * std(I*K) / std(W). The factor q is the median over all blocks, which
    assumes most of the image is untouched. Blocks far below their prediction
    are flagged; blocks whose prediction is too weak to tell (dark, clipped or
    busy areas) are marked unreliable.
    """
    _check_size(fp, path)
    if block is None:
        block = max(128, fp.crop // 4)     # a quarter of the crop: 256 at 1024, 512 at 2048
    rgb = load(path, fp.crop)
    w0, lum0 = residual(rgb), _gray(rgb)
    del rgb
    best = None
    for k in range(4):
        w, lum = np.rot90(w0, k), np.rot90(lum0, k)
        if w.shape != fp.k.shape:
            continue
        value, _ = pce(w, lum * fp.k)
        if best is None or value > best[0]:
            best = (value, k, w, lum)
    if best is None:
        raise ValueError("image size does not fit the fingerprint")
    value, k, w, lum = best
    x = lum * fp.k
    step = block // 4
    ys, xs = _blocks(w, block, step)
    gh, gw = len(ys), len(xs)
    rho = np.zeros((gh, gw))
    ratio = np.zeros((gh, gw))
    clip = lum >= SATURATED
    for i, y in enumerate(ys):
        for j, xx in enumerate(xs):
            a = w[y:y + block, xx:xx + block]
            b = x[y:y + block, xx:xx + block]
            a = a - a.mean()
            b = b - b.mean()
            rho[i, j] = (a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-12)
            # Under W = I*K + noise, rho is about |I*K| / |W|. Clipped pixels carry no pattern.
            live = ~clip[y:y + block, xx:xx + block]
            ratio[i, j] = (b[live].std() if live.any() else 0.0) / (a.std() + 1e-12)
    # rho = q * ratio, where q < 1 measures how accurate the fingerprint is.
    # The median ratio estimate ignores a minority of tampered blocks, however
    # extreme their ratio (a plain least-squares or Huber fit does not).
    ok = ratio > 0
    q = float(np.median(rho[ok] / ratio[ok])) if ok.any() else 0.0
    pred = q * ratio
    r = (rho - pred).ravel()
    spread = 1.4826 * np.median(np.abs(r - np.median(r))) + 1e-12
    z = (rho - pred) / spread
    reliable = pred > min_snr * spread        # blocks where the pattern should stand out
    flagged = reliable & (z < z_flag)

    # Pixel maps in fingerprint orientation, then turned back to the stored image.
    ch, cw = w.shape
    zmap = np.zeros((ch, cw))
    count = np.zeros((ch, cw))
    rel = np.zeros((ch, cw))
    flag = np.zeros((ch, cw))
    for i, yy in enumerate(ys):
        for j, xx in enumerate(xs):
            zmap[yy:yy + block, xx:xx + block] += z[i, j]
            count[yy:yy + block, xx:xx + block] += 1
            rel[yy:yy + block, xx:xx + block] += reliable[i, j]
            flag[yy:yy + block, xx:xx + block] += flagged[i, j]
    count = np.maximum(count, 1)
    zmap, rel, flag = zmap / count, rel / count >= 0.5, flag / count >= 0.5
    zmap, rel, flag = (np.rot90(m, -k) for m in (zmap, rel, flag))

    with Image.open(path) as img:
        sw, sh = img.size
    ox, oy = (sw - zmap.shape[1]) // 2, (sh - zmap.shape[0]) // 2
    box = None
    if flag.any():
        lab = _largest_component(flag[::8, ::8])
        ii, jj = np.nonzero(lab)
        box = (int(ox + jj.min() * 8), int(oy + ii.min() * 8),
               int(ox + (jj.max() + 1) * 8), int(oy + (ii.max() + 1) * 8))
    return LocateResult(rotation=90 * k, pce=round(float(value), 1), block=block, rho=rho,
                        expected=pred, z=z, reliable=reliable, flagged=flagged,
                        offset=(ox, oy), box=box, zmap=zmap, reliable_map=rel, flag_map=flag)


def render_locate(path: str | Path, res: LocateResult) -> Image.Image:
    """Image with flagged areas in red, areas too dark or busy to judge dimmed."""
    with Image.open(path) as img:
        base = np.asarray(img.convert("RGB"), dtype=np.float64)
    out = base * 0.55
    ox, oy = res.offset
    h, w = res.zmap.shape
    region = out[oy:oy + h, ox:ox + w]
    src = base[oy:oy + h, ox:ox + w]
    region[res.reliable_map] = src[res.reliable_map]
    red = np.array([255.0, 40.0, 40.0])
    region[res.flag_map] = src[res.flag_map] * 0.35 + red * 0.65
    out[oy:oy + h, ox:ox + w] = region
    pic = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    from PIL import ImageDraw
    d = ImageDraw.Draw(pic)
    lw = max(2, pic.width // 400)
    d.rectangle((ox, oy, ox + w - 1, oy + h - 1), outline=(255, 255, 255), width=lw)
    if res.box:
        d.rectangle(res.box, outline=(255, 220, 0), width=lw)
    return pic


def _largest_component(mask: np.ndarray) -> np.ndarray:
    """Largest 8-connected group of True cells."""
    seen = np.zeros_like(mask, dtype=bool)
    best = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    for sy in range(h):
        for sx in range(w):
            if not mask[sy, sx] or seen[sy, sx]:
                continue
            stack, comp = [(sy, sx)], []
            seen[sy, sx] = True
            while stack:
                cy, cx = stack.pop()
                comp.append((cy, cx))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
            if len(comp) > best.sum():
                best = np.zeros_like(mask, dtype=bool)
                for cy, cx in comp:
                    best[cy, cx] = True
    return best
