"""Duplicates and near-duplicates across folders, and the best original in each group.

Case work piles up copies: the same photo saved at three qualities, resized
for a message, rotated by a viewer, byte-for-byte duplicated across folders.
This groups them. Exact duplicates share a SHA-256. Near-duplicates share a
perceptual hash: the picture is shrunk to 32x32 grey, its 2-D DCT taken,
and the 64 lowest frequencies apart from the mean are thresholded at their
median into 64 bits (pHash). Re-saving, resizing, mild colour changes and
watermarks move only a few bits; different pictures differ in about half.
A second hash from the gradient between neighbouring pixels of a 9x8
thumbnail (dHash) must agree before two files are grouped, which keeps
smooth pictures with similar layouts apart. Both hashes are taken at the
four rotations and the closest pair counts, so a turned copy is found.

Within a group the likely original is the file with the most pixels,
then the higher JPEG quality, then the most metadata, then the earliest
modification time. Crops are not duplicates by these hashes; use
`compare` for a pair that may be crop and source.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

EXTENSIONS = {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff",
              ".heic", ".heif"}
DISTANCE = 10          # pHash bits that may differ within a group
DHASH_DISTANCE = 16    # and dHash bits


@dataclass
class Entry:
    path: str
    sha256: str
    size: tuple[int, int]
    fmt: str
    bytes: int
    quality: int | None            # closest libjpeg quality for JPEGs
    tags: int                      # EXIF tags present
    mtime: float
    phash: list[int]               # one 64-bit hash per rotation
    dhash: list[int]
    error: str | None = None


@dataclass
class Group:
    members: list[Entry]
    best: Entry
    distances: dict[str, tuple[int, int, int]] = field(default_factory=dict)  # path -> (phash, dhash, rotation)
    exact: bool = False           # all members byte-identical


@dataclass
class DupesResult:
    scanned: int
    unreadable: list[tuple[str, str]]
    groups: list[Group]
    singles: int


_DCT = None


def _dct_matrix(n: int = 32) -> np.ndarray:
    global _DCT
    if _DCT is None:
        k = np.arange(n)[:, None]
        x = np.arange(n)[None, :]
        m = np.cos(np.pi * (2 * x + 1) * k / (2 * n)) * np.sqrt(2 / n)
        m[0] /= np.sqrt(2)
        _DCT = m
    return _DCT


def phash(grey32: np.ndarray) -> int:
    """64-bit perceptual hash of a 32x32 grey picture."""
    m = _dct_matrix(32)
    coeff = m @ grey32 @ m.T
    low = coeff[:8, :8].copy()
    vals = low.ravel()[1:]                     # drop the mean term
    bits = vals > np.median(vals)
    return int("".join("1" if b else "0" for b in bits), 2)


def dhash(grey9x8: np.ndarray) -> int:
    """64-bit difference hash of a 9-wide, 8-high grey picture."""
    bits = (grey9x8[:, 1:] > grey9x8[:, :-1]).ravel()
    return int("".join("1" if b else "0" for b in bits), 2)


def hashes(img: Image.Image) -> tuple[list[int], list[int]]:
    """pHash and dHash of a picture at its four rotations."""
    grey = img.convert("L")
    ph, dh = [], []
    for rot in (0, 90, 180, 270):
        g = grey.rotate(rot, expand=True) if rot else grey
        small = np.asarray(g.resize((32, 32), Image.LANCZOS), dtype=np.float64)
        ph.append(phash(small))
        tiny = np.asarray(g.resize((9, 8), Image.LANCZOS), dtype=np.float64)
        dh.append(dhash(tiny))
    return ph, dh


def distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def closest(e1: Entry, e2: Entry) -> tuple[int, int, int]:
    """Smallest (pHash, dHash) distance over the rotations of e2, with the rotation."""
    best = (65, 65, 0)
    for r, (p, d) in enumerate(zip(e2.phash, e2.dhash)):
        dp, dd = distance(e1.phash[0], p), distance(e1.dhash[0], d)
        if dp < best[0] or (dp == best[0] and dd < best[1]):
            best = (dp, dd, 90 * r)
    return best


def read_entry(path: Path) -> Entry:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    stat = path.stat()
    try:
        with Image.open(path) as img:
            fmt = img.format or "?"
            size = img.size
            quality = None
            if fmt in ("JPEG", "MPO"):
                from .quant import analyze
                q = analyze(img)
                quality = q.closest_quality if q else None
            try:
                tags = len(img.getexif())
            except Exception:
                tags = 0
            ph, dh = hashes(img)
    except Exception as exc:
        return Entry(str(path), sha, (0, 0), "?", len(data), None, 0, stat.st_mtime, [], [],
                     error=str(exc))
    return Entry(str(path), sha, size, fmt, len(data), quality, tags, stat.st_mtime, ph, dh)


def walk(roots) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        root = Path(root)
        if root.is_file():
            files.append(root)
            continue
        for dirpath, _, names in os.walk(root):
            for name in sorted(names):
                if Path(name).suffix.lower() in EXTENSIONS:
                    files.append(Path(dirpath) / name)
    return files


def _rank(e: Entry) -> tuple:
    """Sort key for the likely original: most pixels, best quality, most metadata, oldest."""
    return (-(e.size[0] * e.size[1]), -(e.quality or 0), -e.tags, e.mtime)


def find(roots, max_distance: int = DISTANCE, max_dhash: int = DHASH_DISTANCE) -> DupesResult:
    """Scan the folders and group the duplicates."""
    paths = walk(roots)
    entries = [read_entry(p) for p in paths]
    good = [e for e in entries if e.error is None]
    bad = [(e.path, e.error or "") for e in entries if e.error]

    # Union-find over pairs that are close enough.
    parent = list(range(len(good)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(good)):
        for j in range(i + 1, len(good)):
            if good[i].sha256 == good[j].sha256:
                parent[root(j)] = root(i)
                continue
            dp, dd, _ = closest(good[i], good[j])
            if dp <= max_distance and dd <= max_dhash:
                parent[root(j)] = root(i)

    clusters: dict[int, list[Entry]] = {}
    for i, e in enumerate(good):
        clusters.setdefault(root(i), []).append(e)
    groups = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members.sort(key=_rank)
        best = members[0]
        dist = {m.path: closest(best, m) for m in members}
        exact = len({m.sha256 for m in members}) == 1
        groups.append(Group(members, best, dist, exact))
    groups.sort(key=lambda g: (-len(g.members), g.best.path))
    singles = sum(1 for m in clusters.values() if len(m) == 1)
    return DupesResult(len(entries), bad, groups, singles)
