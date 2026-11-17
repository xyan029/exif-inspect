import io

import numpy as np
import pytest
from PIL import Image

from exif_inspect import resample
from exif_inspect.cli import main


def _picture(size=(384, 320), seed=5):
    """A smooth picture with fine texture, as nothing has resampled it."""
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([
        120 + 60 * np.sin(xx / 19) * np.cos(yy / 23),
        120 + 50 * np.cos((xx - yy) / 29),
        120 + 40 * np.sin(yy / 11 + xx / 37),
    ], axis=2)
    arr = np.clip(base + rng.normal(0, 10, (h, w, 3)), 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _enlarge(img, factor, method=Image.BICUBIC):
    return img.resize((round(img.width * factor), round(img.height * factor)), method)


PATCH = (128, 96, 320, 256)      # x0, y0, x1, y1


def _splice(path, factor=1.5):
    """A 512x384 picture with a patch of another picture, enlarged, pasted in."""
    host = _picture((512, 384), seed=5)
    x0, y0, x1, y1 = PATCH
    donor = _enlarge(_picture((256, 256), seed=9), factor).crop((0, 0, x1 - x0, y1 - y0))
    host.paste(donor, (x0, y0))
    host.save(path)


def _inside(res, truth=PATCH):
    ii, jj = np.nonzero(res.flagged)
    xs = res.offset[0] + jj * res.step + res.tile / 2
    ys = res.offset[1] + ii * res.step + res.tile / 2
    return float(((xs >= truth[0]) & (xs < truth[2]) & (ys >= truth[1]) & (ys < truth[3])).mean())


def test_plain_picture_has_no_trace(tmp_path):
    path = tmp_path / "plain.png"
    _picture().save(path)
    res = resample.analyze(path)
    assert res.verdict == "none"
    assert res.peaks == [] and res.box is None and res.stretched is None
    assert res.judged_fraction == 1.0
    assert not res.jpeg_grid


@pytest.mark.parametrize("factor, method", [(1.25, Image.BICUBIC), (1.25, Image.BILINEAR),
                                            (1.7, Image.BICUBIC), (3.0, Image.BICUBIC)])
def test_enlarged_picture_is_found_with_its_factor(tmp_path, factor, method):
    path = tmp_path / "big.png"
    _enlarge(_picture(), factor, method).save(path)
    res = resample.analyze(path)
    assert res.verdict == "whole"
    assert res.carrying_fraction > 0.9
    top = res.peaks[0]
    assert top.ratio > 5
    assert f"{factor:.2f}x" in top.reading
    assert min(abs(top.fx), abs(top.fy)) < 0.01          # on an axis


def test_blind_spots(tmp_path):
    # At exactly 2x both new pixels of a pair sit a quarter step from an old one,
    # so they are interpolated alike and nothing repeats.
    path = tmp_path / "double.png"
    _enlarge(_picture(), 2.0).save(path)
    assert resample.analyze(path).verdict == "none"
    # At 1.6x the trace falls on 3/8, a frequency of the JPEG grid, which is never counted.
    path = tmp_path / "on_grid.png"
    _enlarge(_picture(), 1.6).save(path)
    assert resample.analyze(path).verdict == "none"


@pytest.mark.parametrize("angle", [10, 35])
def test_turned_picture_reads_as_a_turn(tmp_path, angle):
    path = tmp_path / "turned.png"
    _picture((512, 512)).rotate(angle, resample=Image.BICUBIC).crop((96, 96, 416, 416)).save(path)
    res = resample.analyze(path)
    assert res.verdict == "whole"
    top = res.peaks[0]
    assert top.reading.startswith("turned by ")
    assert abs(float(top.reading.split()[2]) - angle) < 0.5


def test_pasted_enlarged_patch_is_located(tmp_path):
    path = tmp_path / "splice.png"
    _splice(path)
    res = resample.analyze(path)
    assert res.verdict == "region"
    assert "1.50x" in res.peaks[0].reading
    assert _inside(res) > 0.75
    x0, y0, x1, y1 = res.box
    assert x0 >= PATCH[0] - 64 and y0 >= PATCH[1] - 64
    assert x1 <= PATCH[2] + 64 and y1 <= PATCH[3] + 64
    assert resample.render(res).size == (512, 384)


def test_resizing_after_a_jpeg_save_shows_the_stretched_grid(tmp_path):
    buf = io.BytesIO()
    _picture((512, 448)).save(buf, "JPEG", quality=75)
    buf.seek(0)
    with Image.open(buf) as old:
        for factor, width in ((1.25, 10.0), (0.8, 6.4)):
            path = tmp_path / f"resized_{factor}.png"
            _enlarge(old.convert("RGB"), factor).save(path)
            res = resample.analyze(path)
            assert res.verdict == "whole"
            assert res.stretched == (width, factor)


def test_the_files_own_jpeg_grid_is_not_a_trace(tmp_path):
    path = tmp_path / "plain.jpg"
    _picture().save(path, "JPEG", quality=80)
    res = resample.analyze(path)
    assert res.jpeg_grid
    assert res.verdict == "none"


def test_periodic_texture_is_not_a_trace(tmp_path):
    w, h = 384, 320
    rng = np.random.default_rng(2)
    yy, xx = np.mgrid[0:h, 0:w]
    weave = 128 + 50 * np.sin(2 * np.pi * 0.21 * xx) + 30 * np.sin(2 * np.pi * 0.13 * (xx + yy))
    arr = np.clip(weave[:, :, None] + rng.normal(0, 6, (h, w, 3)), 0, 255).astype(np.uint8)
    path = tmp_path / "weave.png"
    Image.fromarray(arr).save(path)
    assert resample.analyze(path).verdict == "none"


def test_flat_and_small_pictures(tmp_path):
    flat = tmp_path / "flat.png"
    Image.new("RGB", (256, 256), (90, 120, 150)).save(flat)
    res = resample.analyze(flat)
    assert res.verdict == "none" and res.judged_fraction == 0

    small = tmp_path / "small.png"
    _picture((120, 200)).save(small)
    with pytest.raises(ValueError):
        resample.analyze(small)


def test_readings():
    assert resample.reading(0.2, 0.0).startswith("enlarged 1.25x (or 5.00x), or reduced to 0.83x")
    assert "turned by 11.5 degrees" in resample.reading(0.2, 0.0)       # a small turn looks the same
    assert "turned" not in resample.reading(0.4, 0.0)
    assert resample.reading(0.2, 0.2).startswith("enlarged 1.25x")      # both axes at once
    # A turn by 30 degrees: (cos 30, sin 30) = (0.866, 0.5), seen as (-0.134, 0.5).
    assert resample.reading(0.134, -0.5) == "turned by 30.0 degrees"
    assert resample.reading(0.293, 0.293) == "turned by 45.0 degrees"
    assert resample.reading(0.31, 0.12).startswith("resampled with a turn")

    comb = [resample.Peak(0.1, 0.0, 9, 9), resample.Peak(0.0, 0.2, 9, 9), resample.Peak(0.3, 0.0, 9, 9)]
    assert resample.stretched_grid(comb) == (10.0, 1.25)
    assert resample.stretched_grid(comb[:1]) is None
    assert resample.stretched_grid([resample.Peak(0.2, 0.0, 9, 9), resample.Peak(0.0, 0.2, 9, 9)]) is None


def test_cli_resample(tmp_path, capsys):
    path = tmp_path / "splice.png"
    _splice(path)
    assert main(["resample", str(path)]) == 0
    out = capsys.readouterr().out
    assert "resampling trace around" in out and "enlarged 1.50x" in out
    assert (tmp_path / "splice_resample.png").exists()

    plain = tmp_path / "plain.png"
    _picture().save(plain)
    assert main(["resample", str(plain)]) == 0
    assert "no resampling trace" in capsys.readouterr().out
    assert not (tmp_path / "plain_resample.png").exists()

    big = tmp_path / "big.png"
    _enlarge(_picture(), 1.25).save(big)
    custom = tmp_path / "map.png"
    assert main(["resample", "-o", str(custom), str(big)]) == 0
    assert "resampling trace across the picture" in capsys.readouterr().out
    assert custom.exists()

    assert main(["resample", str(tmp_path / "missing.png")]) == 1
    assert "error" in capsys.readouterr().out
