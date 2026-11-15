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
    assert "fingerprint from 12 of 12 images (384x384)" in out
    assert ": same camera" in out and "no evidence" in out


def test_locate_finds_a_filled_area(camera):
    fp, k, tmp = camera
    rng = np.random.default_rng(9)
    path = _shot(k, _scene(rng), rng, tmp / "loc.jpg")
    img = Image.open(path)
    # Replace a corner with content that never passed through the sensor,
    # as AI object removal does.
    patch = Image.fromarray(np.full((144, 144, 3), 150, dtype="uint8")).filter(ImageFilter.GaussianBlur(2))
    noisy = np.asarray(patch, dtype=float) + np.random.default_rng(1).normal(0, 1.0, (144, 144, 3))
    img.paste(Image.fromarray(np.clip(noisy, 0, 255).astype("uint8")), (220, 220))
    img.save(tmp / "loc_edit.jpg", quality=95)
    res = prnu.locate(fp, tmp / "loc_edit.jpg", block=64)
    assert res.box is not None
    x0, y0, x1, y1 = res.box
    assert x0 < 300 < x1 and y0 < 300 < y1
    clean = prnu.locate(fp, path, block=64)
    assert clean.flag_map.mean() < 0.02


def test_cli_locate(camera, capsys):
    fp, k, tmp = camera
    fp.save(tmp / "loc.npz")
    rng = np.random.default_rng(10)
    path = _shot(k, _scene(rng), rng, tmp / "cli_loc.jpg")
    assert main(["prnu", "locate", "--block", "64", str(tmp / "loc.npz"), str(path)]) == 0
    assert "pattern checkable" in capsys.readouterr().out
    assert (tmp / "cli_loc_prnu.png").exists()


def test_build_keeps_the_common_resolution(camera, tmp_path):
    fp, k, tmp = camera
    flats = sorted(tmp.glob("flat*.jpg"))
    Image.open(flats[0]).resize((300, 300)).save(tmp_path / "small.jpg", quality=95)
    built = prnu.build([tmp_path / "small.jpg", *flats], crop=SIZE)
    assert built.images == 12 and built.source == (SIZE, SIZE)
    assert built.skipped and "300x300" in built.skipped[0][1]
    built.save(tmp_path / "fp.npz")
    assert prnu.Fingerprint.load(tmp_path / "fp.npz").source == (SIZE, SIZE)
    with pytest.raises(ValueError, match="resolutions must match"):
        prnu.match(built, tmp_path / "small.jpg")
