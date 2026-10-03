"""Drive the MPLAB X command line debugger (mdb) in simulator mode and read what it prints."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .toolchain import NO_WINDOW


class MdbError(Exception):
    """mdb did not do what the plan expected."""


def _kill_tree(proc):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        import os
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass


def run(mdb, commands, log_path, timeout):
    """Write the commands to <log>.mdb, run mdb on it, save stdout to the log and return it."""
    log_path = Path(log_path)
    script = log_path.with_suffix(".mdb")
    script.write_text("\n".join(commands) + "\n", encoding="ascii", newline="\n")
    if str(mdb).lower().endswith(".bat"):
        # one more pair of quotes around the whole line is what cmd needs when both paths have spaces
        args, shell = f'"{mdb}" "{script}"', True
    else:
        args, shell = [str(mdb), str(script)], False
    kw = {"creationflags": NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    proc = subprocess.Popen(args, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.communicate()
        raise MdbError(f"mdb が {timeout} 秒で終わらなかった（止めた）。記録: {log_path}") from None
    text = out.decode("utf-8", errors="replace")
    log_path.write_text(text, encoding="utf-8")
    log_path.with_suffix(".err.log").write_bytes(err)
    if proc.returncode != 0:
        raise MdbError(f"mdb が終了コード {proc.returncode} で終わった。記録: {log_path}")
    return text


def header(device, elf):
    return [f"Device {device}", "Hwtool SIM", f'Program "{Path(elf).as_posix()}"']


def probe_commands(device, elf, source_name, lines, registers):
    cmds = header(device, elf)
    cmds += [f"Break {source_name}:{n}" for n in lines]
    cmds += [f"Print {r}" for r in registers]
    cmds.append("Quit")
    return cmds


def probe_errors(text, source_name, device):
    """Problems a probe run reveals before the long run: lines without code, unknown register names."""
    errors = []
    if "Program succeeded" not in text:
        tail = "\n".join(text.strip().splitlines()[-8:])
        errors.append(f"シミュレータへの書き込みに失敗した:\n{tail}")
    bad = sorted({int(m.group(1)) for m in re.finditer(r":(\d+) could not be resolved", text)})
    if bad:
        errors.append(f"{source_name} の {bad} 行目には止められない（命令の無い行）。文のある行を選ぶ")
    unknown = sorted(set(re.findall(r"^\s*(\w+)=Symbol does not exist", text, re.M)))
    if unknown:
        errors.append(f"{device} に無いレジスタ名: {unknown}")
    return errors


def trace_commands(device, elf, source_name, plan, registers, wait_ms):
    """Commands for the whole plan, and the kind of each stop ('start', 'step' or 'run')."""
    prints = [f"Print {r}" for r in registers] + ["Stopwatch"]
    first = plan[0]["run_to"]
    cmds = header(device, elf) + [f"Break {source_name}:{first}", "Run", f"Wait {wait_ms}", *prints]
    kinds = ["start"]
    breakpoint_no = 0          # mdb numbers breakpoints 0, 1, 2, ... and does not reuse a number
    for action in plan[1:]:
        if "step" in action:
            for _ in range(action["step"]):
                cmds += ["Step", *prints]
                kinds.append("step")
        else:
            cmds += [f"Delete {breakpoint_no}", f"Break {source_name}:{action['run_to']}",
                     "Continue", f"Wait {wait_ms}", *prints]
            breakpoint_no += 1
            kinds.append("run")
    cmds.append("Quit")
    return cmds, kinds


def parse_records(text, registers):
    """One record per stop: source line, address, raw stopwatch reading and the register values."""
    records = []
    values, line, addr, want = {}, None, None, None
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        m = re.match(r"source line:\s*(\d+)", s)
        if m:
            line = int(m.group(1))
            continue
        m = re.match(r"address:\s*(0x[0-9a-fA-F]+)", s)
        if m:
            addr = m.group(1).lower()
            continue
        m = re.match(r"Stopwatch cycle count = (\d+)", s)
        if m:
            missing = [r for r in registers if r not in values]
            if line is None:
                raise MdbError(f"{len(records) + 1} 回目の停止で行番号が読めない（待ち時間内に止まらなかったか、"
                               "行番号の無いところで止まった）")
            if missing:
                raise MdbError(f"{len(records) + 1} 回目の停止で値が読めないレジスタ: {missing}")
            records.append({"line": line, "addr": addr, "reading": int(m.group(1)),
                            "values": {r: values[r] for r in registers}})
            values, line, addr, want = {}, None, None, None
            continue
        m = re.fullmatch(r"(\w+)=(.*)", s)
        if m:
            name, rest = m.group(1), m.group(2).strip()
            if rest.startswith("Symbol does not exist"):
                raise MdbError(f"レジスタ {name} がシミュレータに無い")
            if rest == "":
                want = name
            elif re.fullmatch(r"-?\d+", rest):
                values[name], want = int(rest), None
            continue
        if want and re.fullmatch(r"-?\d+", s):
            values[want], want = int(s), None
    return records


def absolute_cycles(records, kinds):
    """Cycles since reset at each stop.

    The mdb stopwatch restarts at every Run or Continue and keeps counting across Step,
    so a reading is relative to the last time the program was resumed.
    """
    if len(records) != len(kinds):
        raise MdbError(f"止まった回数 {len(records)} が計画の {len(kinds)} 回と合わない")
    out, base, prev = [], 0, 0
    for rec, kind in zip(records, kinds):
        if kind == "start":
            base = 0
        elif kind == "run":
            base = prev
        now = base + rec["reading"]
        if now < prev:
            raise MdbError(f"命令サイクル数が戻った（{prev} から {now}）。ストップウォッチの扱いが想定と違う")
        out.append(now)
        prev = now
    return out
