import io

import numpy as np
from PIL import Image

from exif_inspect import report
from exif_inspect.cli import main


def _photo(path, size=(256, 192)):
    """A textured picture with EXIF, saved twice so ELA and ghost have something to see."""
    w, h = size
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([120 + 60 * np.sin(xx / 17), 120 + 50 * np.cos(yy / 13),
                     128 + 40 * np.sin((xx + yy) / 23)], axis=2)
    arr = np.clip(base + rng.normal(0, 20, (h, w, 3)), 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=60)
    buf.seek(0)
    exif = Image.Exif()
    exif[0x010F], exif[0x0110], exif[0x0131] = "Canon", "EOS R6", "Adobe Photoshop 25.0"
    Image.open(buf).save(path, "JPEG", quality=90, exif=exif)


def test_build_runs_every_tool(tmp_path):
    path = tmp_path / "shot.jpg"
    _photo(path)
    built = report.build(path)
    keys = [s.key for s in built.sections]
    assert keys == [k for k in report.TOOLS if k != "thumbnail"]   # no EXIF thumbnail here
    assert not any(s.failed for s in built.sections)
    assert built.flags >= 1                                        # editor_software at least
    page = built.html
    assert page.startswith("<!DOCTYPE html>")
    assert "shot.jpg" in page and "SHA-256" in page
    assert "Error level analysis" in page and "JPEG ghosts" in page and "XMP editing history" in page
    assert page.count("data:image/jpeg;base64,") >= 2             # the picture and the ELA pair
    assert "Software tag names an image editor" in page
    twice = next(s for s in built.sections if s.key == "dq")
    assert "saved as a JPEG before" in twice.text                  # quality 60, then 90
    ghost = next(s for s in built.sections if s.key == "ghost")
    assert "ghost at quality" in ghost.text or "whole image carries a ghost" in ghost.text


def test_skip_and_output(tmp_path, capsys):
    path = tmp_path / "shot.jpg"
    _photo(path)
    out = tmp_path / "r.html"
    assert main(["report", "--skip", "light,copymove,ghost", "-o", str(out), str(path)]) == 0
    text = capsys.readouterr().out
    assert "report :" in text and "6 tool section(s)" in text
    page = out.read_text(encoding="utf-8")
    assert "Lighting direction" not in page and "JPEG ghosts" not in page
    assert "Noise level" in page and "Resampling traces" in page


def test_unknown_skip_is_rejected(tmp_path):
    path = tmp_path / "shot.jpg"
    _photo(path)
    try:
        main(["report", "--skip", "magic", str(path)])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("argparse should have rejected the unknown tool")


def test_unreadable_file_still_gives_a_page(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not an image")
    built = report.build(path)
    assert built.sections == []
    assert "Could not read the file" in built.html
    assert main(["report", str(path)]) == 1
    assert (tmp_path / "broken_report.html").exists()


def test_embed_downscales_large_pictures():
    big = Image.new("RGB", (3000, 2000), (90, 120, 150))
    buf = io.BytesIO()
    big.save(buf, "PNG")
    uri = report._embed(buf.getvalue())
    assert uri.startswith("data:image/jpeg;base64,")
    import base64
    with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as img:
        assert max(img.size) == report.EMBED_SIDE
