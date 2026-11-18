"""Progressive JPEGs: the coefficient reader against libjpeg, on sound files and on damaged ones.

Converting a JPEG to progressive changes no coefficient and takes one command,
so a reader that stopped at progressive files left `dq` and `stego` one command
away from seeing nothing. The checks here: a progressive file holds exactly the
coefficients of its baseline twin, whatever script wrote it; a file that stops
early is read as far as it goes and says how far; and a file that breaks the
format is refused before its first scan and kept up to the break after it.
"""
import io
import itertools
import random
import struct
import time
import warnings

import numpy as np
import pytest
from PIL import Image

from exif_inspect import dq, jpegdct, stegjpeg
from exif_inspect.cli import main
from progressive_writer import (default_script, deep_script, spectral_script, write_progressive)


# --- building files ---------------------------------------------------------------------------

def _scene(size=(64, 48), seed=1):
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([120 + 60 * np.sin(xx / 9) * np.cos(yy / 11),
                     120 + 50 * np.cos((xx - yy) / 13),
                     120 + 40 * np.sin(yy / 7 + xx / 17)], axis=2)
    return Image.fromarray(np.clip(base + rng.normal(0, 12, (h, w, 3)), 0, 255).astype(np.uint8))


def _jpeg(img, **options):
    buf = io.BytesIO()
    img.save(buf, "JPEG", **options)
    return buf.getvalue()


def _libjpeg(data):
    """The pixels libjpeg decodes, in the file's own colour space; warnings are errors."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        im = Image.open(io.BytesIO(data))
        im.draft("YCbCr", im.size)
        im.load()
        return np.asarray(im).copy()


def _shown(data):
    """Whether libjpeg decodes the file at all."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            Image.open(io.BytesIO(data)).load()
        return True
    except Exception:
        return False


