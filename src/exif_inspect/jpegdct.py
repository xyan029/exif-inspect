"""Quantised DCT coefficients of a baseline JPEG, read and written without decoding to pixels.

Pillow decodes a JPEG straight to pixels. Steganography in JPEGs (JSteg,
F5, OutGuess, Steghide) lives in the quantised coefficients, so this module
parses the file itself: quantisation tables, Huffman tables, frame and scan
headers, then the entropy-coded data, block by block. Baseline and extended
sequential Huffman JPEGs with 8-bit samples are read, in one scan or several;
progressive and arithmetic-coded files are refused. A matching encoder writes
coefficients back out with the standard Huffman tables, which the tests use
to make pictures with known payloads.

The files come from whoever wants something hidden, so the reader has two
duties. A damaged file ends in Corrupt or Unsupported with the reason, never
in an exception of another kind, a long run or a large allocation. And a
file that libjpeg decodes is read exactly as libjpeg reads it, including
its handling of fill bytes, missing tables, repeated scans, lost restart
markers and data that ends early: if the reader and the viewer disagreed
about a file, the tool would be analysing a picture nobody sees.

Coefficients are kept in zigzag order, one row of 64 per block, in the
order the scan visits them within each component.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

ZIGZAG = np.array([
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63])

# Standard Huffman tables (ITU T.81 Annex K), as (bits, values).
_STD_DC_LUMA = ([0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0], list(range(12)))
_STD_DC_CHROMA = ([0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0], list(range(12)))
_STD_AC_LUMA = (
    [0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d],
    [0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
     0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08, 0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0,
     0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
     0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
     0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
     0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
     0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7,
     0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5,
     0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
     0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
     0xf9, 0xfa])
_STD_AC_CHROMA = (
    [0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77],
    [0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
     0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91, 0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0,
     0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
     0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
     0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
     0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
     0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5,
     0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3,
     0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
     0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
     0xf9, 0xfa])


class Unsupported(ValueError):
    """The file is a JPEG this module cannot read (progressive, arithmetic, 12-bit, lossless)."""


class Corrupt(ValueError):
    """The file breaks the JPEG format where libjpeg would give up too."""


STANDALONE = frozenset((0x01, 0xD8, 0xD9)) | frozenset(range(0xD0, 0xD8))   # markers without a length
MAX_WHOLE = 2_796_202        # luma blocks read() decodes without a cap: 179 megapixels, the
                             # size from which Pillow refuses a picture as well
_UNREAD = -2 ** 31           # marks a block a scan did not reach


@dataclass
class Component:
    cid: int
    h: int
    v: int
    tq: int                      # quantisation table id
    td: int = 0                  # DC Huffman table id
    ta: int = 0                  # AC Huffman table id
    blocks_w: int = 0            # blocks per row covered by the scan (padded to the MCU)
    blocks_h: int = 0
    coeffs: np.ndarray = field(default=None, repr=False)   # (blocks_h, blocks_w, 64) zigzag
    q: np.ndarray = field(default=None, repr=False)        # the table in force at its scan


@dataclass
class JpegData:
    width: int
    height: int
    components: list[Component]
    qtables: dict[int, np.ndarray]           # id -> 64 values, zigzag order
    restart_interval: int = 0
    segments: list[tuple[int, bytes]] = field(default_factory=list)   # metadata to carry on re-encode
    notes: list[str] = field(default_factory=list)    # what the reader had to work around
    truncated: bool = False                  # the scan data ended before the picture did

    @property
    def luma(self) -> Component:
        return self.components[0]

    def dequantised(self, index: int = 0) -> np.ndarray:
        c = self.components[index]
        return c.coeffs * self.qtables[c.tq]


# --- markers ---------------------------------------------------------------

def next_marker(data: bytes, i: int) -> tuple[int, int | None, int, int]:
    """The next marker at or after i, found the way libjpeg finds it.

    Bytes that are not FF are skipped as stray, any number of FF fill
    bytes may precede the marker code, and FF 00 is data. Returns the
    offset of the marker's last FF, the marker code (None at the end of
    the file), and how many stray and fill bytes were passed.
    """
    n, start, fill = len(data), i, 0
    while True:
        j = data.find(b"\xff", i)
        if j < 0:
            return n, None, n - start - fill, fill
        k = j + 1
        while k < n and data[k] == 0xFF:
            k += 1
        if k >= n:
            return n, None, j - start - fill, fill
        if data[k] == 0x00:
            i = k + 1
            continue
        fill += k - 1 - j
        return k - 1, data[k], j - start - (fill - (k - 1 - j)), fill


@dataclass
class Walk:
    segments: list[tuple[int, int, bytes]]   # (offset of the marker, marker, payload), SOS included
    scan: int | None                         # where the entropy-coded data starts
    stray: int = 0                           # bytes between segments that belong to none
    fill: int = 0                            # FF fill bytes before markers
    cut: bool = False                        # the file ends inside a segment
    eoi: int | None = None                   # just past an EOI that came before any scan


def walk(data: bytes) -> Walk:
    """Marker segments from the start of a JPEG up to its first scan, tolerant as libjpeg is.

    Every byte-level check of a JPEG in this package goes through here, so
    that a fill byte or a stray byte in front of a marker cannot make one
    of them lose its place while a viewer reads on.
    """
    out = Walk([], None)
    i, n = 2, len(data)
    while True:
        pos, marker, stray, fill = next_marker(data, i)
        out.stray += stray
        out.fill += fill
        if marker is None:
            return out
        if marker == 0xD9:
            out.eoi = pos + 2
            return out
        if marker in STANDALONE:
            i = pos + 2
            continue
        if pos + 4 > n:
            out.cut = True
            return out
        length = (data[pos + 2] << 8) | data[pos + 3]
        if length < 2:
            out.cut = True
            return out
        payload = data[pos + 4:pos + 2 + length]
        out.cut = len(payload) < length - 2
        out.segments.append((pos, marker, payload))
        i = pos + 2 + length
        if marker == 0xDA:
            out.scan = min(i, n)
            return out
        if out.cut:
            return out


# --- Huffman ---------------------------------------------------------------

def _build_lookup(bits: list[int], values: list[int], dc: bool = False) -> list:
    """A 65536-entry table: the next 16 bits of the stream -> (symbol, code length)."""
    if len(bits) != 16 or len(values) != sum(bits) or len(values) > 256:
        raise Corrupt("Huffman table cut short")
    if dc and any(v > 15 for v in values):
        raise Corrupt("DC Huffman table holds a size over 15 bits")
    table = [None] * 65536
    code, k = 0, 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            if code >= 1 << length:
                raise Corrupt("Huffman table has more codes than its lengths allow")
            start = code << (16 - length)
            entry = (values[k], length)
            for j in range(start, start + (1 << (16 - length))):
                table[j] = entry
            code += 1
            k += 1
        code <<= 1
    return table


def _build_codes(bits: list[int], values: list[int]) -> dict[int, tuple[int, int]]:
    """symbol -> (code length, code)."""
    codes = {}
    code, k = 0, 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            codes[values[k]] = (length, code)
            code += 1
            k += 1
        code <<= 1
    return codes


def _extend(v: int, t: int) -> int:
    return v - (1 << t) + 1 if t and v < (1 << (t - 1)) else v


# --- reading ------------------------------------------------------------------

def _read_dqt(seg: bytes, qtables: dict[int, np.ndarray]) -> None:
    p = 0
    while p < len(seg):
        pq, tq = seg[p] >> 4, seg[p] & 15
        size = 128 if pq else 64
        if pq > 1 or tq > 3:
            raise Corrupt(f"quantisation table with precision {pq} and id {tq}")
        if p + 1 + size > len(seg):
            raise Corrupt("quantisation table cut short")
        body = seg[p + 1:p + 1 + size]
        vals = struct.unpack(">64H", body) if pq else list(body)
        qtables[tq] = np.array(vals, dtype=np.int32)
        p += 1 + size


def _read_sof(seg: bytes) -> tuple[int, int, list[Component]]:
    if len(seg) < 6:
        raise Corrupt("frame header cut short")
    if seg[0] != 8:
        raise Unsupported(f"{seg[0]}-bit samples")
    height, width = struct.unpack(">HH", seg[1:5])
    n = seg[5]
    if not 1 <= n <= 4:
        raise Unsupported(f"{n} components")
    if len(seg) < 6 + 3 * n:
        raise Corrupt("frame header cut short")
    if width == 0:
        raise Corrupt("frame header gives a width of 0")
    if height == 0:
        raise Unsupported("height left to a DNL marker")
    comps = [Component(seg[6 + 3 * k], seg[7 + 3 * k] >> 4, seg[7 + 3 * k] & 15, seg[8 + 3 * k])
             for k in range(n)]
    for c in comps:
        if not (1 <= c.h <= 4 and 1 <= c.v <= 4):
            raise Corrupt(f"sampling factors {c.h}x{c.v}")
        if c.tq > 3:
            raise Corrupt(f"quantisation table id {c.tq}")
    return width, height, comps


def _read_dht(seg: bytes, huffman: dict[tuple[int, int], tuple[list[int], list[int]]]) -> None:
    p = 0
    while p < len(seg):
        tc, th = seg[p] >> 4, seg[p] & 15
        if tc > 1 or th > 3:
            raise Corrupt(f"Huffman table with class {tc} and id {th}")
        bits = list(seg[p + 1:p + 17])
        total = sum(bits)
        if len(bits) != 16 or total > 256 or p + 17 + total > len(seg):
            raise Corrupt("Huffman table cut short")
        # Kept as written; a lookup is built only for the tables a scan uses.
        huffman[(tc, th)] = (bits, list(seg[p + 17:p + 17 + total]))
        p += 17 + total


def _read_sos(seg: bytes, components: list[Component]) -> list[Component]:
    """The components of a scan, in the order the scan codes them."""
    if not seg:
        raise Corrupt("scan header cut short")
    ns = seg[0]
    if not 1 <= ns <= len(components) or len(seg) != 2 * ns + 4:
        raise Corrupt("scan header does not fit the frame")
    chosen: list[Component] = []
    for k in range(ns):
        cid, tables = seg[1 + 2 * k], seg[2 + 2 * k]
        # Ids may repeat in the frame; a scan then takes them in frame order, as libjpeg does.
        comp = next((c for c in components if c.cid == cid and all(c is not d for d in chosen)), None)
        if comp is None:
            raise Corrupt(f"scan names component {cid}, which the frame does not have")
        comp.td, comp.ta = tables >> 4, tables & 15
        if comp.td > 3 or comp.ta > 3:
            raise Corrupt(f"scan asks for Huffman tables {comp.td} and {comp.ta}")
        chosen.append(comp)
    return chosen


_STANDARD = {(0, 0): _STD_DC_LUMA, (1, 0): _STD_AC_LUMA, (0, 1): _STD_DC_CHROMA, (1, 1): _STD_AC_CHROMA}


def read(data: bytes, max_blocks: int | None = None) -> JpegData:
    """Parse a baseline JPEG into its quantised coefficients.

    max_blocks caps the number of luma blocks decoded (the scan is decoded in
    order, so this takes the top of the picture, in whole rows of MCUs); None
    decodes everything.

    The file is read the way libjpeg reads it, so that a picture a viewer
    shows is the picture analysed: fill bytes and stray bytes in front of a
    marker are passed over, a file without Huffman tables gets the standard
    ones, components may share an id, and a baseline file may spread its
    components over several scans. Anything unusual of that kind is listed
    in the result's notes. Raises Unsupported for a kind of JPEG that is not
    read and Corrupt for one that libjpeg would refuse as well.
    """
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    n = len(data)
    qtables: dict[int, np.ndarray] = {}
    huffman: dict[tuple[int, int], tuple[list[int], list[int]]] = {}
    lookups: dict[tuple[int, int, int], list] = {}
    components: list[Component] = []
    width = height = 0
    restart = 0
    segments: list[tuple[int, bytes]] = []
    notes: list[str] = []
    stray = fill = scans = unknown = 0
    truncated = False
    rows_cap = 0
    whole = False                # the first scan held every component
    i = 2
    while True:
        pos, marker, s, f = next_marker(data, i)
        stray += s
        fill += f
        if marker is None or marker == 0xD9:
            break
        if marker in STANDALONE:
            i = pos + 2
            continue
        if pos + 4 > n:
            raise Corrupt("the file ends inside a segment")
        length = (data[pos + 2] << 8) | data[pos + 3]
        if length < 2:
            if 0xE0 <= marker <= 0xEF or marker == 0xFE:
                i = pos + 4                  # libjpeg takes such a segment as empty and reads on
                continue
            raise Corrupt("segment length under 2")
        if pos + 2 + length > n:
            raise Corrupt("the file ends inside a segment")
        seg = data[pos + 4:pos + 2 + length]
        i = pos + 2 + length
        if marker == 0xDB:
            _read_dqt(seg, qtables)
        elif marker in (0xC0, 0xC1):
            if components:
                raise Corrupt("two frame headers")
            width, height, components = _read_sof(seg)
            hmax = max(c.h for c in components)
            vmax = max(c.v for c in components)
            mcus_x = -(-width // (8 * hmax))
            mcus_y = -(-height // (8 * vmax))
            rows_cap = mcus_y
            if max_blocks is not None:
                per_row = mcus_x * components[0].h * components[0].v
                rows_cap = min(mcus_y, max(1, -(-max_blocks // per_row)))
            elif mcus_x * mcus_y * components[0].h * components[0].v > MAX_WHOLE:
                raise Unsupported(f"a picture of {width}x{height} is too large to read whole")
        elif marker in (0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            kinds = {0xC2: "progressive", 0xC3: "lossless", 0xC9: "arithmetic-coded",
                     0xCA: "progressive arithmetic-coded"}
            raise Unsupported(kinds.get(marker, "non-baseline") + " JPEG")
        elif marker == 0xC4:
            _read_dht(seg, huffman)
        elif marker == 0xDD:
            if len(seg) < 2:
                raise Corrupt("restart interval cut short")
            restart, = struct.unpack(">H", seg[:2])
        elif marker == 0xDA:
            if not components:
                raise Corrupt("a scan before the frame header")
            chosen = _read_sos(seg, components)
            if len(chosen) > 1 and sum(c.h * c.v for c in chosen) > 10:
                raise Corrupt("more than 10 blocks in an MCU")
            tables = []
            for c in chosen:
                if c.tq not in qtables:
                    raise Corrupt(f"no quantisation table {c.tq}")
                if c.q is None:
                    c.q = qtables[c.tq]
                pair = []
                for tc, th in ((0, c.td), (1, c.ta)):
                    if (tc, th) not in huffman:
                        if (tc, th) not in _STANDARD:
                            raise Corrupt(f"no Huffman table {th} of class {tc}")
                        if not any("Huffman table" in note for note in notes):
                            notes.append("a Huffman table the scan needs is not in the file; the "
                                         "standard one was used, as libjpeg does")
                    bits, values = huffman.get((tc, th)) or _STANDARD[(tc, th)]
                    key = (tc, th, id(bits))
                    if key not in lookups:
                        lookups[key] = _build_lookup(bits, values, dc=not tc)
                    pair.append(lookups[key])
                tables.append(tuple(pair))
            again = [c for c in chosen if c.coeffs is not None]
            targets = []
            for c in chosen:
                if c.coeffs is None:
                    c.blocks_w, c.blocks_h = mcus_x * c.h, rows_cap * c.v
                    c.coeffs = np.zeros((c.blocks_h, c.blocks_w, 64), dtype=np.int32)
                    targets.append(c.coeffs)
                else:
                    # A second scan of a component goes to a sheet of its own, with a mark in
                    # the DC place of every block, and is laid over the first afterwards.
                    sheet = np.zeros_like(c.coeffs)
                    sheet[:, :, 0] = _UNREAD
                    targets.append(sheet)
            if len(chosen) > 1:
                rows, cols = rows_cap, mcus_x
                plan = [(c.v, c.h, a) for c, a in zip(chosen, targets)]
            else:
                # A scan of one component codes just the blocks that hold picture, one per MCU.
                c = chosen[0]
                cols = -(-(-(-width * c.h // hmax)) // 8)
                rows = min(-(-(-(-height * c.v // vmax)) // 8), rows_cap * c.v)
                plan = [(1, 1, targets[0])]
            i, short, bad = _decode_scan(data, i, plan, tables, rows, cols, restart)
            truncated = truncated or short
            unknown += bad
            for c, sheet in zip(chosen, targets):
                if sheet is c.coeffs:
                    continue
                # libjpeg does not clear a block before a later scan writes to it: the DC value
                # is replaced, an AC value only where the later scan codes one.
                read_now = sheet[:, :, 0] != _UNREAD
                keep = sheet != 0
                keep[:, :, 0] = True
                keep &= read_now[:, :, None]
                c.coeffs[keep] = sheet[keep]
            if again and not any("more than one scan" in note for note in notes):
                notes.append(f"component {again[0].cid} has more than one scan; they were laid over "
                             "each other as libjpeg lays them")
            scans += 1
            if scans == 1 and len(chosen) == len(components):
                whole = True
                break                    # one scan with everything: libjpeg stops reading here too
            i = end_of_scan(data, i)
        else:
            segments.append((marker, seg))
    if not scans:
        raise ValueError("no scan found")
    missing = [c.cid for c in components if c.coeffs is None]
    if missing:
        raise Corrupt(f"no scan for component {missing[0]}")
    for c in components:
        if c.tq in qtables and not np.array_equal(qtables[c.tq], c.q):
            raise Unsupported("quantisation table redefined between scans")
    if stray:
        notes.append(f"{stray} stray byte{'s' if stray != 1 else ''} between segments")
    if fill:
        notes.append(f"{fill} fill byte{'s' if fill != 1 else ''} in front of a marker")
    if not whole:
        notes.append(f"the components come in {scans} scans")
    if len({c.cid for c in components}) < len(components):
        notes.append("components share an id")
    if truncated:
        notes.append("the scan data runs out before the picture is complete")
    if unknown:
        notes.append(f"{unknown} Huffman code{'s' if unknown != 1 else ''} that no table holds, "
                     "read as zero the way libjpeg reads them")
    return JpegData(width, height, components, qtables, restart, segments, notes, truncated)


def end_of_scan(data: bytes, pos: int) -> int:
    """Offset of the marker that ends the entropy-coded data starting at pos."""
    n = len(data)
    while True:
        j = data.find(b"\xff", pos)
        if j < 0 or j + 1 >= n:
            return n
        b = data[j + 1]
        if b == 0x00 or 0xD0 <= b <= 0xD7:
            pos = j + 2
        elif b == 0xFF:
            pos = j + 1
        else:
            return j


def _resync(data: bytes, pos: int, expected: int) -> tuple[int, bool]:
    """Where reading goes on at a restart point, and whether a restart marker was taken.

    libjpeg's rule (jpeg_resync_to_restart): the next marker is looked up; the
    expected restart marker, or one far from it, is taken; codes under C0 and
    the one or two restart markers before the expected one are passed over; one
    of the next two, or a marker of another kind, is left where it is, and the
    interval is read as empty.
    """
    while True:
        at, marker, _, _ = next_marker(data, pos)
        if marker is None:
            return len(data), False
        if marker < 0xC0:
            pos = at + 2
            continue
        if not 0xD0 <= marker <= 0xD7:
            return at, False
        ahead = (marker - 0xD0 - expected) & 7
        if ahead in (1, 2):
            return at, False
        if ahead in (6, 7):
            pos = at + 2
            continue
        return at + 2, True


def _decode_scan(data, start, plan, tables, rows, cols, restart):
    """Decode rows x cols MCUs into the arrays of `plan`; returns where it stopped reading,
    whether the data ran out first, and how many codes no table holds.

    `plan` lists, per component of the scan and in the scan's order, the
    blocks an MCU holds down and across and the array they go to.

    Damaged data is read the way libjpeg reads it, so that the result is
    the picture a viewer shows: a code that no table holds costs 17 bits
    and counts as zero, a run past the last coefficient puts its value in
    the last one, a DC value is kept to 16 bits, and once the data has run
    out the remaining blocks stay zero.
    """
    # The bit reader is inlined: this loop is the whole cost of reading a file.
    # Refills keep at least 33 bits in the accumulator: a 16-bit code plus a 15-bit value.
    # `fake` counts the zero bits appended once the data has run out; when one of them has
    # been used (nbits < fake) the decoder is starved, and libjpeg then leaves every block
    # zero until a restart marker gives it data again.
    pos, acc, nbits, fake, n = start, 0, 0, 0, len(data)
    hit_marker = False
    starved = short = False
    bad = 0
    preds = [0] * len(plan)
    plan = [(k, cv, ch, coeffs, tables[k][0], tables[k][1]) for k, (cv, ch, coeffs) in enumerate(plan)]
    count = expected = 0
    for my in range(rows):
        for mx in range(cols):
            if restart and count and count % restart == 0:
                starved = starved or nbits < fake
                pos, taken = _resync(data, pos, expected)
                expected = (expected + 1) & 7
                acc, nbits, fake = 0, 0, 0
                hit_marker = not taken
                if taken:
                    starved = False
                preds = [0] * len(plan)
            elif nbits < fake:
                starved = True
            if starved:
                short = True
                if not restart:
                    return pos, True, bad
                count += 1
                continue
            for k, cv, ch, coeffs, dc_lookup, ac_lookup in plan:
                for by in range(cv):
                    row = coeffs[my * cv + by]
                    for bx in range(ch):
                        block = [0] * 64
                        # --- DC ---
                        while nbits <= 32:
                            if hit_marker or pos >= n:
                                acc <<= 8
                                fake += 8
                            else:
                                b = data[pos]
                                if b == 0xFF:
                                    q = pos + 1
                                    while q < n and data[q] == 0xFF:     # fill bytes
                                        q += 1
                                    if q < n and data[q] == 0:
                                        pos = q + 1
                                    else:
                                        hit_marker = True
                                        continue
                                else:
                                    pos += 1
                                acc = (acc << 8) | b
                            nbits += 8
                        entry = dc_lookup[(acc >> (nbits - 16)) & 0xFFFF]
                        if entry is None:
                            bad += 1
                            nbits -= 17
                        else:
                            t = entry[0]
                            nbits -= entry[1]
                            if t:
                                nbits -= t
                                v = (acc >> nbits) & ((1 << t) - 1)
                                if v < (1 << (t - 1)):
                                    v -= (1 << t) - 1
                                v += preds[k]
                                if not -2147483648 <= v <= 2147483647:
                                    raise Corrupt("DC coefficient out of range")
                                preds[k] = v
                        v = preds[k]
                        block[0] = v if -32768 <= v <= 32767 else ((v + 32768) & 0xFFFF) - 32768
                        # --- AC ---
                        i = 1
                        while i < 64:
                            if nbits < 32:
                                while nbits <= 32:
                                    if hit_marker or pos >= n:
                                        acc <<= 8
                                        fake += 8
                                    else:
                                        b = data[pos]
                                        if b == 0xFF:
                                            q = pos + 1
                                            while q < n and data[q] == 0xFF:     # fill bytes
                                                q += 1
                                            if q < n and data[q] == 0:
                                                pos = q + 1
                                            else:
                                                hit_marker = True
                                                continue
                                        else:
                                            pos += 1
                                        acc = (acc << 8) | b
                                    nbits += 8
                            entry = ac_lookup[(acc >> (nbits - 16)) & 0xFFFF]
                            if entry is None:
                                bad += 1
                                nbits -= 17
                                break
                            rs = entry[0]
                            nbits -= entry[1]
                            r, t = rs >> 4, rs & 15
                            if t == 0:
                                if r == 15:
                                    i += 16
                                    continue
                                break
                            i += r
                            nbits -= t
                            v = (acc >> nbits) & ((1 << t) - 1)
                            if v < (1 << (t - 1)):
                                v -= (1 << t) - 1
                            if i > 63:
                                block[63] = v
                                break
                            block[i] = v
                            i += 1
                        acc &= (1 << nbits) - 1          # keep the accumulator small
                        row[mx * ch + bx] = block
            count += 1
    return pos, short, bad


# --- writing ---------------------------------------------------------------

class _BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.nbits = 0

    def write(self, code: int, length: int):
        self.acc = (self.acc << length) | code
        self.nbits += length
        while self.nbits >= 8:
            self.nbits -= 8
            b = (self.acc >> self.nbits) & 0xFF
            self.out.append(b)
            if b == 0xFF:
                self.out.append(0x00)
        self.acc &= (1 << self.nbits) - 1

    def flush(self):
        if self.nbits:
            self.write((1 << (8 - self.nbits)) - 1, 8 - self.nbits)


def _magnitude(v: int) -> tuple[int, int]:
    """(size, bits) for a coefficient value, as JPEG codes it."""
    a = abs(v)
    size = a.bit_length()
    bits = v if v >= 0 else (v + (1 << size) - 1)
    return size, bits


def write(jd: JpegData, quality_tables: dict[int, np.ndarray] | None = None) -> bytes:
    """Encode coefficients as a baseline JPEG with the standard Huffman tables.

    The quantisation tables and the APP/COM segments read from the source are
    written back, so the result decodes to the same picture when the
    coefficients were not changed.
    """
    qtables = quality_tables or jd.qtables
    comps = jd.components
    out = bytearray(b"\xff\xd8")
    for marker, seg in jd.segments:
        out += b"\xff" + bytes([marker]) + struct.pack(">H", len(seg) + 2) + seg
    for tq, table in sorted(qtables.items()):
        payload = bytes([tq]) + bytes(int(v) for v in table)
        out += b"\xff\xdb" + struct.pack(">H", len(payload) + 2) + payload
    sof = bytes([8]) + struct.pack(">HH", jd.height, jd.width) + bytes([len(comps)])
    for c in comps:
        sof += bytes([c.cid, (c.h << 4) | c.v, c.tq])
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
    tables = [(0x00, _STD_DC_LUMA), (0x10, _STD_AC_LUMA), (0x01, _STD_DC_CHROMA), (0x11, _STD_AC_CHROMA)]
    dht = b""
    for tcth, (bits, values) in tables:
        dht += bytes([tcth]) + bytes(bits) + bytes(values)
    out += b"\xff\xc4" + struct.pack(">H", len(dht) + 2) + dht
    sos = bytes([len(comps)])
    for k, c in enumerate(comps):
        t = 0 if k == 0 else 1
        sos += bytes([c.cid, (t << 4) | t])
    sos += bytes([0, 63, 0])
    out += b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos

    dc_codes = [_build_codes(*_STD_DC_LUMA), _build_codes(*_STD_DC_CHROMA)]
    ac_codes = [_build_codes(*_STD_AC_LUMA), _build_codes(*_STD_AC_CHROMA)]
    hmax = max(c.h for c in comps)
    vmax = max(c.v for c in comps)
    mcus_x = -(-jd.width // (8 * hmax))
    mcus_y = min(-(-jd.height // (8 * vmax)), comps[0].coeffs.shape[0] // comps[0].v)
    w = _BitWriter()
    preds = [0] * len(comps)
    for my in range(mcus_y):
        for mx in range(mcus_x):
            for k, c in enumerate(comps):
                t = 0 if k == 0 else 1
                dcc, acc = dc_codes[t], ac_codes[t]
                for by in range(c.v):
                    for bx in range(c.h):
                        block = c.coeffs[my * c.v + by, mx * c.h + bx]
                        diff = int(block[0]) - preds[k]
                        preds[k] = int(block[0])
                        size, bits = _magnitude(diff)
                        length, code = dcc[size]
                        w.write(code, length)
                        if size:
                            w.write(bits, size)
                        run = 0
                        last = 63
                        while last > 0 and block[last] == 0:
                            last -= 1
                        for i in range(1, last + 1):
                            v = int(block[i])
                            if v == 0:
                                run += 1
                                continue
                            while run > 15:
                                length, code = acc[0xF0]
                                w.write(code, length)
                                run -= 16
                            size, bits = _magnitude(v)
                            length, code = acc[(run << 4) | size]
                            w.write(code, length)
                            w.write(bits, size)
                            run = 0
                        if last < 63:
                            length, code = acc[0x00]
                            w.write(code, length)
    w.flush()
    out += w.out + b"\xff\xd9"
    return bytes(out)


def to_pixels(jd: JpegData, index: int = 0) -> np.ndarray:
    """Inverse DCT of one component, for checking the decoder against a pixel decoder."""
    c = jd.components[index]
    q = jd.qtables[c.tq]
    coeffs = (c.coeffs * q).astype(np.float64)
    natural = np.zeros_like(coeffs)
    natural[:, :, ZIGZAG] = coeffs
    blocks = natural.reshape(c.blocks_h, c.blocks_w, 8, 8)
    n = 8
    k = np.arange(n)[:, None]
    x = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * x + 1) * k / (2 * n)) * np.sqrt(2 / n)
    m[0] /= np.sqrt(2)
    pix = np.einsum("ki,abkl,lj->abij", m, blocks, m) + 128
    out = pix.transpose(0, 2, 1, 3).reshape(c.blocks_h * 8, c.blocks_w * 8)
    return np.clip(np.round(out), 0, 255)
