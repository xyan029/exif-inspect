"""One HTML page with every check run on a picture.

The page is self-contained: the picture, each tool's map and its text
output are embedded, so it can be sent on as one file. Each tool is run
through its own command-line entry so that the page shows exactly what
the command prints, and a tool that fails on a file is reported as such
instead of stopping the rest.
"""

from __future__ import annotations

import base64
import contextlib
import html
import io
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import __version__
from .checks import run_checks
from .extract import read_image

EMBED_SIDE = 1400        # longest side of an embedded picture
EMBED_QUALITY = 85

# Order on the page: (key, title, how to run it).
TOOLS = ("ela", "ghost", "cfa", "noise", "light", "copymove", "thumbnail", "xmp")
TITLES = {
    "ela": "Error level analysis",
    "ghost": "JPEG ghosts",
    "cfa": "Demosaicing traces",
    "noise": "Noise level",
    "light": "Lighting direction",
    "copymove": "Copy-move detection",
    "thumbnail": "EXIF thumbnail",
    "xmp": "XMP editing history",
}


@dataclass
class Section:
    key: str
    title: str
    text: str
    images: list[tuple[str, bytes]] = field(default_factory=list)   # (caption, PNG/JPEG bytes)
    seconds: float = 0.0
    failed: bool = False


@dataclass
class Report:
    path: str
    sections: list[Section]
    flags: int
    html: str


