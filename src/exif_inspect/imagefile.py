"""Open a picture named on the command line.

Every tool opens its input through `opened`, which adds two things to
`Image.open`.

It takes regular files only. A named pipe, a device or a socket carrying a
picture's name would be read until whoever holds the other end writes to
it, which for a pipe planted in a folder of evidence is never.

It opens a PNG the way libpng-based viewers do. libpng discards an
ancillary chunk whose checksum or length is wrong and carries on, so
browsers and image viewers show such a file; Pillow refuses it, and it
refuses a PNG whose compressed text chunks expand past a size limit. One
flipped bit in a comment chunk would in that way put a PNG out of reach of
every tool here while it still opens everywhere else. When Pillow refuses
a PNG, the picture is opened from a copy that keeps the chunks a decoder
needs (IHDR, PLTE, IDAT, IEND, a well-formed tRNS and eXIf), and the faulty
chunks are named in `img.info["png_set_aside"]`.
"""

from __future__ import annotations

import contextlib
import io
import os
import stat
import struct
import zlib
from pathlib import Path

from PIL import Image

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_REPAIR = 256 << 20          # bytes of PNG read into memory for a second attempt
MAX_TEXT = 1 << 20              # Pillow's own limit on one expanded text chunk

# tRNS length that fits each colour type; palette pictures take one byte per entry.
_TRNS_LENGTH = {0: 2, 2: 6}
_FIXED_LENGTH = {b"pHYs": 9, b"cHRM": 32, b"gAMA": 4, b"sRGB": 1}
_TEXT = (b"tEXt", b"zTXt", b"iTXt")


def check_regular(path: str | os.PathLike) -> None:
    """Raise OSError unless the path names a regular file (links are followed)."""
    mode = os.stat(path).st_mode
    if stat.S_ISREG(mode):
        return
    kind = ("a directory" if stat.S_ISDIR(mode) else "a named pipe" if stat.S_ISFIFO(mode)
            else "a device" if stat.S_ISCHR(mode) or stat.S_ISBLK(mode)
            else "a socket" if stat.S_ISSOCK(mode) else "not a regular file")
    raise OSError(f"{kind} cannot be read as a picture")


def png_chunks(data: bytes):
    """(type, body, stored checksum, checksum right) per chunk, up to IEND or the first that overruns."""
    i = len(PNG_SIGNATURE)
    while i + 12 <= len(data):
        length, = struct.unpack(">I", data[i:i + 4])
        ctype = data[i + 4:i + 8]
        if i + 12 + length > len(data):
            return
        body = data[i + 8:i + 8 + length]
        stored, = struct.unpack(">I", data[i + 8 + length:i + 12 + length])
        yield ctype, body, stored, zlib.crc32(ctype + body) == stored
        if ctype == b"IEND":
            return
        i += 12 + length


def _expands_past(blob: bytes, limit: int) -> bool:
    """Whether a zlib stream holds more than `limit` bytes; a damaged stream holds none."""
    try:
        inflater = zlib.decompressobj()
        inflater.decompress(blob, limit)
        return bool(inflater.unconsumed_tail)
    except zlib.error:
        return False


def _oversized_text(ctype: bytes, body: bytes) -> bool:
    if ctype == b"zTXt":
        _, _, rest = body.partition(b"\x00")
        return _expands_past(rest[1:], MAX_TEXT)
    if ctype == b"iTXt":
        _, _, rest = body.partition(b"\x00")
        if rest[:1] == b"\x01":
            fields = rest[2:].split(b"\x00", 2)
            return len(fields) == 3 and _expands_past(fields[2], MAX_TEXT)
    return len(body) > MAX_TEXT


def png_essentials(data: bytes) -> tuple[bytes, list[str]] | None:
    """The PNG rebuilt from the chunks a decoder needs, and a line per kind of chunk found faulty.

    None when the data is not a PNG with a header chunk. Critical chunks are copied as they
    stand, checksum included, so a picture libpng would refuse is not made to look sound.
    Text chunks are left out of the copy whatever their state; nothing here reads them.
    """
    if data[:8] != PNG_SIGNATURE:
        return None
    out = bytearray(PNG_SIGNATURE)
    faulty: dict[str, int] = {}
    colour = None
    for ctype, body, stored, sound in png_chunks(data):
        if colour is None:
            if ctype != b"IHDR" or len(body) != 13:
                return None
            colour = body[9]
        name = ctype.decode("latin-1")
        keep, why = True, None
        if ctype == b"PLTE" and colour != 3 and not sound:
            # Outside palette pictures the palette is a suggestion; libpng warns and carries on.
            keep, why = False, f"{name} chunk with a wrong checksum"
        elif ctype[0] & 0x20:                   # ancillary: a decoder can do without it
            if not sound:
                keep, why = False, f"{name} chunk with a wrong checksum"
            elif ctype == b"tRNS":
                want = _TRNS_LENGTH.get(colour)
                keep = (len(body) == want) if want else (colour == 3 and len(body) <= 256)
                why = None if keep else f"{name} chunk of a length its colour type does not allow"
            elif ctype in _FIXED_LENGTH and len(body) != _FIXED_LENGTH[ctype]:
                keep, why = False, f"{name} chunk of the wrong length"
            elif ctype in _TEXT:
                keep = False
                why = f"{name} chunk that expands past {MAX_TEXT >> 20} MB" if _oversized_text(ctype, body) else None
            elif ctype != b"eXIf":
                keep = False                    # colour management, animation, private chunks
        if keep:
            out += struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", stored)
        elif why:
            faulty[why] = faulty.get(why, 0) + 1
    if colour is None:
        return None
    return bytes(out), [f"{n} x {why}" if n > 1 else why for why, n in faulty.items()]


@contextlib.contextmanager
def opened(path: str | os.PathLike, decode: bool = True):
    """`Image.open(path)` for regular files, with a second attempt for PNGs libpng would show.

    A PNG is decoded here, before the caller sees it, because Pillow meets the chunks behind
    the pixel data only then; `decode=False` is for callers that read the header and no pixels.
    """
    check_regular(path)
    img = None
    try:
        img = Image.open(path)
        if decode and img.format == "PNG":
            img.load()
    except Exception as refusal:
        if img is not None:
            img.close()
        img = _second_attempt(path, refusal, decode)
    with img:
        yield img


def _second_attempt(path, refusal: Exception, decode: bool) -> Image.Image:
    try:
        with open(path, "rb") as fh:
            if fh.read(8) != PNG_SIGNATURE or os.fstat(fh.fileno()).st_size > MAX_REPAIR:
                raise refusal
            fh.seek(0)
            data = fh.read()
        rebuilt = png_essentials(data)
        if rebuilt is None:
            raise refusal
        img = Image.open(io.BytesIO(rebuilt[0]))
        if decode:
            img.load()
    except Exception:
        raise refusal from None       # the copy is refused too: report what the file itself got
    img.info["png_set_aside"] = rebuilt[1] or ["an ancillary chunk Pillow does not accept"]
    img.filename = str(Path(path))
    return img
