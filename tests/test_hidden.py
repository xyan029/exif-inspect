import io
import struct

import numpy as np
from PIL import Image

from exif_inspect import hidden
from exif_inspect.checks import run_checks
from exif_inspect.cli import main
from exif_inspect.extract import read_image


def _picture(size=(256, 192), seed=11):
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([120 + 60 * np.sin(xx / 19) * np.cos(yy / 23), 110 + 50 * np.cos((xx - yy) / 29),
                     130 + 40 * np.sin(yy / 11 + xx / 37)], axis=2)
    return np.clip(base + rng.normal(0, 6, (h, w, 3)), 0, 255).astype(np.uint8)


def _embed(arr, rate, sequential=False, seed=1):
    rng = np.random.default_rng(seed)
    flat = arr.reshape(-1).copy()
    n = int(len(flat) * rate)
    idx = np.arange(n) if sequential else rng.choice(len(flat), n, replace=False)
    flat[idx] = (flat[idx] & 0xFE) | rng.integers(0, 2, n, dtype=np.uint8)
    return flat.reshape(arr.shape)


def _bytes(fmt):
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (200, 30, 30)).save(buf, fmt)
    return buf.getvalue()


# --- trailing data ------------------------------------------------------------

def test_clean_files_have_no_trailer():
    for fmt in ("JPEG", "PNG", "WEBP", "GIF"):
        assert hidden.trailer(_bytes(fmt), fmt) is None
    assert hidden.trailer(_bytes("JPEG") + b"\x00" * 8, "JPEG") is None     # padding only


def test_appended_zip_and_text_are_reported():
    t = hidden.trailer(_bytes("JPEG") + b"PK\x03\x04" + b"\x00" * 300, "JPEG")
    assert t is not None and t.kind.startswith("ZIP") and t.size == 304 and not t.expected
    t = hidden.trailer(_bytes("PNG") + b"meet at noon\n", "PNG")
    assert t.kind == "plain text" and "meet at noon" in t.sample
    t = hidden.trailer(_bytes("WEBP") + _bytes("JPEG"), "WEBP")
    assert t.kind == "JPEG image" and not t.expected
    t = hidden.trailer(_bytes("GIF") + b"%PDF-1.4 ...", "GIF")
    assert t.kind == "PDF document"


def test_mpf_second_image_is_expected():
    jpg = _bytes("JPEG")
    seg = b"\xff\xe2" + struct.pack(">H", 14) + b"MPF\x00" + b"\x00" * 8
    mpo = jpg[:2] + seg + jpg[2:] + jpg
    t = hidden.trailer(mpo, "MPO")
    assert t is not None and t.kind == "JPEG image" and t.expected


def test_trailer_findings(tmp_path):
    path = tmp_path / "carrier.jpg"
    path.write_bytes(_bytes("JPEG") + b"PK\x03\x04" + b"\x00" * 300)
    report = read_image(path)
    assert report.trailer is not None
    codes = {f.code: f for f in run_checks(report)}
    assert "trailing_data" in codes and codes["trailing_data"].level == "flag"
    assert "ZIP" in codes["trailing_data"].message
    clean = tmp_path / "clean.jpg"
    clean.write_bytes(_bytes("JPEG"))
    assert "trailing_data" not in {f.code for f in run_checks(read_image(clean))}


# --- LSB steganalysis ---------------------------------------------------------

def test_clean_picture_reads_clean(tmp_path):
    path = tmp_path / "clean.png"
    Image.fromarray(_picture()).save(path)
    res = hidden.analyze_lsb(path)
    assert res.channels == 3 and res.samples == 256 * 192 * 3
    assert res.rs_rate < hidden.RS_FLAG
    assert not hidden.verdict(res).flag
    assert res.planes.size[1] == 192


def test_random_payload_rate_is_estimated(tmp_path):
    arr = _picture()
    for rate in (0.25, 0.5, 1.0):
        path = tmp_path / f"r{int(rate * 100)}.png"
        Image.fromarray(_embed(arr, rate)).save(path)
        res = hidden.analyze_lsb(path)
        assert abs(res.rs_rate - rate) < 0.12, (rate, res.rs_rate)
        assert hidden.verdict(res).flag


def test_sequential_payload_is_estimated(tmp_path):
    path = tmp_path / "seq.png"
    Image.fromarray(_embed(_picture(), 0.4, sequential=True)).save(path)
    res = hidden.analyze_lsb(path)
    assert 0.28 < res.rs_rate < 0.52
    assert hidden.verdict(res).flag


def test_chi_square_finds_the_embedded_prefix():
    """Samples whose first 40% have evened-out value pairs and whose rest are skewed."""
    rng = np.random.default_rng(2)
    n = 40 * hidden.BLOCK
    base = rng.integers(0, 128, n) * 2                  # every value even: pairs fully skewed
    payload = base.copy()
    cut = int(0.4 * n)
    payload[:cut] |= rng.integers(0, 2, cut)            # random lowest bit evens the pairs out
    blocks, embedded, prefix = hidden.chi_square_attack(payload.astype(np.uint8))
    assert abs(prefix / n - 0.4) < 0.03
    assert abs(embedded - 0.4) < 0.03
    assert blocks[0].probability > 0.9 and blocks[-1].probability < 0.1
    res = hidden.LsbResult(channels=1, samples=n, chi_embedded=embedded, chi_prefix=prefix,
                           rs_rate=0.4)
    assert "from the start" in hidden.verdict(res).message


def test_grayscale_and_palette_pictures(tmp_path):
    gray = tmp_path / "gray.png"
    Image.fromarray(_picture()[:, :, 1]).save(gray)
    assert hidden.analyze_lsb(gray).channels == 1
    pal = tmp_path / "pal.png"
    Image.fromarray(_picture()).convert("P").save(pal)
    assert hidden.analyze_lsb(pal).channels == 3


def test_cli_stego(tmp_path, capsys):
    path = tmp_path / "half.png"
    Image.fromarray(_embed(_picture(), 0.5)).save(path)
    assert main(["stego", str(path)]) == 0
    out = capsys.readouterr().out
    assert "LSB payload likely" in out and "RS analysis" in out
    assert (tmp_path / "half_lsb.png").exists()

    carrier = tmp_path / "carrier.jpg"
    carrier.write_bytes(_bytes("JPEG") + b"PK\x03\x04" + b"\x00" * 300)
    assert main(["stego", "--extract", str(carrier)]) == 0
    out = capsys.readouterr().out
    assert "ZIP archive" in out and "coeffs    :" in out and "too few blocks" in out
    assert (tmp_path / "carrier_trailer.bin").read_bytes().startswith(b"PK\x03\x04")

    assert main([str(carrier)]) == 0
    assert "trailer :" in capsys.readouterr().out
    assert main(["stego", str(tmp_path / "missing.png")]) == 1
