import numpy as np
import pytest
from PIL import Image, ImageFilter

pytest.importorskip("pywt")

from exif_inspect import prnu
from exif_inspect.cli import main

SIZE = 384


def _pattern(seed):
    """A synthetic sensor: a fixed multiplicative pattern, as PRNU is."""
    return np.random.default_rng(seed).normal(0, 0.03, (SIZE, SIZE, 1))


def _shot(k, scene, rng, path):
    noisy = scene * (1 + k) + rng.normal(0, 1.0, scene.shape)
    Image.fromarray(np.clip(noisy, 0, 255).astype("uint8")).save(path, quality=95)
    return path


def _flat(rng):
    base = rng.uniform(140, 200)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    ramp = base + 20 * np.sin(xx / 97 + rng.uniform(0, 6)) + 15 * np.cos(yy / 83)
    return np.repeat(ramp[..., None], 3, axis=2)


def _scene(rng):
    noise = (rng.random((SIZE, SIZE, 3)) * 160 + 60).astype("uint8")
    img = Image.fromarray(noise).filter(ImageFilter.GaussianBlur(4))
    return np.asarray(img, dtype=np.float64)


@pytest.fixture(scope="module")
def camera(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cam")
    rng = np.random.default_rng(0)
    k = _pattern(1)
    flats = [_shot(k, _flat(rng), rng, tmp / f"flat{i}.jpg") for i in range(12)]
    return prnu.build(flats, crop=SIZE), k, tmp


def test_same_camera_matches(camera):
    fp, k, tmp = camera
    rng = np.random.default_rng(5)
    path = _shot(k, _scene(rng), rng, tmp / "same.jpg")
    assert prnu.match(fp, path).same_camera


def test_other_camera_does_not_match(camera):
    fp, _, tmp = camera
    rng = np.random.default_rng(6)
    path = _shot(_pattern(2), _scene(rng), rng, tmp / "other.jpg")
    assert prnu.match(fp, path).pce < 20


def test_rotated_image_matches(camera):
    fp, k, tmp = camera
    rng = np.random.default_rng(7)
    path = _shot(k, _scene(rng), rng, tmp / "turned.jpg")
    Image.open(path).rotate(90, expand=True).save(tmp / "turned90.jpg", quality=95)
    result = prnu.match(fp, tmp / "turned90.jpg")
    assert result.same_camera and result.rotation in (90, 270)


def test_fingerprint_round_trip(camera, tmp_path):
    fp, _, _ = camera
    fp.save(tmp_path / "fp.npz")
    again = prnu.Fingerprint.load(tmp_path / "fp.npz")
    assert again.images == fp.images and again.crop == SIZE
    assert np.allclose(again.k, fp.k, atol=1e-5)


def test_cli_prnu(camera, capsys):
    fp, k, tmp = camera
    rng = np.random.default_rng(8)
    same = _shot(k, _scene(rng), rng, tmp / "cli_same.jpg")
    other = _shot(_pattern(3), _scene(rng), rng, tmp / "cli_other.jpg")
    flats = sorted(str(p) for p in tmp.glob("flat*.jpg"))
    assert main(["prnu", "build", "--crop", str(SIZE), "-o", str(tmp / "cam.npz"), *flats]) == 0
    assert main(["prnu", "match", str(tmp / "cam.npz"), str(same), str(other)]) == 0
    out = capsys.readouterr().out
    assert "fingerprint from 12 of 12 images" in out
    assert ": same camera" in out and "no evidence" in out
