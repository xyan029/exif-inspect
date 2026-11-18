"""What a JPEG shows after each of its scans, and whether the scans agree with each other.

A JPEG may bring its picture in several scans. A progressive file sends
coarse values first and refines them; a sequential one may give each
component a scan of its own. A viewer that waits for the end of the file
shows the result of all of them. A viewer that draws while the file comes
in, and anything that reads a copy cut short, shows what the scans so far
have brought.

In a file an encoder wrote the two never disagree: every band of
coefficients is sent once and then refined bit by bit, so each pass shows
the final picture at lower precision. The format allows more. A scan may
send a band a second time, and the decoder writes the new values over the
old ones; libjpeg notes an "inconsistent progression" for some of these
and nothing for others, and shows the picture either way. A file can thus
open with scans that draw one picture and go on with scans that replace it
by another: one picture for whoever sees the first scans, another for
whoever sees them all.

This module lists a file's scans, classes each one from its header (first
pass over a band, refinement, sent again, out of order), and for a band
sent again compares the values before and after: in how many blocks they
changed, by how much, and where. It renders the picture after chosen
scans with a plain inverse DCT, so the passes can be laid side by side.

It also counts scans that no libjpeg viewer reads at all: a sequential
file whose first scan holds every component is complete for libjpeg, which
stops there, whatever follows in front of the end marker.

A band sent twice with the same values, or scans out of order with nothing
overwritten, change no picture; they are reported as what they are. No
encoder known here writes either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import jpegdct
from .imagefile import check_regular

MAX_BLOCKS = 200000           # luma blocks decoded (12.8 megapixels), from the top of the picture
THUMB = 320                   # long side of the pictures that are compared and shown
MAX_CELLS = 12                # pictures on the sheet
LISTED = 30                   # scans listed one by one
OTHER_PICTURE = 0.5           # below this correlation the earlier picture is called another one


@dataclass
class ScanRow:
    index: int
    components: list[int]            # ids, as the frame header names them
    ss: int
    se: int
    ah: int
    al: int
    size: int                        # bytes, header and data
    kind: str                        # "first", "refine", "again" or "out of order"
    changed: float | None = None     # sent again: share of blocks whose values it changed,
    moved: float | None = None       # by how many sample levels (rms) in component `moved_in`,
    moved_in: int | None = None
    box: tuple[int, int, int, int] | None = None    # and around which pixels
    away: float | None = None        # rendered: rms distance of the picture from the final one
    _where: list[int] = field(default_factory=list, repr=False)      # the components' places in the frame
    _before: dict = field(default_factory=dict, repr=False)          # their bit levels before the scan

    @property
    def band(self) -> str:
        if self.se == 0:
            return "DC"
        if self.ss == 0:
            return "all 64"
        return f"AC {self.ss}" if self.ss == self.se else f"AC {self.ss}-{self.se}"


@dataclass
class PassesResult:
    verdict: str                     # "skipped", "single", "consistent", "order", "resent" or "replaced"
    note: str                        # why the file was skipped, when it was
    progressive: bool = False
    rows: list[ScanRow] = field(default_factory=list)
    extra: tuple[int, int] | None = None     # scans and bytes behind the scan libjpeg stops at,
    unread: Image.Image | None = field(default=None, repr=False)     # the picture they draw alone,
    unread_correlation: float | None = None  # and how it compares with the picture that is shown
    replaced_at: int | None = None           # the first scan that changed what earlier ones drew
    correlation: float | None = None         # the picture in front of it against the final one,
    distance: float | None = None            # and their rms distance in grey levels
    scale: int = 1                           # both measured on pictures reduced by this factor
    covered: float = 1.0
    structure: list[str] = field(default_factory=list)
    thumbs: dict[int, Image.Image] = field(default_factory=dict, repr=False)


def _skipped(note: str) -> PassesResult:
    return PassesResult("skipped", note)


def _place(frame, comp) -> int:
    return next(k for k, c in enumerate(frame) if c is comp)


def _classify(scans: list[jpegdct.Scan], progressive: bool) -> list[ScanRow]:
    """Each scan's kind, from the headers alone: libjpeg's own bookkeeping, kept per coefficient."""
    levels: dict[int, list[int]] = {}
    rows = []
    for s in scans:
        kinds, where, before = [], [], {}
        for comp in s.components:
            k = _place(s.frame, comp)
            lv = levels.setdefault(k, [-1] * 64)
            prev = lv[s.ss:s.se + 1]
            before[k] = list(prev)
            if not progressive or s.ah == 0:
                kinds.append("first" if all(p < 0 for p in prev) else "again")
            else:
                kinds.append("refine" if all(p == s.ah for p in prev) else "out of order")
            lv[s.ss:s.se + 1] = [s.al] * (s.se - s.ss + 1)
            where.append(k)
        kind = next(k for k in ("again", "out of order", "refine", "first") if k in kinds)
        rows.append(ScanRow(s.index, [c.cid for c in s.components], s.ss, s.se, s.ah, s.al,
                            s.end - s.offset, kind, _where=where, _before=before))
    return rows


def _unread(data: bytes, pos: int) -> tuple[int, int] | None:
    """Scans between pos and the end marker, and the bytes they take with their segments."""
    count, i, n = 0, pos, len(data)
    while True:
        at, marker, _, _ = jpegdct.next_marker(data, i)
        if marker is None:
            at = n
            break
        if marker == 0xD9:
            break
        if marker in jpegdct.STANDALONE:
            i = at + 2
            continue
        if at + 4 > n:
            at = n
            break
        length = (data[at + 2] << 8) | data[at + 3]
        i = at + 2 + max(length, 2)
        if marker == 0xDA:
            count += 1
            i = jpegdct.end_of_scan(data, min(i, n))
    return (count, at - pos) if count else None


def _truncated(values: np.ndarray, level: np.ndarray, dc: bool) -> np.ndarray:
    """Coefficients as a pass down to bit `level` leaves them."""
    if dc:
        return values >> level
    return np.sign(values) * (np.abs(values) >> level)


_IDCT = np.cos(np.pi * (2 * np.arange(8)[None, :] + 1) * np.arange(8)[:, None] / 16) * 0.5
_IDCT[0] /= np.sqrt(2)
_IDCT = _IDCT.astype(np.float32)


def _plane(coeffs: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Samples of one component from its coefficients: a plain inverse DCT, no smoothing."""
    rows, cols = coeffs.shape[:2]
    natural = np.empty((rows, cols, 64), dtype=np.float32)
    natural[:, :, jpegdct.ZIGZAG] = coeffs * q.astype(np.float32)
    pix = _IDCT.T @ natural.reshape(rows, cols, 8, 8) @ _IDCT + 128
    return np.clip(np.rint(pix), 0, 255).astype(np.uint8).transpose(0, 2, 1, 3).reshape(rows * 8, cols * 8)


