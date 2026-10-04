"""Drive the MPLAB X command line debugger (mdb) in simulator mode and read what it prints."""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from .project import watch_list
from .toolchain import BELOW_NORMAL, NO_WINDOW, ascii_path

# mdb's Java starts with 1/64 of the machine's memory and grows lazily towards 1/4: 1.3 GB per run on a 32 GB
# machine. Starting small with a cap keeps a run near 0.5 GB with the same records (measured). Taken only when
# JAVA_TOOL_OPTIONS is not set already; PICVIEWER_JAVA_OPTIONS replaces it.
JAVA_OPTIONS = "-Xms64m -Xmx768m -XX:+UseSerialGC"


class MdbError(Exception):
    """mdb did not do what the plan expected."""


def _kill_tree(proc):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass


def _launch(mdb, *extra):
    """Popen arguments for mdb (a .bat, a program, or a list for tests) with the arguments after it."""
    env = dict(os.environ)
    env.setdefault("JAVA_TOOL_OPTIONS", os.environ.get("PICVIEWER_JAVA_OPTIONS", JAVA_OPTIONS))
    # below normal priority, inherited by the java process: a long batch must not make the machine sluggish
    if sys.platform == "win32":
        kw = {"creationflags": NO_WINDOW | BELOW_NORMAL, "env": env}
    else:
        kw = {"start_new_session": True, "preexec_fn": lambda: os.nice(10), "env": env}
    if isinstance(mdb, (list, tuple)):
        return {"args": [str(a) for a in [*mdb, *extra]], "shell": False, **kw}
    if str(mdb).lower().endswith(".bat"):
        # one more pair of quotes around the whole line is what cmd needs when both paths have spaces
        return {"args": " ".join(f'"{a}"' for a in [mdb, *extra]), "shell": True, **kw}
    return {"args": [str(a) for a in [mdb, *extra]], "shell": False, **kw}


def run(mdb, commands, log_path, timeout):
    """Write the commands to <log>.mdb, run mdb on it, save stdout to the log and return it."""
    log_path = Path(log_path)
    script = log_path.with_suffix(".mdb")
    # mdb.bat starts Java with -Dfile.encoding=UTF-8, so paths with Japanese names go through as UTF-8
    script.write_text("\n".join(commands) + "\n", encoding="utf-8", newline="\n")
    proc = subprocess.Popen(stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_launch(mdb, short(script)))
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
    return [f"Device {device}", "Hwtool SIM", f'Program "{short(elf).as_posix()}"']


def short(path):
    """An ASCII form of an existing path for mdb when there is one (mdb reads UTF-8, the short name is safer)."""
    return ascii_path(path) or Path(path)


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


def key_stimulus(device, row, col):
    """SCL for one held key of a scanned matrix: the column follows the row while the program drives it."""
    return "\n".join([
        f'testbench for "{device.lower()}" is',
        "begin",
        "    process is",
        "    begin",
        "        loop",
        f"            wait until {row} == '0';",
        f"            {col} <= '0';",
        f"            wait until {row} == '1';",
        f"            {col} <= '1';",
        "        end loop;",
        "    end process;",
        "end testbench;",
        "",
    ])


