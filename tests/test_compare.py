import numpy as np
from PIL import Image, ImageDraw

from exif_inspect import compare as cmp
from exif_inspect.cli import main


def _scene(size=(600, 400), seed=4):
    """A picture with enough structure everywhere for correlation to lock on."""
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    f1, f2, f3 = rng.uniform(11, 37, 3)                   # the pattern itself depends on the seed
    base = np.stack([128 + 60 * np.sin(xx / f1) * np.cos(yy / f2), 128 + 50 * np.cos((xx + yy) / f3),
                     128 + 40 * np.sin(yy / f2 + xx / f1)], axis=2)
    base += rng.normal(0, 8, (h, w, 3))
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    for i in range(12):                                   # some hard-edged objects
        x, y = rng.integers(0, w - 60), rng.integers(0, h - 60)
        d.rectangle((x, y, x + 40 + i, y + 30 + i), fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
    return img


def _save(img, path, quality=92, exif=None):
    img.save(path, "JPEG", quality=quality, **({"exif": exif} if exif else {}))


def _exif():
    exif = Image.Exif()
    exif[0x010F], exif[0x0110], exif[0x0132] = "Canon", "EOS R6", "2026:01:01 09:00:00"
    return exif


CROP = (120, 60, 560, 380)
PATCH = (300, 160, 400, 260)        # in crop coordinates


def test_crop_scale_and_edit_are_found(tmp_path):
    parent = tmp_path / "parent.jpg"
    child = tmp_path / "child.jpg"
    scene = _scene()
    _save(scene, parent, exif=_exif())
    c = scene.crop(CROP)
    ImageDraw.Draw(c).rectangle(PATCH, fill=(120, 70, 40))
    _save(c.resize((330, 240), Image.LANCZOS), child, quality=78)
    res = cmp.compare(parent, child)
    p = res.placement
    assert p is not None and p.container == "a" and p.rotation == 0
    assert all(abs(got - want) <= 6 for got, want in zip(p.box, CROP))
    assert abs(p.scale - 0.75) < 0.03
    assert p.ncc > 0.8 and 0.5 < p.cover < 0.7
    assert not res.same
    assert res.changes, "the painted patch should be reported"
    x0, y0, x1, y1 = res.changes[0]
    px0, py0 = CROP[0] + PATCH[0], CROP[1] + PATCH[1]
    assert abs(x0 - px0) <= 20 and abs(y0 - py0) <= 20
    assert abs(x1 - (px0 + 100)) <= 20 and abs(y1 - (py0 + 100)) <= 20
    assert res.parent == "a" and "more of the scene" in res.reasons[0]
    assert res.picture is not None
    # The order of the arguments does not change the answer.
    rev = cmp.compare(child, parent)
    assert rev.placement.container == "b" and rev.parent == "b"


def test_rotated_resave_is_the_same_picture(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    scene = _scene()
    _save(scene, a, exif=_exif())
    _save(scene.rotate(90, expand=True), b, quality=92)
    res = cmp.compare(a, b)
    assert res.placement.rotation in (90, 270)
    assert res.same and not res.changes
    assert res.parent == "a" and "metadata" in " ".join(res.reasons)


def test_quality_decides_between_equal_copies(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    scene = _scene()
    _save(scene, a, quality=92)
    _save(scene, b, quality=80)
    res = cmp.compare(a, b)
    assert res.same and res.parent == "a" and "JPEG quality" in res.reasons[-1]


def test_unrelated_pictures_do_not_match(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    _save(_scene(seed=4), a)
    _save(_scene(seed=9), b)
    res = cmp.compare(a, b)
    assert res.placement is None and res.parent is None and res.picture is None


def test_cli_compare(tmp_path, capsys):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    scene = _scene()
    _save(scene, a, exif=_exif())
    _save(scene.crop(CROP), b, quality=85)
    assert main(["compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "B is a crop of A" in out and "parent : A came first" in out
    assert (tmp_path / "a_vs_b.png").exists()
    assert main(["compare", str(a), str(tmp_path / "missing.jpg")]) == 1