def _colour(segments, frame) -> str:
    """How libjpeg takes the components: "L", "YCbCr", "RGB" or "first" (shown as grey)."""
    if len(frame) == 1:
        return "L"
    if len(frame) != 3:
        return "first"
    if any(m == 0xE0 and seg[:5] == b"JFIF\x00" for m, seg in segments):
        return "YCbCr"
    adobe = next((seg for m, seg in segments if m == 0xEE and seg[:5] == b"Adobe" and len(seg) >= 12), None)
    if adobe is not None:
        return "RGB" if adobe[11] == 0 else "YCbCr"
    return "RGB" if [c.cid for c in frame] == [82, 71, 66] else "YCbCr"


class _Geometry:
    """Where each component's own blocks lie, and the size the pictures are drawn at."""

    def __init__(self, width: int, height: int, frame):
        self.width, self.height = width, height
        self.hmax = max(c.h for c in frame)
        self.vmax = max(c.v for c in frame)

    def own(self, c, coeffs: np.ndarray) -> tuple[int, int]:
        """Rows and columns of the component's blocks that hold picture."""
        cols = -(-(-(-self.width * c.h // self.hmax)) // 8)
        rows = -(-(-(-self.height * c.v // self.vmax)) // 8)
        return min(rows, coeffs.shape[0]), min(cols, coeffs.shape[1])

    def shown(self, luma, coeffs: np.ndarray) -> tuple[int, int]:
        """Pixels of the picture that the decoded rows cover."""
        rows, _ = self.own(luma, coeffs)
        return self.width, min(self.height, rows * 8 * self.vmax // luma.v)


def _render(scan: jpegdct.Scan, geo: _Geometry, mode: str, size: tuple[int, int]) -> Image.Image:
    """The picture as it stands after a scan, reduced to `size`."""
    planes = []
    for c in scan.frame[:1] if mode in ("L", "first") else scan.frame:
        coeffs = scan.coeffs(c)
        if coeffs is None or c.q is None:
            planes.append(Image.new("L", size, 128))         # no scan yet: flat, as libjpeg shows it
            continue
        rows, cols = geo.own(c, coeffs)
        plane = _plane(coeffs[:rows, :cols], c.q)
        w = min(-(-geo.width * c.h // geo.hmax), cols * 8)
        h = min(-(-geo.height * c.v // geo.vmax), rows * 8)
        planes.append(Image.fromarray(plane[:h, :w]).resize(size, Image.BILINEAR, reducing_gap=2.0))
    if len(planes) == 1:
        return planes[0]
    return Image.merge(mode, planes).convert("RGB")


def _grey(picture: Image.Image) -> np.ndarray:
    return np.asarray(picture.convert("L"), dtype=np.float64)


def analyze(path: str | Path, sheet: bool = False, max_blocks: int | None = MAX_BLOCKS) -> PassesResult:
    """List the scans of a JPEG and compare what they draw.

    The headers are read first, which costs next to nothing. The coefficients
    are decoded only when a scan sends a band again or when `sheet` asks for
    the pictures.
    """
    check_regular(path)
    data = Path(path).read_bytes()
    if data[:2] != b"\xff\xd8":
        return _skipped("not a JPEG, so there are no scans to follow")
    scans: list[jpegdct.Scan] = []
    try:
        head = jpegdct.read(data, max_blocks=1, on_scan=scans.append)
    except jpegdct.Unsupported as exc:
        return _skipped(f"{exc}, which the coefficient reader does not read")
    except ValueError as exc:                    # jpegdct.Corrupt, or a file without a scan
        return _skipped(f"the JPEG structure is damaged: {exc}")
    rows = _classify(scans, head.progressive)
    structure = [n for n in head.notes if "runs out" not in n and "stop early" not in n and "no scan carries" not in n]
    extra = None
    if not head.progressive and len(scans[0].components) == len(head.components):
        extra = _unread(data, scans[0].end)
    res = PassesResult("consistent", "", head.progressive, rows, extra, structure=structure)
    again = [r for r in rows if r.kind == "again"]
    if len(rows) == 1:
        res.verdict = "single"
    elif any(r.kind == "out of order" for r in rows):
        res.verdict = "order"
    if not again and res.verdict != "order" and not extra and not (sheet and len(rows) > 1):
        return res

    # The full read. What to keep at which scan is known from the headers.
    last = len(rows)
    draw = {1, last} if len(rows) > 1 else {1}
    for r in [r for r in rows if r.kind in ("again", "out of order")][:3]:
        draw |= {r.index - 1, r.index}
    if sheet:
        spare = MAX_CELLS - len(draw)
        others = [k for k in range(1, last + 1) if k not in draw]
        if spare > 0 and others:
            step = max(1, -(-len(others) // spare))
            draw |= set(others[::step][:spare])
    geo = _Geometry(head.width, head.height, head.components)
    mode = _colour(head.segments, head.components)
    held: dict[int, np.ndarray] = {}             # place in the frame -> the band before the next scan
    state = {"size": None}

    def follow(scan: jpegdct.Scan) -> None:
        if scan.index > last:
            return
        row = rows[scan.index - 1]
        if row.kind == "again":
            _measure(row, scan, held, geo)
        if scan.index in draw:
            if state["size"] is None:
                luma = scan.frame[0]
                first = scan.coeffs(luma)
                if first is None:                # the first component has no scan yet: size it from the frame
                    w, h = geo.width, geo.height
                else:
                    w, h = geo.shown(luma, first)
                    res.covered = h / geo.height
                scale = max(1, -(-max(w, h) // THUMB))
                state["size"] = (max(1, w // scale), max(1, h // scale))
                res.scale = scale
            res.thumbs[scan.index] = _render(scan, geo, mode, state["size"])
        held.clear()
        if scan.index < last and rows[scan.index].kind == "again":
            nxt = rows[scan.index]
            for k in nxt._where:
                coeffs = scan.coeffs(scan.frame[k])
                if coeffs is not None:
                    r, c = geo.own(scan.frame[k], coeffs)
                    held[k] = coeffs[:r, :c, nxt.ss:nxt.se + 1].astype(np.int16)

    try:
        jpegdct.read(data, max_blocks=max_blocks, on_scan=follow)
    except jpegdct.Unsupported as exc:
        return _skipped(f"{exc}, which the coefficient reader does not read")
    except ValueError as exc:
        return _skipped(f"the JPEG structure is damaged: {exc}")

    if extra and state["size"]:
        # What the scans libjpeg leaves alone would draw, read as a file of their own.
        rest = data[:scans[0].offset] + data[scans[0].end:]
        count: list[jpegdct.Scan] = []
        shown: dict[int, Image.Image] = {}
        try:
            jpegdct.read(rest, max_blocks=1, on_scan=count.append)
            jpegdct.read(rest, max_blocks=max_blocks, on_scan=lambda sc: shown.update(
                {sc.index: _render(sc, geo, mode, state["size"])}) if sc.index == len(count) else None)
        except ValueError:
            pass
        if shown:
            res.unread = shown[max(shown)]
            a, b = _grey(res.thumbs[1]), _grey(res.unread)
            if a.std() > 0 and b.std() > 0:
                res.unread_correlation = float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
    final = _grey(res.thumbs[last]) if last in res.thumbs else None
    if final is not None:
        for k, thumb in res.thumbs.items():
            rows[k - 1].away = float(np.sqrt(np.mean((_grey(thumb) - final) ** 2)))
    changing = [r for r in again if r.changed]
    if changing:
        res.verdict = "replaced"
        res.replaced_at = changing[0].index
        before = res.thumbs.get(res.replaced_at - 1)
        if before is not None and final is not None:
            a = _grey(before)
            res.distance = float(np.sqrt(np.mean((a - final) ** 2)))
            if a.std() > 0 and final.std() > 0:
                res.correlation = float(np.corrcoef(a.ravel(), final.ravel())[0, 1])
    elif again:
        res.verdict = "resent"
    return res


def _measure(row: ScanRow, scan: jpegdct.Scan, held: dict[int, np.ndarray], geo: _Geometry) -> None:
    """What a scan that sends a band again did to the values that were there."""
    shares, boxes = [], []
    for k in row._where:
        if k not in held:
            continue
        comp = scan.frame[k]
        before = held[k].astype(np.int32)
        r, c = before.shape[:2]
        after = scan.coeffs(comp)[:r, :c, row.ss:row.se + 1]
        prev = np.array(row._before[k])
        sent = prev >= 0                         # coefficients the band held before this scan
        if not sent.any() or not before.size:
            continue
        level = np.maximum(prev, row.al)[sent]
        b, a = before[:, :, sent], after[:, :, sent]
        z = np.arange(row.ss, row.se + 1)[sent]
        same = np.ones(b.shape, dtype=bool)
        for dc in (True, False):
            pick = (z == 0) if dc else (z != 0)
            if pick.any():
                same[:, :, pick] = (_truncated(b[:, :, pick], level[pick], dc)
                                    == _truncated(a[:, :, pick], level[pick], dc))
        changed = ~same.all(axis=2)
        shares.append(float(changed.mean()))
        if row.moved is None:
            diff = (a - b) * comp.q[row.ss:row.se + 1][sent]
            row.moved = float(np.sqrt((diff.astype(np.float64) ** 2).sum() / (64 * r * c)))
            row.moved_in = comp.cid
        if changed.any():
            ys, xs = np.nonzero(changed)
            fx, fy = 8 * geo.hmax // comp.h, 8 * geo.vmax // comp.v
            boxes.append((int(xs.min()) * fx, int(ys.min()) * fy,
                          min(geo.width, (int(xs.max()) + 1) * fx), min(geo.height, (int(ys.max()) + 1) * fy)))
    if shares:
        row.changed = max(shares)
    if boxes:
        row.box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes))


def render(res: PassesResult) -> Image.Image:
    """The pictures after the chosen scans, side by side, each with its scan under it."""
    keys = sorted(res.thumbs) + ([0] if res.unread is not None else [])
    w, h = res.thumbs[keys[0]].size
    cell_w = max(w, 150)
    cols = min(len(keys), 4 if len(keys) > 6 else 3)
    lines = -(-len(keys) // cols)
    pad, label = 8, 28
    sheet = Image.new("RGB", (cols * (cell_w + pad) + pad, lines * (h + label + pad) + pad), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for n, k in enumerate(keys):
        x = pad + (n % cols) * (cell_w + pad)
        y = pad + (n // cols) * (h + label + pad)
        if k == 0:
            sheet.paste(res.unread.convert("RGB"), (x + (cell_w - w) // 2, y))
            d.text((x, y + h + 2), "the scans behind it,", fill=(255, 120, 120))
            d.text((x, y + h + 14), "which libjpeg leaves unread", fill=(255, 120, 120))
            continue
        sheet.paste(res.thumbs[k].convert("RGB"), (x + (cell_w - w) // 2, y))
        row = res.rows[k - 1]
        what = {"first": "", "refine": "", "again": ", sent again", "out of order": ", out of order"}[row.kind]
        colour = (255, 120, 120) if row.kind == "again" and row.changed else (230, 230, 230)
        d.text((x, y + h + 2), f"after scan {k} of {len(res.rows)}", fill=colour)
        d.text((x, y + h + 14), f"{row.band}{what}"[:max(12, cell_w // 6)], fill=colour)
    return sheet
