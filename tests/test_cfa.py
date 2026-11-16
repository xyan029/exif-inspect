import numpy as np
from PIL import Image

from exif_inspect import cfa
from exif_inspect.cli import main


def _picture(size=(320, 256), seed=3):
    """A smooth colour picture with texture, so that residuals are not all zero."""
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([
        120 + 60 * np.sin(xx / 19) * np.cos(yy / 23),
        120 + 50 * np.cos((xx - yy) / 29),
        120 + 40 * np.sin(yy / 11 + xx / 37),
    ], axis=2)
    return np.clip(base + rng.normal(0, 12, (h, w, 3)), 0, 255).astype(np.float32)


def _conv(a, k):
    kh, kw = k.shape
    p = np.pad(a, ((kh // 2, kh // 2), (kw // 2, kw // 2)), mode="reflect")
    out = np.zeros_like(a)
    for i in range(kh):
        for j in range(kw):
            out += k[i, j] * p[i:i + a.shape[0], j:j + a.shape[1]]
    return out


def _demosaic(rgb):
    """Sample an RGGB Bayer mosaic from rgb and fill it back in bilinearly."""
    h, w = rgb.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    r = (yy % 2 == 0) & (xx % 2 == 0)
    b = (yy % 2 == 1) & (xx % 2 == 1)
    g = ~(r | b)
    out = np.zeros_like(rgb)
    k_rb = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], np.float32) / 4
    k_g = np.array([[0, 1, 0], [1, 4, 1], [0, 1, 0]], np.float32) / 4
    for ch, mask, k in ((0, r, k_rb), (1, g, k_g), (2, b, k_rb)):
        plane = np.where(mask, rgb[:, :, ch], 0)
        out[:, :, ch] = _conv(plane, k) / np.maximum(_conv(mask.astype(np.float32), k), 1e-6)
    return np.clip(out, 0, 255)


def _save(arr, path, quality=None):
    img = Image.fromarray(arr.astype(np.uint8))
    if quality is None:
        img.save(path)
    else:
        img.save(path, "JPEG", quality=quality)


PATCH = (96, 64, 224, 160)      # x0, y0, x1, y1 on even coordinates


def _forgery(path, kind, quality=None):
    raw = _picture()
    dem = _demosaic(raw)
    x0, y0, x1, y1 = PATCH
    if kind == "nocfa":
        dem[y0:y1, x0:x1] = raw[y0:y1, x0:x1]
    elif kind == "shift":
        dem[y0:y1, x0:x1] = dem[y0:y1, x0 + 1:x1 + 1].copy()
    _save(dem, path, quality)


def _inside(res, truth=PATCH):
    ii, jj = np.nonzero(res.flagged)
    xs = res.offset[0] + jj * res.block + res.block / 2
    ys = res.offset[1] + ii * res.block + res.block / 2
    return float(((xs >= truth[0]) & (xs < truth[2]) & (ys >= truth[1]) & (ys < truth[3])).mean())


def test_demosaiced_picture_has_strong_trace(tmp_path):
    path = tmp_path / "dem.png"
    _save(_demosaic(_picture()), path)
    res = cfa.analyze(path)
    assert res.trace == "strong"
    assert res.strength < -2                    # the interpolated lattice is x + y even
    assert res.lattice == "x + y even"
    assert res.agreement > 0.95
    assert res.box is None and res.flagged_fraction == 0


def test_plain_picture_has_no_trace(tmp_path):
    path = tmp_path / "plain.png"
    _save(_picture(), path)
    res = cfa.analyze(path)
    assert res.trace == "none"
    assert abs(res.strength) < 0.2
    assert res.lattice == "unknown"
    assert res.box is None


def test_weak_trace_survives_jpeg(tmp_path):
    path = tmp_path / "dem.jpg"
    _save(_demosaic(_picture()), path, quality=85)
    res = cfa.analyze(path)
    assert res.trace in ("strong", "weak")
    assert res.strength < 0
    assert res.box is None


def test_pasted_patch_without_trace_is_located(tmp_path):
    path = tmp_path / "paste.png"
    _forgery(path, "nocfa")
    res = cfa.analyze(path)
    assert res.trace == "strong"
    assert res.box is not None
    assert _inside(res) > 0.8
    x0, y0, x1, y1 = res.box
    assert x0 >= PATCH[0] - 32 and y0 >= PATCH[1] - 32
    assert x1 <= PATCH[2] + 32 and y1 <= PATCH[3] + 32
    assert cfa.render(res).size == (320, 256)


def test_patch_shifted_by_one_pixel_is_located(tmp_path):
    path = tmp_path / "shift.png"
    _forgery(path, "shift")
    res = cfa.analyze(path)
    assert res.box is not None
    assert _inside(res) > 0.8


def test_clipped_pixels_do_not_count(tmp_path):
    raw = _picture()
    raw[:, :, 1] = np.where(raw[:, :, 1] > 150, 255, raw[:, :, 1])     # blown highlights
    path = tmp_path / "clipped.png"
    _save(_demosaic(raw), path)
    res = cfa.analyze(path)
    assert res.trace == "strong"
    assert res.box is None


def test_cli_cfa(tmp_path, capsys):
    path = tmp_path / "paste.png"
    _forgery(path, "nocfa")
    assert main(["cfa", str(path)]) == 0
    out = capsys.readouterr().out
    assert "demosaicing trace : strong" in out
    assert "trace missing around" in out
    assert (tmp_path / "paste_cfa.png").exists()

    plain = tmp_path / "plain.png"
    _save(_picture(), plain)
    assert main(["cfa", str(plain)]) == 0
    assert "no demosaicing trace" in capsys.readouterr().out
    assert not (tmp_path / "plain_cfa.png").exists()

    assert main(["cfa", str(tmp_path / "missing.png")]) == 1
    assert "error" in capsys.readouterr().out
