"""Serve a folder of viewer pages on localhost (127.0.0.1 only)."""
from __future__ import annotations

import html
import os
import re
import signal
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TEXT_TYPES = {".c": "text/plain; charset=utf-8", ".h": "text/plain; charset=utf-8",
              ".md": "text/plain; charset=utf-8", ".json": "application/json; charset=utf-8",
              ".log": "text/plain; charset=utf-8"}
SKIP_DIRS = {"build", ".git", "__pycache__", "node_modules"}


def state_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".cache")
    d = Path(base) / "picviewer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pid_file(port):
    return state_dir() / f"serve-{port}.pid"


def find_pages(root, depth=3):
    root = Path(root)
    pages = []

    def walk(d, level):
        for p in sorted(d.iterdir()):
            if p.is_dir() and level < depth and p.name not in SKIP_DIRS and not p.name.startswith("."):
                walk(p, level + 1)
            elif p.suffix.lower() == ".html":
                pages.append(p)
    walk(root, 0)
    return pages


def page_title(path):
    head = Path(path).read_text(encoding="utf-8", errors="replace")[:4000]
    m = re.search(r"<title>(.*?)</title>", head, re.S | re.I)
    return html.unescape(m.group(1).strip()) if m else Path(path).name


def listing_html(root):
    root = Path(root)
    items = []
    for p in find_pages(root):
        rel = p.relative_to(root).as_posix()
        items.append(f'<li><a href="/{html.escape(rel)}">{html.escape(page_title(p))}</a> <span>{html.escape(rel)}</span></li>')
    body = "\n".join(items) or "<li>HTML がまだ無い。picviewer build で作る。</li>"
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PIC viewer</title>
<style>
:root {{ color-scheme: light dark; --bg: #f1f2f6; --panel: #fff; --fg: #1b1e26; --dim: #596072; --link: #1f5fbf; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #14161b; --panel: #1e222a; --fg: #e4e7ee; --dim: #9ba2b3; --link: #7fb0ff; }} }}
body {{ margin: 0; background: var(--bg); color: var(--fg); font-family: "Yu Gothic UI", "Meiryo UI", system-ui, sans-serif; }}
main {{ max-width: 860px; margin: 0 auto; padding: 24px 20px; }}
h1 {{ font-size: 22px; margin: 0 0 14px; }}
ul {{ list-style: none; padding: 0; margin: 0; }}
li {{ background: var(--panel); border-radius: 10px; padding: 12px 16px; margin: 0 0 10px; }}
a {{ color: var(--link); font-size: 17px; font-weight: 700; }}
span {{ display: block; color: var(--dim); font-size: 13px; margin-top: 2px; }}
</style></head>
<body><main><h1>PIC viewer</h1><ul>
{body}
</ul></main></body></html>
"""


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path in ("", "/") and not (Path(self.directory) / "index.html").is_file():
            body = listing_html(self.directory).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        # a rebuilt page must show up on reload, not an old copy
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def guess_type(self, path):
        return TEXT_TYPES.get(Path(str(path)).suffix.lower()) or super().guess_type(path)

    def log_message(self, fmt, *args):
        if sys.stderr is not None:      # None under pythonw, where there is no console to write to
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def make_server(root, port):
    return ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(Path(root).resolve())))


def serve(root, port, open_browser=False, log=print):
    srv = make_server(root, port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    pid_file(port).write_text(str(os.getpid()))
    log(f"{Path(root).resolve()} を {url} で配信中（止めるには Ctrl+C か picviewer serve --stop --port {port}）")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        pid_file(port).unlink(missing_ok=True)


def stop(port, log=print):
    f = pid_file(port)
    if not f.is_file():
        log(f"ポート {port} で動かしたサーバの記録が無い")
        return False
    pid = int(f.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        log(f"止めた（pid {pid}）")
        ok = True
    except OSError as e:
        log(f"pid {pid} は止められなかった: {e}")
        ok = False
    f.unlink(missing_ok=True)
    return ok
