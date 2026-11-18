"""A JPEG followed scan by scan: files whose scans agree, and files with a second picture in them.

The tests build both kinds. A clean file sends every band of coefficients
once and refines it; whatever script wrote it, `passes` has nothing to
report. A two-picture file is two files with one frame, spliced: the scans
of the first, then the scans of the second, which libjpeg writes over them.
Each case checks three things: what libjpeg shows for the whole file, what
it shows for a copy cut in front of the overwriting scan, and what
`passes` says.
"""
import io
import struct
import warnings

import numpy as np
import pytest
from PIL import Image, ImageFile

from exif_inspect import jpegdct, passes
from exif_inspect.cli import main
from progressive_writer import (deep_script, default_script, scan_offsets, spectral_script, splice,
                                write_progressive)
from test_hostile_jpeg import _encode

SIZE = (192, 160)
ALL = (0, 1, 2)
DC = [(ALL, 0, 0, 0, 0)]


def _picture(kind):
    """Two pictures that share nothing: slanted bars, and rings around a point off centre."""
    w, h = SIZE
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    rng = np.random.default_rng(len(kind))
    if kind == "bars":
        base = np.stack([128 + 90 * np.sign(np.sin((xx + 2 * yy) / 14)), 128 + 60 * np.sin(xx / 23),
                         128 + 60 * np.cos(yy / 19)], axis=2)
    else:
        r = np.hypot(xx - 130, yy - 50)
        base = np.stack([128 + 90 * np.cos(r / 9), 128 + 70 * np.sin(r / 15), 128 - 70 * np.cos(r / 21)], axis=2)
    return Image.fromarray(np.clip(base + rng.normal(0, 6, (h, w, 3)), 0, 255).astype(np.uint8))


def _jpeg(img, **options):
    buf = io.BytesIO()
    img.save(buf, "JPEG", **options)
    return buf.getvalue()


