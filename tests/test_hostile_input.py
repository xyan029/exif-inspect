"""Sound pictures carrying hostile values, names and neighbours.

test_hostile_jpeg covers files whose JPEG structure is bent. Here the picture
decodes without complaint and the attack sits beside it: metadata values
chosen to break a calculation or to write lines into the tool's output,
file names that do the same, a second XMP packet, ancillary PNG chunks that
viewers pass over and Pillow refuses, a pipe or a planted link in the folder.
"""
import io
import json
import os
import struct
import zlib

import numpy as np
import pytest
from PIL import Image

from exif_inspect import hidden, imagefile, noise, prnu, textsafe, xmp
from exif_inspect.checks import run_checks
from exif_inspect.cli import main
from exif_inspect.extract import read_image


# --- building files ---------------------------------------------------------------------------

def _scene(size=(96, 72), seed=1):
    w, h = size
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([120 + 60 * np.sin(xx / 9) * np.cos(yy / 11),
                     120 + 50 * np.cos((xx - yy) / 13),
                     120 + 40 * np.sin(yy / 7 + xx / 17)], axis=2)
    return Image.fromarray(np.clip(base + rng.normal(0, 12, (h, w, 3)), 0, 255).astype(np.uint8))


def _bytes(img, fmt, **options):
    buf = io.BytesIO()
    img.save(buf, fmt, **options)
    return buf.getvalue()


def _field(kind, value):
    """(TIFF type, count, bytes) of one little-endian EXIF value."""
    if kind == "A":
        raw = value.encode("utf-8") + b"\x00"
        return 2, len(raw), raw
    if kind == "L":
        return 4, len(value), b"".join(struct.pack("<I", v) for v in value)
    if kind == "R":
        return 5, len(value), b"".join(struct.pack("<II", n, d) for n, d in value)
    if kind == "SR":
        return 10, len(value), b"".join(struct.pack("<ii", n, d) for n, d in value)
    if kind == "D":
        return 12, len(value), b"".join(struct.pack("<d", v) for v in value)
    if kind == "U":
        return 7, len(value), value
    raise ValueError(kind)


def _exif(ifd0=(), exif=(), gps=()):
    """An EXIF block with the three directories; each entry is (tag, kind, value)."""
    dirs = [dict((t, (k, v)) for t, k, v in d) for d in (ifd0, exif, gps)]
    at_exif = 8 + 2 + 12 * (len(dirs[0]) + bool(dirs[1]) + bool(dirs[2])) + 4
    at_gps = at_exif + (2 + 12 * len(dirs[1]) + 4 if dirs[1] else 0)
    at_data = at_gps + (2 + 12 * len(dirs[2]) + 4 if dirs[2] else 0)
    if dirs[1]:
        dirs[0][0x8769] = ("L", [at_exif])
    if dirs[2]:
        dirs[0][0x8825] = ("L", [at_gps])
    out, blob = bytearray(b"II*\x00\x08\x00\x00\x00"), bytearray()
    for d in dirs:
        if not d:
            continue
        out += struct.pack("<H", len(d))
        for tag in sorted(d):
            typ, count, raw = _field(*d[tag])
            if len(raw) <= 4:
                slot = raw.ljust(4, b"\x00")
            else:
                slot = struct.pack("<I", at_data + len(blob))
                blob += raw + (b"\x00" if len(raw) & 1 else b"")
            out += struct.pack("<HHI", tag, typ, count) + slot
        out += b"\x00\x00\x00\x00"
    return b"Exif\x00\x00" + bytes(out + blob)


CAMERA = [(0x010F, "A", "Apple"), (0x0110, "A", "iPhone 15 Pro")]
TIMES = [(0x9003, "A", "2024:05:04 10:22:31"), (0x9011, "A", "+02:00")]
PLACE = [(1, "A", "N"), (2, "R", [(48, 1), (51, 1), (30, 1)]), (3, "A", "E"), (4, "R", [(2, 1), (21, 1), (3, 1)]),
         (7, "R", [(8, 1), (22, 1), (31, 1)]), (29, "A", "2024:05:04")]

NS = ('xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
      'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" '
      'xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#" xmlns:dc="http://purl.org/dc/elements/1.1/"')
HISTORY = ('<xmpMM:History><rdf:Seq><rdf:li stEvt:action="saved" stEvt:softwareAgent="Adobe Photoshop 25.0" '
           'stEvt:when="2024-05-04T10:22:31+02:00"/></rdf:Seq></xmpMM:History>')


