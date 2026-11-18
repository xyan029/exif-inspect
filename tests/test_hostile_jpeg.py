"""Files written to trip the tool's own JPEG reading, by damage or by unusual but legal structure.

A forensic tool reads files that someone else chose. Two things must hold:
a damaged file ends in a stated refusal and never in a stray exception, a
hang or a huge allocation; and a file that a viewer shows is read as the
picture the viewer shows, however oddly it was written.
"""
import io
import random
import struct

import numpy as np
import pytest
from PIL import Image, ImageFile

from exif_inspect import dq, gainmap, hidden, jpegdct, provenance, xmp
from exif_inspect.cli import main
from exif_inspect.jpegdct import (_STD_AC_CHROMA, _STD_AC_LUMA, _STD_DC_CHROMA, _STD_DC_LUMA, _BitWriter,
                                  _build_codes, _magnitude)

DC = [_build_codes(*_STD_DC_LUMA), _build_codes(*_STD_DC_CHROMA)]
AC = [_build_codes(*_STD_AC_LUMA), _build_codes(*_STD_AC_CHROMA)]


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


def _seg(marker, payload):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def _put_block(w, block, pred, t):
    diff = int(block[0]) - pred
    size, bits = _magnitude(diff)
    length, code = DC[t][size]
    w.write(code, length)
    if size:
        w.write(bits, size)
    run, last = 0, 63
    while last > 0 and block[last] == 0:
        last -= 1
    for i in range(1, last + 1):
        v = int(block[i])
        if v == 0:
            run += 1
            continue
        while run > 15:
            w.write(*reversed(AC[t][0xF0]))
            run -= 16
        size, bits = _magnitude(v)
        w.write(*reversed(AC[t][(run << 4) | size]))
        w.write(bits, size)
        run = 0
    if last < 63:
        w.write(*reversed(AC[t][0x00]))
    return int(block[0])


