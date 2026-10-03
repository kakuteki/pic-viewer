"""picviewer: step a PIC program in the MPLAB X simulator and view it as one HTML page."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__

EPILOG = """\
例:
  picviewer doctor                                   道具が揃っているか見る
  picviewer build examples/led                       コンパイル、シミュレータで実行、HTML を作る
  picviewer render examples/motor                    記録（bundles/*.json）から HTML だけ作り直す
  picviewer wave examples/motor -t pic16f1618 --pin RC5 --at 52
  picviewer serve examples --open                    http://127.0.0.1:8765/ で見る
"""


def build_parser():
    p = argparse.ArgumentParser(prog="picviewer", description=__doc__, epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"picviewer {__version__}")
    tools = argparse.ArgumentParser(add_help=False)
    tools.add_argument("--mplabx", help="MPLAB X の置き場所（例 C:/Program Files/Microchip/MPLABX/v6.35）")
    tools.add_argument("--xc8", help="xc8-cc の場所")
    tools.add_argument("--packs", help="デバイスパックの置き場所を足す")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    sub.add_parser("doctor", parents=[tools], help="MPLAB X・XC8・デバイスパックが見つかるか確かめる")

    b = sub.add_parser("build", parents=[tools], help="コンパイルしてシミュレータで実行し、HTML を作る")
    b.add_argument("project", nargs="?", default=".", help="picviewer.json かそのフォルダ（既定は今いる所）")
    b.add_argument("-t", "--target", action="append", help="この target だけ（何度でも指定できる）")
    b.add_argument("--skip-compile", action="store_true", help="コンパイルしない（前回の ELF を使う）")
    b.add_argument("--reuse-logs", action="store_true",
                   help="build/ に前回のシミュレータの記録があれば実行せずに使う（ソースを変えていないときだけ）")
    b.add_argument("--skip-waves", action="store_true", help="波形を測らない")

    r = sub.add_parser("render", help="記録（bundles/*.json）から HTML だけ作り直す。MPLAB X は要らない")
    r.add_argument("project", nargs="?", default=".")

    w = sub.add_parser("wave", parents=[tools], help="ある行で止めてから 1 命令ずつピンを読み、周期と H の長さを出す")
    w.add_argument("project")
    w.add_argument("-t", "--target", required=True)
    w.add_argument("--pin", required=True, help="ピン名（例 RC5）")
    w.add_argument("--at", type=int, required=True, help="止める行")
    w.add_argument("--samples", type=int, default=800, help="読む回数（既定 800）")

    s = sub.add_parser("serve", help="フォルダの HTML を http://127.0.0.1:<port>/ で見せる")
    s.add_argument("folder", nargs="?", default=".")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--open", action="store_true", help="既定のブラウザで開く")
    s.add_argument("--stop", action="store_true", help="このポートで動かしたサーバを止める")
    return p


def _toolchain(args):
    from .toolchain import Toolchain
    return Toolchain.detect(mplabx=args.mplabx, xc8=args.xc8, packs=args.packs)


def cmd_doctor(args):
    tc = _toolchain(args)
    for line in tc.describe():
        print(line)
    ok = tc.mdb is not None and tc.xc8 is not None and bool(tc.pack_dirs)
    print("判定: " + ("使える" if ok else "足りないものがある（README の「必要なもの」）"))
    return 0 if ok else 1


def cmd_build(args):
    from .pipeline import build_target
    from .project import load
    from .render import render_project
    project = load(args.project)
    targets = [project.target(t) for t in args.target] if args.target else project.targets
    tc = _toolchain(args)
    for t in targets:
        build_target(project, t, tc, compile=not args.skip_compile, reuse_logs=args.reuse_logs,
                     waves=not args.skip_waves)
    out = render_project(project)
    print(f"HTML: {out}")
    return 0


def cmd_render(args):
    from .project import load
    from .render import render_project
    print(f"HTML: {render_project(load(args.project))}")
    return 0


def cmd_wave(args):
    from .mdb import run as mdb_run
    from .project import load
    from .wave import parse_samples, summarize, wave_commands
    project = load(args.project)
    t = project.target(args.target)
    tc = _toolchain(args)
    work = project.build_dir / t.id
    elf = work / (t.source.stem + ".elf")
    if not elf.is_file():
        print(f"ELF が無い: {elf}（先に picviewer build）")
        return 1
    pin = args.pin.upper()
    log = work / f"wave_{pin}_{args.at}.log"
    text = mdb_run(tc.require_mdb(), wave_commands(t.device, elf, t.source.name, args.at, pin, args.samples, t.wait_ms),
                   log, timeout=300 + t.wait_ms / 1000 + args.samples * 0.5)
    w = summarize(parse_samples(text, pin), pin, args.at, args.samples)
    instr_hz = t.fosc_hz / 4 if t.fosc_hz else None
    print(f"{pin}: {w['start']} から {w['end']} サイクルまで {args.samples} 命令を読んだ。端の数 {len(w['edges']) - 1}")
    if w["periods"]:
        avg = sum(w["periods"]) / len(w["periods"])
        freq = f"、約 {instr_hz / avg / 1000:.2f} kHz" if instr_hz else ""
        print(f"周期（サイクル）: {w['periods']}{freq}")
    if w["highs"]:
        print(f"H の長さ（サイクル）: {w['highs']}")
    print(f"H の割合: {w['high_fraction'] * 100:.1f} %")
    print(f"記録: {log}")
    return 0


def cmd_serve(args):
    from .server import serve, stop
    if args.stop:
        return 0 if stop(args.port) else 1
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"フォルダが無い: {folder}")
        return 1
    serve(folder, args.port, open_browser=args.open)
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    from .compiler import CompileError
    from .mdb import MdbError
    from .project import ProjectError
    from .render import RenderError
    from .toolchain import ToolNotFound
    handlers = {"doctor": cmd_doctor, "build": cmd_build, "render": cmd_render, "wave": cmd_wave, "serve": cmd_serve}
    try:
        return handlers[args.command](args)
    except (ProjectError, ToolNotFound, CompileError, MdbError, RenderError, ValueError) as e:
        print(f"picviewer: {e}", file=sys.stderr)
        return 1