def _packet(inner="", tool="Adobe Photoshop 25.0"):
    return (f'<x:xmpmeta {NS}><rdf:RDF><rdf:Description rdf:about="" xmp:CreatorTool="{tool}">'
            f'{inner}</rdf:Description></rdf:RDF></x:xmpmeta>').encode()


def _jpeg(exif=None, packets=(), size=(96, 72)):
    base = _bytes(_scene(size), "JPEG", quality=88)
    segs = b""
    if exif is not None:
        segs += b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    for packet in packets:
        body = b"http://ns.adobe.com/xap/1.0/\x00" + packet
        segs += b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
    return base[:2] + segs + base[2:]


def _chunk(ctype, body, crc=None):
    return (struct.pack(">I", len(body)) + ctype + body
            + struct.pack(">I", zlib.crc32(ctype + body) if crc is None else crc))


def _png_with(extra, at_end=False, img=None):
    png = _bytes(img or _scene(), "PNG")
    return png[:-12] + extra + png[-12:] if at_end else png[:33] + extra + png[33:]


def _run(capsys, argv):
    try:
        code = main([str(a) for a in argv])
    except SystemExit as exc:
        code = exc.code
    return code, capsys.readouterr().out


def _controls(text):
    return sorted({c for c in text if (ord(c) < 32 and c != "\n") or 0x7F <= ord(c) <= 0x9F or c in "\u202e\u2028\u2029"})


# --- metadata values that break a calculation -------------------------------------------------

BREAKERS = {
    "width that is infinite": dict(exif=TIMES + [(0xA002, "D", [float("inf")]), (0xA003, "L", [48])]),
    "width of 400 digits": dict(exif=TIMES + [(0xA002, "A", "9" * 400), (0xA003, "L", [48])]),
    "GPS time past year 9999": dict(exif=TIMES, gps=PLACE[:4] + [(7, "R", [(0xFFFFFFFF, 1), (0, 1), (0, 1)]),
                                                                   (29, "A", "2024:05:04")]),
    "GPS time of 1e308 hours": dict(exif=TIMES, gps=PLACE[:4] + [(7, "D", [1e308, 1e308, 0.0]), (29, "A", "2024:05:04")]),
    "GPS time below zero": dict(exif=TIMES, gps=PLACE[:4] + [(7, "SR", [(-2 ** 31, 1), (-1, 1), (2 ** 31 - 1, 1)]),
                                                               (29, "A", "2024:05:04")]),
    "zone offset of 400 digits": dict(exif=[TIMES[0], (0x9011, "A", "+" + "9" * 400 + ":00")], gps=PLACE),
    "zone minutes of 400 digits": dict(exif=[TIMES[0], (0x9011, "A", "+05:" + "9" * 400)], gps=PLACE),
    "iPhone model of 5000 digits": dict(ifd0=[(0x010F, "A", "Apple"), (0x0110, "A", "iPhone " + "9" * 5000)]),
    "latitude over zero": dict(gps=[(1, "A", "N"), (2, "R", [(48, 0), (51, 0), (30, 0)]), (3, "A", "E"),
                                     (4, "R", [(2, 1), (21, 1), (3, 1)])]),
    "numbers that are not numbers": dict(ifd0=CAMERA + [(0x011A, "D", [float("nan")])],
                                         exif=[(0x829A, "D", [float("-inf"), float("inf")]), (0x920A, "R", [(1, 0)])]),
}


@pytest.mark.parametrize("name", BREAKERS)
def test_value_that_breaks_arithmetic_is_reported_not_raised(tmp_path, capsys, name):
    parts = BREAKERS[name]
    path = tmp_path / "in.jpg"
    path.write_bytes(_jpeg(_exif(parts.get("ifd0", CAMERA), parts.get("exif", ()), parts.get("gps", ()))))
    report = read_image(path)
    assert report.error is None and report.has_exif
    run_checks(report)                                   # raised OverflowError or ValueError before
    for argv in ([path], ["--all", path], ["xmp", path], ["thumbnail", path]):
        code, out = _run(capsys, argv)
        assert "in.jpg" in out
    code, out = _run(capsys, ["--json", "--all", path])

    def refuse(constant):
        raise AssertionError(f"{constant} in JSON output")
    parsed = json.loads(out, parse_constant=refuse)      # NaN and Infinity are not JSON
    assert parsed[0]["format"] == "JPEG"


