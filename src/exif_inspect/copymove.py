"""Copy-move forgery detection by block matching.

A copy-move forgery pastes one part of an image somewhere else in the same
image (cloning a person, hiding an object under nearby grass). The pasted
area and its source look almost identical, and they are offset from each
other by one constant shift. The detector:

1. scales the image down and slides a square block over every position,
2. describes each block by a 4x4 grid of mean intensities (robust to mild
   JPEG compression and noise),
3. sorts the descriptors so similar blocks land next to each other,
4. pairs similar blocks that sit far apart and votes on their shift,
5. reports shifts backed by many block pairs as cloned regions.

Flat areas (sky, walls) are skipped because they match everywhere. Strongly
repetitive textures such as brick or tiles can still produce false alarms.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .imagefile import opened


@dataclass
class Clone:
    shift: tuple[int, int]            # (dx, dy) from source to copy, original pixels
    pairs: int                        # block pairs voting for this shift
    pixel_diff: float                 # median per-pixel difference of matched blocks
    source_box: tuple[int, int, int, int]
    copy_box: tuple[int, int, int, int]


@dataclass
class CopyMoveResult:
    clones: list[Clone] = field(default_factory=list)
    scale: float = 1.0
    blocks_checked: int = 0


def _integral(a: np.ndarray) -> np.ndarray:
    ii = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    ii[1:, 1:] = a.cumsum(0).cumsum(1)
    return ii


def _box_sum(ii: np.ndarray, y: np.ndarray, x: np.ndarray, h: int, w: int) -> np.ndarray:
    return ii[y + h, x + w] - ii[y, x + w] - ii[y + h, x] + ii[y, x]


def detect(
    path: str | Path,
    block: int = 16,
    max_side: int = 640,
    min_std: float = 6.0,
    tol: float = 1.5,
    neighbors: int = 8,
    min_pairs: int = 200,
    max_shared: float = 0.5,
    blur: float = 1.0,
) -> CopyMoveResult:
    with opened(path) as img:
        gray = img.convert("L")
    scale = min(1.0, max_side / max(gray.size))
    if scale < 1.0:
        gray = gray.resize(
            (max(1, round(gray.width * scale)), max(1, round(gray.height * scale))),
            Image.LANCZOS,
        )
    # A light blur makes descriptors tolerant to sub-pixel misalignment after resizing.
    a = np.asarray(gray.filter(ImageFilter.GaussianBlur(blur)), dtype=np.float64)
    H, W = a.shape
    if H < block * 3 or W < block * 3:
        return CopyMoveResult(scale=scale)

    ii, ii2 = _integral(a), _integral(a * a)
    ys, xs = np.meshgrid(np.arange(H - block + 1), np.arange(W - block + 1), indexing="ij")
    Y, X = ys.ravel(), xs.ravel()

    # Skip low-texture blocks: they match each other everywhere.
    n = block * block
    mean = _box_sum(ii, Y, X, block, block) / n
    var = _box_sum(ii2, Y, X, block, block) / n - mean ** 2
    keep = np.sqrt(np.maximum(var, 0)) >= min_std
    Y, X = Y[keep], X[keep]
    if len(Y) < 2:
        return CopyMoveResult(scale=scale, blocks_checked=0)

    cell = block // 4
    feats = np.stack(
        [_box_sum(ii, Y + gy * cell, X + gx * cell, cell, cell) / (cell * cell)
         for gy in range(4) for gx in range(4)],
        axis=1,
    )

    order = np.lexsort(np.round(feats / 4).T[::-1])
    min_dist = block * 2
    votes: Counter = Counter()
    pair_store: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for k in range(1, neighbors + 1):
        i, j = order[:-k], order[k:]
        close = np.abs(feats[i] - feats[j]).mean(axis=1) < tol
        i, j = i[close], j[close]
        dx, dy = X[j] - X[i], Y[j] - Y[i]
        far = np.hypot(dx, dy) >= min_dist
        i, j, dx, dy = i[far], j[far], dx[far], dy[far]
        # Canonical direction so (a->b) and (b->a) count as one shift.
        flip = (dy < 0) | ((dy == 0) & (dx < 0))
        src = np.where(flip, j, i)
        dst = np.where(flip, i, j)
        dx, dy = np.where(flip, -dx, dx), np.where(flip, -dy, dy)
        for s, d, sx, sy in zip(src, dst, dx, dy):
            key = (int(sx), int(sy))
            votes[key] += 1
            pair_store.setdefault(key, []).append((int(s), int(d)))

    # Group shifts that differ by one pixel: resampling smears an exact offset.
    groups: list[tuple[int, int, int, np.ndarray, np.ndarray]] = []
    used: set[tuple[int, int]] = set()
    for (sx, sy), _ in votes.most_common():
        if (sx, sy) in used:
            continue
        group = [(sx + a_, sy + b_) for a_ in (-1, 0, 1) for b_ in (-1, 0, 1)
                 if (sx + a_, sy + b_) in votes]
        total = sum(votes[g] for g in group)
        if total < min_pairs:
            break
        used.update(group)
        pairs = [p for g in group for p in pair_store[g]]
        groups.append((sx, sy, total,
                       np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])))

    # Repetitive textures (railings, tiles, windows) match at many different
    # shifts, so their blocks show up in several groups. A cloned region
    # matches its copy at one shift only.
    members = [set(src.tolist()) | set(dst.tolist()) for *_, src, dst in groups]
    # How many groups each block is in. Counting once keeps this step linear in the number
    # of pairs; a picture of one repeated tile makes thousands of groups, and comparing each
    # against the union of all the others took minutes on it.
    in_groups: Counter = Counter()
    for m in members:
        in_groups.update(m)
    clones: list[Clone] = []
    for n_, (sx, sy, total, src_idx, dst_idx) in enumerate(groups):
        shared = sum(1 for b in members[n_] if in_groups[b] > 1) / len(members[n_])
        if shared > max_shared:
            continue
        sample = np.linspace(0, len(src_idx) - 1, min(200, len(src_idx))).astype(int)
        pixel_diff = float(np.median([
            np.abs(a[Y[s]:Y[s] + block, X[s]:X[s] + block]
                   - a[Y[t]:Y[t] + block, X[t]:X[t] + block]).mean()
            for s, t in zip(src_idx[sample], dst_idx[sample])
        ]))

        def box(idx: np.ndarray) -> tuple[int, int, int, int]:
            x0, y0 = X[idx].min(), Y[idx].min()
            x1, y1 = X[idx].max() + block, Y[idx].max() + block
            return tuple(int(round(v / scale)) for v in (x0, y0, x1, y1))

        clones.append(Clone(
            shift=(int(round(sx / scale)), int(round(sy / scale))),
            pairs=total,
            pixel_diff=round(pixel_diff, 2),
            source_box=box(src_idx),
            copy_box=box(dst_idx),
        ))
    return CopyMoveResult(clones=clones, scale=scale, blocks_checked=int(len(Y)))


def draw(path: str | Path, result: CopyMoveResult) -> Image.Image:
    """Original image with source boxes in orange, copies in cyan, and arrows."""
    with opened(path) as img:
        out = img.convert("RGB")
    d = ImageDraw.Draw(out)
    width = max(2, out.width // 300)
    for c in result.clones:
        d.rectangle(c.source_box, outline=(255, 140, 0), width=width)
        d.rectangle(c.copy_box, outline=(0, 220, 255), width=width)
        sx = (c.source_box[0] + c.source_box[2]) // 2
        sy = (c.source_box[1] + c.source_box[3]) // 2
        d.line([(sx, sy), (sx + c.shift[0], sy + c.shift[1])], fill=(255, 255, 0), width=width)
    return out
