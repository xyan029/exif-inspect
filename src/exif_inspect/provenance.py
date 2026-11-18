"""Detect content-provenance signals stored in an image file.

Looks for C2PA Content Credentials in the places the C2PA spec puts them
(PNG caBX chunk, JPEG APP11 JUMBF segments, WebP C2PA chunk) and for the
IPTC "digital source type" values that label AI-generated media.

This only reports that the signals are present. It does not validate the
C2PA signature; use a C2PA verifier for signer identity and edit history.
Invisible pixel watermarks (for example SynthID) are out of scope.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import jpegdct

# IPTC digital source type codes, most specific first.
_SOURCE_TYPES = (
    (b"compositewithtrainedalgorithmicmedia", "compositeWithTrainedAlgorithmicMedia"),
    (b"trainedalgorithmicmedia", "trainedAlgorithmicMedia"),
)


@dataclass
class Provenance:
    c2pa: bool = False
    container: str | None = None        # where the manifest was found
    digital_source_type: str | None = None
    generator: str | None = None        # best-effort claim generator name

    @property
    def found(self) -> bool:
        return self.c2pa or self.digital_source_type is not None


def _png_has_cabx(data: bytes) -> bool:
    i = 8  # skip the PNG signature
    while i + 8 <= len(data):
        length, = struct.unpack(">I", data[i:i + 4])
        ctype = data[i + 4:i + 8]
        if ctype == b"caBX":
            return True
        if ctype == b"IEND":
            break
        i += 12 + length
    return False


def _jpeg_has_app11_jumbf(data: bytes) -> bool:
    return any(marker == 0xEB and (b"jumb" in segment or b"c2pa" in segment)
               for _, marker, segment in jpegdct.walk(data).segments)


def _webp_has_c2pa(data: bytes) -> bool:
    i = 12  # skip "RIFF" size "WEBP"
    while i + 8 <= len(data):
        fourcc = data[i:i + 4]
        size, = struct.unpack("<I", data[i + 4:i + 8])
        if fourcc == b"C2PA":
            return True
        i += 8 + size + (size & 1)
    return False


def _cbor_text_at(data: bytes, pos: int) -> str | None:
    """Decode a CBOR text string starting at pos, if there is one."""
    if pos >= len(data):
        return None
    head = data[pos]
    if 0x60 <= head <= 0x77:
        n, start = head - 0x60, pos + 1
    elif head == 0x78 and pos + 1 < len(data):
        n, start = data[pos + 1], pos + 2
    elif head == 0x79 and pos + 2 < len(data):
        n, start = struct.unpack(">H", data[pos + 1:pos + 3])[0], pos + 3
    else:
        return None
    try:
        text = data[start:start + n].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if text.isprintable() else None


def _claim_generator(data: bytes) -> str | None:
    # C2PA 1.x: "claim_generator": "<text>"
    key = b"\x6fclaim_generator"
    pos = data.find(key)
    while pos != -1:
        value = _cbor_text_at(data, pos + len(key))
        if value:
            return value
        pos = data.find(key, pos + 1)
    # C2PA 2.x: "claim_generator_info": [{"name": "<text>", ...}]
    key = b"\x74claim_generator_info"
    pos = data.find(key)
    if pos != -1:
        window_end = pos + len(key) + 64
        name = data.find(b"\x64name", pos, window_end)
        if name != -1:
            return _cbor_text_at(data, name + 5)
    return None


def scan(data: bytes, fmt: str | None) -> Provenance:
    prov = Provenance()

    if fmt == "PNG" and _png_has_cabx(data):
        prov.c2pa, prov.container = True, "PNG caBX chunk"
    elif fmt in ("JPEG", "MPO") and _jpeg_has_app11_jumbf(data):
        prov.c2pa, prov.container = True, "JPEG APP11 segment"
    elif fmt == "WEBP" and _webp_has_c2pa(data):
        prov.c2pa, prov.container = True, "WebP C2PA chunk"
    elif b"jumb" in data and b"c2pa" in data:
        prov.c2pa, prov.container = True, "embedded JUMBF box"

    lowered = data.lower()
    for needle, name in _SOURCE_TYPES:
        if needle in lowered:
            prov.digital_source_type = name
            break

    if prov.c2pa:
        prov.generator = _claim_generator(data)
    return prov