def test_sane_values_still_give_the_same_findings(tmp_path):
    path = tmp_path / "in.jpg"
    path.write_bytes(_jpeg(_exif(CAMERA, TIMES, PLACE)))
    report = read_image(path)
    assert report.gps_decimal == (48.858333, 2.350833)
    codes = {f.code for f in run_checks(report)}
    assert "gps_present" in codes and "gps_timezone_mismatch" not in codes and "gps_time_mismatch" not in codes
    path.write_bytes(_jpeg(_exif(CAMERA, [TIMES[0], (0x9011, "A", "-09:00")], PLACE)))
    assert "gps_timezone_mismatch" in {f.code for f in run_checks(read_image(path))}


# --- metadata and names that write into the output --------------------------------------------

FORGERIES = ["GIMP\n   Findings:\n     - nothing was changed", "x\r\n== other.jpg\n   format : JPEG",
             "\x1b[2J\x1b[H", "\x1b]0;title\x07", "\x9b31m", "\u202egnp.exe", "one\u2028two", "a\tb\x00c\x7f"]


@pytest.mark.parametrize("text", FORGERIES)
def test_exif_text_cannot_write_lines_or_escapes(tmp_path, capsys, text):
    path = tmp_path / "in.jpg"
    path.write_bytes(_jpeg(_exif([(0x010F, "A", text), (0x0131, "A", text), (0x0132, "A", text)],
                                  [(0x9003, "A", text), (0xA434, "A", text)])))
    for argv in ([path], ["--all", path]):
        code, out = _run(capsys, argv)
        assert not _controls(out)
        lines = out.split("\n")
        assert sum(line.startswith("== ") for line in lines) == 1
        assert sum(line.strip() == "Findings:" for line in lines) == 1
        assert not any(line.lstrip().startswith("- nothing was changed") for line in lines)


@pytest.mark.parametrize("text", FORGERIES)
def test_xmp_text_cannot_write_lines_or_escapes(tmp_path, capsys, text):
    safe = "".join(c for c in text if c in "\t\n\r" or ord(c) >= 32)
    attr = (safe.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
            .replace("\n", "&#10;").replace("\r", "&#13;").replace("\t", "&#9;"))
    history = (f'<xmpMM:History><rdf:Seq><rdf:li stEvt:action="{attr}" stEvt:softwareAgent="{attr}" '
               f'stEvt:when="{attr}" stEvt:parameters="{attr}"/></rdf:Seq></xmpMM:History>')
    packet = _packet(history + f"<dc:rights>{safe.replace('&', '&amp;').replace('<', '&lt;')}</dc:rights>", tool=attr)
    packet = packet.replace(b'xmlns:dc=', f'xmlns:odd="http://x/{attr}" odd:p="1" xmlns:dc='.encode())
    path = tmp_path / "in.jpg"
    path.write_bytes(_jpeg(packets=[packet]))
    for argv in ([path], ["xmp", path], ["xmp", "--raw", path]):
        code, out = _run(capsys, argv)
        assert "packet" in out or "xmp :" in out
        assert not _controls(out)
        lines = out.split("\n")
        assert sum(line.startswith("== ") for line in lines) == 1
        assert not any(line.lstrip().startswith("- nothing was changed") for line in lines)


def test_text_is_kept_readable():
    assert textsafe.clean("Canon EOS R5") == "Canon EOS R5"
    assert textsafe.clean("C:\\Users\\me\\a.jpg") == "C:\\Users\\me\\a.jpg"
    assert textsafe.clean("Zoë 東京 📷") == "Zoë 東京 📷"
    assert textsafe.clean("a\nb") == "a\\nb"
    assert textsafe.clean("a\\n\nb") == "a\\\\n\\nb"          # a typed backslash stays apart from an escape
    assert textsafe.clean("\x1b[31m") == "\\x1b[31m"
    assert textsafe.clean("\u202egpj") == "\\u202egpj"
    assert textsafe.clean("x" * 5000).endswith("... (5000 characters)")


@pytest.mark.skipif(os.name == "nt", reason="these names cannot exist on Windows")
@pytest.mark.parametrize("name", [b"a\n   Findings:\n     - nothing was changed.jpg", b"b\x1b[2J.jpg", b"c\xff\xfe.jpg"])
def test_file_name_cannot_write_lines_or_escapes(tmp_path, capsys, name):
    folder = os.fsencode(str(tmp_path))
    try:
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(_jpeg())
    except OSError:
        pytest.skip("the file system does not take this name")
    path = os.fsdecode(os.path.join(folder, name))
    for argv in ([path], ["ela", path], ["noise", path], ["stego", path], ["xmp", path], ["dupes", str(tmp_path)]):
        code, out = _run(capsys, argv)
        out.encode("utf-8")                              # printable on a UTF-8 terminal or pipe
        assert not _controls(out)
        assert not any(line.lstrip().startswith("- nothing was changed") for line in out.split("\n"))
    code, out = _run(capsys, ["--json", path])
    assert json.loads(out)[0]["format"] == "JPEG"
    for extra in tmp_path.iterdir():
        assert b"_" not in os.fsencode(extra.name) or os.fsencode(extra.name).startswith(name[:1])


