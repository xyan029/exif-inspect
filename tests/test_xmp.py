import io
import struct
import zlib

from PIL import Image

from exif_inspect import xmp
from exif_inspect.checks import run_checks
from exif_inspect.cli import main
from exif_inspect.extract import read_image

HEAD = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" '
        'xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#" '
        'xmlns:stRef="http://ns.adobe.com/xap/1.0/sType/ResourceRef#" '
        'xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" '
        'xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" '
        'xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" ')
TAIL = '</rdf:Description></rdf:RDF></x:xmpmeta>'

LIGHTROOM = HEAD + (
    'xmp:CreatorTool="Adobe Photoshop Lightroom Classic 13.0 (Macintosh)" '
    'xmp:CreateDate="2024-05-04T10:22:31" xmp:ModifyDate="2024-05-06T18:40:12+02:00" '
    'xmpMM:DocumentID="xmp.did:1111" xmpMM:OriginalDocumentID="xmp.did:0000" '
    'crs:Version="16.0" crs:Exposure2012="+0.65" crs:Contrast2012="0" crs:Shadows2012="+25" '
    'crs:HasCrop="True" crs:CropTop="0.05" crs:CropLeft="0.1" crs:CropBottom="0.95" '
    'crs:CropRight="0.9" crs:RawFileName="DSC_0421.NEF">'
    '<xmpMM:History><rdf:Seq>'
    '<rdf:li stEvt:action="derived" stEvt:parameters="converted from image/x-nikon-nef to image/jpeg"/>'
    '<rdf:li stEvt:action="saved" stEvt:when="2024-05-06T18:40:12+02:00" '
    'stEvt:softwareAgent="Adobe Photoshop Lightroom Classic 13.0 (Macintosh)" stEvt:changed="/"/>'
    '</rdf:Seq></xmpMM:History>'
    '<xmpMM:DerivedFrom stRef:documentID="xmp.did:0000"/>'
    '<crs:RetouchAreas><rdf:Seq>'
    '<rdf:li><rdf:Description crs:SpotType="heal"/></rdf:li>'
    '<rdf:li><rdf:Description crs:SpotType="clone"/></rdf:li>'
    '</rdf:Seq></crs:RetouchAreas>'
    '<crs:MaskGroupBasedCorrections><rdf:Seq><rdf:li rdf:parseType="Resource">'
    '<crs:What>Correction</crs:What><crs:LocalExposure2012>0.5</crs:LocalExposure2012>'
    '</rdf:li></rdf:Seq></crs:MaskGroupBasedCorrections>'
) + TAIL

FIREFLY = HEAD + (
    'xmp:CreatorTool="Adobe Firefly" '
    'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia">'
    '<xmpMM:History><rdf:Seq><rdf:li stEvt:action="created" stEvt:softwareAgent="Adobe Firefly"/>'
    '</rdf:Seq></xmpMM:History>'
) + TAIL

PLAIN = HEAD + 'xmp:CreatorTool="Nikon Transfer 2.0" xmp:CreateDate="2024-05-04T10:22:31">' + TAIL


def _jpeg(path, packet, original="2024:05:04 10:22:31"):
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "NIKON", "D750"
    exif.get_ifd(0x8769)[0x9003] = original
    Image.new("RGB", (48, 32), "tan").save(path, "JPEG", xmp=packet.encode(), exif=exif)


def _png(path, packet):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(buf, "PNG")
    data = buf.getvalue()
    payload = b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + packet.encode()
    crc = zlib.crc32(b"iTXt" + payload) & 0xFFFFFFFF
    chunk = struct.pack(">I", len(payload)) + b"iTXt" + payload + struct.pack(">I", crc)
    iend = data.rindex(b"IEND") - 4
    path.write_bytes(data[:iend] + chunk + data[iend:])


def _codes(path):
    return {f.code for f in run_checks(read_image(path))}


def test_lightroom_packet_is_parsed(tmp_path):
    path = tmp_path / "lr.jpg"
    _jpeg(path, LIGHTROOM)
    x = read_image(path).xmp
    assert x.present and x.container == "JPEG APP1 segment"
    assert x.creator_tool.startswith("Adobe Photoshop Lightroom")
    assert x.create_date == "2024-05-04T10:22:31"
    assert x.document_id == "xmp.did:1111" and x.original_document_id == "xmp.did:0000"
    assert x.derived_from == "xmp.did:0000"
    assert [e.action for e in x.history] == ["derived", "saved"]
    assert x.history[1].agent.startswith("Adobe Photoshop Lightroom")
    assert x.history[1].when == "2024-05-06T18:40:12+02:00"
    assert x.history[0].parameters.startswith("converted from")
    assert x.raw_file == "DSC_0421.NEF"
    assert x.has_crop and x.crop == (0.1, 0.05, 0.9, 0.95)
    assert x.retouch_areas == 2
    assert x.local_adjustments == 1
    assert x.crs_settings["Exposure2012"] == "+0.65"
    assert "Contrast2012" not in x.crs_settings          # zero is the default
    assert x.ai_marker is None
    assert "crs" in x.namespaces and "xmpMM" in x.namespaces


