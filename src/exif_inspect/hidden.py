"""Data hidden in an image file: bytes after the end of the picture, and LSB steganography.

Two places to hide things. The simplest is after the picture: every viewer
stops reading a JPEG at its EOI marker, a PNG at its IEND chunk and a WebP
at the end of its RIFF container, so whatever follows (an archive, a second
image, a text file) rides along unseen. The other is inside the pixels:
replacing the least significant bit of each sample changes the picture by
at most one grey level, invisibly, but it changes the statistics of the
sample values in ways that can be measured.

Three steganalysis views are offered for lossless pictures:

- the LSB planes drawn as pictures (the "visual attack"): a natural image's
  lowest bit still follows the content, an embedded payload is pure noise;
- the chi-square attack of Westfeld and Pfitzmann (1999), which notices that
  embedding evens out the counts of each pair of values 2k and 2k+1, run
  in sequence so that a payload written from the top of the image shows as
  the stretch of the picture where the test fires;
- RS analysis (Fridrich, Goljan and Du, 2001), which flips the lowest bits
  of small pixel groups and counts how many become smoother or noisier, and
  from the asymmetry estimates how much of the picture carries a payload
  even when the bits were spread at random.

JPEG does not store samples, so LSB methods do not apply to it; its
steganography lives in DCT coefficients and is not covered here.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .imagefile import opened

from . import jpegdct

# --- trailing data -----------------------------------------------------------

SNIFF = [
    (b"PK\x03\x04", "ZIP archive (also DOCX, APK, JAR)"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"GIF8", "GIF image"),
    (b"RIFF", "RIFF container (WebP, WAV, AVI)"),
    (b"%PDF", "PDF document"),
    (b"Rar!", "RAR archive"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip archive"),
    (b"\x1f\x8b", "gzip data"),
    (b"BZh", "bzip2 data"),
    (b"\x00\x00\x00\x1cftyp", "ISO media (MP4, HEIC)"),
    (b"\x00\x00\x00\x18ftyp", "ISO media (MP4, HEIC)"),
    (b"MZ", "Windows executable"),
    (b"\x7fELF", "ELF executable"),
    (b"<?xml", "XML text"),
    (b"<x:xmpmeta", "XMP packet"),
]


@dataclass
class Trailer:
    offset: int                 # where the picture ends
    size: int                   # bytes after it
    kind: str                   # what the bytes look like
    sample: str                 # printable preview of the start
    expected: bool = False      # a second image that the container announces (MPF)


def _jpeg_end(data: bytes) -> tuple[int | None, bool]:
    """Offset just past the first EOI marker, and whether an MPF segment announces more images."""
    head = jpegdct.walk(data)
    mpf = any(marker == 0xE2 and payload[:4] == b"MPF\x00" for _, marker, payload in head.segments)
    if head.scan is None:
        return head.eoi, mpf
    # After the first scan come restart markers and stuffed bytes, which are data, and in a
    # file of several scans more segments, which are skipped by their length.
    i, n = head.scan, len(data)
    while True:
        j = jpegdct.end_of_scan(data, i)
        if j + 1 >= n:
            return None, mpf
        marker = data[j + 1]
        if marker == 0xD9:
            return j + 2, mpf
        if marker in jpegdct.STANDALONE:
            i = j + 2
            continue
        if j + 4 > n:
            return None, mpf
        length = (data[j + 2] << 8) | data[j + 3]
        if length < 2:
            return None, mpf
        i = j + 2 + length


def _png_end(data: bytes) -> int | None:
    i = 8
    while i + 8 <= len(data):
        length, = struct.unpack(">I", data[i:i + 4])
        ctype = data[i + 4:i + 8]
        i += 12 + length
        if ctype == b"IEND":
            return i
    return None


# Chunks the WebP container format defines; a decoder reads these and passes over the rest.
_WEBP_CHUNKS = frozenset((b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF", b"ICCP", b"EXIF", b"XMP "))


def _webp_end(data: bytes) -> int | None:
    """Offset just past the last chunk a WebP decoder reads.

    The size field in the RIFF header is the file's own claim and is trusted only as an
    upper bound: enlarged to cover appended bytes, it would otherwise make them part of the
    picture. A file in the simple format is one VP8 or VP8L chunk; in the extended format
    the chunks are followed while they carry a name the format defines.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    size, = struct.unpack("<I", data[4:8])
    limit = min(len(data), 8 + size + (size & 1))
    i = 12
    simple = data[12:16] in (b"VP8 ", b"VP8L")
    while i + 8 <= limit and data[i:i + 4] in _WEBP_CHUNKS:
        length, = struct.unpack("<I", data[i + 4:i + 8])
        i = min(len(data), i + 8 + length + (length & 1))
        if simple:
            break
    return i if i > 12 else None


