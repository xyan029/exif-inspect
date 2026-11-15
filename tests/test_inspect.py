import json

import pytest
from PIL import Image

from exif_inspect.checks import run_checks
from exif_inspect.cli import main
from exif_inspect.extract import read_image


def _codes(report):
    return {f.code for f in run_checks(report)}


@pytest.fixture
def plain_jpeg(tmp_path):
    path = tmp_path / "plain.jpg"
    Image.new("RGB", (32, 24), "red").save(path)
    return path


@pytest.fixture
def edited_jpeg(tmp_path):
    """JPEG with camera tags, an editor Software tag, mismatched times and GPS."""
    path = tmp_path / "edited.jpg"
    exif = Image.Exif()
    exif[0x010F] = "Canon"                      # Make
    exif[0x0110] = "EOS R6"                     # Model
    exif[0x0131] = "Adobe Photoshop 25.0"       # Software
    exif[0x0132] = "2026:01:02 10:00:00"        # DateTime (last saved)
    exif.get_ifd(0x8769)[0x9003] = "2026:01:01 09:00:00"   # DateTimeOriginal
    exif.get_ifd(0x8825).update({
        1: "N", 2: (43.0, 39.0, 36.0),          # 43.66 N
        3: "W", 4: (79.0, 23.0, 24.0),          # 79.39 W
    })
    Image.new("RGB", (32, 24), "blue").save(path, exif=exif)
    return path


def test_plain_image_has_no_exif(plain_jpeg):
    report = read_image(plain_jpeg)
    assert report.error is None
    assert not report.has_exif
    assert _codes(report) == {"no_exif"}
    assert len(report.sha256) == 64


def test_edited_image_findings(edited_jpeg):
    report = read_image(edited_jpeg)
    assert report.groups["Image"]["Make"] == "Canon"
    assert report.groups["Exif"]["DateTimeOriginal"] == "2026:01:01 09:00:00"
    assert report.gps_decimal == (43.66, -79.39)
    assert {"editor_software", "time_mismatch", "gps_present"} <= _codes(report)
    assert "no_camera" not in _codes(report)


def test_missing_file_is_reported(tmp_path):
    report = read_image(tmp_path / "nope.jpg")
    assert report.error
    assert _codes(report) == {"unreadable"}


def test_non_image_file(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("not an image")
    report = read_image(path)
    assert report.error
    assert report.sha256  # still hashed


def test_cli_json(edited_jpeg, capsys):
    assert main(["--json", str(edited_jpeg)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["gps_decimal"] == [43.66, -79.39]
    assert any(f["code"] == "editor_software" for f in data[0]["findings"])


def test_cli_text_and_exit_code(plain_jpeg, tmp_path, capsys):
    assert main([str(plain_jpeg), str(tmp_path / "missing.jpg")]) == 1
    out = capsys.readouterr().out
    assert "No EXIF metadata" in out
    assert "Could not read file" in out
