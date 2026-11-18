import io

import numpy as np
from PIL import Image

from exif_inspect import dq, jpegdct
from exif_inspect.cli import main
from exif_inspect.quant import STD_LUMA, scaled_table


def _picture(size=(384, 320), seed=5):
    """A smooth picture with fine texture that no JPEG has touched."""
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([
        120 + 60 * np.sin(xx / 19) * np.cos(yy / 23),
        120 + 50 * np.cos((xx - yy) / 29),
        120 + 40 * np.sin(yy / 11 + xx / 37),
    ], axis=2)
    return Image.fromarray(np.clip(base + rng.normal(0, 10, (h, w, 3)), 0, 255).astype(np.uint8))


def _through_jpeg(img, quality, **options):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, **options)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _standard_steps(quality):
    """libjpeg's luma steps for the coefficients the tool reads, in zigzag order."""
    zigzag = np.array(STD_LUMA)[jpegdct.ZIGZAG]
    return [scaled_table([int(zigzag[z])], quality)[0] for z in dq.FREQS]


PATCH = (128, 96, 256, 192)      # x0, y0, x1, y1


def _splice(path, first=70, second=90):
    """Saved at `first`, a patch of another picture pasted in, saved again at `second`."""
    host = _through_jpeg(_picture(), first)
    x0, y0, x1, y1 = PATCH
    host.paste(_picture((256, 256), seed=9).crop((0, 0, x1 - x0, y1 - y0)), (x0, y0))
    host.save(path, "JPEG", quality=second)


def _inside(res, truth=PATCH, margin=16):
    ii, jj = np.nonzero(res.flagged)
    xs, ys = jj * 8 + 4, ii * 8 + 4
    return float(((xs >= truth[0] - margin) & (xs < truth[2] + margin)
                  & (ys >= truth[1] - margin) & (ys < truth[3] + margin)).mean())


def test_single_save_shows_nothing(tmp_path):
    for quality in (95, 75):
        path = tmp_path / f"once_{quality}.jpg"
        _picture().save(path, "JPEG", quality=quality)
        res = dq.analyze(path)
        assert res.verdict == "none"
        assert res.steps == [] and res.box is None and res.first_quality is None


def test_second_save_at_higher_quality_gives_the_first_table(tmp_path):
    for first, second in ((70, 90), (85, 95), (60, 80), (50, 75)):
        path = tmp_path / f"twice_{first}_{second}.jpg"
        _through_jpeg(_picture(), first).save(path, "JPEG", quality=second)
        res = dq.analyze(path)
        assert res.verdict == "double"
        assert len(res.steps) >= 12
        lo, hi = res.first_quality
        assert lo <= first <= hi and hi - lo <= 4
        assert res.box is None and res.single_fraction < 0.01


def test_second_save_at_lower_quality_or_same_table_is_not_seen(tmp_path):
    for first, second in ((90, 75), (85, 85)):
        path = tmp_path / f"twice_{first}_{second}.jpg"
        _through_jpeg(_picture(), first).save(path, "JPEG", quality=second)
        assert dq.analyze(path).verdict == "none"


def test_steps_are_exact_when_the_coefficients_reach_several_multiples(tmp_path):
    # With strong texture each coefficient spans many multiples of the first step,
    # which tells neighbouring steps apart.
    rng = np.random.default_rng(1)
    busy = Image.fromarray(np.clip(np.asarray(_picture(), dtype=np.float64)
                                   + rng.normal(0, 30, (320, 384, 3)), 0, 255).astype(np.uint8))
    path = tmp_path / "busy.jpg"
    _through_jpeg(busy, 60).save(path, "JPEG", quality=85)
    res = dq.analyze(path)
    truth = _standard_steps(60)
    assert len(res.steps) == len(dq.FREQS)
    assert [s.first for s in res.steps] == truth
    assert res.first_quality == (60, 60)


