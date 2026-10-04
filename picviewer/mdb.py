"""Drive the MPLAB X command line debugger (mdb) in simulator mode and read what it prints."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .toolchain import BELOW_NORMAL, NO_WINDOW


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
    # below normal priority, inherited by the java process: a long batch must not make the machine sluggish
    if sys.platform == "win32":
        kw = {"creationflags": NO_WINDOW | BELOW_NORMAL}
    else:
        import os
        kw = {"start_new_session": True, "preexec_fn": lambda: os.nice(10)}
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


def trace_commands(device, elf, source_name, plan, registers, wait_ms, variables=()):
    """Commands for the whole plan, and the kind of each stop ('start', 'step', 'run' or 'write').

    Breakpoints and watchpoints share one numbering (0, 1, 2, ...) and a number is never reused,
    so the ones in force can be deleted by number before the next run_to or until_write.
    A Wait that runs out prints nothing and leaves the target running, so every Wait is followed by
    Halt: on a halted target it does nothing, on a running one it stops where the program is.
    """
    prints = [f"Print {r}" for r in registers] + [f"Print {v}" for v in variables] + ["Stopwatch"]
    cmds = header(device, elf)
    kinds, active = [], []
    next_no, started = 0, False

    def clear():
        cmds.extend(f"Delete {n}" for n in active)
        active.clear()

    def add(command):
        nonlocal next_no
        cmds.append(command)
        active.append(next_no)
        next_no += 1

    for action in plan:
        if "set" in action:
            cmds.extend(f"write pin {pin} {level}" for pin, level in action["set"].items())
        elif "step" in action:
            for _ in range(action["step"]):
                cmds += ["Step", *prints]
                kinds.append("step")
        elif "run_to" in action:
            clear()
            add(f"Break {source_name}:{action['run_to']}")
            cmds += ["Continue" if started else "Run", f"Wait {wait_ms}", "Halt", *prints]
            kinds.append("run" if started else "start")
            started = True
        elif "until_write" in action:
            clear()
            add(f"Watch {action['until_write']} W")
            if action.get("until"):
                add(f"Break {source_name}:{action['until']}")
            wait = action.get("wait_ms", wait_ms)
            for _ in range(action["count"]):
                cmds += ["Continue", f"Wait {wait}", "Halt", *prints]
                kinds.append("write")
    cmds.append("Quit")
    return cmds, kinds


def parse_records(text, registers):
    """One record per stop: source line, address, raw stopwatch reading and the register values.

    'timeout' marks a stop made by our Halt after a Wait ran out: mdb prints "Simulator halted" right
    after the Halt, and no "Single breakpoint" (which a breakpoint or watchpoint hit always prints).
    """
    records = []
    values, line, addr, want = {}, None, None, None
    hit = halted = False
    prev = ""
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("Single breakpoint"):
            hit = True
        elif s == "Simulator halted" and prev == "Halt":
            halted = True
        prev = s
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
            timeout = halted and not hit
            if line is None and not timeout:
                raise MdbError(f"{len(records) + 1} 回目の停止で行番号が読めない（行番号の無いところで止まった）")
            if missing:
                raise MdbError(f"{len(records) + 1} 回目の停止で値が読めないレジスタ: {missing}")
            records.append({"line": line, "addr": addr, "reading": int(m.group(1)),
                            "values": {r: values[r] for r in registers}, "timeout": timeout})
            values, line, addr, want = {}, None, None, None
            hit = halted = False
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
        elif kind in ("run", "write"):
            base = prev
        now = base + rec["reading"]
        if now < prev:
            raise MdbError(f"命令サイクル数が戻った（{prev} から {now}）。ストップウォッチの扱いが想定と違う")
        out.append(now)
        prev = now
    return out