def _gif_end(data: bytes) -> int | None:
    """Offset just past the GIF trailer byte, found by following the blocks.

    A search for the last 0x3B in the file would take any appended data that ends in that
    byte for part of the picture. Decoders stop at the trailer block, or at the first byte
    that starts no block, and what follows either is not shown.
    """
    n = len(data)
    if n < 13 or data[:3] != b"GIF":
        return None
    i = 13
    if data[10] & 0x80:                             # global colour table
        i += 3 << ((data[10] & 7) + 1)
    while i < n:
        block = data[i]
        if block == 0x3B:                           # trailer
            return i + 1
        if block == 0x21:                           # extension: label, then sub-blocks
            i += 2
        elif block == 0x2C:                         # image: descriptor, colour table, code size
            if i + 10 > n:
                return None
            flags = data[i + 9]
            i += 10 + (3 << ((flags & 7) + 1) if flags & 0x80 else 0) + 1
        else:
            return i
        while True:                                 # sub-blocks, closed by one of length zero
            if i >= n:
                return None
            length = data[i]
            i += 1 + length
            if length == 0:
                break
    return None


def sniff(blob: bytes) -> str:
    for magic, name in SNIFF:
        if blob.startswith(magic):
            return name
    head = blob[:512]
    if head and all(32 <= b < 127 or b in (9, 10, 13) for b in head):
        return "plain text"
    try:
        head.decode("utf-8")
        if sum(32 <= b < 127 or b in (9, 10, 13) for b in head) > 0.8 * len(head):
            return "UTF-8 text"
    except UnicodeDecodeError:
        pass
    return "binary data"


def trailer(data: bytes, fmt: str | None) -> Trailer | None:
    """Bytes after the end of the picture, or None when there are none worth a word."""
    expected = False
    if fmt in ("JPEG", "MPO"):
        end, mpf = _jpeg_end(data)
        expected = mpf
    elif fmt == "PNG":
        end = _png_end(data)
    elif fmt == "WEBP":
        end = _webp_end(data)
    elif fmt == "GIF":
        end = _gif_end(data)
    else:
        return None
    if end is None or end >= len(data):
        return None
    rest = data[end:]
    # A little padding of zeros or whitespace is not a trailer.
    if len(rest) <= 16 and not rest.strip(b"\x00\r\n\t "):
        return None
    kind = sniff(rest)
    if kind == "JPEG image" and expected:
        expected = True
    else:
        expected = False
    sample = "".join(chr(b) if 32 <= b < 127 else "." for b in rest[:48])
    return Trailer(end, len(rest), kind, sample, expected)


# --- LSB steganalysis --------------------------------------------------------

BLOCK = 4096            # samples per chi-square block, in scan order
CHI_THRESHOLD = 0.8     # probability of embedding at which a block counts as embedded
RS_GROUP = 4            # pixels per RS group
RS_MASK = (0, 1, 1, 0)  # the flipping mask
RS_FLAG = 0.05          # RS rate from which a payload is reported (clean pictures stay under 0.03)


@dataclass
class ChiBlock:
    start: int              # first sample index
    probability: float      # 1 - chi-square p-value: closeness to the even pair counts of a payload


@dataclass
class LsbResult:
    channels: int
    samples: int
    chi_embedded: float             # share of samples in blocks that look embedded (sequential attack)
    chi_prefix: int                 # samples at the start of the scan that look embedded, in a row
    chi_blocks: list[ChiBlock] = field(default_factory=list)
    rs_rate: float = 0.0            # estimated share of pixels carrying a payload (random LSB)
    rs_per_channel: list[float] = field(default_factory=list)
    lsb_mean: float = 0.5           # share of ones in the lowest bit
    lsb_entropy: float = 0.0        # bits per LSB over 8-sample bytes, 8.0 for pure noise
    planes: Image.Image | None = field(default=None, repr=False)


