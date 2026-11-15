import io
import struct
import zlib

from PIL import Image

from exif_inspect.checks import run_checks
from exif_inspect.extract import read_image
from exif_inspect.provenance import scan


def _codes(report):
    return {f.code for f in run_checks(report)}


def _png_with_chunk(ctype: bytes, payload: bytes) -> bytes:
    """A small PNG with one extra chunk inserted before IEND."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(buf, "PNG")
    data = buf.getvalue()
    iend = data.rindex(b"IEND") - 4
    crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
    chunk = struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)
    return data[:iend] + chunk + data[iend:]


def _fake_manifest(generator="ChatGPT", source=b"trainedAlgorithmicMedia"):
    """Bytes shaped like a JUMBF/C2PA manifest, enough for detection tests."""
    gen = generator.encode()
    return (
        b"\x00\x00\x00\x40jumb\x00\x00\x00\x1ejumdc2pa\x00"
        + b"\x6fclaim_generator" + bytes([0x60 + len(gen)]) + gen
        + b"digitalSourceType http://cv.iptc.org/newscodes/digitalsourcetype/"
        + source
    )


# --- extension_mismatch ---------------------------------------------------

def test_png_named_jpg(tmp_path):
    path = tmp_path / "chat.jpg"
    Image.new("RGB", (20, 20), "blue").save(path, "PNG")
    report = read_image(path)
    assert report.format == "PNG"
    assert "extension_mismatch" in _codes(report)


def test_matching_extension_is_quiet(tmp_path):
    path = tmp_path / "ok.jpeg"
    Image.new("RGB", (20, 20), "blue").save(path, "JPEG")
    assert "extension_mismatch" not in _codes(read_image(path))


# --- dimension_mismatch ---------------------------------------------------

def _jpeg_with_exif_size(path, actual, recorded):
    exif = Image.Exif()
    exif.get_ifd(0x8769).update({0xA002: recorded[0], 0xA003: recorded[1]})
    Image.new("RGB", actual, "gray").save(path, "JPEG", exif=exif)


def test_cropped_after_exif(tmp_path):
    path = tmp_path / "crop.jpg"
    _jpeg_with_exif_size(path, actual=(796, 894), recorded=(1206, 894))
    findings = {f.code: f.message for f in run_checks(read_image(path))}
    assert "dimension_mismatch" in findings
    assert "1206x894" in findings["dimension_mismatch"]
    assert "796x894" in findings["dimension_mismatch"]


def test_dimensions_match_or_rotated(tmp_path):
    same = tmp_path / "same.jpg"
    _jpeg_with_exif_size(same, actual=(300, 200), recorded=(300, 200))
    rotated = tmp_path / "rot.jpg"
    _jpeg_with_exif_size(rotated, actual=(300, 200), recorded=(200, 300))
    assert "dimension_mismatch" not in _codes(read_image(same))
    assert "dimension_mismatch" not in _codes(read_image(rotated))


# --- provenance -----------------------------------------------------------

def test_png_cabx_manifest(tmp_path):
    path = tmp_path / "gen.png"
    path.write_bytes(_png_with_chunk(b"caBX", _fake_manifest()))
    report = read_image(path)
    prov = report.provenance
    assert prov.c2pa and prov.container == "PNG caBX chunk"
    assert prov.generator == "ChatGPT"
    assert prov.digital_source_type == "trainedAlgorithmicMedia"
    codes = _codes(report)
    assert {"c2pa_manifest", "ai_generated_marker", "no_exif"} <= codes
    no_exif = next(f for f in run_checks(report) if f.code == "no_exif")
    assert "screenshots" not in no_exif.message  # no misleading hint


def test_jpeg_app11_manifest(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buf, "JPEG")
    data = buf.getvalue()
    payload = b"JP" + _fake_manifest(source=b"compositeWithTrainedAlgorithmicMedia")
    app11 = b"\xff\xeb" + struct.pack(">H", len(payload) + 2) + payload
    path = tmp_path / "edited.jpg"
    path.write_bytes(data[:2] + app11 + data[2:])
    report = read_image(path)
    assert report.provenance.container == "JPEG APP11 segment"
    assert report.provenance.digital_source_type == "compositeWithTrainedAlgorithmicMedia"
    assert "ai_composite_marker" in _codes(report)


def test_plain_files_have_no_provenance(tmp_path):
    path = tmp_path / "plain.png"
    Image.new("RGB", (8, 8)).save(path)
    report = read_image(path)
    assert not report.provenance.found
    codes = _codes(report)
    assert not {"c2pa_manifest", "ai_generated_marker"} & codes
    assert scan(b"random bytes", None).found is False


# --- quantization tables (v0.4.0) -----------------------------------------

from exif_inspect.quant import STD_LUMA, scaled_table  # noqa: E402


def test_libjpeg_quality_is_recovered(tmp_path):
    for q in (30, 75, 85, 95):
        path = tmp_path / f"q{q}.jpg"
        Image.new("RGB", (32, 32), "white").save(path, "JPEG", quality=q)
        info = read_image(path).jpeg_quant
        assert info.standard and info.quality == q
        assert info.subsampling == "4:2:0"


def test_custom_tables_are_flagged(tmp_path):
    flat = [3] * 64  # nothing like the standard tables
    path = tmp_path / "custom.jpg"
    Image.new("RGB", (32, 32), "white").save(path, "JPEG", qtables=[flat, flat])
    report = read_image(path)
    info = report.jpeg_quant
    assert not info.standard and info.quality is None
    msg = next(f.message for f in run_checks(report) if f.code == "jpeg_tables")
    assert "Custom quantization tables" in msg and info.fingerprint in msg


def test_png_has_no_tables(tmp_path):
    path = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(path)
    assert read_image(path).jpeg_quant is None


def test_scaled_table_matches_libjpeg_reference():
    assert scaled_table(STD_LUMA, 50) == STD_LUMA
    assert scaled_table(STD_LUMA, 100) == [1] * 64


def test_known_fingerprint_is_named(tmp_path, monkeypatch):
    from exif_inspect import quant
    flat = [3] * 64
    path = tmp_path / "known.jpg"
    Image.new("RGB", (16, 16)).save(path, "JPEG", qtables=[flat, flat])
    fp = read_image(path).jpeg_quant.fingerprint
    monkeypatch.setitem(quant.KNOWN_TABLES, fp, "Test encoder")
    report = read_image(path)
    assert report.jpeg_quant.known_as == "Test encoder"
    msg = next(f.message for f in run_checks(report) if f.code == "jpeg_tables")
    assert "Test encoder" in msg


def test_wechat_quality_hint(tmp_path):
    path = tmp_path / "wx.jpg"
    Image.new("RGB", (16, 16)).save(path, "JPEG", quality=76)
    report = read_image(path)
    assert report.jpeg_quant.standard and "WeChat" in report.jpeg_quant.known_as
    msg = next(f.message for f in run_checks(report) if f.code == "jpeg_tables")
    assert "Consistent with WeChat" in msg
