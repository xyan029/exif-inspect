"""Copy-move detection with SIFT keypoints, for clones that were scaled or rotated.

Block matching (copymove.py) compares square blocks pixel for pixel, so a
clone that was shrunk, enlarged or turned before pasting no longer matches.
SIFT keypoints are described in a way that does not change with scale or
rotation, so they still match. The detector:

1. finds SIFT keypoints on a grey copy of the image, resized to twice its
   size within 1000-2400 pixels on the longest side,
2. matches every keypoint against all the others and keeps a match only
   when it is clearly better than the next best one (the g2NN test),
3. drops matches between points that sit close together,
4. fits similarity transforms (shift + scale + rotation) to the matches with
   RANSAC, one after another, and keeps those backed by enough keypoints,
5. drops transforms whose keypoints have many look-alikes or also take part
   in other transforms, which is what repetitive textures (windows, tiles,
   railings) produce, and transforms whose two regions overlap.

Needs OpenCV: pip install "exif-inspect[keypoints]".
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .imagefile import opened

try:
    import cv2
except ImportError:  # pragma: no cover - exercised only without OpenCV
    cv2 = None


@dataclass
class KeypointClone:
    scale: float                      # size of region B relative to region A
    rotation: float                   # degrees, counter-clockwise, A -> B
    matches: int                      # keypoint matches that agree with the transform
    box_a: tuple[int, int, int, int]  # original pixels
    box_b: tuple[int, int, int, int]
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = field(repr=False, default_factory=list)


@dataclass
class KeypointResult:
    clones: list[KeypointClone] = field(default_factory=list)
    scale: float = 1.0                # analysis scale relative to the original
    keypoints: int = 0


def available() -> bool:
    return cv2 is not None


def _box(pts: np.ndarray, inv: float, pad: float) -> tuple[int, int, int, int]:
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    return (int((x0 - pad) * inv), int((y0 - pad) * inv),
            int(math.ceil((x1 + pad) * inv)), int(math.ceil((y1 + pad) * inv)))


def detect(
    path: str | Path,
    size: int | None = None,
    ratio: float = 0.6,
    min_dist: float = 30.0,
    min_matches: int = 8,
    ransac_px: float = 4.0,
    max_shared: float = 0.5,
    neighbors: int = 8,
    local: int = 6,
    max_twins: int = 3,
    twin_dist: float = 100.0,
) -> KeypointResult:
    if cv2 is None:
        raise RuntimeError('keypoint mode needs OpenCV: pip install "exif-inspect[keypoints]"')

    with opened(path) as img:
        grey = img.convert("L")
    # By default work at twice the original size, kept between 1000 and 2400
    # pixels on the longest side: enlarging lets a small, shrunken clone
    # carry enough keypoints, the cap keeps large photos fast.
    if size is None:
        size = min(max(2 * max(grey.size), 1000), 2400)
    scale = size / max(grey.size)
    if abs(scale - 1.0) > 0.01:
        grey = grey.resize((round(grey.width * scale), round(grey.height * scale)), Image.LANCZOS)
    arr = np.asarray(grey, dtype=np.uint8)

    sift = cv2.SIFT_create()
    kps, des = sift.detectAndCompute(arr, None)
    result = KeypointResult(scale=scale, keypoints=len(kps))
    if des is None or len(kps) < 2 * min_matches:
        return result

    pts = np.array([k.pt for k in kps], dtype=np.float32)
    k = min(neighbors + 1, len(kps))
    # Approximate nearest neighbours (FLANN k-d trees): exact matching is
    # quadratic and too slow for the tens of thousands of keypoints of a
    # large photo.
    knn = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=128)).knnMatch(des, des, k=k)

    # g2NN: walk down each point's neighbour list while each distance is
    # clearly smaller than the next one; every neighbour passed is a match.
    pairs: set[tuple[int, int]] = set()
    # How many near-identical twins each keypoint has. A pasted copy gives a
    # point one or two twins; tiles and lattices give it many. OpenCV scales
    # SIFT descriptors to length 512; unrelated points sit 200-350 apart.
    twins = np.zeros(len(kps), dtype=int)
    for row in knn:
        row = [m for m in row if m.trainIdx != m.queryIdx]
        if row:
            twins[row[0].queryIdx] = sum(m.distance < twin_dist for m in row)
        for a, b in zip(row, row[1:]):
            if a.distance >= ratio * b.distance:
                break
            i, j = a.queryIdx, a.trainIdx
            if np.hypot(*(pts[i] - pts[j])) >= min_dist:
                pairs.add((min(i, j), max(i, j)))
    if len(pairs) < min_matches:
        return result

    # Either end of a pair may be the source, so try both directions.
    pair_list = sorted(pairs)
    src_idx = np.array([p[0] for p in pair_list] + [p[1] for p in pair_list])
    dst_idx = np.array([p[1] for p in pair_list] + [p[0] for p in pair_list])
    pair_id = np.concatenate([np.arange(len(pair_list))] * 2)
    S = pts[src_idx, 0] + 1j * pts[src_idx, 1]
    D = pts[dst_idx, 0] + 1j * pts[dst_idx, 1]

    # A similarity transform is z -> a*z + t with complex a. Two matches fix
    # it. A cloned region's matches sit close together, so each hypothesis is
    # built from one match and a match whose source point is nearby; plain
    # RANSAC would rarely draw two matches from a small clone.
    rng = np.random.default_rng(0)
    seeds = np.arange(len(S)) if len(S) <= 3000 else rng.choice(len(S), 3000, replace=False)
    hyp_a, hyp_t = [], []
    for chunk in np.array_split(seeds, max(1, len(seeds) // 500)):
        dist = np.abs(S[chunk, None] - S[None, :])
        dist[dist < 5] = np.inf          # too close to fix scale and rotation
        near = np.argsort(dist, axis=1)[:, :local]
        s1, d1 = S[chunk, None], D[chunk, None]
        s2, d2 = S[near], D[near]
        a = (d2 - d1) / (s2 - s1)
        ok = np.isfinite(a) & (np.abs(a) > 0.1) & (np.abs(a) < 10)
        hyp_a.append(a[ok])
        hyp_t.append((d1 - a * s1)[ok])
    hyp_a = np.concatenate(hyp_a)
    hyp_t = np.concatenate(hyp_t)
    if len(hyp_a) == 0:
        return result
    inliers = np.abs(hyp_a[:, None] * S[None, :] + hyp_t[:, None] - D[None, :]) < ransac_px

    found = []
    alive = np.ones(len(S), dtype=bool)
    while True:
        score = (inliers & alive).sum(axis=1)
        best = int(score.argmax())
        if score[best] < 4:
            break
        hit = np.flatnonzero(inliers[best] & alive)
        # Refine on all inliers by least squares, then collect inliers again.
        model, _ = cv2.estimateAffinePartial2D(pts[src_idx[hit]], pts[dst_idx[hit]], method=cv2.LMEDS)
        if model is not None:
            a = model[0, 0] + 1j * model[1, 0]
            t = model[0, 2] + 1j * model[1, 2]
            refined = np.flatnonzero((np.abs(a * S + t - D) < ransac_px) & alive)
            if len(refined) >= len(hit):
                hit = refined
        else:
            a, t = hyp_a[best], hyp_t[best]
        used = np.unique(pair_id[hit])
        alive[np.isin(pair_id, used)] = False
        found.append((np.array([[a.real, -a.imag, t.real], [a.imag, a.real, t.imag]]), hit, len(used)))

    # Keypoints taking part in three or more transforms point to periodic
    # texture (a point pasted twice legitimately takes part in two).
    membership = Counter()
    for _, hit, _n in found:
        for n in set(src_idx[hit]) | set(dst_idx[hit]):
            membership[n] += 1

    inv = 1.0 / scale
    for model, hit, n_pairs in found:
        if n_pairs < min_matches:
            continue
        s_idx, d_idx = src_idx[hit], dst_idx[hit]
        a, b = float(model[0, 0]), float(model[1, 0])
        s = math.hypot(a, b)
        rot = -math.degrees(math.atan2(b, a))   # image y points down
        if s < 0.1 or s > 10:
            continue
        nodes = set(s_idx) | set(d_idx)
        shared = sum(membership[n] > 2 or twins[n] > max_twins for n in nodes) / len(nodes)
        if shared > max_shared:
            continue
        pa, pb = pts[s_idx], pts[d_idx]
        # Distinct locations, not one blob seen at several scales.
        if len(np.unique(np.round(pa / 2), axis=0)) < min_matches:
            continue
        if s > 1.0:                       # report from the larger region to the smaller
            pa, pb, s, rot = pb, pa, 1.0 / s, -rot
        rot = (rot + 180.0) % 360.0 - 180.0
        # Regions that overlap heavily are a short repeat (a lattice, a fence),
        # not a pasted copy.
        ix = max(0.0, min(pa[:, 0].max(), pb[:, 0].max()) - max(pa[:, 0].min(), pb[:, 0].min()))
        iy = max(0.0, min(pa[:, 1].max(), pb[:, 1].max()) - max(pa[:, 1].min(), pb[:, 1].min()))
        small = min(np.ptp(pa[:, 0]) * np.ptp(pa[:, 1]), np.ptp(pb[:, 0]) * np.ptp(pb[:, 1]))
        if small > 0 and ix * iy / small > 0.5:
            continue
        sizes = [kps[n].size for n in nodes]
        pad = float(np.median(sizes)) / 2
        result.clones.append(KeypointClone(
            scale=round(s, 3), rotation=round(rot, 1), matches=n_pairs,
            box_a=_box(pa, inv, pad), box_b=_box(pb, inv, pad * s),
            pairs=[(tuple(p), tuple(q)) for p, q in zip((pa * inv).tolist(), (pb * inv).tolist())],
        ))
    result.clones.sort(key=lambda c: -c.matches)
    return result


def draw(path: str | Path, result: KeypointResult) -> Image.Image:
    """Original image with region A in orange, region B in cyan and match lines."""
    with opened(path) as img:
        out = img.convert("RGB")
    d = ImageDraw.Draw(out)
    width = max(2, out.width // 300)
    for c in result.clones:
        for p, q in c.pairs:
            d.line([p, q], fill=(255, 255, 0), width=max(1, width // 2))
        d.rectangle(c.box_a, outline=(255, 140, 0), width=width)
        d.rectangle(c.box_b, outline=(0, 220, 255), width=width)
    return out
