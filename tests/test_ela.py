import io

from PIL import Image, ImageStat

from exif_inspect.cli import main
from exif_inspect.ela import error_level, run_ela


def _textured(size, sigma, seed_shift=0):
    """Synthetic textured RGB image built from Pillow noise."""
    w, h = size
    bands = [Image.effect_noise((w, h), sigma + 7 * i + seed_shift) for i in range(3)]
    return Image.merge("RGB", bands)


def _jpeg(img, q):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _make_forgery(path):
    """Base compressed at q=70, uncompressed patch pasted, saved once at q=90."""
    base = _jpeg(_textured((240, 160), 20), 70)
    patch = _textured((60, 60), 60, seed_shift=3)
    base.paste(patch, (150, 70))
    base.save(path, "JPEG", quality=90)
    return (150, 70, 210, 130)


def _mean(img, box=None):
    return ImageStat.Stat(img.crop(box) if box else img).mean[0]


def test_pasted_patch_has_higher_error(tmp_path):
    path = tmp_path / "forged.jpg"
    box = _make_forgery(path)
    with Image.open(path) as img:
        err = error_level(img, 90)
    inside = _mean(err, box)
    left_strip = _mean(err, (0, 0, 120, 160))
    assert inside > 1.5 * left_strip


def test_run_ela_result(tmp_path):
    path = tmp_path / "forged.jpg"
    _make_forgery(path)
    result = run_ela(path)
    assert result.source_format == "JPEG"
    assert result.heatmap.mode == "RGB"
    assert result.heatmap.size == (240, 160)
    assert result.max_error >= result.mean_error > 0
    assert result.scale > 0

    gray = run_ela(path, gray=True, scale=10)
    assert gray.heatmap.mode == "L"
    assert gray.scale == 10


def test_cli_ela_writes_png(tmp_path, capsys):
    path = tmp_path / "forged.jpg"
    _make_forgery(path)
    assert main(["ela", str(path)]) == 0
    out = tmp_path / "forged_ela.png"
    assert out.exists()
    assert "mean" in capsys.readouterr().out

    custom = tmp_path / "pair.png"
    assert main(["ela", "--side-by-side", "-o", str(custom), str(path)]) == 0
    with Image.open(custom) as img:
        assert img.size == (480, 160)


def test_cli_ela_png_source_note(tmp_path, capsys):
    path = tmp_path / "shot.png"
    _textured((40, 40), 30).save(path)
    assert main(["ela", str(path)]) == 0
    assert "designed for JPEG" in capsys.readouterr().out


def test_cli_ela_bad_input(tmp_path, capsys):
    assert main(["ela", str(tmp_path / "missing.jpg")]) == 1
    assert "error" in capsys.readouterr().out
