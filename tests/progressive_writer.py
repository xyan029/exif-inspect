"""A progressive JPEG encoder for the tests: any scan script, from coefficients already quantised.

Pillow writes progressive files with one script, libjpeg's default. Other
encoders split the spectrum and the bit planes differently, and a file
written against the tool can use any split the format allows. This encoder
takes the script as a list, so the tests can cover what Pillow never writes:
no successive approximation, many narrow bands, four refinement passes, DC
scans per component, scans in an order that makes no sense.

The statements follow libjpeg's jcphuff.c. Every AC symbol is coded with one
table that holds all 256 of them, which is wasteful and always valid.
"""
from __future__ import annotations

import struct

from exif_inspect.jpegdct import (_STD_DC_CHROMA, _STD_DC_LUMA, JpegData, _BitWriter, _build_codes,
                                  _magnitude)

# 254 codes of 8 bits and 2 of 9: room for every (run, size) symbol, and no code of all ones.
UNIVERSAL_AC = ([0] * 7 + [254, 2] + [0] * 7, list(range(256)))

# Scripts: (components by index, first coefficient, last coefficient, Ah, Al) per scan.
def default_script(n: int) -> list[tuple]:
    """libjpeg's jpeg_simple_progression, for one or three components."""
    if n == 1:
        return [((0,), 0, 0, 0, 1), ((0,), 1, 5, 0, 2), ((0,), 6, 63, 0, 2), ((0,), 1, 63, 2, 1),
                ((0,), 0, 0, 1, 0), ((0,), 1, 63, 1, 0)]
    everything = tuple(range(n))
    return ([(everything, 0, 0, 0, 1), ((0,), 1, 5, 0, 2)] + [((k,), 1, 63, 0, 1) for k in range(1, n)]
            + [((0,), 6, 63, 0, 2), ((0,), 1, 63, 2, 1), (everything, 0, 0, 1, 0)]
            + [((k,), 1, 63, 1, 0) for k in range(1, n)] + [((0,), 1, 63, 1, 0)])


def spectral_script(n: int, bands=((1, 1), (2, 5), (6, 20), (21, 63))) -> list[tuple]:
    """Spectral selection alone: every scan carries full precision."""
    return [(tuple(range(n)), 0, 0, 0, 0)] + [((k,), a, b, 0, 0) for a, b in bands for k in range(n)]


def deep_script(n: int, top: int = 3) -> list[tuple]:
    """Successive approximation from bit `top` down, DC one component at a time."""
    out = [((k,), 0, 0, 0, top) for k in range(n)] + [((k,), 1, 63, 0, top) for k in range(n)]
    for bit in range(top, 0, -1):
        out += [((k,), 0, 0, bit, bit - 1) for k in range(n)] + [((k,), 1, 63, bit, bit - 1) for k in range(n)]
    return out