def test_lightroom_findings(tmp_path):
    path = tmp_path / "lr.jpg"
    _jpeg(path, LIGHTROOM)
    codes = _codes(path)
    assert {"xmp_editor", "xmp_history", "xmp_derived", "xmp_raw", "xmp_retouch",
            "xmp_local_adjust", "xmp_crop"} <= codes
    assert "xmp_ai" not in codes
    assert "xmp_date_mismatch" not in codes             # XMP and EXIF agree


def test_date_mismatch(tmp_path):
    path = tmp_path / "lr.jpg"
    _jpeg(path, LIGHTROOM, original="2023:01:01 00:00:00")
    assert "xmp_date_mismatch" in _codes(path)


def test_ai_marker(tmp_path):
    path = tmp_path / "ff.jpg"
    _jpeg(path, FIREFLY)
    x = read_image(path).xmp
    assert x.ai_marker == "firefly"
    assert x.digital_source_type == "trainedAlgorithmicMedia"
    assert "xmp_ai" in _codes(path)


def test_plain_packet_is_quiet(tmp_path):
    path = tmp_path / "plain.jpg"
    _jpeg(path, PLAIN)
    codes = _codes(path)
    assert not any(c.startswith("xmp_") for c in codes)
    assert read_image(path).xmp.creator_tool == "Nikon Transfer 2.0"


def test_no_packet(tmp_path):
    path = tmp_path / "none.jpg"
    Image.new("RGB", (16, 16)).save(path, "JPEG")
    x = read_image(path).xmp
    assert not x.present and x.history == []


def test_png_and_webp_containers(tmp_path):
    png = tmp_path / "p.png"
    _png(png, LIGHTROOM)
    x = read_image(png).xmp
    assert x.container == "PNG iTXt chunk" and x.retouch_areas == 2
    webp = tmp_path / "w.webp"
    Image.new("RGB", (16, 16)).save(webp, "WEBP", xmp=FIREFLY.encode())
    assert read_image(webp).xmp.container == "WebP XMP chunk"


def test_extended_xmp_is_stitched(tmp_path):
    """A packet split the Adobe way: main APP1 names a GUID, continuation APP1s carry the rest."""
    guid = "A" * 32
    main_packet = (HEAD + 'xmlns:xmpNote="http://ns.adobe.com/xmp/note/" '
                   f'xmpNote:HasExtendedXMP="{guid}" xmp:CreatorTool="Big Editor">' + TAIL)
    extension = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
                 'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
                 'xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" crs:RawFileName="X.CR2"/>'
                 '</rdf:RDF></x:xmpmeta>').encode()
    buf = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buf, "JPEG", xmp=main_packet.encode())
    data = buf.getvalue()
    half = len(extension) // 2
    segs = b""
    for offset, chunk in ((0, extension[:half]), (half, extension[half:])):
        body = (b"http://ns.adobe.com/xmp/extension/\x00" + guid.encode()
                + struct.pack(">II", len(extension), offset) + chunk)
        segs += b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
    path = tmp_path / "ext.jpg"
    path.write_bytes(data[:2] + segs + data[2:])
    packet, where = xmp.find_packet(path.read_bytes(), "JPEG")
    assert where == "JPEG APP1 segments (extended)"
    assert b"X.CR2" in packet and b"Big Editor" in packet


def test_cli_xmp(tmp_path, capsys):
    path = tmp_path / "lr.jpg"
    _jpeg(path, LIGHTROOM)
    assert main(["xmp", str(path)]) == 0
    out = capsys.readouterr().out
    assert "history (2 steps" in out
    assert "retouch         2 healed or cloned" in out
    assert "raw file        DSC_0421.NEF" in out
    assert main(["xmp", "--raw", str(path)]) == 0
    assert "<x:xmpmeta" in capsys.readouterr().out
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "xmp :" in out and "2 history step(s)" in out
    assert main(["--json", str(path)]) == 0
    assert '"raw"' not in capsys.readouterr().out
    assert main(["xmp", str(tmp_path / "missing.jpg")]) == 1