def _embed(data: bytes) -> str:
    """A data URI for an image, downscaled and recompressed when large."""
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        if max(img.size) > EMBED_SIDE:
            img.thumbnail((EMBED_SIDE, EMBED_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=EMBED_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _run(argv: list[str]) -> tuple[str, int]:
    """Run a subcommand in-process and capture what it prints."""
    from .cli import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = main(argv)
        except SystemExit as exc:            # argparse errors
            code = int(exc.code or 0)
        except Exception as exc:             # a tool crashing must not sink the page
            print(f"   error  : {exc}")
            code = 1
    return out.getvalue(), code


def run_tool(key: str, src: Path, workdir: Path, grid: bool = False) -> Section:
    """Run one tool on the file, collecting its text and the pictures it wrote."""
    out = workdir / f"{src.stem}_{key}.png"
    argv = [key]
    if key == "ela":
        argv += ["--side-by-side", "-o", str(out)]
    elif key == "ghost":
        argv += ["-o", str(out)] + (["--grid"] if grid else [])
    elif key in ("cfa", "noise", "light", "copymove"):
        argv += ["-o", str(out)]
    elif key == "thumbnail":
        argv += ["-o", str(out)]
    argv.append(str(src))
    started = time.time()
    text, code = _run(argv)
    section = Section(key, TITLES[key], text.rstrip(), seconds=time.time() - started,
                      failed=code != 0 and "error" in text)
    if out.exists():
        section.images.append((_caption(key), out.read_bytes()))
    return section


def _caption(key: str) -> str:
    return {
        "ela": "original (left) and error level heatmap (right)",
        "ghost": "cyan: region with a JPEG ghost the rest of the picture lacks",
        "cfa": "red: demosaicing trace missing; dimmed: cannot judge",
        "noise": "red: noisier than its peers; blue: smoother; dimmed: cannot judge",
        "light": "arrows point toward the light; green agrees, red disagrees, yellow: common direction",
        "copymove": "orange: source; cyan: copy",
        "thumbnail": "EXIF thumbnail, main image, difference",
    }.get(key, "")


def build(path: str | Path, tools: tuple[str, ...] = TOOLS, grid: bool = False) -> Report:
    """Run the checks and the chosen tools on one file and lay the results out as HTML."""
    src = Path(path)
    report = read_image(src)
    findings = run_checks(report)
    summary_text, _ = _run([str(src)])
    sections: list[Section] = []
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        if not report.error:
            for key in tools:
                if key == "thumbnail" and report.thumbnail is None:
                    continue
                sections.append(run_tool(key, src, workdir, grid))
    flags = sum(1 for f in findings if f.level == "flag")
    page = _page(src, report, findings, summary_text, sections)
    return Report(str(src), sections, flags, page)


def _page(src: Path, report, findings, summary_text: str, sections: list[Section]) -> str:
    esc = html.escape
    parts = [f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>exif-inspect report: {esc(src.name)}</title>
<style>
 body {{ font: 15px/1.45 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #222;
        max-width: 1100px; margin: 2em auto; padding: 0 1.2em; background: #fff; }}
 h1 {{ font-size: 1.5em; margin-bottom: .2em; }}  h2 {{ font-size: 1.2em; margin-top: 2em;
        border-bottom: 1px solid #ddd; padding-bottom: .2em; }}
 .meta {{ color: #666; font-size: .9em; }}
 pre {{ background: #f6f7f8; border: 1px solid #e3e5e8; border-radius: 6px; padding: .8em 1em;
        overflow-x: auto; font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre; }}
 ul.findings {{ list-style: none; padding: 0; }}
 ul.findings li {{ position: relative; margin: .35em 0; padding-left: 1.6em; }}
 ul.findings li::before {{ position: absolute; left: .2em; }}
 .flag::before {{ content: "!"; font-weight: bold; color: #c0392b; }}
 .info::before {{ content: "\\2013"; color: #888; }}
 figure {{ margin: 1em 0; }}  figure img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; }}
 figcaption {{ color: #666; font-size: .85em; margin-top: .3em; }}
 .time {{ float: right; color: #999; font-size: .8em; font-weight: normal; }}
 .failed h2 {{ color: #c0392b; }}
 footer {{ margin-top: 3em; color: #888; font-size: .85em; border-top: 1px solid #ddd; padding-top: .6em; }}
</style></head><body>
<h1>{esc(src.name)}</h1>
<p class="meta">exif-inspect {esc(__version__)} report, made {time.strftime('%Y-%m-%d %H:%M')}.
 SHA-256 {esc(report.sha256 or '-')}.</p>
"""]
    if report.error:
        parts.append(f"<p class='flag'>Could not read the file: {esc(report.error)}</p>")
    else:
        w, h = report.size or (0, 0)
        parts.append(f"<p class='meta'>{esc(report.format or '?')} {w}x{h} {esc(report.mode or '')}</p>")
        try:
            parts.append(f"<figure><img src='{_embed(src.read_bytes())}' alt='the picture'>"
                         "<figcaption>the picture as given</figcaption></figure>")
        except Exception:
            pass
    flagged = [f for f in findings if f.level == "flag"]
    noted = [f for f in findings if f.level != "flag"]
    parts.append(f"<h2>Findings <span class='time'>{len(flagged)} flagged, {len(noted)} notes</span></h2>")
    parts.append("<ul class='findings'>")
    for f in flagged + noted:
        parts.append(f"<li class='{esc(f.level)}' title='{esc(f.code)}'>{esc(f.message)}</li>")
    parts.append("</ul>")
    parts.append("<h2>Metadata</h2><pre>" + esc(summary_text.rstrip()) + "</pre>")
    for s in sections:
        cls = " class='failed'" if s.failed else ""
        parts.append(f"<section{cls}><h2>{esc(s.title)} <span class='time'>{s.seconds:.1f} s</span></h2>")
        for caption, data in s.images:
            try:
                parts.append(f"<figure><img src='{_embed(data)}' alt='{esc(s.title)}'>"
                             f"<figcaption>{esc(caption)}</figcaption></figure>")
            except Exception:
                continue
        parts.append("<pre>" + esc(s.text) + "</pre></section>")
    parts.append("<footer>Every line here is a lead for a human examiner, not a verdict: metadata is "
                 "easy to edit or strip, and each pixel method has the limits described in the "
                 "exif-inspect README.</footer>")
    parts.append("</body></html>\n")
    return "".join(parts)
