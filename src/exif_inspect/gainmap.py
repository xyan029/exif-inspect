"""Find the HDR gain map stored next to the main image.

iPhones (and Pixel and other Ultra HDR cameras) save a small grayscale
"gain map" with each photo, which a display uses to brighten highlights.
In a JPEG it is a second image listed in the MPF (Multi-Picture Format)
APP2 segment; in a HEIC it is an auxiliary image.

What matters for forensics is how an editor treats it. Apple Photos and
Google Photos rewrite it together with the picture; other editors drop it
or leave it as it was. A gain map whose size no longer fits the picture,
or a missing one in a photo that should have one, shows that the file was
re-saved by some program after capture.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass

from PIL import Image

from . import jpegdct

_APPLE = (b"hdrgainmap", b"HDRGainMap")
_ULTRA_HDR = (b"hdrgm:", b"urn:iso:std:iso:ts:21496")


@dataclass
class GainMapInfo:
    container: str                 # "MPF" or "HEIF auxiliary image"
    kind: str                      # "Apple", "Ultra HDR / ISO 21496" or "unknown"
    size: tuple[int, int]          # gain map width, height
    main_size: tuple[int, int]     # image width, height

    @property
    def scale(self) -> tuple[float, float]:
        return (self.size[0] / self.main_size[0], self.size[1] / self.main_size[1])

    @property
    def misfit(self) -> bool:
        """The gain map no longer has the proportions it was written with."""
        sx, sy = self.scale
        if abs(sx - sy) > 0.02 * max(sx, sy):
            return True                      # aspect ratio differs
        return self.kind == "Apple" and abs(sx - 0.5) > 0.02   # Apple writes half size


def _kind(blob: bytes) -> str:
    if any(k in blob for k in _APPLE):
        return "Apple"
    if any(k in blob for k in _ULTRA_HDR):
        return "Ultra HDR / ISO 21496"
    return "unknown"


def _mpf_images(data: bytes) -> list[tuple[int, int]]:
    """(offset, size) of every image after the first, from the MPF segment."""
    for pos, marker, payload in jpegdct.walk(data).segments:
        if marker != 0xE2 or payload[:4] != b"MPF\x00":
            continue
        base = pos + 8                           # offsets count from the TIFF header
        tiff = payload[4:]
        end = "<" if tiff[:2] == b"II" else ">"
        out: list[tuple[int, int]] = []
        try:
            ifd, = struct.unpack(end + "I", tiff[4:8])
            count, = struct.unpack(end + "H", tiff[ifd:ifd + 2])
            for k in range(count):
                tag, _typ, n, value = struct.unpack(
                    end + "HHII", tiff[ifd + 2 + 12 * k:ifd + 14 + 12 * k])
                if tag != 0xB002:
                    continue
                for e in range(n // 16):
                    _attr, size, off, _d1, _d2 = struct.unpack(
                        end + "IIIHH", tiff[value + 16 * e:value + 16 * e + 16])
                    if off:                      # the first image has offset 0
                        out.append((base + off, size))
                break
        except struct.error:                     # the segment ends before the table does
            pass
        return out
    return []


def from_jpeg(data: bytes, main_size: tuple[int, int]) -> GainMapInfo | None:
    for off, size in _mpf_images(data):
        blob = data[off:off + size]
        kind = _kind(blob[:65536])
        if kind == "unknown":
            continue
        try:
            with Image.open(io.BytesIO(blob)) as gm:
                return GainMapInfo("MPF", kind, gm.size, main_size)
        except Exception:
            continue
    return None


def from_heif(path) -> GainMapInfo | None:
    try:
        import pillow_heif
    except ImportError:
        return None
    try:
        heif = pillow_heif.open_heif(path)
        for name, ids in heif.info.get("aux", {}).items():
            if "hdrgainmap" in name and ids:
                aux = heif.get_aux_image(ids[0])
                return GainMapInfo("HEIF auxiliary image", "Apple", aux.size, heif.size)
    except Exception:
        return None
    return None