def _shown(data):
    """libjpeg's picture as grey levels; warnings are let through, as viewers let them."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return np.asarray(Image.open(io.BytesIO(data)).convert("L"), dtype=np.float64)


def _corr(a, b):
    return float(np.corrcoef(a.ravel(), b.ravel())[0, 1])


def _seg(marker, payload):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


OPTIONS = dict(quality=85, subsampling=2)
BARS, RINGS = _picture("bars"), _picture("rings")
A, B = (jpegdct.read(_jpeg(p, **OPTIONS)) for p in (BARS, RINGS))
GA, GB = (jpegdct.read(_jpeg(p.convert("L"), quality=85)) for p in (BARS, RINGS))
SEEN_A, SEEN_B = _shown(_jpeg(BARS, **OPTIONS)), _shown(_jpeg(RINGS, **OPTIONS))


def _write(tmp_path, data, name="file.jpg"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --- files whose scans agree --------------------------------------------------------------------

CLEAN = {
    "Pillow, baseline": lambda: _jpeg(RINGS, **OPTIONS),
    "Pillow, progressive": lambda: _jpeg(RINGS, progressive=True, **OPTIONS),
    "Pillow, progressive, grey, restart markers": lambda: _jpeg(RINGS.convert("L"), progressive=True, quality=60,
                                                                 restart_marker_blocks=2),
    "libjpeg's default script": lambda: write_progressive(B),
    "spectral selection only": lambda: write_progressive(B, spectral_script(3)),
    "one scan per coefficient": lambda: write_progressive(GB, spectral_script(1, tuple((k, k) for k in range(1, 64)))),
    "three refinement passes": lambda: write_progressive(B, deep_script(3)),
    "AC bands in front of the DC": lambda: write_progressive(B, list(reversed(spectral_script(3)[1:])) + spectral_script(3)[:1]),
    "sequential, one scan per component": lambda: _encode(B, layout=[[0], [1], [2]]),
    "sequential, luma then both chroma": lambda: _encode(B, layout=[[0], [1, 2]]),
}


@pytest.mark.parametrize("name", CLEAN)
def test_file_from_an_encoder_has_nothing_to_report(tmp_path, name):
    res = passes.analyze(_write(tmp_path, CLEAN[name]()), sheet=True)
    assert res.verdict in ("single", "consistent") and res.replaced_at is None and res.extra is None
    assert all(row.kind in ("first", "refine") and row.changed is None for row in res.rows)
    assert res.verdict == "single" or len(res.thumbs) >= 2
    if len(res.rows) > 1:
        aways = [res.rows[k - 1].away for k in sorted(res.thumbs)]
        assert aways[-1] == 0 and aways[0] > 0


def test_clean_file_is_judged_from_its_headers_alone(tmp_path, monkeypatch):
    path = _write(tmp_path, _jpeg(RINGS, progressive=True, **OPTIONS))
    calls = []
    real = jpegdct.read
    monkeypatch.setattr(jpegdct, "read", lambda data, max_blocks=None, on_scan=None: (
        calls.append(max_blocks), real(data, max_blocks=max_blocks, on_scan=on_scan))[1])
    res = passes.analyze(path)
    assert res.verdict == "consistent" and calls == [1] and not res.thumbs
    assert [(r.band, r.kind) for r in res.rows[:3]] == [("DC", "first"), ("AC 1-5", "first"), ("AC 1-63", "first")]
    assert [r.kind for r in res.rows[-4:]] == ["refine"] * 4 and len(res.rows) == 10


# --- two pictures in one file -------------------------------------------------------------------

def _twofaced(first, then, grey=False):
    return splice(write_progressive(GA if grey else A, first), write_progressive(GB if grey else B, then))


TWO = {
    # name: (file, the scan that overwrites, whether the whole file is exactly picture B)
    "DC of A, then B": lambda: (_twofaced(DC, default_script(3)), 2, True),
    "coarse DC of A, then B": lambda: (_twofaced([(ALL, 0, 0, 0, 1)], default_script(3)), 2, True),
    "grey": lambda: (_twofaced([((0,), 0, 0, 0, 0)], default_script(1), grey=True), 2, True),
    "all of A, then all of B": lambda: (_twofaced(default_script(3), default_script(3)), 11, False),
    "sequential, three scans each": lambda: (splice(_encode(A, layout=[[0], [1], [2]]),
                                                    _encode(B, layout=[[0], [1], [2]])), 4, False),
    "sequential, luma of A alone": lambda: (splice(_encode(A, layout=[[0]]), _encode(B, layout=[[0], [1], [2]])),
                                            2, False),
}


@pytest.mark.parametrize("name", TWO)
def test_second_picture_under_the_first_is_found(tmp_path, name):
    data, at, exact = TWO[name]()
    whole = _shown(data)
    assert _corr(whole, SEEN_B) > 0.9 and _corr(whole, SEEN_A) < 0.3      # every viewer ends on B,
    if exact:
        assert np.array_equal(whole, _shown(write_progressive(GB if name == "grey" else B)))
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        cut = _shown(data[:scan_offsets(data)[at - 1]] + b"\xff\xd9")
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = False
    assert _corr(cut, SEEN_A) > 0.8 and _corr(cut, SEEN_B) < 0.3          # and a copy cut short shows A

    res = passes.analyze(_write(tmp_path, data))
    assert res.verdict == "replaced" and res.replaced_at == at
    row = res.rows[at - 1]
    assert row.kind == "again" and row.changed > 0.9 and row.moved > 20
    assert row.box == (0, 0) + SIZE
    assert res.correlation < passes.OTHER_PICTURE and res.distance > 40
    assert {at - 1, at, len(res.rows)} <= set(res.thumbs)
    before, final = (np.asarray(res.thumbs[k].convert("L"), dtype=np.float64) for k in (at - 1, len(res.rows)))
    small_a, small_b = (np.asarray(Image.fromarray(x.astype(np.uint8)).resize(res.thumbs[1].size), dtype=np.float64)
                        for x in (SEEN_A, SEEN_B))
    assert _corr(before, small_a) > 0.8 and _corr(final, small_b) > 0.9
    sheet = passes.render(res)
    assert sheet.width > res.thumbs[1].width and sheet.mode == "RGB"


def test_change_in_one_place_is_located(tmp_path):
    patched = RINGS.copy()
    patched.paste(BARS.crop((0, 0, 48, 32)), (96, 64))
    P = jpegdct.read(_jpeg(patched, **OPTIONS))
    data = splice(write_progressive(P, DC), write_progressive(B))
    assert np.array_equal(_shown(data), _shown(write_progressive(B)))
    res = passes.analyze(_write(tmp_path, data))
    assert res.verdict == "replaced" and res.replaced_at == 2
    assert res.rows[1].box == (96, 64, 144, 96) and 0.03 < res.rows[1].changed < 0.08
    assert res.correlation > passes.OTHER_PICTURE                     # the same picture, one part apart


def test_chroma_alone_and_ac_alone_count_too(tmp_path):
    colours = splice(write_progressive(A, [((1, 2), 0, 0, 0, 0)]), write_progressive(B))
    res = passes.analyze(_write(tmp_path, colours))
    assert res.verdict == "replaced" and res.rows[1].moved_in == 2 and res.rows[1].changed > 0.9
    bands = spectral_script(3)
    detail = splice(write_progressive(B, bands[:1]), splice(write_progressive(A, bands[1:]), write_progressive(B, bands[1:])))
    res = passes.analyze(_write(tmp_path, detail, "detail.jpg"))
    assert res.verdict == "replaced" and res.replaced_at == 14
    assert all(r.kind == "again" for r in res.rows[13:]) and all(r.kind == "first" for r in res.rows[:13])
    assert res.correlation > passes.OTHER_PICTURE                     # same block means, other detail


def test_band_sent_twice_with_the_same_values_changes_nothing(tmp_path, capsys):
    data = splice(write_progressive(B, DC), write_progressive(B, spectral_script(3)))
    path = _write(tmp_path, data)
    res = passes.analyze(path)
    assert res.verdict == "resent" and res.rows[1].kind == "again" and res.rows[1].changed == 0
    assert res.replaced_at is None and res.correlation is None
    assert main(["passes", str(path)]) == 0
    out = capsys.readouterr().out
    assert "again with the values already there" in out and "another picture" not in out


def test_scan_header_without_data_reaches_one_mcu(tmp_path):
    # libjpeg decodes the first MCU of such a scan from zero bits and leaves the others alone.
    clean = write_progressive(B)
    at = scan_offsets(clean)
    hollow = clean[:at[1]] + _seg(0xDA, bytes([3, 1, 0, 2, 0x10, 3, 0x10, 0, 0, 0])) + clean[at[1]:]
    assert np.abs(_shown(hollow) - _shown(clean))[16:, :].max() == 0
    res = passes.analyze(_write(tmp_path, hollow))
    assert res.verdict == "replaced" and res.rows[1].box == (0, 0, 16, 16)
    assert res.rows[1].changed < 0.01 and res.correlation > 0.95


def test_refinement_out_of_order_is_named(tmp_path, capsys):
    script = [(ALL, 0, 0, 0, 1), ((0,), 1, 63, 2, 1), ((0,), 1, 63, 1, 0), (ALL, 0, 0, 1, 0)]
    path = _write(tmp_path, write_progressive(B, script))
    res = passes.analyze(path)
    assert res.verdict == "order" and [r.kind for r in res.rows] == ["first", "out of order", "refine", "refine"]
    assert main(["passes", str(path)]) == 0
    assert "before the bit above it was sent" in capsys.readouterr().out


def test_quantisation_table_defined_again_does_not_hide_the_file(tmp_path):
    # libjpeg keeps the table a component was first scanned under; a second definition behind
    # that scan used to make the reader refuse a file that every viewer shows.
    data, _, _ = TWO["DC of A, then B"]()
    other = jpegdct.read(_jpeg(RINGS, quality=40, subsampling=2))
    dqt = b"".join(_seg(0xDB, bytes([tq]) + bytes(int(v) for v in table)) for tq, table in sorted(other.qtables.items()))
    at = scan_offsets(data)[1]
    again = data[:at] + dqt + data[at:]
    assert np.array_equal(_shown(again), _shown(data))
    jd = jpegdct.read(again)
    assert np.array_equal(jd.qtables[jd.luma.tq], B.qtables[B.luma.tq])
    assert any("defined again" in note for note in jd.notes)
    y = _shown(again)
    assert np.abs(jpegdct.to_pixels(jd, 0)[:y.shape[0], :y.shape[1]] - np.asarray(
        Image.open(io.BytesIO(again)).convert("YCbCr"))[..., 0]).mean() < 0.6
    assert passes.analyze(_write(tmp_path, again)).verdict == "replaced"


# --- scans libjpeg never reads -----------------------------------------------------------------

def test_scans_behind_a_complete_first_scan_are_counted_and_drawn(tmp_path, capsys):
    data = splice(_encode(A), _encode(B, layout=[[0], [1], [2]]))
    with pytest.raises(OSError):                                      # Pillow, strict, refuses the file;
        Image.open(io.BytesIO(data)).load()
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        assert _corr(_shown(data), SEEN_A) > 0.9                      # told to go on, it shows the first scan
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = False
    path = _write(tmp_path, data)
    res = passes.analyze(path)
    assert res.verdict == "single" and res.extra == (3, len(data) - 2 - scan_offsets(data)[1])
    small_b = np.asarray(Image.fromarray(SEEN_B.astype(np.uint8)).resize(res.unread.size), dtype=np.float64)
    assert _corr(np.asarray(res.unread.convert("L"), dtype=np.float64), small_b) > 0.9
    assert res.unread_correlation < passes.OTHER_PICTURE
    assert main(["passes", str(path)]) == 0
    out = capsys.readouterr().out
    assert "3 more scans" in out and "another picture" in out and (tmp_path / "file_passes.png").exists()
    # the same scans behind a first scan that leaves a component out are read, and reported as sent again
    assert passes.analyze(_write(tmp_path, splice(_encode(A, layout=[[0, 1]]), _encode(B, layout=[[0], [1], [2]])),
                                 "partial.jpg")).verdict == "replaced"


# --- the command ---------------------------------------------------------------------------------

def test_cli_lists_the_scans_and_writes_the_sheet(tmp_path, capsys):
    data, _, _ = TWO["DC of A, then B"]()
    two = _write(tmp_path, data, "two.jpg")
    clean = _write(tmp_path, write_progressive(B), "clean.jpg")
    assert main(["passes", str(clean), str(two)]) == 0
    out = capsys.readouterr().out
    assert "progressive, 10; every band is sent once and refined in order" in out
    assert "scan 2 sends the DC of components 1, 2 and 3 again and changes it in" in out
    assert "up to scan 1 the file shows another picture than at its end" in out
    assert (tmp_path / "two_passes.png").exists() and not (tmp_path / "clean_passes.png").exists()
    assert main(["passes", "--sheet", "-o", str(tmp_path / "sheet.png"), str(clean)]) == 0
    out = capsys.readouterr().out
    assert "refines bit 0" in out and "from the final picture" in out
    with Image.open(tmp_path / "sheet.png") as sheet:
        assert sheet.width > 300


def test_cli_on_other_files(tmp_path, capsys):
    png = tmp_path / "p.png"
    RINGS.save(png)
    cut = _write(tmp_path, _jpeg(RINGS, **OPTIONS)[:300], "cut.jpg")
    plain = _write(tmp_path, _jpeg(RINGS, **OPTIONS), "plain.jpg")
    assert main(["passes", str(png), str(cut), str(plain)]) == 0
    out = capsys.readouterr().out
    assert "not a JPEG" in out and "the JPEG structure is damaged" in out and "sequential, one scan" in out
    assert main(["passes", str(tmp_path / "missing.jpg")]) == 1


def test_mutated_two_picture_files_end_in_a_result(tmp_path):
    rng = np.random.default_rng(5)
    seeds = [TWO[name]()[0] for name in ("DC of A, then B", "all of A, then all of B", "sequential, three scans each")]
    seeds.append(splice(_encode(A), _encode(B, layout=[[0], [1], [2]])))
    path = tmp_path / "m.jpg"
    for _ in range(150):
        data = bytearray(seeds[rng.integers(len(seeds))])
        for _ in range(int(rng.choice((1, 1, 2, 4)))):
            at = int(rng.integers(2, len(data)))
            kind = int(rng.integers(5))
            if kind == 0:
                data[at] ^= 1 << int(rng.integers(8))
            elif kind == 1:
                data[at] = int(rng.choice((0, 1, 0x3F, 0x80, 0xFF)))
            elif kind == 2:
                del data[at:at + int(rng.choice((1, 2, 9, 60)))]
            elif kind == 3:
                del data[at:]
            else:
                data[at:at] = bytes([0xFF, int(rng.choice((0xD0, 0xD9, 0xDA, 0xC4, 0xDB, 0xC2, 0x00)))])
        path.write_bytes(bytes(data))
        res = passes.analyze(path)
        assert res.verdict in ("skipped", "single", "consistent", "order", "resent", "replaced")
        if res.thumbs:
            passes.render(res)
