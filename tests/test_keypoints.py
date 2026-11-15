import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

pytest.importorskip("cv2")

from exif_inspect.cli import main
from exif_inspect.keypoints import detect


def _scene(size=(900, 700), seed=1):
    """Random shapes over noise: plenty of corners and blobs, nothing repeated."""
    rng = np.random.default_rng(seed)
    noise = (rng.random((size[1], size[0], 3)) * 60 + 90).astype("uint8")
    img = Image.fromarray(noise).filter(ImageFilter.GaussianBlur(1.5))
    d = ImageDraw.Draw(img)
    for _ in range(400):
        x, y = rng.integers(0, size[0]), rng.integers(0, size[1])
        w, h = rng.integers(6, 40, 2)
        colour = tuple(int(c) for c in rng.integers(0, 256, 3))
        if rng.random() < 0.5:
            d.ellipse((x, y, x + w, y + h), fill=colour)
        else:
            pts = [(int(x + rng.integers(-w, w)), int(y + rng.integers(-h, h))) for _ in range(4)]
            d.polygon(pts, fill=colour)
    return img


def _forge(img, factor=1.0, angle=0.0):
    patch = img.crop((120, 100, 420, 340))
    patch = patch.resize((int(patch.width * factor), int(patch.height * factor)), Image.LANCZOS)
    if angle:
        patch = patch.rotate(angle, expand=True, resample=Image.BICUBIC)
    out = img.copy()
    out.paste(patch, (img.width - patch.width - 20, img.height - patch.height - 20))
    return out


def test_detects_scaled_clone(tmp_path):
    path = tmp_path / "scaled.jpg"
    _forge(_scene(), factor=0.5).save(path, quality=88)
    clones = detect(path).clones
    assert len(clones) == 1
    assert abs(clones[0].scale - 0.5) < 0.03
    assert abs(clones[0].rotation) < 2
    x0, y0, x1, y1 = clones[0].box_a
    assert x0 < 200 and y0 < 180 and x1 > 340 and y1 > 260


def test_detects_rotated_clone(tmp_path):
    path = tmp_path / "rotated.jpg"
    _forge(_scene(), factor=0.8, angle=30).save(path, quality=88)
    clones = detect(path).clones
    assert len(clones) == 1
    assert abs(clones[0].scale - 0.8) < 0.04
    assert abs(abs(clones[0].rotation) - 30) < 2


def test_clean_scene_has_no_clones(tmp_path):
    path = tmp_path / "clean.jpg"
    _scene(seed=2).save(path, quality=88)
    assert detect(path).clones == []


def test_tiles_are_not_clones(tmp_path):
    tile = np.asarray(_scene(size=(60, 60), seed=3))
    path = tmp_path / "tiles.jpg"
    Image.fromarray(np.tile(tile, (10, 14, 1))).save(path, quality=88)
    assert detect(path).clones == []


def test_cli_keypoints(tmp_path, capsys):
    path = tmp_path / "scaled.jpg"
    _forge(_scene(), factor=0.5).save(path, quality=88)
    assert main(["copymove", "--keypoints", str(path)]) == 0
    out = capsys.readouterr().out
    assert "scale 0.5" in out
    assert (tmp_path / "scaled_copymove.png").exists()