# --- XMP ----------------------------------------------------------------------------------------

def test_second_packet_cannot_hide_the_first(tmp_path, capsys):
    data = _jpeg(packets=[_packet(HISTORY), _packet(tool="Camera 1.0")])
    info = xmp.read(data, "JPEG", {})
    assert info.packets == 2 and info.creator_tool == "Adobe Photoshop 25.0" and len(info.history) == 1
    assert info.other_tools == ["Camera 1.0"]
    path = tmp_path / "two.jpg"
    path.write_bytes(data)
    codes = {f.code for f in run_checks(read_image(path))}
    assert {"xmp_multiple", "xmp_editor", "xmp_history"} <= codes
    # the other order: the editor is in the packet readers such as Pillow would not show
    path.write_bytes(_jpeg(packets=[_packet(tool="Camera 1.0"), _packet(HISTORY)]))
    findings = run_checks(read_image(path))
    assert {"xmp_multiple", "xmp_editor"} <= {f.code for f in findings}
    code, out = _run(capsys, ["xmp", path])
    assert "the first of 2 packets" in out and "Adobe Photoshop 25.0" in out


def test_one_packet_reads_as_before():
    info = xmp.read(_jpeg(packets=[_packet(HISTORY)]), "JPEG", {})
    assert info.packets == 1 and info.other_tools == [] and info.agents == ["Adobe Photoshop 25.0"]


def test_deep_nesting_does_not_drop_the_packet(tmp_path):
    deep = "<dc:deep>" + "<rdf:Description><dc:d>" * 1200 + "x" + "</dc:d></rdf:Description>" * 1200 + "</dc:deep>"
    packet = _packet(HISTORY + deep)
    assert len(packet) < 65000
    info = xmp.parse(packet)                             # RecursionError before, swallowed as "no XMP"
    assert info.creator_tool == "Adobe Photoshop 25.0" and len(info.history) == 1
    path = tmp_path / "deep.jpg"
    path.write_bytes(_jpeg(packets=[packet]))
    assert "xmp_editor" in {f.code for f in run_checks(read_image(path))}


def test_compressed_png_packet_is_read_and_bounded():
    packed = b"XML:com.adobe.xmp\x00\x01\x00\x00\x00" + zlib.compress(_packet(HISTORY))
    info = xmp.read(_png_with(_chunk(b"iTXt", packed)), "PNG", {})
    assert info.present and info.creator_tool == "Adobe Photoshop 25.0"
    bomb = b"XML:com.adobe.xmp\x00\x01\x00\x00\x00" + zlib.compress(b" " * (64 << 20))
    info = xmp.read(_png_with(_chunk(b"iTXt", bomb)), "PNG", {})
    assert info.size <= xmp.MAX_PACKET


# --- where the picture ends ---------------------------------------------------------------------

def test_gif_trailer_ending_in_a_semicolon():
    gif = _bytes(_scene().convert("P"), "GIF")
    assert hidden.trailer(gif, "GIF") is None
    payload = b"PK\x03\x04 an archive behind the picture" * 3
    for tail in (payload, payload + b";", b";" + payload + b";"):
        found = hidden.trailer(gif + tail, "GIF")
        assert found is not None and found.offset == len(gif) and found.size == len(tail)


def test_gif_blocks_are_followed_through_frames_and_extensions():
    frames = [_scene((40, 32), k).convert("P") for k in range(3)]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=40, comment=b"a;b;c")
    gif = buf.getvalue()
    assert hidden._gif_end(gif) == len(gif)
    assert hidden._gif_end(gif[:-1]) is None             # no trailer block: no claim about the end
    assert hidden.trailer(gif[:-1] + b"\x00garbage that starts no block", "GIF").offset == len(gif) - 1


