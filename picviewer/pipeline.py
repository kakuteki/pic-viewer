"""build = compile with XC8, check and run the plan in the simulator, measure waves, write the bundle."""
from __future__ import annotations

import datetime
from pathlib import Path

from . import bundle as bundle_io
from .bundle import make_bundle, make_steps, read_source, register_info
from .compiler import FAST_VAR, compile_target, compiled_name, elf_path, skipped_per_ms
from .linetab import line_at, read_line_table, writer_address
from .mdb import (MdbError, key_stimulus, parse_records, plan_wait_seconds, probe_commands, probe_errors,
                  run as mdb_run, run_trace, script, skipped_stops, trace_chunks)
from .picdef import PicDef
from .project import ProjectError, keypad_keys, keypad_of, plan_lines, plan_pins, plan_watches
from .toolchain import find_device_file, xc8_version
from .wave import parse_samples, summarize, wave_commands


def _date_of(path):
    return datetime.date.fromtimestamp(Path(path).stat().st_mtime).isoformat()


def _say(message):
    print(message, flush=True)       # keep progress in order with error messages when the output is piped


def keypad_setup(target, work):
    """What trace_commands needs for a keypad: its columns, its keys and one SCL file per key the plan presses."""
    pad = keypad_of(target.circuit)
    if pad is None:
        return None
    keys = keypad_keys(pad)
    folder = Path(work) / "keys"
    folder.mkdir(parents=True, exist_ok=True)
    scl = {}
    for i, label in enumerate(sorted({a["press"] for a in target.trace if "press" in a})):
        path = folder / f"key{i}.scl"            # labels such as + and * do not belong in file names
        path.write_text(key_stimulus(target.device, *keys[label]), encoding="ascii", newline="\n")
        scl[label] = path
    return {"cols": [c.upper() for c in pad["cols"]], "keys": keys, "scl": scl}


def check_names(target, picdef):
    """Names mdb would not check for us: a wrong pin stops mdb, and a wrong watched register is accepted silently."""
    known = {n.upper() for names in picdef.pins() for n in names}
    errors = []
    pad = keypad_of(target.circuit)
    pad_pins = [p.upper() for p in pad["rows"] + pad["cols"]] if pad else []
    bad_pins = [p for p in plan_pins(target.trace) + pad_pins if p.upper() not in known]
    if bad_pins:
        errors.append(f"{target.device} に無いピン名: {bad_pins}（RB0、AN0 のようにデバイス定義ファイルの名前で書く）")
    for reg in plan_watches(target.trace):
        try:
            if picdef.sfr(reg)["width"] > 8:
                errors.append(f"until_write の {reg} は 8 ビットのレジスタではない")
        except KeyError:
            errors.append(f"until_write の {reg} というレジスタは {target.device} に無い")
    if errors:
        raise MdbError("\n".join(errors))


def build_target(project, target, tc, *, compile=True, reuse_logs=False, waves=True, probe=True, session=True,
                 log=_say):
    """session: keep mdb open and read each stop before the next (run_trace); False runs the plan as one script."""
    if target.init_error:
        # a placeholder init wrote so that the index lists the program: its plan was never read from the source
        raise ProjectError(f"init がこのプログラムを読めなかった: {target.init_error}\n"
                           "ソースを直してから init --force をやり直す（計画を手で書いたなら init_error を消す）")
    work = project.build_dir / target.id
    work.mkdir(parents=True, exist_ok=True)
    elf = elf_path(target, work)
    if compile:
        fast = f"（__delay_ms を 1/{target.fast_forward} にした版）" if target.fast_forward else ""
        log(f"[{target.id}] XC8 でコンパイル: {target.source_name}{fast}")
        elf = compile_target(tc.require_xc8(), target, work)
    device_file, pack_name, pack_version = find_device_file(target.device, tc.pack_dirs)
    picdef = PicDef(device_file)
    registers = register_info(picdef, target.registers)
    check_names(target, picdef)
    src = compiled_name(target)          # mdb finds breakpoints by the file name the ELF was built from
    variables = [FAST_VAR] if target.fast_forward else []

    def need_simulator():
        if not elf.is_file():
            raise MdbError(f"ELF が無い: {elf}（--skip-compile を外す）")
        return tc.require_mdb()

    trace_log = work / "trace.log"
    if reuse_logs and trace_log.is_file():
        log(f"[{target.id}] 前回のシミュレータの記録を使う: {trace_log.name}")
        text = trace_log.read_text(encoding="utf-8")
    else:
        mdb = need_simulator()
        if probe:
            lines = sorted(set(plan_lines(target.trace)) | {w.at for w in target.waves})
            names = list(dict.fromkeys(target.registers + plan_watches(target.trace) + variables))
            log(f"[{target.id}] シミュレータで下調べ（止める行 {lines} とレジスタ名）")
            probe_text = mdb_run(mdb, probe_commands(target.device, elf, src, lines, names),
                                 work / "probe.log", timeout=300)
            errors = probe_errors(probe_text, target.source_name, target.device)
            if errors:
                raise MdbError("\n".join(errors))
        chunks = trace_chunks(target.device, elf, src, target.trace, target.registers,
                              target.wait_ms, variables, keypad=keypad_setup(target, work))
        stops = sum(1 for _, s in chunks if s)
        if session:
            log(f"[{target.id}] シミュレータで実行（最大 {stops} 回止めてレジスタを読む）")
            text = run_trace(mdb, chunks, trace_log, target.registers + variables, src)
        else:
            log(f"[{target.id}] シミュレータで実行（{stops} 回止めてレジスタを読む、一括）")
            text = mdb_run(mdb, script(chunks), trace_log,
                           timeout=300 + plan_wait_seconds(target.trace, target.wait_ms) + stops * 3)

    table = read_line_table(elf.with_suffix(".cmf"), src)
    steps = make_steps(target.trace, parse_records(text, target.registers + variables, src), registers,
                       writer_line=lambda a: line_at(table, writer_address(target.device, a)) if table else None,
                       line_of=lambda a: line_at(table, a) if table else None,
                       delay_var=FAST_VAR if target.fast_forward else None,
                       us_per_ms=skipped_per_ms(target.fast_forward) if target.fast_forward else 0,
                       skipped=skipped_stops(text))

    wave_results = []
    if waves:
        for w in target.waves:
            wave_log = work / f"wave_{w.pin}_{w.at}.log"
            if reuse_logs and wave_log.is_file():
                log(f"[{target.id}] 前回の {w.pin} の波形の記録を使う: {wave_log.name}")
                wave_text = wave_log.read_text(encoding="utf-8")
            else:
                log(f"[{target.id}] {w.pin} の波形を測る（{w.at} 行目で止めてから {w.samples} 命令）")
                wave_text = mdb_run(need_simulator(),
                                    wave_commands(target.device, elf, src, w.at, w.pin, w.samples, target.wait_ms),
                                    wave_log, timeout=300 + target.wait_ms / 1000 + w.samples * 0.5)
            wave_results.append(summarize(parse_samples(wave_text, w.pin), w.pin, w.at, w.samples))

    b = make_bundle(
        target, picdef, {"name": pack_name, "version": pack_version},
        {"mplabx": tc.mplabx_version, "xc8": xc8_version(tc.xc8)},
        read_source(target.source), registers, steps, wave_results, _date_of(trace_log),
    )
    bundle_io.write(project.bundle_path(target.id), b)
    log(f"[{target.id}] 記録を書いた: {project.bundle_path(target.id).relative_to(project.dir).as_posix()}"
        f"（{len(steps)} ステップ、波形 {len(wave_results)} 本）")
    return b
