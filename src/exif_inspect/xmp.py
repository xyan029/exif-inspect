"""XMP metadata: the editing history and settings that editors write into a file.

Adobe's tools, and many others, keep an XML packet (XMP) in the file next
to EXIF. Lightroom and Camera Raw store every development setting in it,
including the crop, the local adjustments and the places where the
healing brush was used; Photoshop and Lightroom append an event to
`xmpMM:History` on every save, with the software, the time and what
changed; `xmpMM:DerivedFrom` points at the document a file was made from;
`xmp:CreatorTool` names the last program that wrote the packet. None of
it is protected, and many apps and platforms strip it, so what it says is
a lead, and what it does not say means nothing.

The packet is read from JPEG APP1 segments (with the extended-XMP
continuation Adobe uses for large packets), PNG iTXt chunks, WebP XMP
chunks, and Pillow's info for other formats. It is parsed with the
standard library, after a size cap, into a flat dictionary keyed by the
usual namespace prefixes.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

MAX_PACKET = 8 << 20     # bytes of XMP we are willing to parse

_XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
_EXT_HEADER = b"http://ns.adobe.com/xmp/extension/\x00"

# Namespace URI -> the prefix everyone uses for it.
PREFIXES = {
    "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights",
    "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#": "stEvt",
    "http://ns.adobe.com/xap/1.0/sType/ResourceRef#": "stRef",
    "http://ns.adobe.com/xap/1.0/sType/Dimensions#": "stDim",
    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://ns.adobe.com/exif/1.0/": "exif",
    "http://ns.adobe.com/exif/1.0/aux/": "aux",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://ns.adobe.com/lightroom/1.0/": "lr",
    "http://ns.adobe.com/hdr-gain-map/1.0/": "hdrgm",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://iptc.org/std/Iptc4xmpExt/2008-02-29/": "Iptc4xmpExt",
    "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/": "Iptc4xmpCore",
    "http://ns.useplus.org/ldf/xmp/1.0/": "plus",
    "http://www.gimp.org/xmp/": "GIMP",
    "http://ns.google.com/photos/1.0/container/": "Container",
    "http://ns.google.com/photos/1.0/camera/": "GCamera",
    "http://ns.microsoft.com/photo/1.0/": "MicrosoftPhoto",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "adobe:ns:meta/": "x",
}
_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_XML = "{http://www.w3.org/XML/1998/namespace}"

# Words in a software name or credit that point at generative AI.
AI_WORDS = ("firefly", "generative", "stable diffusion", "midjourney", "dall-e", "dall·e",
            "google ai", "galaxy ai", "magic eraser", "magic editor", "ai eraser", "imagen",
            "gemini", "trainedalgorithmicmedia")

# Camera Raw sliders worth showing when they are not at their default.
CRS_SLIDERS = ("Exposure2012", "Contrast2012", "Highlights2012", "Shadows2012", "Whites2012",
               "Blacks2012", "Texture", "Clarity2012", "Dehaze", "Vibrance", "Saturation",
               "Temperature", "Tint", "Sharpness", "LuminanceSmoothing", "ColorNoiseReduction",
               "Exposure", "Contrast", "Clarity", "Brightness")


@dataclass
class HistoryEvent:
    action: str | None
    agent: str | None
    when: str | None
    changed: str | None
    instance_id: str | None = None
    parameters: str | None = None


@dataclass
class XmpInfo:
    present: bool = False
    container: str | None = None
    size: int = 0
    creator_tool: str | None = None
    create_date: str | None = None
    modify_date: str | None = None
    metadata_date: str | None = None
    document_id: str | None = None
    original_document_id: str | None = None
    instance_id: str | None = None
    derived_from: str | None = None
    history: list[HistoryEvent] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)     # distinct software names, in order
    raw_file: str | None = None
    has_crop: bool | None = None
    crop: tuple[float, float, float, float] | None = None   # left, top, right, bottom, 0-1
    retouch_areas: int = 0
    local_adjustments: int = 0
    crs_settings: dict[str, str] = field(default_factory=dict)
    digital_source_type: str | None = None
    credit: str | None = None
    rights: str | None = None
    ai_marker: str | None = None
    namespaces: list[str] = field(default_factory=list)
    raw: str = field(default="", repr=False, compare=False)


# --- finding the packet --------------------------------------------------

def _jpeg_segments(data: bytes):
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xDA:
            break
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length, = struct.unpack(">H", data[i + 2:i + 4])
        yield marker, data[i + 4:i + 2 + length]
        i += 2 + length


def _from_jpeg(data: bytes) -> tuple[bytes | None, str | None]:
    main = None
    parts: dict[str, list[tuple[int, bytes]]] = {}
    for marker, seg in _jpeg_segments(data):
        if marker != 0xE1:
            continue
        if seg.startswith(_XMP_HEADER):
            main = seg[len(_XMP_HEADER):]
        elif seg.startswith(_EXT_HEADER) and len(seg) >= len(_EXT_HEADER) + 40:
            body = seg[len(_EXT_HEADER):]
            guid = body[:32].decode("ascii", "replace")
            offset, = struct.unpack(">I", body[36:40])
            parts.setdefault(guid, []).append((offset, body[40:]))
    if main is None:
        return None, None
    if parts:
        # The main packet names the GUID of its continuation; stitch it on.
        match = re.search(rb'xmpNote:HasExtendedXMP="([0-9A-Fa-f]{32})"', main)
        guid = match.group(1).decode() if match else next(iter(parts))
        if guid in parts:
            extra = b"".join(chunk for _, chunk in sorted(parts[guid]))
            return main + extra, "JPEG APP1 segments (extended)"
    return main, "JPEG APP1 segment"


def _from_png(data: bytes) -> bytes | None:
    i = 8
    while i + 8 <= len(data):
        length, = struct.unpack(">I", data[i:i + 4])
        ctype = data[i + 4:i + 8]
        if ctype == b"iTXt":
            chunk = data[i + 8:i + 8 + length]
            if chunk.startswith(b"XML:com.adobe.xmp\x00"):
                rest = chunk[len(b"XML:com.adobe.xmp\x00"):]
                # compression flag, method, language tag, translated keyword
                if len(rest) >= 2 and rest[0] == 0:
                    rest = rest[2:]
                    rest = rest.split(b"\x00", 2)
                    if len(rest) == 3:
                        return rest[2]
        if ctype == b"IEND":
            break
        i += 12 + length
    return None


def _from_webp(data: bytes) -> bytes | None:
    i = 12
    while i + 8 <= len(data):
        fourcc = data[i:i + 4]
        size, = struct.unpack("<I", data[i + 4:i + 8])
        if fourcc == b"XMP ":
            return data[i + 8:i + 8 + size]
        i += 8 + size + (size & 1)
    return None


def find_packet(data: bytes, fmt: str | None, info: dict | None = None) -> tuple[bytes | None, str | None]:
    """The raw XMP bytes and where they were found, or (None, None)."""
    if fmt in ("JPEG", "MPO"):
        packet, where = _from_jpeg(data)
        if packet is not None:
            return packet, where
    elif fmt == "PNG":
        packet = _from_png(data)
        if packet is not None:
            return packet, "PNG iTXt chunk"
    elif fmt == "WEBP":
        packet = _from_webp(data)
        if packet is not None:
            return packet, "WebP XMP chunk"
    if info and info.get("xmp"):
        packet = info["xmp"]
        return (packet if isinstance(packet, bytes) else str(packet).encode()), f"{fmt} metadata"
    start = data.find(b"<x:xmpmeta")
    if start != -1:
        end = data.find(b"</x:xmpmeta>", start)
        if end != -1:
            return data[start:end + len(b"</x:xmpmeta>")], "embedded packet"
    return None, None


# --- parsing ----------------------------------------------------------------

def _prefix(tag: str) -> str:
    if tag.startswith("{"):
        uri, local = tag[1:].split("}", 1)
        pre = PREFIXES.get(uri)
        if pre is None:
            pre = uri.rstrip("/#").rsplit("/", 1)[-1] or uri
        return f"{pre}:{local}"
    return tag


def _value(elem: ET.Element) -> Any:
    """A property element's value: text, a list, or a dict for a struct."""
    children = list(elem)
    if not children:
        if any(not k.startswith(_RDF) and not k.startswith(_XML) for k in elem.attrib):
            return _struct(elem)          # a struct written as attributes
        if elem.get(f"{_RDF}resource"):
            return elem.get(f"{_RDF}resource")
        return (elem.text or "").strip()
    first = children[0]
    if first.tag in (f"{_RDF}Seq", f"{_RDF}Bag", f"{_RDF}Alt"):
        return [_value(li) for li in first if li.tag == f"{_RDF}li"]
    if first.tag == f"{_RDF}Description":
        return _struct(first)
    return _struct(elem)