def trace_chunks(device, elf, source_name, plan, registers, wait_ms, variables=(), keypad=None):
    """The plan as (commands, stop) pairs in order. stop is None for commands that only set things up,
    otherwise the commands make one stop and end with Stopwatch, and stop says what it is:
    {"index": n-th stop of the plan, "kind": 'start', 'step', 'run' or 'write', "wait": ms,
    and for 'write' "segment": the n-th until_write (as bundle.expand_plan counts) and "until": its line}.

    keypad: {"cols": [column pins], "keys": {label: (row, column)}, "scl": {label: SCL file}}. The columns
    start high (the simulator has no pull-ups); a press loads the key's SCL with Stim, a release clears it.

    Breakpoints and watchpoints share one numbering (0, 1, 2, ...) and a number is never reused,
    so the ones in force can be deleted by number before the next run_to or until_write.
    A Wait that runs out prints nothing and leaves the target running, so every Wait is followed by
    Halt: on a halted target it does nothing, on a running one it stops where the program is.
    """
    prints = [f"Print {r}" for r in registers] + [f"Print {v}" for v in variables] + ["Stopwatch"]
    chunks, setup, active = [], header(device, elf), []
    next_no, stops, segment, started = 0, 0, 0, False
    held = None
    if keypad:
        setup.extend(f"write pin {c} high" for c in keypad["cols"])

    def stop(commands, **info):
        nonlocal setup, stops
        if setup:
            chunks.append((setup, None))
            setup = []
        chunks.append((commands, {"index": stops, **info}))
        stops += 1

    def let_go():
        nonlocal held
        if held is not None:
            setup.extend(["Stim", f"write pin {keypad['keys'][held][1]} high"])
            held = None

    def clear():
        setup.extend(f"Delete {n}" for n in active)
        active.clear()

    def add(command):
        nonlocal next_no
        setup.append(command)
        active.append(next_no)
        next_no += 1

    for action in plan:
        if "set" in action:
            setup.extend(f"write pin {pin} {level}" for pin, level in action["set"].items())
        elif "press" in action:
            let_go()
            setup.append(f'Stim "{short(keypad["scl"][action["press"]]).as_posix()}"')
            held = action["press"]
        elif "release" in action:
            let_go()
        elif "step" in action:
            for _ in range(action["step"]):
                stop(["Step", *prints], kind="step", wait=0)
        elif "run_to" in action:
            clear()
            add(f"Break {source_name}:{action['run_to']}")
            stop(["Continue" if started else "Run", f"Wait {wait_ms}", "Halt", *prints],
                 kind="run" if started else "start", wait=wait_ms)
            started = True
        elif "until_write" in action:
            clear()
            segment += 1
            for reg in watch_list(action):              # several watchpoints: a write to any of them stops
                add(f"Watch {reg} W")
            if action.get("until"):
                add(f"Break {source_name}:{action['until']}")
            wait = action.get("wait_ms", wait_ms)
            for _ in range(action["count"]):
                stop(["Continue", f"Wait {wait}", "Halt", *prints], kind="write", wait=wait,
                     segment=segment, until=action.get("until"))
    if setup:
        chunks.append((setup, None))
    return chunks


def script(chunks):
    """trace_chunks as one script for run()."""
    return [c for commands, _ in chunks for c in commands] + ["Quit"]


def trace_commands(device, elf, source_name, plan, registers, wait_ms, variables=(), keypad=None):
    """Commands for the whole plan as one script, and the kind of each stop ('start', 'step', 'run' or 'write')."""
    chunks = trace_chunks(device, elf, source_name, plan, registers, wait_ms, variables, keypad)
    return script(chunks), [s["kind"] for _, s in chunks if s]


# what a session writes into the log for a stop it did not make, so that the log alone tells the stops apart
NOT_MADE = "picviewer: stop {} not made (its until_write had ended)"
NOT_MADE_RE = re.compile(r"^picviewer: stop (\d+) not made", re.M)
STOPWATCH_RE = re.compile(r"Stopwatch cycle count = \d+")
# the prompt of mdb's terminal comes before the next line it prints when commands arrive through a pipe
PROMPT_RE = re.compile(r"^(?:\x1b\[[0-9;?]*[A-Za-z]|>\s?)+")
ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def skipped_stops(text):
    """The stops a session left out, as it wrote them into its log (none in a log from run())."""
    return {int(n) for n in NOT_MADE_RE.findall(text)}


def _clean(line):
    return PROMPT_RE.sub("", ESCAPE_RE.sub("", line.replace("\r", "")))


class Session:
    """One mdb kept open: commands go in through a pipe, and what it printed is read back as it comes,
    so the next commands can depend on how the last stop went."""

    def __init__(self, mdb, log_path):
        self.log_path = Path(log_path)
        self.lines, self.sent = [], []
        self.err = open(self.log_path.with_suffix(".err.log"), "wb")
        self.proc = subprocess.Popen(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err, **_launch(mdb))
        self.queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                self.queue.put(raw.decode("utf-8", errors="replace"))
        except (OSError, ValueError):          # the pipe closed under us after a kill
            pass
        self.queue.put(None)

    def send(self, commands):
        self.sent.extend(commands)
        self.proc.stdin.write("".join(c + "\n" for c in commands).encode("utf-8"))
        self.proc.stdin.flush()

    def note(self, text):
        self.lines.append(text + "\n")

    def read_until(self, pattern, seconds):
        """The lines printed up to the first one that matches. MdbError if mdb ends or the time runs out first."""
        deadline = time.monotonic() + seconds
        got = []
        while True:
            try:
                line = self.queue.get(timeout=max(deadline - time.monotonic(), 0.001))
            except queue.Empty:
                raise MdbError(f"mdb が {seconds:.0f} 秒たっても止まらない（止めた）。記録: {self.log_path}") from None
            if line is None:
                raise MdbError(f"mdb が途中で終わった。記録: {self.log_path}")
            line = _clean(line)
            self.lines.append(line)
            got.append(line)
            if pattern.match(line.strip()):
                return got

    def close(self, seconds=60, quit=True):
        """Quit (or kill), keep the rest of what was printed and write the log; returns the whole text."""
        try:
            if quit and self.proc.poll() is None:
                self.send(["Quit"])
                self.proc.wait(timeout=seconds)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if self.proc.poll() is None:
            _kill_tree(self.proc)
            self.proc.wait()
        while True:
            try:
                line = self.queue.get(timeout=5)
            except queue.Empty:
                break
            if line is None:
                break
            self.lines.append(_clean(line))
        for f in (self.proc.stdin, self.proc.stdout, self.err):
            try:
                f.close()
            except OSError:
                pass
        text = "".join(self.lines)
        self.log_path.write_text(text, encoding="utf-8")
        self.log_path.with_suffix(".mdb").write_text("\n".join(self.sent) + "\n", encoding="utf-8", newline="\n")
        return text