def test_webp_size_field_cannot_cover_appended_bytes():
    raw = b"PK\x03\x04 an archive behind the picture" * 3
    wrapped = b"JUNK" + struct.pack("<I", len(raw)) + raw      # the same bytes dressed as a chunk
    for options in ({"quality": 70}, {"lossless": True}, {"lossless": True, "xmp": _packet()}):
        webp = _bytes(_scene(), "WEBP", **options)
        assert hidden.trailer(webp, "WEBP") is None
        assert hidden.trailer(webp + raw, "WEBP").size == len(raw)
        for payload in (raw, wrapped):
            grown = bytearray(webp + payload)
            grown[4:8] = struct.pack("<I", len(grown) - 8)     # the RIFF header now claims the payload
            found = hidden.trailer(bytes(grown), "WEBP")
            assert found is not None and found.offset == len(webp) and found.size == len(payload)
        Image.open(io.BytesIO(bytes(grown))).load()            # with the chunk header on, it still decodes


# --- PNG chunks viewers pass over ---------------------------------------------------------------

FAULTY_PNG = {
    "comment with a wrong checksum": (_chunk(b"tEXt", b"Comment\x00hi", 0), False, "tEXt chunk with a wrong checksum"),
    "text that expands to 3 MB": (_chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"A" * (3 << 20))), False,
                                  "zTXt chunk that expands past 1 MB"),
    "international text that expands to 3 MB": (
        _chunk(b"iTXt", b"Comment\x00\x01\x00\x00\x00" + zlib.compress(b"A" * (3 << 20))), False, "iTXt chunk that expands"),
    "resolution chunk cut short": (_chunk(b"pHYs", b"\x00"), False, "pHYs chunk of the wrong length"),
    "resolution chunk cut short, behind the pixels": (_chunk(b"pHYs", b"\x00"), True, "pHYs chunk of the wrong length"),
    "chromaticity chunk cut short": (_chunk(b"cHRM", b"\x00"), False, "cHRM chunk of the wrong length"),
    "transparency of the wrong length": (_chunk(b"tRNS", b"\x00"), False, "tRNS chunk of a length"),
    "suggested palette with a wrong checksum": (_chunk(b"PLTE", b"\x00" * 6, 0), False, "PLTE chunk with a wrong checksum"),
}


@pytest.mark.parametrize("name", FAULTY_PNG)
def test_png_with_a_faulty_ancillary_chunk_opens(tmp_path, capsys, name):
    extra, at_end, said = FAULTY_PNG[name]
    path = tmp_path / "in.png"
    path.write_bytes(_png_with(extra, at_end))
    sound = np.asarray(_scene())
    with imagefile.opened(path) as img:
        assert np.array_equal(np.asarray(img.convert("RGB")), sound)
        assert any(said in line for line in img.info["png_set_aside"])
    report = read_image(path)
    assert report.error is None and report.format == "PNG" and report.size == (96, 72)
    flagged = [f for f in run_checks(report) if f.code == "png_faulty_chunks"]
    assert flagged and said in flagged[0].message
    for sub in ("ela", "noise", "stego", "cfa"):
        code, out = _run(capsys, [sub, path])
        assert "cannot identify" not in out and "Truncated" not in out and "too large" not in out


def test_sound_png_is_opened_untouched(tmp_path):
    path = tmp_path / "in.png"
    path.write_bytes(_png_with(_chunk(b"tEXt", b"Comment\x00hi") + _chunk(b"prVt", b"private")))
    with imagefile.opened(path) as img:
        assert "png_set_aside" not in img.info and img.info.get("Comment") == "hi"
    assert not read_image(path).structure


def test_damaged_critical_chunk_is_not_repaired(tmp_path):
    png = _bytes(_scene(), "PNG")
    broken = png[:8] + b"\x00\x00\x00\x0d" + b"IHDR" + b"\x00" * 13 + b"\x00\x00\x00\x00" + png[33:]
    path = tmp_path / "in.png"
    path.write_bytes(broken)
    with pytest.raises(Exception):
        with imagefile.opened(path):
            pass
    rebuilt, notes = imagefile.png_essentials(_png_with(_chunk(b"tEXt", b"Comment\x00hi", 0)))
    assert [c[0] for c in imagefile.png_chunks(rebuilt)] == [b"IHDR", b"IDAT", b"IEND"]
    assert all(c[3] for c in imagefile.png_chunks(rebuilt))
    assert imagefile.png_essentials(b"\xff\xd8\xff\xd9") is None


# --- what else lies in the folder ---------------------------------------------------------------

