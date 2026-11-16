import io

import numpy as np
from PIL import Image

from exif_inspect import ghost
from exif_inspect.cli import main


def _photo(size=(256, 192), seed=1):
    """A textured RGB picture with smooth and busy areas, deterministic."""
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([
        128 + 60 * np.sin(xx / 23) * np.cos(yy / 17),
        128 + 50 * np.cos((xx + yy) / 31),
        128 + 40 * np.sin(yy / 13),
    ], axis=2)
    texture = rng.normal(0, 25, (h, w, 1))          # fine detail so ghosts can show
    return Image.fromarray(np.clip(base + texture, 0, 255).astype(np.uint8))


def _jpeg(img, q):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


BOX = (64, 48, 192, 144)   # multiples of 8, so the patch lands on the JPEG grid


def _paste(path, q1, q2, offset=(0, 0)):
    """Patch from a q1 copy pasted back into the picture, which is saved at q2."""
    base = _photo()
    donor = _jpeg(base, q1)
    dx, dy = offset
    patch = donor.crop((BOX[0] + dx, BOX[1] + dy, BOX[2] + dx, BOX[3] + dy))
    forged = base.copy()
    forged.paste(patch, BOX[:2])
    forged.save(path, "JPEG", quality=q2)


def _overlap(box, truth=BOX):
    ix = max(0, min(box[2], truth[2]) - max(box[0], truth[0]))
    iy = max(0, min(box[3], truth[3]) - max(box[1], truth[1]))
    return ix * iy / ((box[2] - box[0]) * (box[3] - box[1]))


def test_single_compression_has_no_ghost(tmp_path):
    path = tmp_path / "clean.jpg"
    _photo().save(path, "JPEG", quality=90)
    res = ghost.analyze(path)
    assert res.current_quality == 90
    assert res.global_ghosts == []
    assert res.ghost_quality is None and res.box is None
    assert res.judged_fraction > 0.9
    assert res.curve[res.qualities.index(90)] < 1.0


def test_double_compression_shows_earlier_quality(tmp_path):
    path = tmp_path / "twice.jpg"
    _jpeg(_photo(), 60).save(path, "JPEG", quality=90)
    res = ghost.analyze(path)
    assert res.current_quality == 90
    assert [q for q, _ in res.global_ghosts] == [60]
    assert res.global_ghosts[0][1] >= ghost.GLOBAL_DEPTH


def test_higher_earlier_quality_leaves_no_ghost(tmp_path):
    path = tmp_path / "down.jpg"
    _jpeg(_photo(), 90).save(path, "JPEG", quality=70)
    res = ghost.analyze(path)
    assert res.current_quality == 70
    assert res.global_ghosts == []


def test_pasted_patch_is_located(tmp_path):
    path = tmp_path / "paste.jpg"
    _paste(path, 60, 90)
    res = ghost.analyze(path)
    assert res.global_ghosts == []
    assert res.ghost_quality is not None and abs(res.ghost_quality - 60) <= 3
    assert res.shift == (0, 0)
    assert _overlap(res.box) > 0.8
    assert res.ghost_fraction < 0.5
    picture = ghost.render(res)
    assert picture.size == (256, 192)


def test_off_grid_patch_needs_grid_search(tmp_path):
    path = tmp_path / "offgrid.jpg"
    _paste(path, 60, 90, offset=(3, 5))
    assert ghost.analyze(path).ghost_quality is None
    res = ghost.analyze(path, grid=True)
    assert res.searched_grid
    # The patch's grid sits where the donor's did: 3 and 5 pixels back, modulo 8.
    assert res.shift == (-3 % 8, -5 % 8)
    assert res.ghost_quality is not None and abs(res.ghost_quality - 60) <= 3
    assert _overlap(res.box) > 0.8


def test_png_that_was_a_jpeg(tmp_path):
    path = tmp_path / "once.png"
    _jpeg(_photo(), 75).save(path, "PNG")
    res = ghost.analyze(path)
    assert res.current_quality is None
    assert res.source_format == "PNG"
    assert [q for q, _ in res.global_ghosts] == [75]


def test_harmonic_qualities():
    assert ghost._harmonic(60, 80)      # tables at 80 are half those at 60
    assert ghost._harmonic(50, 75)
    assert not ghost._harmonic(60, 70)
    assert not ghost._harmonic(80, 60)


def test_dips_marks_local_minima_only():
    curve = np.array([5.0, 4.0, 1.0, 4.0, 3.0, 2.0, 1.0, 0.0])
    dips = ghost._dips(curve[:, None], 2)[:, 0]
    assert dips[2] == 3.0                   # 1.0 sits 3 below both neighbours
    assert all(d <= 0 for i, d in enumerate(dips) if i != 2)


def test_sparkline_shape(tmp_path):
    path = tmp_path / "clean.jpg"
    _photo().save(path, "JPEG", quality=85)
    bars, axis = ghost.sparkline(ghost.analyze(path))
    assert len(bars) == len(axis) == 61         # 40 to 100
    assert axis.startswith("40") and "100" in axis


def test_cli_ghost(tmp_path, capsys):
    path = tmp_path / "paste.jpg"
    _paste(path, 60, 90)
    assert main(["ghost", str(path)]) == 0
    out = capsys.readouterr().out
    assert "saved at quality 90" in out
    assert "ghost at quality" in out
    assert (tmp_path / "paste_ghost.png").exists()

    clean = tmp_path / "clean.jpg"
    _photo().save(clean, "JPEG", quality=90)
    assert main(["ghost", "--curve", str(clean)]) == 0
    out = capsys.readouterr().out
    assert "no ghost found" in out and "--grid" in out
    assert "q90 " in out
    assert not (tmp_path / "clean_ghost.png").exists()


def test_cli_ghost_errors(tmp_path, capsys):
    assert main(["ghost", str(tmp_path / "missing.jpg")]) == 1
    assert "error" in capsys.readouterr().out
    tiny = tmp_path / "tiny.jpg"
    Image.new("RGB", (20, 20)).save(tiny)
    assert main(["ghost", str(tiny)]) == 1
    assert "too small" in capsys.readouterr().out
