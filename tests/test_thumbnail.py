import io
import struct

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from exif_inspect.checks import run_checks
from exif_inspect.cli import main
from exif_inspect.extract import read_image
from exif_inspect.thumbnail import thumbnail_bytes


def _scene(size=(640, 480), seed=1):
    rng = np.random.default_rng(seed)
    noise = (rng.random((size[1], size[0], 3)) * 60 + 90).astype("uint8")
    img = Image.fromarray(noise).filter(ImageFilter.GaussianBlur(1.5))
    d = ImageDraw.Draw(img)
    for _ in range(120):
        x, y = rng.integers(0, size[0]), rng.integers(0, size[1])
        w, h = rng.integers(20, 90, 2)
        colour = tuple(int(c) for c in rng.integers(0, 256, 3))
        d.ellipse((x, y, x + w, y + h), fill=colour)
    return img


def _exif_with_thumbnail(thumb: Image.Image) -> bytes:
    """Little-endian EXIF block: IFD0 with a Make tag, IFD1 holding a JPEG thumbnail."""
    buf = io.BytesIO()
    thumb.save(buf, "JPEG", quality=80)
    data = buf.getvalue()
    make = b"TestCam\x00"
    header = b"II" + struct.pack("<HI", 42, 8)
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x010F, 2, len(make), 26) + struct.pack("<I", 34)
    ifd1 = (struct.pack("<H", 3)
            + struct.pack("<HHIHH", 0x0103, 3, 1, 6, 0)
            + struct.pack("<HHII", 0x0201, 4, 1, 76)
            + struct.pack("<HHII", 0x0202, 4, 1, len(data))
            + struct.pack("<I", 0))
    return b"Exif\x00\x00" + header + ifd0 + make + ifd1 + data


def _save(tmp_path, name, main_img, thumb_src):
    path = tmp_path / name
    main_img.save(path, "JPEG", quality=90,
                  exif=_exif_with_thumbnail(thumb_src.resize((160, 120), Image.LANCZOS)))
    return path


def _codes(path):
    return {f.code for f in run_checks(read_image(path))}


def test_thumbnail_is_extracted(tmp_path):
    scene = _scene()
    path = _save(tmp_path, "clean.jpg", scene, scene)
    with Image.open(path) as img:
        data = thumbnail_bytes(img.info.get("exif"))
    assert data[:2] == b"\xff\xd8"
    assert Image.open(io.BytesIO(data)).size == (160, 120)


def test_untouched_image_matches(tmp_path):
    scene = _scene()
    path = _save(tmp_path, "clean.jpg", scene, scene)
    report = read_image(path)
    assert report.thumbnail.match > 0.9
    assert not report.thumbnail.cropped and report.thumbnail.changed_box is None
    assert not {c for c in _codes(path) if c.startswith("thumbnail")}


def test_painted_area_is_found(tmp_path):
    scene = _scene()
    edited = scene.copy()
    ImageDraw.Draw(edited).rectangle((380, 150, 560, 330), fill=(120, 140, 100))
    path = _save(tmp_path, "painted.jpg", edited, scene)
    assert "thumbnail_edited" in _codes(path)
    x0, y0, x1, y1 = read_image(path).thumbnail.changed_box
    assert x0 < 470 < x1 and y0 < 240 < y1


def test_crop_is_found(tmp_path):
    scene = _scene()
    cropped = scene.crop((100, 120, 500, 420))
    path = _save(tmp_path, "cropped.jpg", cropped, scene)
    report = read_image(path)
    assert "thumbnail_crop" in _codes(path)
    x0, y0, x1, y1 = report.thumbnail.crop_box
    assert abs(x0 - 100 / 640) < 0.03 and abs(x1 - 500 / 640) < 0.03


def test_replaced_picture_is_flagged(tmp_path):
    path = _save(tmp_path, "swapped.jpg", _scene(seed=2), _scene(seed=3))
    assert "thumbnail_unrelated" in _codes(path)


def test_rotated_pixels_are_not_an_edit(tmp_path):
    scene = _scene()
    path = _save(tmp_path, "rotated.jpg", scene.rotate(90, expand=True), scene)
    codes = _codes(path)
    assert "thumbnail_rotated" in codes
    assert not codes & {"thumbnail_edited", "thumbnail_crop", "thumbnail_unrelated"}


def test_no_thumbnail(tmp_path):
    path = tmp_path / "plain.jpg"
    _scene().save(path, quality=90)
    assert read_image(path).thumbnail is None


def test_cli_thumbnail(tmp_path, capsys):
    scene = _scene()
    path = _save(tmp_path, "cropped.jpg", scene.crop((100, 120, 500, 420)), scene)
    assert main(["thumbnail", "--extract", str(path)]) == 0
    out = capsys.readouterr().out
    assert "wider scene" in out
    assert (tmp_path / "cropped_thumbnail.png").exists()
    assert (tmp_path / "cropped_thumb.jpg").read_bytes()[:2] == b"\xff\xd8"