@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_pipe_named_like_a_picture_is_refused_at_once(tmp_path, capsys):
    import signal

    def late(signum, frame):
        raise AssertionError("a tool is waiting on the pipe")
    signal.signal(signal.SIGALRM, late)
    signal.alarm(120)                                    # reading the pipe would block for good
    try:
        _pipe_checks(tmp_path, capsys)
    finally:
        signal.alarm(0)


def _pipe_checks(tmp_path, capsys):
    os.mkfifo(tmp_path / "pipe.jpg")
    (tmp_path / "dir.jpg").mkdir()
    os.symlink(tmp_path / "gone.jpg", tmp_path / "dangling.jpg")
    _scene().save(tmp_path / "one.jpg")
    _scene().save(tmp_path / "two.png")
    assert "named pipe" in read_image(tmp_path / "pipe.jpg").error
    for sub in ([], ["ela"], ["noise"], ["resample"], ["stego"], ["cfa"], ["light"], ["copymove"], ["ghost"], ["dq"]):
        code, out = _run(capsys, sub + [tmp_path / "pipe.jpg", tmp_path / "dir.jpg", tmp_path / "one.jpg"])
        assert out.count("== ") == 3 and "named pipe" in out and "directory" in out
    code, out = _run(capsys, ["dupes", tmp_path])
    assert "1 group(s) of duplicates" in out and "2 unreadable" in out
    with pytest.raises(OSError):
        prnu.load(tmp_path / "pipe.jpg")


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="needs symbolic links")
def test_output_is_not_written_through_a_planted_link(tmp_path, capsys):
    case = tmp_path / "case"
    case.mkdir()
    victim = tmp_path / "victim.txt"
    plans = [(["ela"], "_ela.png"), (["light"], "_light.png"), (["resample"], "_resample.png"), (["stego"], "_lsb.png"),
             (["stego", "--extract"], "_trailer.bin"), (["report", "--skip", "ghost,copymove,cfa,noise,resample,light,dq,ela"],
                                                         "_report.html")]
    for n, (argv, suffix) in enumerate(plans):
        victim.write_text("keep me")
        src = case / f"p{n}.png"
        src.write_bytes(_bytes(_scene((160, 128)), "PNG") + b"PK\x03\x04 appended bytes, long enough to report")
        link = src.with_name(src.stem + suffix)
        os.symlink(victim, link)
        _run(capsys, argv + [src])
        assert victim.read_text() == "keep me", argv
        if argv[0] in ("ela", "stego", "report"):        # these always write: a new file stands where the link was
            assert link.exists() and not link.is_symlink(), argv


# --- pictures too small, fingerprints that are something else -----------------------------------

def test_small_pictures_are_refused_in_words(tmp_path, capsys):
    path = tmp_path / "tiny.png"
    _scene((20, 12)).save(path)
    with pytest.raises(ValueError, match="smaller than one"):
        noise.analyze(path)
    if not prnu.available():
        return
    fp = tmp_path / "fp.npz"
    code, out = _run(capsys, ["prnu", "build", "-o", fp, path, path])
    assert code == 1 and "too small for PRNU" in out and not fp.exists()


def test_flat_pictures_give_a_number(tmp_path, capsys):
    if not prnu.available():
        pytest.skip("needs PyWavelets")
    flat = tmp_path / "flat.png"
    Image.new("RGB", (128, 128), (0, 0, 0)).save(flat)
    fp = tmp_path / "fp.npz"
    _run(capsys, ["prnu", "build", "-o", fp, flat, flat])
    code, out = _run(capsys, ["prnu", "match", fp, flat])
    assert "PCE 0.0" in out and "nan" not in out and "inf" not in out


def test_fingerprint_file_that_is_something_else(tmp_path, capsys):
    photo = tmp_path / "p.png"
    _scene((200, 160)).save(photo)
    files = {"empty": {}, "tiny": {"k": np.zeros((1, 1)), "images": 1, "crop": 1},
             "nan": {"k": np.full((64, 64), np.nan), "images": 3, "crop": 64},
             "shape": {"k": np.zeros((5, 7, 3)), "images": 3, "crop": 512}, "text": None}
    for name, arrays in files.items():
        path = tmp_path / f"{name}.npz"
        if arrays is None:
            path.write_text("not an archive")
        else:
            np.savez(path, **arrays)
        with pytest.raises(Exception):
            prnu.Fingerprint.load(path)
        if prnu.available():
            for sub in ("match", "locate"):
                code, out = _run(capsys, ["prnu", sub, path, photo])
                assert code == 1 and "is not a fingerprint file" in out