def write_progressive(jd: JpegData, script: list[tuple] | None = None, restart: int = 0,
                      eoi: bool = True) -> bytes:
    """The coefficients of `jd` as a progressive JPEG coded by `script`."""
    comps = jd.components
    script = script or default_script(len(comps))
    out = bytearray(b"\xff\xd8")
    for marker, seg in jd.segments:
        out += b"\xff" + bytes([marker]) + struct.pack(">H", len(seg) + 2) + seg
    for tq, table in sorted(jd.qtables.items()):
        payload = bytes([tq]) + bytes(int(v) for v in table)
        out += b"\xff\xdb" + struct.pack(">H", len(payload) + 2) + payload
    sof = bytes([8]) + struct.pack(">HH", jd.height, jd.width) + bytes([len(comps)])
    for c in comps:
        sof += bytes([c.cid, (c.h << 4) | c.v, c.tq])
    out += b"\xff\xc2" + struct.pack(">H", len(sof) + 2) + sof
    dht = b""
    for tcth, (bits, values) in ((0x00, _STD_DC_LUMA), (0x01, _STD_DC_CHROMA), (0x10, UNIVERSAL_AC)):
        dht += bytes([tcth]) + bytes(bits) + bytes(values)
    out += b"\xff\xc4" + struct.pack(">H", len(dht) + 2) + dht
    if restart:
        out += b"\xff\xdd" + struct.pack(">HH", 4, restart)
    dc_codes = [_build_codes(*_STD_DC_LUMA), _build_codes(*_STD_DC_CHROMA)]
    ac_codes = _build_codes(*UNIVERSAL_AC)
    hmax, vmax = max(c.h for c in comps), max(c.v for c in comps)
    mcus_x = -(-jd.width // (8 * hmax))
    mcus_y = -(-jd.height // (8 * vmax))

    for indices, ss, se, ah, al in script:
        chosen = [comps[k] for k in indices]
        sos = bytes([len(chosen)])
        for k, c in zip(indices, chosen):
            sos += bytes([c.cid, (0 if k == 0 else 1) << 4])
        sos += bytes([ss, se, (ah << 4) | al])
        out += b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos
        w = _BitWriter()
        if len(chosen) > 1:
            rows, cols = mcus_y, mcus_x
            units = [(k, c, c.v, c.h) for k, c in zip(indices, chosen)]
        else:
            c = chosen[0]
            cols = -(-(-(-jd.width * c.h // hmax)) // 8)
            rows = -(-(-(-jd.height * c.v // vmax)) // 8)
            units = [(indices[0], c, 1, 1)]
        preds = {k: 0 for k in indices}
        eobrun = 0
        pending: list[int] = []            # correction bits that follow the next EOB symbol

        def flush_eob():
            nonlocal eobrun
            if eobrun:
                size = eobrun.bit_length() - 1
                length, code = ac_codes[size << 4]
                w.write(code, length)
                if size:
                    w.write(eobrun & ((1 << size) - 1), size)
                eobrun = 0
            for bit in pending:
                w.write(bit, 1)
            pending.clear()

        count = 0
        for my in range(rows):
            for mx in range(cols):
                if restart and count and count % restart == 0:
                    flush_eob()
                    w.flush()
                    w.out += bytes([0xFF, 0xD0 + (count // restart - 1) % 8])
                    preds = {k: 0 for k in indices}
                count += 1
                for k, c, cv, ch in units:
                    for by in range(cv):
                        for bx in range(ch):
                            block = [int(v) for v in c.coeffs[my * cv + by, mx * ch + bx]]
                            if ss == 0 and ah == 0:
                                value = block[0] >> al
                                size, bits = _magnitude(value - preds[k])
                                preds[k] = value
                                length, code = dc_codes[0 if k == 0 else 1][size]
                                w.write(code, length)
                                if size:
                                    w.write(bits, size)
                            elif ss == 0:
                                w.write((block[0] >> al) & 1, 1)
                            elif ah == 0:
                                run = 0
                                for i in range(ss, se + 1):
                                    v = block[i]
                                    mag = abs(v) >> al
                                    if mag == 0:
                                        run += 1
                                        continue
                                    flush_eob()
                                    while run > 15:
                                        length, code = ac_codes[0xF0]
                                        w.write(code, length)
                                        run -= 16
                                    size = mag.bit_length()
                                    length, code = ac_codes[(run << 4) | size]
                                    w.write(code, length)
                                    w.write((mag if v >= 0 else ~mag) & ((1 << size) - 1), size)
                                    run = 0
                                if run:
                                    eobrun += 1
                                    if eobrun == 0x7FFF:
                                        flush_eob()
                            else:
                                mags = {i: abs(block[i]) >> al for i in range(ss, se + 1)}
                                last_new = max((i for i, m in mags.items() if m == 1), default=0)
                                run = 0
                                held: list[int] = []
                                for i in range(ss, se + 1):
                                    mag = mags[i]
                                    if mag == 0:
                                        run += 1
                                        continue
                                    while run > 15 and i <= last_new:
                                        flush_eob()
                                        length, code = ac_codes[0xF0]
                                        w.write(code, length)
                                        run -= 16
                                        for bit in held:
                                            w.write(bit, 1)
                                        held = []
                                    if mag > 1:
                                        held.append(mag & 1)
                                        continue
                                    flush_eob()
                                    length, code = ac_codes[(run << 4) | 1]
                                    w.write(code, length)
                                    w.write(0 if block[i] < 0 else 1, 1)
                                    for bit in held:
                                        w.write(bit, 1)
                                    held = []
                                    run = 0
                                if run or held:
                                    eobrun += 1
                                    pending.extend(held)
                                    if eobrun == 0x7FFF or len(pending) > 937:
                                        flush_eob()
        flush_eob()
        w.flush()
        out += w.out
    if eoi:
        out += b"\xff\xd9"
    return bytes(out)


def scan_offsets(data: bytes) -> list[int]:
    """Where the SOS markers of a file written here lie."""
    return [k for k in range(len(data) - 1) if data[k] == 0xFF and data[k + 1] == 0xDA]


def splice(first: bytes, then: bytes) -> bytes:
    """One file from two with the same frame and tables: the scans of `then` behind those of `first`."""
    return first[:-2] + then[scan_offsets(then)[0]:]
