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

Needs PyWavelets: pip install "exif-inspect[prnu]".
"""

from __future__ import annotations

from dataclasses import dataclass
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
CROP = 2048          # side of the centre crop analysed (smaller images use what fits)
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
        arr = np.asarray(img.convert("RGB"), dtype=np.float64)
    h, w = arr.shape[:2]
    if min(h, w) < crop:
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
    out = np.empty(rgb.shape, dtype=np.float64)
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

    def save(self, path: str | Path) -> None:
        np.savez_compressed(path, k=self.k.astype(np.float32), images=self.images, crop=self.crop)

    @classmethod
    def load(cls, path: str | Path) -> "Fingerprint":
        d = np.load(path)
        return cls(k=d["k"].astype(np.float64), images=int(d["images"]), crop=int(d["crop"]))


def build(paths, crop: int = CROP) -> Fingerprint:
    """Estimate a camera fingerprint from several images of the same camera."""
    num = den = None
    used = 0
    shape = None
    for path in paths:
        rgb = load(path, crop)
        if shape is None:
            shape = rgb.shape
        if rgb.shape != shape:
            rgb = np.rot90(rgb)
            if rgb.shape != shape:
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
    return Fingerprint(k=_spectral_whiten(_zero_mean(k)), images=used, crop=shape[0])


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