def _struct(elem: ET.Element) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in elem.attrib.items():
        if not key.startswith(_RDF) and not key.startswith(_XML):
            out[_prefix(key)] = val
    for child in elem:
        if child.tag == f"{_RDF}Description":
            out.update(_struct(child))
        elif child.tag.startswith(_RDF):
            continue
        else:
            out[_prefix(child.tag)] = _value(child)
    return out


def flatten(packet: bytes) -> dict[str, Any]:
    """All top-level properties of a packet in one dictionary keyed 'prefix:Name'."""
    text = packet[:MAX_PACKET]
    # Padding and xpacket processing instructions are fine for the parser;
    # stray NULs and a UTF-8 byte-order mark are not.
    text = text.replace(b"\x00", b"").lstrip(b"\xef\xbb\xbf")
    return _top_level(ET.fromstring(text))


def _top_level(root: ET.Element) -> dict[str, Any]:
    out: dict[str, Any] = {}
    rdf = root if root.tag == f"{_RDF}RDF" else root.find(f".//{_RDF}RDF")
    if rdf is None:
        return out
    for desc in rdf:
        if desc.tag == f"{_RDF}Description":
            out.update(_struct(desc))
    return out


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
        if isinstance(value, dict):
            value = next(iter(value.values()), None)
    if isinstance(value, dict):
        return None
    text = str(value).strip()
    return text or None


