"""Lighting direction from occluding contours, and whether objects agree on it.

Along the outline where an object hides what is behind it, the surface
turns away from the camera, so its normal lies in the image plane and
points straight out of the outline. Under one distant light and a matte
surface, the brightness just inside the outline is a linear function of
that normal: I = R * (N . L) + A, with L the light direction projected on
the image, R the surface reflectance and A the ambient light (Johnson and
Farid, "Exposing digital forgeries by detecting inconsistencies in
lighting", 2005). A least-squares fit along one outline gives L for that
object. All objects in a real photograph share one light, so an object
whose outline disagrees with the rest was lit somewhere else.

The tool finds outlines on its own: edge pixels are grouped into chains,
each chain's normals are pointed away from its centre, the brightness a
few pixels inside is sampled, and the fit is kept when the chain curves
enough to pin the direction down and the model explains the brightness.
Edges between two materials, printed patterns, folds and shadows also
make chains; the quality checks drop most of them, and the rest show as
scatter. Several lights, a nearby light, shiny surfaces and cast shadows
break the model, so an outlier is a lead to look at, not a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .imagefile import opened

SIZE = 1200            # longest side analysed, in pixels
MIN_PIXELS = 60        # edge pixels a chain needs
MIN_ARC = 100.0        # degrees of normal directions a chain must cover
MIN_FIT = 0.7          # share of the brightness variation the model must explain
MIN_CONTRAST = 10.0    # fitted R, in grey levels: how much the shading varies
MAX_SIGMA = 20.0       # degrees of uncertainty allowed on one chain
INSIDE = (2.5, 3.5, 4.5)   # distances inside the outline where brightness is sampled
DISAGREE = 35.0        # degrees from the common direction that count as an outlier
CONSENSUS = 0.6        # share of the evidence that must agree before anything is an outlier
OUTLIER_SIGMA = 8.0    # an outlier must be this well determined
OUTLIER_FIT = 0.8      # and fit the model this well

COMPASS = ["the right", "the upper right", "above", "the upper left",
           "the left", "the lower left", "below", "the lower right"]


@dataclass
class Contour:
    angle: float                 # light direction in degrees, 0 = from the right, 90 = from above
    sigma: float                 # its uncertainty in degrees
    pixels: int
    arc: float                   # degrees of normal directions covered
    fit: float                   # R^2 of the shading model
    contrast: float              # fitted R
    box: tuple[int, int, int, int]
    centre: tuple[float, float]
    points: np.ndarray = field(repr=False)   # (n, 2) x, y in image pixels
    outlier: bool = False
    deviation: float = 0.0       # degrees from the common direction
    region: int | None = None    # index of the --box it was chosen for


@dataclass
class LightResult:
    contours: list[Contour]
    chains: int                  # edge chains found before the quality checks
    direction: float | None      # common light direction, degrees, when there is one
    spread: float | None         # weighted circular spread of the agreeing contours
    consensus: bool              # whether enough outlines agree to call the rest outliers
    size: tuple[int, int]
    rgb: np.ndarray | None = field(default=None, repr=False)

    @property
    def outliers(self) -> list[Contour]:
        return [c for c in self.contours if c.outlier]


def compass(angle: float) -> str:
    return COMPASS[int(((angle + 22.5) % 360) // 45)]


def _blur(a: np.ndarray) -> np.ndarray:
    """Separable binomial blur, about a Gaussian with sigma 1."""
    k = np.array([1, 4, 6, 4, 1], dtype=np.float32) / 16
    p = np.pad(a, 2, mode="edge")
    rows = sum(k[i] * p[:, i:i + a.shape[1]] for i in range(5))
    return sum(k[i] * rows[i:i + a.shape[0], :] for i in range(5))


def _sobel(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = np.pad(a, 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]
          - p[:-2, :-2] - 2 * p[1:-1, :-2] - p[2:, :-2]) / 8
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]
          - p[:-2, :-2] - 2 * p[:-2, 1:-1] - p[:-2, 2:]) / 8
    return gx, gy


def _thin(mag: np.ndarray, gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    """Keep pixels whose gradient magnitude peaks along the gradient (non-maximum suppression)."""
    h, w = mag.shape
    p = np.pad(mag, 1, mode="constant")
    ang = (np.degrees(np.arctan2(gy, gx)) + 180) % 180
    out = np.zeros_like(mag, dtype=bool)
    shifts = {0: ((0, 1), (0, -1)), 45: ((1, 1), (-1, -1)),
              90: ((1, 0), (-1, 0)), 135: ((1, -1), (-1, 1))}
    for d, ((dy1, dx1), (dy2, dx2)) in shifts.items():
        sel = (np.abs(ang - d) <= 22.5) | (d == 0) & (ang > 157.5)
        n1 = p[1 + dy1:1 + dy1 + h, 1 + dx1:1 + dx1 + w]
        n2 = p[1 + dy2:1 + dy2 + h, 1 + dx2:1 + dx2 + w]
        out |= sel & (mag >= n1) & (mag >= n2)
    return out


def _label(mask: np.ndarray) -> np.ndarray:
    """8-connected component labels of a boolean mask, 0 for background."""
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    ys, xs = np.nonzero(mask)
    current = 0
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if labels[sy, sx]:
            continue
        current += 1
        labels[sy, sx] = current
        stack = [(sy, sx)]
        while stack:
            cy, cx = stack.pop()
            for ny in (cy - 1, cy, cy + 1):
                if ny < 0 or ny >= h:
                    continue
                for nx in (cx - 1, cx, cx + 1):
                    if 0 <= nx < w and mask[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = current
                        stack.append((ny, nx))
    return labels


def _sample(gray: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear sample, clamped to the image."""
    h, w = gray.shape
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x - x0, y - y0
    top = gray[y0, x0] * (1 - fx) + gray[y0, x0 + 1] * fx
    bottom = gray[y0 + 1, x0] * (1 - fx) + gray[y0 + 1, x0 + 1] * fx
    return top * (1 - fy) + bottom * fy


