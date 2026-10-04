"""picviewer: step a PIC program in the MPLAB X simulator and view it as one HTML page."""
from __future__ import annotations

import argparse
import os
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
  picviewer init blink.c                             ソースを読んで blink/picviewer.json を作る
  picviewer init 授業/PIC1 授業/PIC2 -o course        フォルダの .c と .xc8 を全部（1 本ごとにフォルダ）
  picviewer build course --keep-going --no-probe     course の下のプロジェクトを全部。失敗しても続ける
  picviewer index course                             course/index.html（一覧のページ）を作る
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
    b.add_argument("project", nargs="*", default=["."],
                   help="picviewer.json かそのフォルダ（既定は今いる所）。picviewer.json の無いフォルダは、その下を全部")
    b.add_argument("-t", "--target", action="append", help="この target だけ（何度でも指定できる）")
    b.add_argument("--skip-compile", action="store_true", help="コンパイルしない（前回の ELF を使う）")
    b.add_argument("--reuse-logs", action="store_true",
                   help="build/ に前回のシミュレータの記録があれば実行せずに使う（ソースを変えていないときだけ）")
    b.add_argument("--skip-waves", action="store_true", help="波形を測らない")
    b.add_argument("--no-probe", action="store_true",
                   help="止める行とレジスタ名の下調べ（シミュレータを 1 回余分に起こす）を省く")
    b.add_argument("--keep-going", action="store_true",
                   help="1 本失敗しても次へ進む。失敗の中身は build/error.txt に残り、index のページにも出る")

    r = sub.add_parser("render", help="記録（bundles/*.json）から HTML だけ作り直す。MPLAB X は要らない")
    r.add_argument("project", nargs="*", default=["."], help="build と同じ。picviewer.json の無いフォルダは、その下を全部")

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

    i = sub.add_parser("init", parents=[tools], help="C のソースを読んで picviewer.json を作る（何本でも）")
    i.add_argument("sources", nargs="+", help="ソース（.c や .xc8）。フォルダを渡すとその下の .c と .xc8 を全部")
    i.add_argument("-o", "--out", default=".", help="作る場所（既定は今いる所）。1 本ごとに <out>/<ソースの名前>/ を作る")
    i.add_argument("-d", "--device", default="PIC16F886", help="マイコン（既定 PIC16F886）")
    i.add_argument("--switches", choices=["auto", "low", "high"], default="auto",
                   help="スイッチを押したときの値。auto は ! や ~ を付けて読むピンを 0、ほかを 1 とみる（既定）")
    i.add_argument("--switch-bank", help="ポートをまとめて読むとき（PORTC = PORTA など）のスイッチのピン（例 RA0,RA1,RA2,RA3）")
    i.add_argument("--writes", type=int, default=16, help="LED のポートへの書き込みを何回追うか（既定 16）")
    i.add_argument("--force", action="store_true", help="picviewer.json があっても作り直す")
    i.add_argument("--keep-going", action="store_true", help="1 本失敗しても次へ進む")

    x = sub.add_parser("index", help="フォルダの下のプロジェクトを一覧にした index.html を作る")
    x.add_argument("folder", nargs="?", default=".")
    x.add_argument("--title", help="ページの題（既定はフォルダの名前）")
    return p


def _toolchain(args):
    from .toolchain import Toolchain
    return Toolchain.detect(mplabx=args.mplabx, xc8=args.xc8, packs=args.packs)


def _errors():
    from .compiler import CompileError
    from .mdb import MdbError
    from .project import ProjectError
    from .render import RenderError
    return ProjectError, CompileError, MdbError, RenderError, ValueError, OSError


def cmd_doctor(args):
    tc = _toolchain(args)
    for line in tc.describe():
        print(line)
    ok = tc.mdb is not None and tc.xc8 is not None and bool(tc.pack_dirs)
    print("判定: " + ("使える" if ok else "足りないものがある（README の「必要なもの」）"))
    return 0 if ok else 1


def _project_paths(raw_paths):
    from .index import find_projects
    from .project import ProjectError
    out = []
    for raw in raw_paths:
        p = Path(raw)
        if p.is_dir() and not (p / "picviewer.json").is_file():
            found = find_projects(p)
            if not found:
                raise ProjectError(f"{p} にも、その下にも picviewer.json が無い")
            out += found
        else:
            out.append(p)
    return out


