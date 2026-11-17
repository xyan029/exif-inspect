"""Read EXIF metadata from an image file into plain Python data."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

try:  # optional: lets Pillow open iPhone HEIC/HEIF photos
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False

from .gainmap import GainMapInfo, from_heif, from_jpeg
from .provenance import Provenance, scan
from .quant import QuantInfo, analyze
from .thumbnail import ThumbInfo
from .thumbnail import analyze as analyze_thumbnail
from .xmp import XmpInfo
from .xmp import read as read_xmp
from .hidden import Trailer
from .hidden import trailer as find_trailer

# Sub-IFDs that Pillow does not expand by default.
_SUB_IFDS = {
    "Exif": ExifTags.IFD.Exif,
    "GPS": ExifTags.IFD.GPSInfo,
    "Interop": ExifTags.IFD.Interop,
}


@dataclass
class ImageReport:
    """Everything we extract from one file."""

    path: str
    sha256: str
    format: str | None
    size: tuple[int, int] | None
    mode: str | None
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    gps_decimal: tuple[float, float] | None = None
    provenance: Provenance = field(default_factory=Provenance)
    jpeg_quant: QuantInfo | None = None
    thumbnail: ThumbInfo | None = None
    gain_map: GainMapInfo | None = None
    xmp: XmpInfo = field(default_factory=XmpInfo)
    trailer: Trailer | None = None
    error: str | None = None

    @property
    def has_exif(self) -> bool:
        return any(self.groups.values())


def sha256_of(path: Path, chunk_size: int = 1 << 16) -> str:
    """Hash the raw file bytes (useful for chain-of-custody notes)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean(value: Any, max_bytes: int = 64) -> Any:
    """Turn EXIF values into something printable and JSON-safe."""
    if isinstance(value, bytes):
        text = value.rstrip(b"\x00")
        try:
            decoded = text.decode("utf-8")
            if decoded.isprintable():
                return decoded
        except UnicodeDecodeError:
            pass
        if len(value) > max_bytes:
            return f"<{len(value)} bytes>"
        return value.hex()
    if isinstance(value, str):
        return value.rstrip("\x00").strip()
    if isinstance(value, tuple):
        return [_clean(v) for v in value]
    # IFDRational and similar numeric types
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        if value.denominator == 0:
            return None
        return float(value)
    return value


def _tag_name(tag_id: int, gps: bool = False) -> str:
    table = ExifTags.GPSTAGS if gps else ExifTags.TAGS
    return table.get(tag_id, f"Unknown_0x{tag_id:04X}")


def _dms_to_decimal(dms: Any, ref: Any) -> float | None:
    """Convert (deg, min, sec) plus N/S/E/W reference into signed degrees."""
    try:
        deg, minutes, seconds = (float(x) for x in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    decimal = deg + minutes / 60 + seconds / 3600
    if str(ref).upper() in {"S", "W"}:
        decimal = -decimal
    return round(decimal, 6)


def read_image(path: str | Path) -> ImageReport:
    """Extract metadata from a single image. Never raises on bad input."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return ImageReport(str(path), "", None, None, None, error=str(exc))
    digest = hashlib.sha256(data).hexdigest()

    try:
        with Image.open(path) as img:
            report = ImageReport(
                path=str(path),
                sha256=digest,
                format=img.format,
                size=img.size,
                mode=img.mode,
            )
            exif = img.getexif()
            jpeg_quant = analyze(img)
            img_info = dict(img.info)
            try:
                thumbnail = analyze_thumbnail(img)
            except Exception:  # a damaged thumbnail should not sink the report
                thumbnail = None
    except Exception as exc:  # Pillow raises many different types
        msg = str(exc)
        if not HEIF_SUPPORT and path.suffix.lower() in (".heic", ".heif"):
            msg += " (install HEIC support: pip install 'exif-inspect[heic]')"
        return ImageReport(str(path), digest, None, None, None, error=msg)

    # Top-level IFD0 tags (camera make, software, timestamps...)
    report.groups["Image"] = {
        _tag_name(tag): _clean(val)
        for tag, val in exif.items()
        if tag not in _SUB_IFDS.values()
    }

    raw_gps: dict[int, Any] = {}
    for group, ifd_id in _SUB_IFDS.items():
        try:
            ifd = exif.get_ifd(ifd_id)
        except Exception:
            ifd = {}
        if group == "GPS":
            raw_gps = dict(ifd)
        report.groups[group] = {
            _tag_name(tag, gps=(group == "GPS")): _clean(val)
            for tag, val in ifd.items()
        }

    report.provenance = scan(data, report.format)
    try:
        report.xmp = read_xmp(data, report.format, img_info)
    except Exception:  # a damaged packet should not sink the report
        report.xmp = XmpInfo()
    try:
        report.trailer = find_trailer(data, report.format)
    except Exception:
        report.trailer = None
    report.jpeg_quant = jpeg_quant
    report.thumbnail = thumbnail
    try:
        if report.format in ("JPEG", "MPO"):
            report.gain_map = from_jpeg(data, report.size)
        elif report.format == "HEIF":
            report.gain_map = from_heif(path)
    except Exception:  # a damaged MPF segment should not sink the report
        report.gain_map = None

    # GPS tag ids: 1 LatRef, 2 Lat, 3 LonRef, 4 Lon
    if 2 in raw_gps and 4 in raw_gps:
        lat = _dms_to_decimal(raw_gps[2], raw_gps.get(1, "N"))
        lon = _dms_to_decimal(raw_gps[4], raw_gps.get(3, "E"))
        if lat is not None and lon is not None:
            report.gps_decimal = (lat, lon)

    return report
