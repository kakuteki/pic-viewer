"""Render one self-contained HTML page from the project file and its bundles."""
from __future__ import annotations

import html
import json
from pathlib import Path

from . import __version__
from . import bundle as bundle_io

WEB = Path(__file__).resolve().parent / "web"
DATA_MARKER = "/*__DATA__*/"
SCRIPTS_MARKER = "/*__SCRIPTS__*/"
TITLE_MARKER = "<title>PIC viewer</title>"


class RenderError(Exception):
    """The page cannot be built from what is there."""


def load_bundles(project):
    out = {}
    for t in project.targets:
        path = project.bundle_path(t.id)
        if not path.is_file():
            raise RenderError(f"{t.id} の記録（{path}）が無い。先に picviewer build を実行する")
        b = bundle_io.read(path)
        if b.get("device") != t.device:
            raise RenderError(f"{path} は {b.get('device')} の記録で、プロジェクトの {t.device} と違う。build をやり直す")
        out[t.id] = b
    return out


def page_data(project, bundles):
    targets = []
    for t in project.targets:
        b = bundles[t.id]
        targets.append({
            "id": t.id, "device": b["device"], "summary": t.summary, "fosc_hz": t.fosc_hz,
            "source_name": b["source_name"], "source": b["source"], "pack": b["pack"],
            "tools": b["tools"], "traced": b["traced"], "fast_forward": b.get("fast_forward"),
            "vdd": b["vdd"], "pins": b["pins"],
            "regs": b["regs"], "steps": b["steps"], "waves": b.get("waves", []),
            "notes": t.notes, "circuit": t.circuit, "counters": t.counters,
        })
    return {"title": project.title, "tool": f"picviewer {__version__}", "targets": targets}


def _read(path):
    return path.read_text(encoding="utf-8")


def render_html(project, bundles):
    data = page_data(project, bundles)
    types = []
    for t in data["targets"]:
        if t["circuit"]["type"] not in types:
            types.append(t["circuit"]["type"])
    if "pins" not in types:
        types.append("pins")       # the fallback view is always there
    js = "\n".join([_read(WEB / "circuits" / f"{ty}.js") for ty in types] + [_read(WEB / "core.js")])
    if "</script" in js.lower():
        raise RenderError("埋め込む JavaScript に </script が入っている")
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    blob = blob.replace("</", "<\\/").replace("<!--", "<\\u0021--")
    page = _read(WEB / "viewer.html")
    for marker in (DATA_MARKER, SCRIPTS_MARKER, TITLE_MARKER):
        if page.count(marker) != 1:
            raise RenderError(f"ひな形に {marker} がちょうど 1 つ無い")
    # split instead of replace, so text inside the data can never be taken for the second marker
    head, rest = page.split(DATA_MARKER)
    middle, tail = rest.split(SCRIPTS_MARKER)
    head = head.replace(TITLE_MARKER, f"<title>{html.escape(project.title)}</title>")
    return head + blob + middle + js + tail


def render_project(project):
    page = render_html(project, load_bundles(project))
    project.output.parent.mkdir(parents=True, exist_ok=True)
    project.output.write_text(page, encoding="utf-8", newline="\n")
    return project.output
