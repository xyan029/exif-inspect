import numpy as np
from PIL import Image

from exif_inspect import noise
from exif_inspect.cli import main


def _scene(size=(384, 288), seed=5):
    """Soft shapes with a few edges, no noise yet."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    base = 110 + 50 * np.sin(xx / 41) * np.cos(yy / 37) + 25 * np.cos((xx + yy) / 53)
    base[(xx - 100) ** 2 + (yy - 90) ** 2 < 40 ** 2] += 60          # a bright disc
    base[(yy > 200) & (xx > 220)] -= 45                              # a dark corner
    return np.clip(base, 20, 230).astype(np.float32)


def _noisy(arr, sigma, seed=0):
    rng = np.random.default_rng(seed)
    return np.clip(arr + rng.normal(0, sigma, arr.shape), 0, 255)


def _save(gray, path, quality=None):
    img = Image.fromarray(gray.astype(np.uint8)).convert("RGB")
    if quality is None:
        img.save(path)
    else:
        img.save(path, "JPEG", quality=quality)


PATCH = (128, 96, 256, 192)     # x0, y0, x1, y1


def _splice(path, patch_sigma, scene_sigma=4.0):
    scene = _scene()
    out = _noisy(scene, scene_sigma, seed=1)
    x0, y0, x1, y1 = PATCH
    out[y0:y1, x0:x1] = _noisy(scene[y0:y1, x0:x1], patch_sigma, seed=2)
    _save(out, path)


def _inside(res, truth=PATCH):
    ii, jj = np.nonzero(res.flagged)
    xs = res.offset[0] + jj * res.block + res.block / 2
    ys = res.offset[1] + ii * res.block + res.block / 2
    return float(((xs >= truth[0]) & (xs < truth[2]) & (ys >= truth[1]) & (ys < truth[3])).mean())


def test_untouched_picture_has_one_level(tmp_path):
    path = tmp_path / "plain.png"
    _save(_noisy(_scene(), 4.0), path)
    res = noise.analyze(path)
    assert 2.0 < res.level < 5.0
    assert res.judged_fraction > 0.7
    assert res.box is None and res.kind is None
    assert not res.noisier.any() and not res.smoother.any()


def test_noisier_patch_is_located(tmp_path):
    path = tmp_path / "noisier.png"
    _splice(path, patch_sigma=14.0)
    res = noise.analyze(path)
    assert res.kind == "noisier"
    assert res.ratio > 1.8
    assert _inside(res) > 0.8
    assert noise.render(res).size == (384, 288)


def test_smoothed_patch_is_located(tmp_path):
    path = tmp_path / "smooth.png"
    _splice(path, patch_sigma=0.0)
    res = noise.analyze(path)
    assert res.kind == "smoother"
    assert res.ratio < 0.6
    assert _inside(res) > 0.8


def test_level_follows_brightness(tmp_path):
    scene = _scene()
    sigma = 1.0 + 6.0 * scene / 255          # photon-like: brighter means noisier
    rng = np.random.default_rng(3)
    path = tmp_path / "photon.png"
    _save(np.clip(scene + rng.normal(0, 1, scene.shape) * sigma, 0, 255), path)
    res = noise.analyze(path)
    assert res.rising
    assert res.box is None                    # a smooth trend is not an inconsistency


def test_jpeg_untouched_is_not_flagged(tmp_path):
    path = tmp_path / "plain.jpg"
    _save(_noisy(_scene(), 4.0), path, quality=88)
    res = noise.analyze(path)
    assert res.box is None


def test_cli_noise(tmp_path, capsys):
    path = tmp_path / "noisier.png"
    _splice(path, patch_sigma=14.0)
    assert main(["noise", str(path)]) == 0
    out = capsys.readouterr().out
    assert "noise : about" in out
    assert "more noise than areas like it" in out
    assert (tmp_path / "noisier_noise.png").exists()

    plain = tmp_path / "plain.png"
    _save(_noisy(_scene(), 4.0), plain)
    assert main(["noise", "-o", str(tmp_path / "x.png"), str(plain)]) == 0
    assert "no region differs" in capsys.readouterr().out
    assert not (tmp_path / "x.png").exists()

    assert main(["noise", str(tmp_path / "missing.png")]) == 1
    assert "error" in capsys.readouterr().out
