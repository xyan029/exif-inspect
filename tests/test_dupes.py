import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from exif_inspect import dupes
from exif_inspect.cli import main


def _scene(seed, size=(400, 300)):
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    f1, f2 = rng.uniform(13, 41, 2)
    base = np.stack([128 + 70 * np.sin(xx / f1) * np.cos(yy / f2), 128 + 50 * np.cos((xx - yy) / f2),
                     128 + 40 * np.sin(yy / f1)], axis=2) + rng.normal(0, 6, (h, w, 3))
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    for i in range(8):
        x, y = rng.integers(0, w - 80), rng.integers(0, h - 80)
        d.ellipse((x, y, x + 50 + i * 3, y + 40 + i * 2), fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
    return img


def _exif():
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "Canon", "EOS R6"
    return exif


def _folder(tmp_path):
    a, b = _scene(1), _scene(2)
    (tmp_path / "sub").mkdir()
    a.save(tmp_path / "a.jpg", "JPEG", quality=92, exif=_exif())
    a.save(tmp_path / "sub" / "a_low.jpg", "JPEG", quality=70)
    a.resize((200, 150), Image.LANCZOS).save(tmp_path / "a_small.png")
    a.rotate(90, expand=True).save(tmp_path / "a_rot.jpg", "JPEG", quality=85)
    wm = a.copy()
    ImageDraw.Draw(wm).rectangle((10, 10, 120, 40), fill=(255, 255, 255))
    wm.save(tmp_path / "a_wm.jpg", "JPEG", quality=90)
    shutil.copy(tmp_path / "a.jpg", tmp_path / "sub" / "IMG_0001.jpg")
    b.save(tmp_path / "b.jpg", "JPEG", quality=90)
    (tmp_path / "notes.txt").write_text("not a picture")
    (tmp_path / "broken.jpg").write_bytes(b"\xff\xd8\xff garbage")


def test_hashes_are_stable_and_discriminating():
    a, b = _scene(1), _scene(2)
    pa, da = dupes.hashes(a)
    pb, db = dupes.hashes(b)
    assert dupes.distance(pa[0], pb[0]) > 20
    pa2, _ = dupes.hashes(a.resize((200, 150), Image.LANCZOS))
    assert dupes.distance(pa[0], pa2[0]) <= 6
    # The rotated picture's hash at the matching rotation is close to the original's.
    pr, _ = dupes.hashes(a.rotate(90, expand=True))
    assert min(dupes.distance(pa[0], p) for p in pr) <= 6


def test_groups_and_best_original(tmp_path):
    _folder(tmp_path)
    res = dupes.find([tmp_path])
    assert res.scanned == 8                      # notes.txt is skipped, broken.jpg counted
    assert [p for p, _ in res.unreadable] == [str(tmp_path / "broken.jpg")]
    assert len(res.groups) == 1
    g = res.groups[0]
    names = sorted(Path(e.path).name for e in g.members)
    assert names == ["IMG_0001.jpg", "a.jpg", "a_low.jpg", "a_rot.jpg", "a_small.png", "a_wm.jpg"]
    assert g.best.path.endswith("a.jpg") or g.best.path.endswith("IMG_0001.jpg")
    assert not g.exact
    rot = next(m for m in g.members if m.path.endswith("a_rot.jpg"))
    assert g.distances[rot.path][2] in (90, 270)
    assert res.singles == 1                      # b.jpg


def test_exact_copies_form_an_exact_group(tmp_path):
    img = _scene(3)
    img.save(tmp_path / "x.jpg", "JPEG", quality=90)
    shutil.copy(tmp_path / "x.jpg", tmp_path / "y.jpg")
    res = dupes.find([tmp_path])
    assert len(res.groups) == 1 and res.groups[0].exact


def test_distance_zero_keeps_only_near_identical(tmp_path):
    _folder(tmp_path)
    strict = dupes.find([tmp_path], max_distance=0)
    loose = dupes.find([tmp_path], max_distance=10)
    assert len(strict.groups[0].members) <= len(loose.groups[0].members)


def test_cli_dupes(tmp_path, capsys):
    _folder(tmp_path)
    assert main(["dupes", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "group 1: 6 files" in out and "likely original" in out and "unreadable" in out
    assert main(["dupes", "--json", str(tmp_path)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["scanned"] == 8 and len(data["groups"]) == 1
    assert len(data["groups"][0]["members"]) == 6