def run_trace(mdb, chunks, log_path, registers, source_name=None, margin=120):
    """Run trace_chunks in one mdb kept open and return its log, as run() would for trace_commands.

    A write stop is read before the next is sent. Two waits in a row that run out with nothing written, or a
    stop at the until line, end that until_write: the rest of its stops would each wait the whole time again
    (the program is waiting for an input the plan has not given yet) or only repeat the end. Those stops are
    not made, and the log says so (skipped_stops), so make_steps lines the records up with the plan.
    """
    session = Session(mdb, log_path)
    ended, quiet = set(), {}
    try:
        for commands, stop in chunks:
            if stop is None:
                session.send(commands)
                continue
            segment = stop.get("segment")
            if segment in ended:
                session.note(NOT_MADE.format(stop["index"]))
                continue
            session.send(commands)
            got = session.read_until(STOPWATCH_RE, stop["wait"] / 1000 + margin)
            if stop["kind"] != "write":
                continue
            rec = parse_records("".join(got), registers, source_name)[-1]
            quiet[segment] = quiet.get(segment, 0) + 1 if rec["timeout"] else 0
            if quiet[segment] >= 2 or (stop.get("until") and rec["line"] == stop["until"]):
                ended.add(segment)
    except BaseException:
        session.close(quit=False)
        raise
    # every stop was read back already: an mdb that ends badly after Quit has lost nothing
    return session.close()


def parse_records(text, registers, source_name=None):
    """One record per stop: source line, address, raw stopwatch reading and the register values.

    A stop in another file (XC8's own routines such as awmod.c for %) keeps that file's name in 'where'
    and no line: its line number belongs to that file, not to source_name.

    'timeout' is True when no "Single breakpoint" was printed for the stop. A breakpoint or watchpoint hit
    always prints it, so after Continue (or Run) its absence means our Halt stopped the program when the
    Wait ran out; make_steps applies this only to stops that came from Continue, not to steps.
    'line' is None where the ELF has no line for the address (code the compiler added); whether that
    is acceptable depends on the kind of stop, which make_steps knows.
    """
    records = []
    values, line, addr, want, file = {}, None, None, None, None
    hit = False
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("Single breakpoint"):
            hit = True
        m = re.match(r"source line:\s*(\d+)", s)
        if m:
            line = int(m.group(1))
            continue
        m = re.match(r"file:\s*(.+)$", s)
        if m:
            file = m.group(1).strip()
            continue
        m = re.match(r"address:\s*(0x[0-9a-fA-F]+)", s)
        if m:
            addr = m.group(1).lower()
            continue
        m = re.match(r"Stopwatch cycle count = (\d+)", s)
        if m:
            missing = [r for r in registers if r not in values]
            timeout = not hit
            if missing:
                raise MdbError(f"{len(records) + 1} 回目の停止で値が読めないレジスタ: {missing}")
            where = None
            if file and source_name and Path(file.replace("\\", "/")).name.lower() != source_name.lower():
                where, line = Path(file.replace("\\", "/")).name, None
            records.append({"line": line, "addr": addr, "reading": int(m.group(1)),
                            "values": {r: values[r] for r in registers}, "timeout": timeout, "where": where})
            values, line, addr, want, file = {}, None, None, None, None
            hit = False
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


def plan_wait_seconds(plan, default_wait_ms):
    """The longest the waits of a plan can take, in seconds (each run_to and each until_write stop)."""
    total = 0
    for a in plan:
        if "run_to" in a:
            total += default_wait_ms
        elif "until_write" in a:
            total += a["count"] * a.get("wait_ms", default_wait_ms)
    return total / 1000


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