def edge_chains(gray: np.ndarray, low: float = 6.0, high: float = 14.0):
    """Edge pixel chains with their unit gradients.

    Returns the label image, the gradient components and the list of labels
    that contain at least one strong edge pixel (hysteresis).
    """
    g = _blur(gray)
    gx, gy = _sobel(g)
    mag = np.hypot(gx, gy)
    thin = _thin(mag, gx, gy) & (mag >= low)
    labels = _label(thin)
    strong = np.unique(labels[thin & (mag >= high)])
    return labels, gx, gy, [int(v) for v in strong if v]


def fit_chain(gray: np.ndarray, ys: np.ndarray, xs: np.ndarray,
              gx: np.ndarray, gy: np.ndarray) -> tuple | None:
    """Light direction for one chain of edge pixels, or None when it cannot be trusted."""
    n = len(ys)
    if n < MIN_PIXELS:
        return None
    nx, ny = gx[ys, xs], gy[ys, xs]
    norm = np.hypot(nx, ny) + 1e-9
    nx, ny = nx / norm, ny / norm
    cx, cy = xs.mean(), ys.mean()
    # Normals point out of the object, which the chain curves around.
    flip = (xs - cx) * nx + (ys - cy) * ny < 0
    nx[flip], ny[flip] = -nx[flip], -ny[flip]
    bins = np.unique(((np.degrees(np.arctan2(ny, nx)) + 360) % 360 // 10).astype(int))
    arc = 10.0 * len(bins)
    if arc < MIN_ARC:
        return None
    inside = np.mean([_sample(gray, xs - d * nx, ys - d * ny) for d in INSIDE], axis=0)
    design = np.column_stack([nx, ny, np.ones(n)])
    coef, *_ = np.linalg.lstsq(design, inside, rcond=None)
    pred = design @ coef
    sse = float(((inside - pred) ** 2).sum())
    sst = float(((inside - inside.mean()) ** 2).sum()) + 1e-9
    fit = 1 - sse / sst
    a, b = coef[0], coef[1]
    contrast = float(np.hypot(a, b))
    if fit < MIN_FIT or contrast < MIN_CONTRAST:
        return None
    # Neighbouring edge pixels are not independent samples; count one in three.
    dof = max(1.0, n / 3 - 3)
    cov = (sse / dof) * np.linalg.inv(design.T @ design)
    var = (b * b * cov[0, 0] + a * a * cov[1, 1] - 2 * a * b * cov[0, 1]) / (a * a + b * b) ** 2
    sigma = float(np.degrees(np.sqrt(max(var, 0))))
    if sigma > MAX_SIGMA:
        return None
    # Image y runs down, so flip it to make 90 degrees "from above".
    angle = float(np.degrees(np.arctan2(-b, a)) % 360)
    return angle, sigma, arc, fit, contrast, (cx, cy)


def _circular_mean(angles: np.ndarray, weights: np.ndarray) -> tuple[float, float]:
    rad = np.radians(angles)
    c = float((weights * np.cos(rad)).sum())
    s = float((weights * np.sin(rad)).sum())
    mean = float(np.degrees(np.arctan2(s, c)) % 360)
    r = min(1.0, np.hypot(c, s) / weights.sum())
    spread = abs(float(np.degrees(np.sqrt(-2 * np.log(max(r, 1e-9))))))
    return mean, spread


def _diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def analyze(path: str | Path, boxes: list[tuple[int, int, int, int]] | None = None) -> LightResult:
    """Estimate the light direction from outlines in an image.

    Without boxes every usable outline counts. With boxes, each box stands
    for one object and the best outline inside it (most pixels, weighted by
    how well the model fits) gives that object's direction.
    """
    with opened(path) as img:
        rgb = np.asarray(img.convert("RGB"))
    h, w = rgb.shape[:2]
    scale = min(1.0, SIZE / max(h, w))
    small = Image.fromarray(rgb)
    if scale < 1.0:
        small = small.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    gray = np.asarray(small.convert("L"), dtype=np.float32)

    labels, gx, gy, strong = edge_chains(gray)
    contours = []
    for lab in strong:
        ys, xs = np.nonzero(labels == lab)
        region = None
        if boxes:
            cx, cy = xs.mean() / scale, ys.mean() / scale
            hits = [i for i, (x0, y0, x1, y1) in enumerate(boxes)
                    if x0 <= cx <= x1 and y0 <= cy <= y1]
            if not hits:
                continue
            region = hits[0]
        res = fit_chain(gray, ys, xs, gx, gy)
        if res is None:
            continue
        angle, sigma, arc, fit, contrast, (cx, cy) = res
        pts = np.column_stack([xs, ys]) / scale
        box = (int(pts[:, 0].min()), int(pts[:, 1].min()),
               int(pts[:, 0].max()) + 1, int(pts[:, 1].max()) + 1)
        contours.append(Contour(round(angle, 1), round(sigma, 1), len(ys), arc, round(fit, 3),
                                round(contrast, 1), box, (cx / scale, cy / scale), pts,
                                region=region))
    if boxes:
        # One outline per box: the one with the most pixels, weighted by fit.
        best: dict[int, Contour] = {}
        for c in contours:
            if c.region not in best or c.pixels * c.fit > best[c.region].pixels * best[c.region].fit:
                best[c.region] = c
        contours = [best[i] for i in sorted(best)]

    direction = spread = None
    consensus = False
    if contours:
        angles = np.array([c.angle for c in contours])
        weights = np.array([c.pixels / (c.sigma + 2.0) ** 2 for c in contours])
        # The largest group of outlines that agree within DISAGREE of one of them.
        groups = [np.array([_diff(a, seed) <= DISAGREE for a in angles]) for seed in angles]
        group = max(groups, key=lambda g: weights[g].sum())
        consensus = weights[group].sum() >= CONSENSUS * weights.sum()
        if consensus:
            direction, spread = _circular_mean(angles[group], weights[group])
            for c in contours:
                c.deviation = round(_diff(c.angle, direction), 1)
                # Only a well-determined outline is called out; a loose fit
                # that merely differs is left as scatter.
                c.outlier = (c.deviation > max(DISAGREE, 3 * c.sigma)
                             and c.sigma <= OUTLIER_SIGMA and c.fit >= OUTLIER_FIT)
            direction, spread = round(direction, 1), round(spread, 1)
    return LightResult(contours, len(strong), direction, spread, consensus, (w, h), rgb)


def render(res: LightResult) -> Image.Image:
    """The picture with each used outline drawn and an arrow pointing toward its light."""
    pic = Image.fromarray(res.rgb).convert("RGB")
    d = ImageDraw.Draw(pic)
    w, h = res.size
    lw = max(1, round(max(w, h) / 600))
    length = max(30, max(w, h) // 15)
    for c in res.contours:
        colour = (255, 60, 60) if c.outlier else (60, 230, 90)
        step = max(1, len(c.points) // 400)
        for x, y in c.points[::step]:
            d.ellipse((x - lw, y - lw, x + lw, y + lw), fill=colour)
        _arrow(d, c.centre, c.angle, length, colour, lw * 2)
    if res.direction is not None:
        margin = length + 10
        _arrow(d, (margin, margin), res.direction, length, (255, 220, 0), lw * 3)
    return pic


def _arrow(d: ImageDraw.ImageDraw, start, angle: float, length: float, colour, width: int):
    rad = np.radians(angle)
    dx, dy = np.cos(rad), -np.sin(rad)
    x0, y0 = start
    x1, y1 = x0 + dx * length, y0 + dy * length
    d.line((x0, y0, x1, y1), fill=colour, width=width)
    head = length * 0.3
    for turn in (150, -150):
        t = np.radians(angle + turn)
        d.line((x1, y1, x1 + np.cos(t) * head, y1 - np.sin(t) * head), fill=colour, width=width)