def test_another_encoders_table_is_read_without_a_quality(tmp_path):
    custom = [min(255, 3 + (i % 8) + (i // 8)) for i in range(64)]       # no libjpeg quality
    buf = io.BytesIO()
    _picture().save(buf, "JPEG", qtables=[custom, custom])               # tables used as given
    buf.seek(0)
    path = tmp_path / "custom.jpg"
    Image.open(buf).convert("RGB").save(path, "JPEG", quality=98)
    res = dq.analyze(path)
    assert res.verdict == "double"
    assert res.first_quality is None
    zigzag = np.array(custom)[jpegdct.ZIGZAG]
    assert sum(s.first == zigzag[s.index] for s in res.steps) >= len(res.steps) - 1


def test_pasted_patch_is_located(tmp_path):
    path = tmp_path / "splice.jpg"
    _splice(path)
    res = dq.analyze(path)
    assert res.verdict == "region"
    assert res.first_quality[0] <= 70 <= res.first_quality[1]
    assert _inside(res) > 0.95
    x0, y0, x1, y1 = res.box
    assert x0 >= PATCH[0] - 24 and y0 >= PATCH[1] - 24
    assert x1 <= PATCH[2] + 24 and y1 <= PATCH[3] + 24
    covered = res.flagged.sum() * 64 / ((PATCH[2] - PATCH[0]) * (PATCH[3] - PATCH[1]))
    assert covered > 0.8
    assert dq.render(res).size == (384, 320)


def test_patch_off_the_grid_and_grayscale_file(tmp_path):
    host = _through_jpeg(_picture(), 75)
    host.paste(_picture((256, 256), seed=9).crop((0, 0, 100, 90)), (131, 77))
    path = tmp_path / "gray.jpg"
    host.convert("L").save(path, "JPEG", quality=92)
    res = dq.analyze(path)
    assert res.verdict == "region"
    assert _inside(res, (131, 77, 231, 167)) > 0.95


def test_clipped_blocks_are_not_judged(tmp_path):
    img = _picture()
    arr = np.asarray(img).copy()
    arr[40:120, 200:320] = 255                    # a blown-out area with texture at its edge
    path = tmp_path / "clipped.jpg"
    _through_jpeg(Image.fromarray(arr), 70).save(path, "JPEG", quality=90)
    res = dq.analyze(path)
    assert res.verdict == "double"
    assert not res.judged[5:15, 25:40].any()
    assert res.box is None


def test_files_that_cannot_be_read_are_skipped(tmp_path):
    png = tmp_path / "pic.png"
    _picture().save(png)
    res = dq.analyze(png)
    assert res.verdict == "skipped" and "not a JPEG" in res.note

    progressive = tmp_path / "progressive.jpg"
    _picture().save(progressive, "JPEG", quality=85, progressive=True)
    res = dq.analyze(progressive)
    assert res.verdict == "skipped" and "baseline" in res.note

    small = tmp_path / "small.jpg"
    _picture((96, 96)).save(small, "JPEG", quality=85)
    assert dq.analyze(small).verdict == "skipped"


def test_block_cap_reads_the_top_of_the_picture(tmp_path):
    path = tmp_path / "twice.jpg"
    _through_jpeg(_picture(), 70).save(path, "JPEG", quality=90)
    res = dq.analyze(path, max_blocks=900)
    assert res.verdict == "double"
    assert 0.4 < res.covered < 0.6
    assert res.first_quality[0] <= 70 <= res.first_quality[1]


def test_cli_dq(tmp_path, capsys):
    path = tmp_path / "splice.jpg"
    _splice(path)
    assert main(["dq", str(path)]) == 0
    out = capsys.readouterr().out
    assert "saved as a JPEG before" in out and "libjpeg's table at" in out
    assert "was compressed only once" in out
    assert (tmp_path / "splice_dq.png").exists()

    once = tmp_path / "once.jpg"
    _picture().save(once, "JPEG", quality=90)
    png = tmp_path / "pic.png"
    _picture().save(png)
    assert main(["dq", str(once), str(png)]) == 0
    out = capsys.readouterr().out
    assert "no trace of an earlier JPEG save" in out and "skipped : not a JPEG" in out
    assert not (tmp_path / "once_dq.png").exists()

    twice = tmp_path / "twice.jpg"
    _through_jpeg(_picture(), 85).save(twice, "JPEG", quality=95)
    assert main(["dq", str(twice)]) == 0
    assert "no region stands apart" in capsys.readouterr().out

    assert main(["dq", str(tmp_path / "missing.jpg")]) == 1
    assert "error" in capsys.readouterr().out