def _chi_square(values: np.ndarray) -> float:
    """Probability that the pairs of values (2k, 2k+1) are as evened out as a payload makes them."""
    counts = np.bincount(values, minlength=256).astype(np.float64)
    even, odd = counts[0::2], counts[1::2]
    expected = (even + odd) / 2
    keep = expected > 0
    if keep.sum() < 2:
        return 0.0
    chi = float(((even[keep] - expected[keep]) ** 2 / expected[keep]).sum())
    dof = int(keep.sum()) - 1
    # A payload evens the pairs out, so a small chi-square (a large p-value) means embedding.
    return _chi2_sf(chi, dof)


def _chi2_sf(x: float, k: int) -> float:
    """Survival function of the chi-square distribution, series for the regularised gamma."""
    if x <= 0:
        return 1.0
    a, half = k / 2.0, x / 2.0
    # Wilson-Hilferty approximation is enough: the test is read against 0.8, not 0.05.
    if k > 30:
        from math import erf, sqrt
        z = ((x / k) ** (1 / 3) - (1 - 2 / (9 * k))) / sqrt(2 / (9 * k))
        return 0.5 * (1 - erf(z / sqrt(2)))
    # Regularised lower incomplete gamma by series.
    from math import exp, lgamma, log
    term, total, n = 1.0 / a, 1.0 / a, 1
    while n < 500:
        term *= half / (a + n)
        total += term
        if term < total * 1e-12:
            break
        n += 1
    p_lower = exp(-half + a * log(half) - lgamma(a)) * total
    return max(0.0, min(1.0, 1.0 - p_lower))


def chi_square_attack(samples: np.ndarray, block: int = BLOCK) -> tuple[list[ChiBlock], float, int]:
    """Run the pair-of-values test over consecutive blocks of samples in scan order."""
    blocks: list[ChiBlock] = []
    n = len(samples)
    embedded = 0
    prefix, prefix_open = 0, True
    for start in range(0, n - block + 1, block):
        p = _chi_square(samples[start:start + block])
        blocks.append(ChiBlock(start, round(p, 3)))
        if p >= CHI_THRESHOLD:
            embedded += block
            if prefix_open:
                prefix += block
        else:
            prefix_open = False
    return blocks, (embedded / n if n else 0.0), prefix


