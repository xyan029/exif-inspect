import io
import struct

from PIL import Image

from exif_inspect.checks import run_checks
from exif_inspect.extract import read_image
from exif_inspect.gainmap import from_jpeg

APPLE_XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:Description '
             b'apdi:AuxiliaryImageType="urn:com:apple:photo:2020:aux:hdrgainmap"/>'
             b'</x:xmpmeta>')


def _jpeg(img, exif=None):
    buf = io.BytesIO()
    kw = {"quality": 90}
    if exif is not None:
        kw["exif"] = exif
    img.save(buf, "JPEG", **kw)
    return buf.getvalue()


def _segment(marker, payload):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def _mpo(main_size, gain_size, exif=None, xmp=APPLE_XMP):
    """Main JPEG + gain map JPEG joined by a minimal MPF index."""
    main = _jpeg(Image.new("RGB", main_size, (120, 90, 60)), exif)
    gain = _jpeg(Image.new("L", gain_size, 128))
    gain = gain[:2] + _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp) + gain[2:]
    entries_at = 8 + 2 + 3 * 12 + 4
    ifd = struct.pack(">H", 3)
    ifd += struct.pack(">HHI4s", 0xB000, 7, 4, b"0100")
    ifd += struct.pack(">HHII", 0xB001, 4, 1, 2)
    ifd += struct.pack(">HHII", 0xB002, 7, 32, entries_at)
    ifd += struct.pack(">I", 0)
    header = b"MM\x00\x2a" + struct.pack(">I", 8)
    mpf_len = 4 + len(header) + len(ifd) + 32
    main_len = len(main) + 4 + mpf_len    # APP2 adds marker, length, payload
    base = 2 + 4 + 4                       # SOI, APP2 marker+length, "MPF\0"
    entries = struct.pack(">IIIHH", 0x20030000, main_len, 0, 0, 0)
    entries += struct.pack(">IIIHH", 0, len(gain), main_len - base, 0, 0)
    app2 = _segment(0xE2, b"MPF\x00" + header + ifd + entries)
    return main[:2] + app2 + main[2:] + gain


def _iphone_exif():
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 16 Pro"
    return exif


def test_finds_apple_gain_map():
    data = _mpo((400, 300), (200, 150))
    g = from_jpeg(data, (400, 300))
    assert g is not None and g.kind == "Apple" and g.size == (200, 150)
    assert g.scale == (0.5, 0.5) and not g.misfit


def test_resized_image_flags_misfit(tmp_path):
    path = tmp_path / "resized.jpg"
    path.write_bytes(_mpo((294, 220), (200, 150), _iphone_exif()))
    report = read_image(path)
    assert report.gain_map is not None and report.gain_map.misfit
    codes = {f.code for f in run_checks(report)}
    assert "gain_map_misfit" in codes and "gain_map_missing" not in codes


def test_aspect_mismatch_flags_any_kind():
    data = _mpo((400, 300), (200, 200), xmp=b'<x hdrgm:Version="1.0"/>')
    g = from_jpeg(data, (400, 300))
    assert g.kind == "Ultra HDR / ISO 21496" and g.misfit


def test_iphone_photo_without_gain_map(tmp_path):
    path = tmp_path / "resaved.jpg"
    path.write_bytes(_jpeg(Image.new("RGB", (64, 48)), _iphone_exif()))
    codes = {f.code for f in run_checks(read_image(path))}
    assert "gain_map_missing" in codes


def test_old_iphone_and_other_cameras_not_flagged(tmp_path):
    for make, model in (("Apple", "iPhone 8"), ("Canon", "Canon EOS R5")):
        exif = Image.Exif()
        exif[0x010F], exif[0x0110] = make, model
        path = tmp_path / f"{model}.jpg"
        path.write_bytes(_jpeg(Image.new("RGB", (64, 48)), exif))
        codes = {f.code for f in run_checks(read_image(path))}
        assert "gain_map_missing" not in codes