def _encode(jd, layout=None, ids=None, dht=True, restart=0, fill=0, decoys=()):
    """The coefficients of `jd` as a baseline file with the standard Huffman tables.

    layout: the component indices of each scan (default: one scan with all);
    ids: component ids to write; dht: whether to write the tables at all;
    restart: MCUs per restart interval; fill: FF bytes in front of each
    restart marker and of EOI; decoys: scans written with noise.
    """
    comps = jd.components
    layout = layout or [list(range(len(comps)))]
    ids = ids or [c.cid for c in comps]
    out = bytearray(b"\xff\xd8")
    for tq, table in sorted(jd.qtables.items()):
        out += _seg(0xDB, bytes([tq]) + bytes(int(v) for v in table))
    sof = bytes([8]) + struct.pack(">HH", jd.height, jd.width) + bytes([len(comps)])
    for c, cid in zip(comps, ids):
        sof += bytes([cid, (c.h << 4) | c.v, c.tq])
    out += _seg(0xC0, sof)
    if dht:
        out += _seg(0xC4, b"".join(bytes([tcth]) + bytes(bits) + bytes(values) for tcth, (bits, values) in
                                  ((0x00, _STD_DC_LUMA), (0x10, _STD_AC_LUMA), (0x01, _STD_DC_CHROMA),
                                   (0x11, _STD_AC_CHROMA))))
    if restart:
        out += _seg(0xDD, struct.pack(">H", restart))
    hmax, vmax = max(c.h for c in comps), max(c.v for c in comps)
    mcus_x, mcus_y = -(-jd.width // (8 * hmax)), -(-jd.height // (8 * vmax))
    rng = np.random.default_rng(3)
    for n, group in enumerate(layout):
        sos = bytes([len(group)])
        for k in group:
            t = 0 if k == 0 else 1
            sos += bytes([ids[k], (t << 4) | t])
        out += _seg(0xDA, sos + bytes([0, 63, 0]))
        source = {}
        for k in group:
            a = comps[k].coeffs
            if n in decoys:
                a = np.zeros_like(a)
                a[:, :, :6] = rng.integers(-20, 21, a.shape[:2] + (6,))
            source[k] = a
        if len(group) == 1:                      # one block per MCU, only where there is picture
            c = comps[group[0]]
            cols = -(-(-(-jd.width * c.h // hmax)) // 8)
            rows = -(-(-(-jd.height * c.v // vmax)) // 8)
            shape = {group[0]: (1, 1)}
        else:
            cols, rows = mcus_x, mcus_y
            shape = {k: (comps[k].v, comps[k].h) for k in group}
        w = _BitWriter()
        preds = {k: 0 for k in group}
        count = rst = 0
        for my in range(rows):
            for mx in range(cols):
                if restart and count and count % restart == 0:
                    w.flush()
                    w.out += b"\xff" * fill + bytes([0xFF, 0xD0 + rst % 8])
                    rst += 1
                    preds = {k: 0 for k in group}
                for k in group:
                    cv, ch = shape[k]
                    for by in range(cv):
                        for bx in range(ch):
                            preds[k] = _put_block(w, source[k][my * cv + by, mx * ch + bx], preds[k],
                                                  0 if k == 0 else 1)
                count += 1
        w.flush()
        out += w.out
    return bytes(out + b"\xff" * fill + b"\xff\xd9")


def _offset(data, marker):
    return jpegdct.walk(data).segments[[m for _, m, _ in jpegdct.walk(data).segments].index(marker)][0]


def _before(data, marker, extra):
    at = _offset(data, marker)
    return data[:at] + extra + data[at:]


def _patched(data, marker, change):
    """The file with one segment's payload passed through `change`."""
    at, _, payload = next(s for s in jpegdct.walk(data).segments if s[1] == marker)
    new = bytes(change(bytearray(payload)))
    return data[:at] + _seg(marker, new) + data[at + 4 + len(payload):]


def _luma(data, tolerant=False):
    """The luma plane as libjpeg decodes it."""
    ImageFile.LOAD_TRUNCATED_IMAGES = tolerant
    try:
        im = Image.open(io.BytesIO(data))
        im.draft("YCbCr", im.size)
        return np.asarray(im.convert("YCbCr"))[:, :, 0].astype(int)
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = False


SEED = _jpeg(_scene(), quality=85, subsampling=2)
JD = jpegdct.read(SEED)
PLAIN = _encode(JD)


# --- legal or tolerated structure: read as the viewer shows it -----------------------------------

REWRITES = {
    "fill byte before SOS": (lambda: _before(PLAIN, 0xDA, b"\xff"), "fill byte"),
    "fill bytes before SOF": (lambda: _before(PLAIN, 0xC0, b"\xff" * 7), "fill byte"),
    "stray bytes before SOS": (lambda: _before(PLAIN, 0xDA, b"JUNK!"), "stray byte"),
    "no Huffman tables": (lambda: _encode(JD, dht=False), "standard one"),
    "restart markers behind fill bytes": (lambda: _encode(JD, restart=2, fill=3), ""),
    "one scan per component": (lambda: _encode(JD, layout=[[0], [1], [2]]), "3 scans"),
    "luma last": (lambda: _encode(JD, layout=[[1], [2], [0]]), "3 scans"),
    "luma alone, chroma together": (lambda: _encode(JD, layout=[[0], [1, 2]]), "2 scans"),
    "components share an id": (lambda: _encode(JD, ids=[1, 1, 1]), "share an id"),
    "an empty APP segment with length 0": (lambda: _before(PLAIN, 0xC0, b"\xff\xe5\x00\x00"), ""),
}


@pytest.mark.parametrize("name", REWRITES)
def test_rewrites_a_viewer_accepts_read_the_same(name):
    build, note = REWRITES[name]
    data = build()
    assert np.array_equal(_luma(data), _luma(SEED)), "premise: libjpeg shows the same picture"
    jd = jpegdct.read(data)
    for a, b in zip(jd.components, JD.components):
        assert np.array_equal(a.coeffs, b.coeffs)
    assert any(note in n for n in jd.notes) if note else True


def test_untouched_file_has_no_notes():
    assert JD.notes == [] and not JD.truncated
    assert jpegdct.read(_jpeg(_scene(), quality=85, restart_marker_blocks=3)).notes == []


def test_second_scan_of_a_component_is_laid_over_the_first():
    """A noise scan of luma, the chroma scans, then the real luma. libjpeg keeps the first scan's
    values wherever the second codes none, and the reader must return that mixture."""
    data = _encode(JD, layout=[[0], [1], [2], [0]], decoys=(0,))
    jd = jpegdct.read(data)
    assert any("more than one scan" in n for n in jd.notes)
    assert not np.array_equal(jd.luma.coeffs, JD.luma.coeffs)
    ours = jpegdct.to_pixels(jd)[:48, :64]
    assert np.abs(ours - _luma(data)).max() <= 3


def test_scan_after_a_complete_one_is_not_read():
    data = PLAIN[:-2] + _encode(JD, decoys=(0,))[_offset(PLAIN, 0xDA):]
    jd = jpegdct.read(data)
    assert np.array_equal(jd.luma.coeffs, JD.luma.coeffs)


# --- damage: a refusal with a reason, never a stray exception ---------------------------------

def _set(index, value):
    def change(p):
        p[index] = value
        return p
    return change


DAMAGE = {
    "quantisation table cut short": lambda: _patched(PLAIN, 0xDB, lambda p: p[:40]),
    "quantisation table id 4": lambda: _patched(PLAIN, 0xDB, _set(0, 0x04)),
    "quantisation table precision 2": lambda: _patched(PLAIN, 0xDB, _set(0, 0x20)),
    "frame header cut short": lambda: _patched(PLAIN, 0xC0, lambda p: p[:9]),
    "frame header empty": lambda: _patched(PLAIN, 0xC0, lambda p: b""),
    "width 0": lambda: _patched(PLAIN, 0xC0, lambda p: p[:3] + b"\x00\x00" + p[5:]),
    "sampling factor 0": lambda: _patched(PLAIN, 0xC0, _set(7, 0x02)),
    "sampling factor 5": lambda: _patched(PLAIN, 0xC0, _set(7, 0x51)),
    "frame asks for table 9": lambda: _patched(PLAIN, 0xC0, _set(8, 9)),
    "frame asks for a table the file lacks": lambda: _patched(PLAIN, 0xC0, _set(8, 3)),
    "two frame headers": lambda: _before(PLAIN, 0xC4, PLAIN[_offset(PLAIN, 0xC0):_offset(PLAIN, 0xC4)]),
    "Huffman table cut short": lambda: _patched(PLAIN, 0xC4, lambda p: p[:20]),
    "Huffman table of class 2": lambda: _patched(PLAIN, 0xC4, _set(0, 0x20)),
    "Huffman table with too many codes": lambda: _patched(
        PLAIN, 0xC4, lambda p: bytes(p) + bytes([0x00, 3] + [0] * 15 + [0, 1, 2])),
    "DC size of 200 bits": lambda: _patched(PLAIN, 0xC4, _set(17, 200)),
    "scan header cut short": lambda: _patched(PLAIN, 0xDA, lambda p: p[:3]),
    "scan header empty": lambda: _patched(PLAIN, 0xDA, lambda p: b""),
    "scan names a component the frame lacks": lambda: _patched(PLAIN, 0xDA, _set(1, 77)),
    "scan asks for Huffman table 9": lambda: _patched(PLAIN, 0xDA, _set(2, 0x90)),
    "scan asks for a table no standard covers": lambda: _patched(PLAIN, 0xDA, _set(2, 0x22)),
    "scan before the frame header": lambda: _before(PLAIN, 0xC0, _seg(0xDA, bytes([1, 1, 0, 0, 63, 0]))),
    "restart interval cut short": lambda: _before(PLAIN, 0xDA, _seg(0xDD, b"\x01")),
    "segment longer than the file": lambda: PLAIN[:_offset(PLAIN, 0xC4) + 60],
    "segment length 1": lambda: _before(PLAIN, 0xDA, b"\xff\xdb\x00\x01"),
}


@pytest.mark.parametrize("name", DAMAGE)
def test_damaged_structure_is_refused_with_a_reason(name):
    with pytest.raises(jpegdct.Corrupt) as info:
        jpegdct.read(DAMAGE[name]())
    assert str(info.value)


@pytest.mark.parametrize("name, change", [
    ("12-bit", _set(0, 12)),
    ("height left to DNL", lambda p: p[:1] + b"\x00\x00" + p[3:]),
    ("5 components", _set(5, 5)),
])
def test_valid_kinds_the_reader_does_not_read_are_named(name, change):
    with pytest.raises(jpegdct.Unsupported):
        jpegdct.read(_patched(PLAIN, 0xC0, change))


def test_no_scan_and_not_a_jpeg():
    with pytest.raises(ValueError, match="no scan found"):
        jpegdct.read(PLAIN[:_offset(PLAIN, 0xDA)] + b"\xff\xd9")
    with pytest.raises(ValueError, match="not a JPEG"):
        jpegdct.read(b"\x89PNG\r\n\x1a\n")


def test_random_damage_never_escapes_as_another_exception():
    """A small fuzz run. The scratch fuzzer behind this file ran 250 000 such inputs; this keeps a
    slice of it in the suite so that a new parsing path cannot bring a stray exception back."""
    rng = random.Random(7)
    seeds = [SEED, PLAIN, _jpeg(_scene().convert("L"), quality=80),
             _jpeg(_scene(), quality=90, optimize=True, restart_marker_blocks=4)]
    for _ in range(600):
        data = bytearray(rng.choice(seeds))
        head = _offset(bytes(data), 0xDA) + 14
        for _ in range(rng.choice((1, 1, 2, 3))):
            if len(data) < 4:
                break
            kind = rng.randrange(4)
            at = rng.randrange(2, min(len(data), head if rng.random() < 0.8 else len(data)))
            if kind == 0:
                data[at] ^= 1 << rng.randrange(8)
            elif kind == 1:
                data[at] = rng.choice((0, 1, 2, 3, 4, 8, 15, 16, 17, 0x7F, 0x80, 0xFE, 0xFF))
            elif kind == 2:
                del data[at:at + rng.choice((1, 2, 17, 64))]
            else:
                del data[at:]
        try:
            jd = jpegdct.read(bytes(data))
            jd.dequantised(0)
            jpegdct.to_pixels(jd)
        except (jpegdct.Corrupt, jpegdct.Unsupported):
            pass
        except ValueError as exc:
            assert str(exc) in ("not a JPEG", "no scan found"), exc


# --- damaged data: read the way libjpeg reads it -------------------------------------------------

def _agreement(jd, reference):
    """Share of the luma blocks whose pixels are libjpeg's, among blocks with honest values.

    Where damage produced coefficients no picture has, libjpeg's integer
    transform wraps around and the pixels differ for equal coefficients;
    such blocks are left out.
    """
    c = jd.luma
    coeffs = (c.coeffs * jd.qtables[c.tq]).astype(float)
    natural = np.zeros_like(coeffs)
    natural[:, :, jpegdct.ZIGZAG] = coeffs
    k, x = np.arange(8)[:, None], np.arange(8)[None, :]
    m = np.cos(np.pi * (2 * x + 1) * k / 16) * 0.5
    m[0] /= np.sqrt(2)
    raw = np.einsum("ki,abkl,lj->abij", m, natural.reshape(c.blocks_h, c.blocks_w, 8, 8), m) + 128
    h, w = reference.shape[0] // 8, reference.shape[1] // 8
    raw = raw[:h, :w]
    ref = reference[:h * 8, :w * 8].reshape(h, 8, w, 8).transpose(0, 2, 1, 3)
    sane = (raw.min(axis=(2, 3)) > -250) & (raw.max(axis=(2, 3)) < 500)
    same = (np.abs(np.clip(np.round(raw), 0, 255) - ref) <= 3).all(axis=(2, 3))
    return float((same & sane).sum() / max(1, sane.sum())), int(sane.sum())


def _scan_cut(data, share):
    start = jpegdct.walk(data).scan
    body = data[start:-2]
    return data[:start] + body[:int(len(body) * share)] + b"\xff\xd9"


def test_truncated_scan_leaves_the_rest_zero_as_libjpeg_does():
    data = _scan_cut(_jpeg(_scene((128, 96)), quality=85), 0.6)
    jd = jpegdct.read(data)
    assert jd.truncated and any("runs out" in n for n in jd.notes)
    share, compared = _agreement(jd, _luma(data, tolerant=True))
    assert share == 1.0 and compared > 150
    assert not jd.luma.coeffs[-1].any()          # the last row of blocks was never coded


def test_lost_restart_interval_is_resynchronised_as_libjpeg_does():
    """The data of some intervals is overwritten; decoding picks up again at the next marker."""
    data = bytearray(_jpeg(_scene((128, 96)), quality=85, restart_marker_blocks=4))
    start = jpegdct.walk(bytes(data)).scan
    rng = random.Random(5)
    marks = [i for i in range(start, len(data) - 2) if data[i] == 0xFF and 0xD0 <= data[i + 1] <= 0xD7]
    for a, b in ((marks[3], marks[4]), (marks[9], marks[10])):
        for i in range(a + 2, b):
            data[i] = rng.randrange(0, 0xFF)
    jd = jpegdct.read(bytes(data))
    share, compared = _agreement(jd, _luma(bytes(data), tolerant=True))
    assert share >= 0.98 and compared > 100


def test_fill_bytes_inside_the_scan_are_passed():
    """FF FF 00 in the entropy-coded data is one stuffed FF behind a fill byte, which the
    standard does not allow and libjpeg accepts. The picture goes on behind it; libjpeg-turbo
    can leave stray values in the one MCU where it happens, and nowhere else."""
    data = _jpeg(_scene((128, 96)), quality=95)
    start = jpegdct.walk(data).scan
    at = data.index(b"\xff\x00", start)
    padded = data[:at] + b"\xff" + data[at:]
    jd = jpegdct.read(padded)
    assert np.array_equal(jd.luma.coeffs, jpegdct.read(data).luma.coeffs)
    share, compared = _agreement(jd, _luma(padded))
    assert share >= 0.97 and compared == 192


# --- size: a small file must not buy a long run or a large allocation ------------------------------

def _claiming(data, width, height):
    return _patched(data, 0xC0, lambda p: p[:1] + struct.pack(">HH", height, width) + p[5:])


def test_a_kilobyte_claiming_a_huge_picture_is_cheap():
    small = _jpeg(_scene().convert("L"), quality=85)
    with pytest.raises(jpegdct.Corrupt, match="65500"):       # past the size libjpeg takes at all
        jpegdct.read(_claiming(small, 65535, 65535))
    bomb = _claiming(small, 65500, 65500)
    assert len(bomb) < 2000
    with pytest.raises(jpegdct.Unsupported, match="too large"):
        jpegdct.read(bomb)
    jd = jpegdct.read(bomb, max_blocks=200000)
    assert jd.truncated
    assert jd.luma.coeffs.shape[0] * jd.luma.coeffs.shape[1] <= 200000 + 8192
    assert int((jd.luma.coeffs != 0).any(axis=2).sum()) <= 50      # the 48 real blocks, one padded


def test_the_cap_reads_whole_rows_and_allocates_no_more():
    data = _jpeg(_scene((512, 384)), quality=85)
    jd = jpegdct.read(data, max_blocks=500)
    assert jd.luma.blocks_h == jd.luma.coeffs.shape[0] == 8          # 4 rows of 32 MCUs, 2 blocks high
    assert jd.components[1].coeffs.shape[0] == 4
    whole = jpegdct.read(data)
    assert np.array_equal(jd.luma.coeffs, whole.luma.coeffs[:8])


def test_many_huffman_tables_cost_nothing_until_used():
    extra = _seg(0xC4, (bytes([0x03]) + bytes([1] + [0] * 15) + b"\x00") * 3000)
    data = _before(PLAIN, 0xDA, extra * 4)
    assert np.array_equal(jpegdct.read(data).luma.coeffs, JD.luma.coeffs)


# --- the other byte-level checks go through the same walker ------------------------------------

XMP = (b'http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
       b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" '
       b'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="some editor"/></rdf:RDF></x:xmpmeta>')
PAYLOAD = b"PK\x03\x04" + b"appended after the picture " * 4


@pytest.mark.parametrize("name, build", [
    ("as written", lambda d: d),
    ("fill byte before SOS", lambda d: _before(d, 0xDA, b"\xff")),
    ("fill byte before the first segment", lambda d: d[:2] + b"\xff" + d[2:]),
    ("stray bytes before SOS", lambda d: _before(d, 0xDA, b"\x00\x01\x02")),
])
def test_trailer_xmp_and_c2pa_are_found_behind_fill_and_stray_bytes(name, build):
    marked = PLAIN[:2] + _seg(0xE1, XMP) + _seg(0xEB, b"JP\x00\x00jumb c2pa") + PLAIN[2:]
    data = build(marked) + PAYLOAD
    t = hidden.trailer(data, "JPEG")
    assert t is not None and t.size == len(PAYLOAD) and t.kind.startswith("ZIP")
    assert xmp.read(data, "JPEG", {}).creator_tool == "some editor"
    assert provenance.scan(data, "JPEG").c2pa


def test_trailer_is_found_after_a_file_of_several_scans():
    data = _encode(JD, layout=[[0], [1], [2]]) + PAYLOAD
    t = hidden.trailer(data, "JPEG")
    assert t is not None and t.offset == len(data) - len(PAYLOAD)


def test_cut_mpf_table_gives_no_gain_map_and_no_exception():
    mpf = b"MPF\x00II*\x00\x08\x00\x00\x00\x03\x00" + b"\x02\xb0\x07\x00\x20\x00\x00\x00\x32\x00\x00\x00"
    for cut in range(4, len(mpf) + 1):
        data = PLAIN[:2] + _seg(0xE2, mpf[:cut]) + PLAIN[2:]
        assert gainmap.from_jpeg(data, (64, 48)) is None


# --- dq on such files ----------------------------------------------------------------------------

def _forged(tmp_path):
    host = Image.open(io.BytesIO(_jpeg(_scene((384, 320), seed=5), quality=70))).convert("RGB")
    host.paste(_scene((256, 256), seed=9).crop((0, 0, 128, 96)), (128, 96))
    data = _jpeg(host, quality=90)
    return jpegdct.read(data)


@pytest.mark.parametrize("name, build", [
    ("fill byte", lambda jd: _before(_encode(jd), 0xDA, b"\xff")),
    ("no Huffman tables, one scan per component", lambda jd: _encode(jd, layout=[[0], [1], [2]], dht=False)),
    ("components share an id", lambda jd: _encode(jd, ids=[1, 1, 1])),
])
def test_dq_finds_the_patch_however_the_file_is_written(tmp_path, capsys, name, build):
    path = tmp_path / "forged.jpg"
    path.write_bytes(build(_forged(tmp_path)))
    res = dq.analyze(path)
    assert res.verdict == "region" and res.structure
    x0, y0, x1, y1 = res.box
    assert x0 <= 136 and y0 <= 104 and x1 >= 248 and y1 >= 184
    assert main(["dq", str(path)]) == 0
    assert "structure :" in capsys.readouterr().out


def test_dq_skips_a_damaged_file_with_the_reason(tmp_path):
    path = tmp_path / "damaged.jpg"
    path.write_bytes(_patched(PLAIN, 0xC4, lambda p: p[:20]))
    res = dq.analyze(path)
    assert res.verdict == "skipped" and "Huffman table cut short" in res.note
    path.write_bytes(_claiming(_jpeg(_scene().convert("L"), quality=85), 65535, 65535))
    assert dq.analyze(path).verdict == "skipped"