def _rs_counts(plane: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
    """Share of groups that get noisier (regular) and smoother (singular) under the mask flip."""
    h, w = plane.shape
    w = w - w % RS_GROUP
    g = plane[:, :w].astype(np.int32).reshape(h, w // RS_GROUP, RS_GROUP)

    def flipped(x: np.ndarray, m: np.ndarray) -> np.ndarray:
        out = x.copy()
        pos = m > 0
        out[..., pos] = x[..., pos] ^ 1                       # 0<->1, 2<->3, ...
        neg = m < 0
        out[..., neg] = ((x[..., neg] + 1) ^ 1) - 1           # -1<->0, 1<->2, ...
        return out

    f0 = np.abs(np.diff(g, axis=2)).sum(axis=2)
    f1 = np.abs(np.diff(flipped(g, mask), axis=2)).sum(axis=2)
    total = f0.size
    return float((f1 > f0).sum() / total), float((f1 < f0).sum() / total)


def rs_analysis(plane: np.ndarray) -> float:
    """Estimated share of pixels whose lowest bit carries a random payload."""
    mask = np.array(RS_MASK)
    rm, sm = _rs_counts(plane, mask)            # the picture, at p/2 flipped bits
    rnm, snm = _rs_counts(plane, -mask)
    flipped_all = plane ^ 1                     # the picture at 1 - p/2
    rm1, sm1 = _rs_counts(flipped_all, mask)
    rnm1, snm1 = _rs_counts(flipped_all, -mask)
    d0, d1 = rm - sm, rm1 - sm1                 # R_M - S_M at p/2 and at 1 - p/2
    dn0, dn1 = rnm - snm, rnm1 - snm1           # the same with the negative mask
    # Fridrich's quadratic in x = p/2:
    # 2(d1 + d0) x^2 + (dn0 - dn1 - d1 - 3 d0) x + d0 - dn0 = 0, root of smaller magnitude
    a = 2 * (d1 + d0)
    b = dn0 - dn1 - d1 - 3 * d0
    c = d0 - dn0
    # At full embedding R_M and S_M meet (d0 and d1 vanish) and the quadratic
    # loses its real roots; the share of the clean asymmetry that is left,
    # d0 / dn0, then stands in for the estimate.
    fallback = 1.0 - min(1.0, max(0.0, d0 / dn0)) if dn0 > 1e-6 else 0.0
    if abs(a) < 1e-9:
        if abs(b) < 1e-9:
            return fallback
        z = -c / b
    else:
        disc = b * b - 4 * a * c
        if disc < 0:
            return fallback
        roots = [(-b + s * disc ** 0.5) / (2 * a) for s in (1, -1)]
        z = min(roots, key=abs)
    if abs(z - 0.5) < 1e-9:
        return 1.0
    p = z / (z - 0.5)
    if not 0.0 <= p <= 1.0:
        return fallback
    return float(p)


def lsb_planes(arr: np.ndarray) -> Image.Image:
    """The lowest bit of each channel drawn as a black-and-white picture, side by side."""
    h, w = arr.shape[:2]
    channels = arr.shape[2] if arr.ndim == 3 else 1
    planes = [(arr[:, :, c] if arr.ndim == 3 else arr) & 1 for c in range(channels)]
    out = Image.new("L", (w * channels + 4 * (channels - 1), h), 128)
    for c, plane in enumerate(planes):
        out.paste(Image.fromarray((plane * 255).astype(np.uint8)), (c * (w + 4), 0))
    return out


def analyze_lsb(path: str | Path) -> LsbResult:
    """Steganalysis of the lowest bits of a lossless picture."""
    with opened(path) as img:
        if img.mode in ("1", "P", "I", "F", "I;16"):
            img = img.convert("RGB")
        elif img.mode == "LA":
            img = img.convert("L")
        elif img.mode == "RGBA":
            img = img.convert("RGB")
        arr = np.asarray(img)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    h, w, channels = arr.shape
    samples = arr.reshape(-1)                      # scan order: row by row, channel by channel
    blocks, embedded, prefix = chi_square_attack(samples)
    rates = [rs_analysis(arr[:, :, c]) for c in range(channels)]
    bits = samples & 1
    n8 = len(bits) - len(bits) % 8
    packed = np.packbits(bits[:n8].reshape(-1, 8), axis=1).reshape(-1)
    counts = np.bincount(packed, minlength=256) / max(1, len(packed))
    nz = counts[counts > 0]
    entropy = float(-(nz * np.log2(nz)).sum())
    return LsbResult(channels=channels, samples=int(len(samples)), chi_embedded=round(embedded, 3),
                     chi_prefix=int(prefix), chi_blocks=blocks,
                     rs_rate=round(float(np.mean(rates)), 3),
                     rs_per_channel=[round(r, 3) for r in rates],
                     lsb_mean=round(float(bits.mean()), 4), lsb_entropy=round(entropy, 3),
                     planes=lsb_planes(arr))


@dataclass
class Verdict:
    flag: bool
    message: str


def verdict(res: LsbResult) -> Verdict:
    """One sentence on whether the lowest bits carry a payload, and where."""
    prefix = res.chi_prefix / res.samples if res.samples else 0.0
    if res.rs_rate >= RS_FLAG:
        where = ""
        if prefix >= 0.05 and abs(prefix - res.rs_rate) <= 0.15:
            where = f", written in sequence from the start of the picture ({prefix:.0%} of the scan)"
        return Verdict(True, f"LSB payload likely: RS analysis estimates about {res.rs_rate:.0%} "
                             f"of the samples{where}.")
    return Verdict(False, f"No sign of an LSB payload (RS estimate {res.rs_rate:.0%}).")