def _bool(value: Any) -> bool | None:
    text = _text(value)
    if text is None:
        return None
    return text.lower() in ("true", "1", "yes")


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else (1 if value else 0)


def parse(packet: bytes, container: str | None = None) -> XmpInfo:
    """Pick the forensic essentials out of a packet."""
    info = XmpInfo(present=True, container=container, size=len(packet))
    info.raw = packet.decode("utf-8", "replace")
    try:
        props = flatten(packet)
    except ET.ParseError:
        return info
    info.namespaces = sorted({k.split(":", 1)[0] for k in props})
    info.creator_tool = _text(props.get("xmp:CreatorTool"))
    info.create_date = _text(props.get("xmp:CreateDate"))
    info.modify_date = _text(props.get("xmp:ModifyDate"))
    info.metadata_date = _text(props.get("xmp:MetadataDate"))
    info.document_id = _text(props.get("xmpMM:DocumentID"))
    info.original_document_id = _text(props.get("xmpMM:OriginalDocumentID"))
    info.instance_id = _text(props.get("xmpMM:InstanceID"))
    derived = props.get("xmpMM:DerivedFrom")
    if isinstance(derived, dict):
        info.derived_from = (_text(derived.get("stRef:documentID")) or
                             _text(derived.get("stRef:instanceID")) or
                             _text(derived.get("stRef:filePath")))
    elif derived:
        info.derived_from = _text(derived)

    for item in props.get("xmpMM:History") or []:
        if not isinstance(item, dict):
            continue
        event = HistoryEvent(
            action=_text(item.get("stEvt:action")),
            agent=_text(item.get("stEvt:softwareAgent")),
            when=_text(item.get("stEvt:when")),
            changed=_text(item.get("stEvt:changed")),
            instance_id=_text(item.get("stEvt:instanceID")),
            parameters=_text(item.get("stEvt:parameters")),
        )
        info.history.append(event)
        if event.agent and event.agent not in info.agents:
            info.agents.append(event.agent)
    if info.creator_tool and info.creator_tool not in info.agents:
        info.agents.insert(0, info.creator_tool)

    info.raw_file = _text(props.get("crs:RawFileName"))
    info.has_crop = _bool(props.get("crs:HasCrop"))
    try:
        edges = [float(_text(props[f"crs:Crop{side}"])) for side in ("Left", "Top", "Right", "Bottom")]
        if info.has_crop:
            info.crop = tuple(round(v, 4) for v in edges)
    except (KeyError, TypeError, ValueError):
        pass
    info.retouch_areas = _count(props.get("crs:RetouchAreas")) or _count(props.get("crs:RetouchInfo"))
    info.local_adjustments = sum(_count(props.get(key)) for key in (
        "crs:MaskGroupBasedCorrections", "crs:GradientBasedCorrections",
        "crs:PaintBasedCorrections", "crs:CircularGradientBasedCorrections"))
    for name in CRS_SLIDERS:
        value = _text(props.get(f"crs:{name}"))
        if value is not None and value.lstrip("+-") not in ("0", "0.0", "0.00"):
            info.crs_settings[name] = value
    for name in ("Version", "ProcessVersion", "WhiteBalance", "ToneCurveName2012", "CameraProfile"):
        value = _text(props.get(f"crs:{name}"))
        if value is not None:
            info.crs_settings[name] = value

    info.digital_source_type = _text(props.get("Iptc4xmpExt:DigitalSourceType"))
    if info.digital_source_type and "/" in info.digital_source_type:
        info.digital_source_type = info.digital_source_type.rstrip("/").rsplit("/", 1)[-1]
    info.credit = _text(props.get("photoshop:Credit"))
    info.rights = _text(props.get("dc:rights"))

    haystack = " ".join(filter(None, info.agents + [info.credit or "", info.digital_source_type or ""])).lower()
    for word in AI_WORDS:
        if word in haystack:
            info.ai_marker = word
            break
    return info


def read(data: bytes, fmt: str | None, info: dict | None = None) -> XmpInfo:
    """Find and parse the XMP packet of a file; an empty XmpInfo when there is none."""
    packet, where = find_packet(data, fmt, info)
    if packet is None:
        return XmpInfo()
    return parse(packet, where)
