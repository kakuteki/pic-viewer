"""picviewer index: one page listing every project below a folder, with links to their viewer pages."""
from __future__ import annotations

import html
import re
from collections import Counter
from pathlib import Path

from . import bundle as bundle_io
from .mdb import MdbError
from .project import ProjectError, load

SKIP_DIRS = {"build", "bundles", ".git", "__pycache__", "node_modules"}
ERROR_FILE = "error.txt"         # written into a project's build folder when a batch build of it failed


def natural(name):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", str(name))]


def find_projects(root):
    """picviewer.json files below root, in natural order (pic2 before pic10)."""
    root = Path(root)
    found = []

    def walk(d):
        if (d / "picviewer.json").is_file():
            found.append(d / "picviewer.json")
        for p in sorted(d.iterdir(), key=lambda p: natural(p.name)):
            if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith("."):
                walk(p)
    walk(root)
    return found


def entry(root, path):
    """What the index shows for one project."""
    rel = path.parent.relative_to(root)
    e = {"group": rel.parent.as_posix() if rel.parent != Path(".") else "", "name": rel.name or path.parent.name,
         "page": None, "error": None, "summary": "", "targets": []}
    try:
        p = load(path)
    except ProjectError as err:
        e["error"] = str(err)
        return e
    e["name"] = p.title if rel == Path(".") else rel.name
    if p.output.is_file():
        e["page"] = p.output.relative_to(root).as_posix()
    err = p.build_dir / ERROR_FILE
    if err.is_file():
        e["error"] = err.read_text(encoding="utf-8").strip()
    e["summary"] = p.targets[0].summary
    for t in p.targets:
        info = {"device": t.device, "steps": None}
        b = p.bundle_path(t.id)
        if b.is_file():
            try:
                data = bundle_io.read(b)
            except (MdbError, ValueError):
                data = None
            if data:
                kinds = Counter(s["kind"] for s in data["steps"])
                info.update(steps=len(data["steps"]), writes=kinds["write"], timeouts=kinds["timeout"],
                            traced=data["traced"])
        e["targets"].append(info)
    return e


def card(e):
    esc = html.escape
    name = f'<a href="{esc(e["page"])}">{esc(e["name"])}</a>' if e["page"] else f'<b>{esc(e["name"])}</b>'
    facts = []
    for t in e["targets"]:
        if t["steps"] is None:
            facts.append(f"{t['device']}: 記録なし")
        else:
            s = f"{t['device']}: {t['steps']} ステップ（書き込みで止めた所 {t['writes']}"
            s += f"、書かないまま待った所 {t['timeouts']}" if t["timeouts"] else ""
            facts.append(s + f"）記録 {t['traced']}")
    parts = [f'<li class="card{" bad" if e["error"] else ""}">', f"<h3>{name}</h3>"]
    if e["summary"]:
        parts.append(f'<p class="sum">{esc(e["summary"])}</p>')
    if facts:
        parts.append(f'<p class="facts">{esc(" / ".join(facts))}</p>')
    if e["error"]:
        parts.append(f'<pre class="err">{esc(e["error"])}</pre>')
    parts.append("</li>")
    return "".join(parts)


def index_html(root, title):
    root = Path(root).resolve()
    entries = [entry(root, p) for p in find_projects(root)]
    groups = {}
    for e in entries:
        groups.setdefault(e["group"], []).append(e)
    ok = sum(1 for e in entries if e["page"] and not e["error"])
    bad = sum(1 for e in entries if e["error"])
    sections = []
    for g in sorted(groups, key=natural):
        head = f"<h2>{html.escape(g)}</h2>" if g else ""
        sections.append(f'<section>{head}<ul>{"".join(card(e) for e in groups[g])}</ul></section>')
    body = "\n".join(sections) or "<p>picviewer.json が見つからない。picviewer init で作る。</p>"
    count = f"{len(entries)} 本（見られる {ok} 本、失敗 {bad} 本）"
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: light dark; --bg: #f1f2f6; --panel: #ffffff; --fg: #1b1e26; --dim: #596072;
  --link: #1f5fbf; --bad: #fbe3df; --bad-ink: #9a2a14; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #14161b; --panel: #1e222a; --fg: #e4e7ee; --dim: #9ba2b3;
  --link: #7fb0ff; --bad: #3d2420; --bad-ink: #ffb4a3; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg); font-size: 14px; line-height: 1.5;
  font-family: "Yu Gothic UI", "Meiryo UI", "Hiragino Sans", "Noto Sans JP", system-ui, sans-serif; }}
main {{ max-width: 1280px; margin: 0 auto; padding: 20px 16px 32px; }}
h1 {{ font-size: 22px; margin: 0; }}
.count {{ color: var(--dim); margin: 2px 0 18px; }}
h2 {{ font-size: 16px; margin: 18px 0 8px; }}
ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 10px;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr)); }}
.card {{ background: var(--panel); border-radius: 10px; padding: 10px 14px 12px; min-width: 0; }}
.card.bad {{ background: var(--bad); }}
h3 {{ margin: 0; font-size: 17px; }}
a {{ color: var(--link); }}
.sum {{ margin: 4px 0 0; }}
.facts {{ margin: 4px 0 0; color: var(--dim); font-size: 12px; }}
.err {{ margin: 6px 0 0; color: var(--bad-ink); font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere;
  font-family: Consolas, "BIZ UDGothic", monospace; max-height: 9em; overflow-y: auto; }}
</style></head>
<body><main><h1>{html.escape(title)}</h1><p class="count">{count}</p>
{body}
</main></body></html>
"""


def write_index(root, title=None):
    root = Path(root)
    out = root / "index.html"
    out.write_text(index_html(root, title or root.resolve().name), encoding="utf-8", newline="\n")
    return out
