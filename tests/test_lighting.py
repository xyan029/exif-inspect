import io

import numpy as np
from PIL import Image

from exif_inspect import lighting
from exif_inspect.cli import main


def _sphere(canvas, centre, radius, angle, elevation=30, albedo=(200, 170, 150), ambient=0.25):
    """Draw a matte sphere lit from `angle` degrees (0 = from the right, 90 = from above)."""
    h, w = canvas.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    x = (xx - centre[0]) / radius
    y = -(yy - centre[1]) / radius
    r2 = x * x + y * y
    inside = r2 <= 1
    z = np.sqrt(np.clip(1 - r2, 0, 1))
    a, e = np.radians(angle), np.radians(elevation)
    light = (np.cos(a) * np.cos(e), np.sin(a) * np.cos(e), np.sin(e))
    shade = np.clip(x * light[0] + y * light[1] + z * light[2], 0, 1)
    shade = ambient + (1 - ambient) * shade
    for ch in range(3):
        canvas[..., ch][inside] = albedo[ch] * shade[inside]


def _scene(angles, path, background=90):
    """Four spheres on a plain background, each lit from its own angle, saved as JPEG."""
    canvas = np.full((360, 480, 3), float(background))
    for centre, radius, angle in zip([(120, 180), (360, 180), (240, 90), (240, 270)],
                                     [70, 60, 50, 45], angles):
        _sphere(canvas, centre, radius, angle)
    canvas += np.random.default_rng(0).normal(0, 3, canvas.shape)
    img = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8))
    img.save(path, "JPEG", quality=90)


BOXES = [(40, 100, 200, 260), (290, 110, 430, 250), (180, 30, 300, 150), (185, 215, 295, 325)]


def _diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def test_single_sphere_direction(tmp_path):
    for angle in (0, 75, 160, 250, 330):
        path = tmp_path / f"sphere_{angle}.jpg"
        canvas = np.full((300, 300, 3), 90.0)
        _sphere(canvas, (150, 150), 90, angle)
        Image.fromarray(canvas.astype(np.uint8)).save(path, "JPEG", quality=90)
        res = lighting.analyze(path)
        assert res.consensus and res.direction is not None
        assert _diff(res.direction, angle) < 5
        assert len(res.contours) == 1
        assert res.contours[0].fit > 0.9


def test_consistent_scene_has_no_outlier(tmp_path):
    path = tmp_path / "same.jpg"
    _scene([40, 40, 40, 40], path)
    res = lighting.analyze(path)
    assert res.consensus
    assert _diff(res.direction, 40) < 5
    assert len(res.contours) == 4
    assert res.outliers == []
    assert res.spread < 5


def test_spliced_sphere_is_flagged(tmp_path):
    path = tmp_path / "spliced.jpg"
    _scene([40, 220, 40, 40], path)
    res = lighting.analyze(path)
    assert res.consensus and _diff(res.direction, 40) < 5
    assert len(res.outliers) == 1
    bad = res.outliers[0]
    assert _diff(bad.angle, 220) < 5
    assert bad.deviation > 170
    x0, y0, x1, y1 = bad.box
    assert 290 <= x0 and x1 <= 430 and 110 <= y0 and y1 <= 250   # the right-hand sphere
    picture = lighting.render(res)
    assert picture.size == (480, 360)


def test_boxes_pick_one_outline_each(tmp_path):
    path = tmp_path / "spliced.jpg"
    _scene([40, 100, 40, 40], path)
    res = lighting.analyze(path, BOXES[:3])
    assert [c.region for c in res.contours] == [0, 1, 2]
    assert res.consensus
    assert [c.outlier for c in res.contours] == [False, True, False]
    assert _diff(res.contours[1].angle, 100) < 5
    # A box with nothing in it gives no outline.
    empty = lighting.analyze(path, [(0, 0, 40, 40)])
    assert empty.contours == []


def test_no_consensus_when_outlines_scatter(tmp_path):
    path = tmp_path / "scatter.jpg"
    _scene([0, 90, 180, 270], path)
    res = lighting.analyze(path)
    assert len(res.contours) == 4
    assert not res.consensus
    assert res.direction is None
    assert res.outliers == []


def test_compass_words():
    assert lighting.compass(0) == "the right"
    assert lighting.compass(90) == "above"
    assert lighting.compass(180) == "the left"
    assert lighting.compass(270) == "below"
    assert lighting.compass(44) == "the upper right"
    assert lighting.compass(350) == "the right"


def test_cli_light(tmp_path, capsys):
    path = tmp_path / "spliced.jpg"
    _scene([40, 220, 40, 40], path)
    assert main(["light", str(path)]) == 0
    out = capsys.readouterr().out
    assert "light from 40 degrees (the upper right)" in out
    assert "220 degrees" in out and "180 off the rest" in out
    assert (tmp_path / "spliced_light.png").exists()

    assert main(["light", "--box", "40,100,200,260", "--box", "290,110,430,250",
                 "-o", str(tmp_path / "boxes.png"), str(path)]) == 0
    out = capsys.readouterr().out
    assert "box 1" in out and "box 2" in out
    assert (tmp_path / "boxes.png").exists()


def test_cli_light_nothing_usable(tmp_path, capsys):
    flat = tmp_path / "flat.png"
    Image.new("RGB", (120, 120), (90, 90, 90)).save(flat)
    assert main(["light", str(flat)]) == 0
    assert "no outline" in capsys.readouterr().out
    assert main(["light", str(tmp_path / "missing.jpg")]) == 1
    assert "error" in capsys.readouterr().out
