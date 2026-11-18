import io

import numpy as np
import pytest
from PIL import Image

from exif_inspect import jpegdct, stegjpeg
from exif_inspect.cli import main
from progressive_writer import write_progressive


def _scene(size=(512, 384), seed=5):
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([128 + 60 * np.sin(xx / 17) * np.cos(yy / 23), 128 + 50 * np.cos((xx + yy) / 31),
                     128 + 40 * np.sin(yy / 13 + xx / 29)], axis=2)
    base += rng.normal(0, 10, (h, w, 3))
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))


def _jpeg(img, **kw):
    buf = io.BytesIO()
    img.save(buf, "JPEG", **kw)
    return buf.getvalue()


# --- the coefficient reader and writer ------------------------------------------

@pytest.mark.parametrize("subsampling,extra", [(0, {}), (1, {}), (2, {}), (2, {"restart_marker_blocks": 5})])
def test_read_write_round_trip(subsampling, extra):
    data = _jpeg(_scene((200, 136)), quality=85, subsampling=subsampling, **extra)
    jd = jpegdct.read(data)
    assert jd.width == 200 and jd.height == 136
    assert jd.luma.coeffs.shape[2] == 64
    assert bool(extra) == (jd.restart_interval > 0)
    out = jpegdct.write(jd)
    again = jpegdct.read(out)
    for a, b in zip(jd.components, again.components):
        assert np.array_equal(a.coeffs, b.coeffs)
    # The rewritten file decodes to the same pixels as the original.
    y1 = np.asarray(Image.open(io.BytesIO(data)).convert("YCbCr"))[:, :, 0]
    y2 = np.asarray(Image.open(io.BytesIO(out)).convert("YCbCr"))[:, :, 0]
    assert np.array_equal(y1, y2)


def test_pixels_match_pillow():
    data = _jpeg(_scene((160, 96)), quality=90, subsampling=0)
    jd = jpegdct.read(data)
    ours = jpegdct.to_pixels(jd)
    theirs = np.asarray(Image.open(io.BytesIO(data)).convert("YCbCr"))[:, :, 0].astype(int)
    assert np.abs(ours.astype(int) - theirs).max() <= 2


def test_max_blocks_caps_the_read():
    data = _jpeg(_scene((512, 384)), quality=85)
    jd = jpegdct.read(data, max_blocks=500)
    assert 500 <= jd.luma.coeffs.shape[0] * jd.luma.coeffs.shape[1] < 64 * 48


def test_progressive_twin_holds_the_same_coefficients():
    scene = _scene((160, 96))
    base = jpegdct.read(_jpeg(scene, quality=85))
    prog = jpegdct.read(_jpeg(scene, quality=85, progressive=True))
    assert prog.progressive and prog.complete and not base.progressive
    for a, b in zip(base.components, prog.components):
        assert np.array_equal(a.coeffs, b.coeffs)


# --- the detectors -------------------------------------------------------------

def test_clean_jpeg_is_not_flagged(tmp_path):
    p = tmp_path / "clean.jpg"
    p.write_bytes(_jpeg(_scene(), quality=80))
    res = stegjpeg.analyze(p)
    assert res.error is None and res.blocks == 64 * 48
    assert res.lsb_embedded == 0.0
    assert res.f5_ratio is not None and res.f5_ratio < stegjpeg.F5_NOTE
    assert not stegjpeg.verdict(res).flag


def test_jsteg_payload_is_found_and_placed(tmp_path):
    clean = _jpeg(_scene(), quality=80)
    rng = np.random.default_rng(0)
    usable = stegjpeg.analyze(_write(tmp_path, "c.jpg", clean)).usable
    full = stegjpeg.embed_jsteg(clean, rng.integers(0, 2, usable))
    res = stegjpeg.analyze(_write(tmp_path, "full.jpg", full))
    assert res.lsb_embedded > 0.7
    v = stegjpeg.verdict(res)
    assert v.flag and "JSteg" in v.message
    # A payload in the first third of the usable coefficients fills a prefix of the stream.
    part = stegjpeg.embed_jsteg(clean, rng.integers(0, 2, usable // 3))
    res = stegjpeg.analyze(_write(tmp_path, "part.jpg", part))
    assert 0.15 < res.lsb_embedded < 0.5
    assert res.lsb_prefix >= res.lsb_embedded * 0.8
    assert stegjpeg.verdict(res).flag


def test_f5_shrinkage_raises_the_calibrated_estimate(tmp_path):
    clean = _jpeg(_scene(), quality=80)
    base = stegjpeg.analyze(_write(tmp_path, "c.jpg", clean)).f5_ratio
    heavy = stegjpeg.embed_f5_like(clean, 0.3)
    res = stegjpeg.analyze(_write(tmp_path, "f5.jpg", heavy))
    assert res.lsb_embedded == 0.0            # shrinkage is not LSB replacement
    assert res.f5_ratio > base + 0.15
    assert res.f5_ratio >= stegjpeg.F5_NOTE


def test_small_jpeg_skips_calibration(tmp_path):
    p = _write(tmp_path, "tiny.jpg", _jpeg(_scene((96, 64)), quality=80))
    res = stegjpeg.analyze(p)
    assert res.f5_ratio is None
    assert "too few blocks" in stegjpeg.verdict(res).message


def test_cli_stego_on_jpeg(tmp_path, capsys):
    clean = _jpeg(_scene(), quality=80)
    p = _write(tmp_path, "c.jpg", clean)
    assert main(["stego", str(p)]) == 0
    out = capsys.readouterr().out
    assert "coeffs    :" in out and "lsb pairs :" in out and "No sign of coefficient" in out
    usable = stegjpeg.analyze(p).usable
    q = _write(tmp_path, "j.jpg", stegjpeg.embed_jsteg(clean, np.random.default_rng(1).integers(0, 2, usable)))
    assert main(["stego", str(q)]) == 0
    assert "! LSB replacement in the coefficients likely" in capsys.readouterr().out
    # the progressive form of the same payload is read and flagged like the baseline one
    prog = _write(tmp_path, "p.jpg", write_progressive(jpegdct.read(q.read_bytes())))
    assert main(["stego", str(prog)]) == 0
    assert "! LSB replacement in the coefficients likely" in capsys.readouterr().out


def _write(folder, name, data):
    p = folder / name
    p.write_bytes(data)
    return p