def _own(c, jd):
    """The blocks of a component that hold picture; a scan of one component codes no others."""
    hmax, vmax = max(x.h for x in jd.components), max(x.v for x in jd.components)
    cols = -(-(-(-jd.width * c.h // hmax)) // 8)
    rows = -(-(-(-jd.height * c.v // vmax)) // 8)
    return c.coeffs[:rows, :cols]


def _same(a, b):
    return all(np.array_equal(_own(x, a), _own(y, a)) for x, y in zip(a.components, b.components))


def _scans(data):
    """Offsets of the SOS markers."""
    return [k for k in range(len(data) - 1) if data[k] == 0xFF and data[k + 1] == 0xDA]


def _seg(marker, payload):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


SCRIPTS = {
    "libjpeg's default": default_script,
    "spectral selection only": spectral_script,
    "one band per component": lambda n: spectral_script(n, ((1, 63),)),
    "one scan per coefficient": lambda n: spectral_script(n, tuple((k, k) for k in range(1, 64))),
    "three refinement passes": deep_script,
    "one refinement pass": lambda n: deep_script(n, 1),
    "AC before DC": lambda n: list(reversed(spectral_script(n)[1:])) + spectral_script(n)[:1],
}
BASE = {sub: jpegdct.read(_jpeg(_scene(), quality=80, subsampling=sub)) for sub in (0, 1, 2)}
GREY = jpegdct.read(_jpeg(_scene().convert("L"), quality=80))


# --- sound files --------------------------------------------------------------------------------

@pytest.mark.parametrize("size, quality, sub, restart", [
    ((64, 48), 85, 0, 0), ((64, 48), 85, 2, 0), ((97, 61), 50, 1, 0), ((97, 61), 100, 2, 3), ((8, 8), 90, 2, 0),
    ((1, 1), 75, 0, 0), ((17, 300), 30, 2, 0), ((200, 150), 5, 2, 0), ((200, 150), 95, 0, 2)])
def test_pillow_progressive_holds_the_coefficients_of_its_baseline_twin(size, quality, sub, restart):
    options = dict(quality=quality, subsampling=sub)
    if restart:
        options["restart_marker_blocks"] = restart
    base = jpegdct.read(_jpeg(_scene(size), **options))
    prog = jpegdct.read(_jpeg(_scene(size), progressive=True, **options))
    assert prog.progressive and prog.complete and prog.scans == 10 and not prog.notes
    assert not base.progressive and base.complete
    for a, b in zip(base.components, prog.components):
        assert np.array_equal(a.coeffs, b.coeffs)


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("sub, restart", [(0, 0), (2, 0), (1, 5), (2, 1), ("grey", 0), ("grey", 4)])
def test_any_scan_script_reads_back_and_matches_libjpeg(script, sub, restart):
    jd = GREY if sub == "grey" else BASE[sub]
    data = write_progressive(jd, SCRIPTS[script](len(jd.components)), restart=restart)
    if script == "AC before DC":
        assert _shown(data)                              # libjpeg warns about the order and decodes
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            im = Image.open(io.BytesIO(data))
            im.draft("YCbCr", im.size)
            shown = np.asarray(im).copy()
    else:
        shown = _libjpeg(data)
    assert np.array_equal(shown, _libjpeg(jpegdct.write(jd)))      # the encoder is right
    back = jpegdct.read(data)
    assert _same(jd, back) and back.complete and not back.notes    # and so is the reader
    assert back.scans == len(SCRIPTS[script](len(jd.components)))


def test_capped_read_takes_the_top_rows_of_every_scan():
    data = _jpeg(_scene((256, 192)), quality=85, subsampling=2, progressive=True)
    whole = jpegdct.read(data)
    top = jpegdct.read(data, max_blocks=200)
    assert top.luma.coeffs.shape[0] == 8 < whole.luma.coeffs.shape[0]
    for a, b in zip(top.components, whole.components):
        assert np.array_equal(a.coeffs, b.coeffs[:a.coeffs.shape[0]])


# --- files that stop early ----------------------------------------------------------------------

def _known_to(value, bit, dc):
    """A coefficient as the scans down to bit `bit` leave it."""
    if dc:
        return (value >> bit) << bit
    return (abs(value) >> bit << bit) * (1 if value >= 0 else -1)


@pytest.mark.parametrize("sub", [0, 2])
@pytest.mark.parametrize("keep", range(1, 10))
def test_file_cut_between_scans_reads_as_the_passes_it_has(sub, keep):
    jd = BASE[sub]
    script = default_script(3)
    data = write_progressive(jd, script)
    cut = data[:_scans(data)[keep]] + b"\xff\xd9"
    assert _shown(cut)
    back = jpegdct.read(cut)
    assert back.scans == keep and not back.complete
    for k, (a, b) in enumerate(zip(jd.components, back.components)):
        sent = [-1] * 64
        for comps, ss, se, ah, al in script[:keep]:
            if k in comps:
                sent[ss:se + 1] = [al] * (se - ss + 1)
        assert b.sent == b.held == sent
        want = np.zeros_like(_own(a, jd))
        for z in range(64):
            if sent[z] >= 0:
                want[:, :, z] = np.vectorize(lambda v: _known_to(int(v), sent[z], z == 0))(_own(a, jd)[:, :, z])
        assert np.array_equal(_own(b, jd), want)
    assert any("passes stop early" in note for note in back.notes)


def test_file_cut_inside_a_scan_keeps_what_came_before_the_cut():
    jd = BASE[0]
    data = write_progressive(jd, spectral_script(3))
    at = _scans(data)
    cut = data[:at[1] + 40]                              # inside the second scan: luma, coefficient 1
    back = jpegdct.read(cut)
    assert back.truncated and back.scans == 2
    assert back.luma.sent[:3] == [0, 0, -1]              # what the scan headers announce
    assert back.luma.held[:3] == [0, -1, -1]             # what every block received
    assert back.luma.unsent == 63 and "63 of the 64 coefficients never arrived" in back.notes[-1]
    assert np.array_equal(back.luma.coeffs[:, :, 0], jd.luma.coeffs[:, :, 0])      # the DC scan is whole
    got, want = back.luma.coeffs[:, :, 1].ravel(), jd.luma.coeffs[:, :, 1].ravel()
    filled = int(np.flatnonzero(got)[-1]) + 1
    assert 0 < filled < len(got) and np.array_equal(got[:filled], want[:filled]) and not got[filled:].any()
    assert not back.luma.coeffs[:, :, 2:].any()


def test_incomplete_progressive_is_skipped_by_dq_and_stego(tmp_path, capsys):
    data = _jpeg(_scene((320, 240)), quality=85, progressive=True)
    early = tmp_path / "early.jpg"
    early.write_bytes(data[:_scans(data)[5]] + b"\xff\xd9")
    assert dq.analyze(early).verdict == "skipped"
    res = stegjpeg.analyze(early)
    assert res.error and "passes stop early" in res.error
    for sub in ("dq", "stego"):
        assert main([sub, str(early)]) == 0
        assert "passes stop early" in capsys.readouterr().out


def test_scan_without_data_does_not_count_as_a_pass(tmp_path):
    # The last pass of the luma is taken out and a scan header with nothing behind it put in its
    # place. libjpeg shows the file without a warning; every AC coefficient lost its lowest bit.
    data = write_progressive(jpegdct.read(_jpeg(_scene((320, 240)), quality=85)))
    at = _scans(data)
    assert data[at[9] + 4:at[9] + 10] == bytes([1, 1, 0, 1, 63, 0x10])          # luma, 1..63, last bit
    hollow = data[:at[9]] + _seg(0xDA, bytes([1, 1, 0, 1, 63, 0x10])) + b"\xff\xd9"
    assert _shown(hollow)
    back = jpegdct.read(hollow)
    assert back.luma.sent == [0] * 64 and back.luma.held == [0] + [1] * 63
    assert not back.complete and back.luma.bits_missing == 1
    assert not (back.luma.coeffs[:, :, 1:] & 1).any()
    path = tmp_path / "hollow.jpg"
    path.write_bytes(hollow)
    assert dq.analyze(path).verdict == "skipped" and "lowest 1 bit" in dq.analyze(path).note
    assert "lowest 1 bit" in stegjpeg.analyze(path).error


def test_component_without_a_scan_reads_flat_as_libjpeg_shows_it():
    jd = BASE[0]
    for data in (write_progressive(jd, [((0,), 0, 0, 0, 0), ((0,), 1, 63, 0, 0)]),):
        assert _shown(data)
        back = jpegdct.read(data)
        assert np.array_equal(_own(back.luma, jd), _own(jd.luma, jd))
        assert not back.components[1].coeffs.any() and not back.components[2].coeffs.any()
        assert "no scan carries components 2 and 3; they read as flat grey" in back.notes and not back.complete


# --- the same analysis on the progressive form --------------------------------------------------

def test_dq_and_stego_give_the_same_answer_on_the_progressive_form(tmp_path):
    scene = _scene((480, 360), 3)
    once = io.BytesIO()
    scene.save(once, "JPEG", quality=70)
    twice = _jpeg(Image.open(io.BytesIO(once.getvalue())), quality=90)
    base = tmp_path / "twice.jpg"
    base.write_bytes(twice)
    prog = tmp_path / "twice_progressive.jpg"
    prog.write_bytes(write_progressive(jpegdct.read(twice)))
    a, b = dq.analyze(base), dq.analyze(prog)
    assert a.verdict == b.verdict == "double"
    assert [(s.index, s.first) for s in a.steps] == [(s.index, s.first) for s in b.steps]
    sa, sb = stegjpeg.analyze(base), stegjpeg.analyze(prog)
    assert (sa.blocks, sa.nonzero_ac, sa.usable, sa.lsb_embedded) == (sb.blocks, sb.nonzero_ac, sb.usable, sb.lsb_embedded)
    assert sb.progressive and not sa.progressive


# --- files that break the format ----------------------------------------------------------------

def _first_sos(data, change):
    at = _scans(data)[0]
    length = struct.unpack(">H", data[at + 2:at + 4])[0]
    return data[:at + 4] + change(bytearray(data[at + 4:at + 2 + length])) + data[at + 2 + length:]


def _set(index, value):
    def change(payload):
        payload[index] = value
        return bytes(payload)
    return change


PROG = _jpeg(_scene(), quality=80, subsampling=0, progressive=True)
REFUSED = {
    "DC scan that reaches into the AC band": _set(-2, 5),
    "band that ends before it starts": lambda p: bytes(p[:-3]) + bytes([9, 3, 0]),
    "band past coefficient 63": lambda p: bytes(p[:1]) + bytes(p[1:3]) + bytes([1, 64, 0]),
    "AC scan of two components": lambda p: bytes(p[:-3]) + bytes([1, 5, 0]),
    "refinement that skips a bit": _set(-1, 0x31),
    "bit position 14": _set(-1, 0x0E),
    "components in falling order": lambda p: bytes(p[:1]) + bytes(p[3:5]) + bytes(p[1:3]) + bytes(p[5:]),
    "one component twice": lambda p: bytes(p[:3]) + bytes(p[1:3]) + bytes(p[5:]),
    "DC table 5": _set(2, 0x50),
}


@pytest.mark.parametrize("name", REFUSED)
def test_bad_first_scan_is_refused_as_libjpeg_refuses_it(name):
    data = _first_sos(PROG, REFUSED[name])
    assert not _shown(data)
    with pytest.raises(jpegdct.Corrupt) as info:
        jpegdct.read(data)
    assert str(info.value)


def test_progressive_file_without_its_tables_is_refused():
    at = PROG.index(b"\xff\xc4")
    length = struct.unpack(">H", PROG[at + 2:at + 4])[0]
    data = PROG[:at] + PROG[at + 2 + length:]            # the first DHT, which holds the DC tables
    assert not _shown(data)
    with pytest.raises(jpegdct.Corrupt, match="Huffman table"):
        jpegdct.read(data)


BREAKS = {
    "marker FF05": b"\xff\x05",
    "a second SOI": b"\xff\xd8",
    "a second frame header": _seg(0xC2, bytes([8, 0, 48, 0, 64, 1, 1, 0x11, 0])),
    "quantisation table 7": _seg(0xDB, bytes([7]) + bytes(64)),
    "Huffman table cut short": _seg(0xC4, bytes([0x10]) + bytes([1] * 16)),
    "restart interval of three bytes": _seg(0xDD, b"\x00\x00\x00"),
    "scan of a component the frame lacks": _seg(0xDA, bytes([1, 9, 0, 1, 5, 0])),
}


@pytest.mark.parametrize("name", BREAKS)
def test_break_after_the_first_scans_keeps_what_they_brought(name):
    at = _scans(PROG)[3]
    data = PROG[:at] + BREAKS[name] + PROG[at:]
    assert not _shown(data)                              # a viewer that waits for the end has nothing
    back = jpegdct.read(data)
    assert back.scans == 3 and not back.complete
    assert any("breaks the format" in note for note in back.notes)
    kept = jpegdct.read(PROG[:at] + b"\xff\xd9")
    for a, b in zip(kept.components, back.components):
        assert np.array_equal(a.coeffs, b.coeffs)
    with pytest.raises(jpegdct.Corrupt):                 # the same thing in front of the first scan
        jpegdct.read(PROG[:_scans(PROG)[0]] + BREAKS[name] + PROG[_scans(PROG)[0]:])


def test_scan_component_lists_are_accepted_exactly_as_libjpeg_accepts_them():
    head = PROG[:_scans(PROG)[0]]
    for n in (1, 2, 3):
        for ids in itertools.product((1, 2, 3, 9), repeat=n):
            sos = bytes([n]) + b"".join(bytes([c, 0]) for c in ids) + bytes([0, 0, 0])
            data = head + _seg(0xDA, sos) + b"\xff\xd9"
            try:
                jpegdct.read(data)
                ours = True
            except jpegdct.Corrupt:
                ours = False
            assert ours == _shown(data), ids


def test_many_scans_are_read_until_the_work_would_be_out_of_proportion():
    jd = GREY
    data = write_progressive(jd, spectral_script(1))
    at = _scans(data)
    one = data[at[1]:at[2]]
    many = data[:at[2]] + one * 300 + data[at[2]:]       # a small picture may be gone over 300 times
    assert _shown(many)
    assert _same(jd, jpegdct.read(many))
    # 4000 x 4000 pixels claimed and 200 scans without data: refused before the work is done
    head = data[:at[0]]
    sof = head.index(b"\xff\xc2")
    big = head[:sof + 5] + struct.pack(">HH", 4000, 4000) + head[sof + 9:]
    started = time.time()
    with pytest.raises(jpegdct.Unsupported, match="go over the picture"):
        jpegdct.read(big + _seg(0xDA, bytes([1, 1, 0, 1, 63, 0])) * 200 + b"\xff\xd9")
    assert time.time() - started < 20


def test_mutated_progressive_files_end_in_a_result_or_a_stated_refusal():
    rng = random.Random(11)
    seeds = [PROG, _jpeg(_scene(), quality=60, subsampling=2, progressive=True, restart_marker_blocks=2),
             write_progressive(BASE[2], deep_script(3), restart=3), write_progressive(GREY, spectral_script(1))]
    started = time.time()
    for _ in range(500):
        data = bytearray(rng.choice(seeds))
        for _ in range(rng.choice((1, 1, 2, 4))):
            kind = rng.randrange(5)
            at = rng.randrange(2, len(data))
            if kind == 0:
                data[at] ^= 1 << rng.randrange(8)
            elif kind == 1:
                data[at] = rng.choice((0, 1, 0x3F, 0x40, 0x7F, 0x80, 0xFF))
            elif kind == 2:
                del data[at:at + rng.choice((1, 2, 9, 60))]
            elif kind == 3:
                del data[at:]
            else:
                data[at:at] = bytes([0xFF, rng.choice((0xD0, 0xD5, 0xD9, 0xDA, 0xC4, 0xDB, 0xC2, 0x00, 0x05))])
        try:
            jd = jpegdct.read(bytes(data))
        except (jpegdct.Corrupt, jpegdct.Unsupported):
            continue
        except ValueError as exc:
            assert str(exc) in ("not a JPEG", "no scan found")
            continue
        for c in jd.components:
            assert c.coeffs.shape == (c.blocks_h, c.blocks_w, 64)
            assert -32768 <= c.coeffs.min() and c.coeffs.max() <= 32767
    assert time.time() - started < 120
