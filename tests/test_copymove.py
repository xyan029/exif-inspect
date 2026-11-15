import numpy as np
from PIL import Image, ImageFilter

from exif_inspect.cli import main
from exif_inspect.copymove import detect


def _texture(size=300, seed=1):
    rng = np.random.default_rng(seed)
    arr = (rng.random((size, size, 3)) * 255).astype("uint8")
    return Image.fromarray(arr).filter(ImageFilter.GaussianBlur(2))


def test_detects_pasted_region(tmp_path):
    img = _texture()
    img.paste(img.crop((30, 40, 90, 100)), (200, 180))
    path = tmp_path / "fake.jpg"
    img.save(path, quality=90)
    clones = detect(path).clones
    assert len(clones) == 1
    assert clones[0].shift == (170, 140)
    x0, y0, x1, y1 = clones[0].copy_box
    assert abs(x0 - 200) <= 4 and abs(y0 - 180) <= 4


def test_clean_image_has_no_clones(tmp_path):
    path = tmp_path / "clean.jpg"
    _texture(seed=2).save(path, quality=90)
    assert detect(path).clones == []


def test_repetitive_texture_is_not_a_clone(tmp_path):
    rng = np.random.default_rng(3)
    tile = (rng.random((24, 24, 3)) * 255).astype("uint8")
    path = tmp_path / "tiles.jpg"
    Image.fromarray(np.tile(tile, (12, 12, 1))).save(path, quality=90)
    assert detect(path).clones == []


def test_detects_after_downscaling(tmp_path):
    img = _texture(size=1200, seed=4).resize((1600, 1600))
    img.paste(img.crop((200, 300, 500, 600)), (1000, 900))
    path = tmp_path / "big.jpg"
    img.save(path, quality=88)
    clones = detect(path).clones
    assert clones and abs(clones[0].shift[0] - 800) <= 6 and abs(clones[0].shift[1] - 600) <= 6


def test_cli_copymove(tmp_path, capsys):
    img = _texture()
    img.paste(img.crop((30, 40, 90, 100)), (200, 180))
    path = tmp_path / "fake.jpg"
    img.save(path, quality=90)
    assert main(["copymove", str(path)]) == 0
    out = capsys.readouterr().out
    assert "clone 1: shift (170, 140)" in out
    assert (tmp_path / "fake_copymove.png").exists()