def cmd_build(args):
    from .index import ERROR_FILE
    from .pipeline import build_target
    from .project import ProjectError, load
    from .render import render_project
    paths = _project_paths(args.project)
    if args.target and len(paths) > 1:
        raise ProjectError("-t はプロジェクトが 1 つのときだけ使える")
    tc = _toolchain(args)
    failed = []
    for path in paths:
        project = None
        try:
            project = load(path)
            targets = [project.target(t) for t in args.target] if args.target else project.targets
            for t in targets:
                build_target(project, t, tc, compile=not args.skip_compile, reuse_logs=args.reuse_logs,
                             waves=not args.skip_waves, probe=not args.no_probe)
            print(f"HTML: {render_project(project)}", flush=True)
            (project.build_dir / ERROR_FILE).unlink(missing_ok=True)
        except _errors() as e:
            if not args.keep_going:
                raise
            failed.append(path)
            print(f"picviewer: {path}: {e}", file=sys.stderr, flush=True)
            if project is not None:
                project.build_dir.mkdir(parents=True, exist_ok=True)
                (project.build_dir / ERROR_FILE).write_text(f"{e}\n", encoding="utf-8")
    if len(paths) > 1:
        print(f"{len(paths)} 本のうち成功 {len(paths) - len(failed)} 本、失敗 {len(failed)} 本")
    return 1 if failed else 0


def cmd_render(args):
    from .project import load
    from .render import RenderError, render_project
    paths = _project_paths(args.project)
    failed = 0
    for path in paths:
        try:
            print(f"HTML: {render_project(load(path))}")
        except RenderError as e:
            if len(paths) == 1:
                raise
            failed += 1                    # a project whose build failed has no bundle yet
            print(f"picviewer: {path}: {e}", file=sys.stderr)
    return 1 if failed else 0


def cmd_wave(args):
    from .compiler import compiled_name
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
    text = mdb_run(tc.require_mdb(), wave_commands(t.device, elf, compiled_name(t), args.at, pin, args.samples, t.wait_ms),
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


def _sources(raw_paths):
    from .index import SKIP_DIRS, natural
    from .project import ProjectError
    out = []
    for raw in raw_paths:
        p = Path(raw)
        if p.is_dir():
            found = [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in (".c", ".xc8")
                     and not f.name.startswith("._") and not SKIP_DIRS & set(f.relative_to(p).parts[:-1])]
            out += sorted(found, key=lambda f: natural(f.relative_to(p).as_posix()))
        elif p.is_file():
            out.append(p)
        else:
            raise ProjectError(f"ソースが無い: {p}")
    if not out:
        raise ProjectError("ソース（.c か .xc8）が 1 本も見つからない")
    return [f.resolve() for f in out]


def cmd_init(args):
    from .init import init_source
    sources = _sources(args.sources)
    base = Path(os.path.commonpath([str(s.parent) for s in sources]))
    bank = [s.strip().upper() for s in args.switch_bank.split(",") if s.strip()] if args.switch_bank else None
    tc = _toolchain(args)
    failed = 0
    for src in sources:
        project_dir = Path(args.out) / src.parent.relative_to(base) / src.stem
        try:
            f = init_source(src, project_dir, args.device, tc, polarity=args.switches, bank=bank,
                            writes=args.writes, force=args.force)
        except _errors() as e:
            if not args.keep_going:
                raise
            failed += 1
            print(f"picviewer: {src.name}: {e}", file=sys.stderr, flush=True)
            continue
        out = f"PORT{f['out']}{'（7 セグ）' if f['seg7'] else ''}" if f["out"] else "なし"
        sw = "、".join(f["switches"]) or "なし"
        print(f"{project_dir.as_posix()}/picviewer.json: 最初の行 {f['first']}、初期設定 {f['setup']} 行、"
              f"出力 {out}、スイッチ {sw}{'、入力待ちから始まる' if f['wait_loop'] else ''}", flush=True)
    if len(sources) > 1:
        print(f"{len(sources)} 本のうち作った {len(sources) - failed} 本、失敗 {failed} 本")
    return 1 if failed else 0


def cmd_index(args):
    from .index import write_index
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"フォルダが無い: {folder}")
        return 1
    print(f"一覧: {write_index(folder, args.title)}")
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    from .toolchain import ToolNotFound
    handlers = {"doctor": cmd_doctor, "build": cmd_build, "render": cmd_render, "wave": cmd_wave,
                "serve": cmd_serve, "init": cmd_init, "index": cmd_index}
    try:
        return handlers[args.command](args)
    except (ToolNotFound, *_errors()) as e:
        print(f"picviewer: {e}", file=sys.stderr)
        return 1
