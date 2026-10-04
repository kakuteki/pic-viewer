"""picviewer init: write a picviewer.json for a C source by reading the code and XC8's line table.

The guess fits the usual exercise board: LEDs (or a one-digit 7-segment LED) on the port the program
writes most, push switches on the input pins it reads. The plan releases every switch, steps through the
straight lines at the start of main, then follows the writes to the LED port while it presses each switch
in turn. Which value means "pressed" is read from the conditions the program tests the pin in: the value
that makes an if true, or that ends an empty while loop (a wait), is the pressed one.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from .bundle import read_source
from .compiler import CompileError, compile_target, compiled_name
from .linetab import read_line_table
from .picdef import PicDef
from .project import ProjectError, Target, normalize_device, parse_plan
from .toolchain import find_device_file

ASSIGN = r"(?:=|<<=|>>=|\|=|&=|\^=|\+=|-=)(?!=)"
LOOP_RE = re.compile(r"\b(while|for|do)\b")
CONTROL_RE = re.compile(r"\b(if|while|for|do|switch|goto)\b")
CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
NOT_CALLS = {"if", "while", "for", "switch", "return", "sizeof", "__delay_ms", "__delay_us", "_delay", "NOP",
             "CLRWDT", "SLEEP", "__nop", "di", "ei"}
SETUP_REGS = ["OSCCON", "ADCON1", "ANSEL", "ANSELH", "ANSELA", "ANSELB", "ANSELC", "ANSELD", "ANSELE"]
PORTS = "ABCDE"
# a..g shapes of 0 to 9 with bit 0 = a; five of them among the constants mean a 7-segment table
SEG7_CATHODE = {0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x27, 0x7F, 0x6F, 0x67}
SEG7_ANODE = {~s & 0xFF for s in SEG7_CATHODE}            # 0xC0, 0xF9, 0xA4 ...: a 0 lights the segment
# letters seen on such displays (H, L, n, _, b, C, -, P, E, F), for constants written straight to a port
SEG7_LETTERS = {0x76, 0x38, 0x54, 0x08, 0x7C, 0x39, 0x40, 0x73, 0x79, 0x71}
SEG7_ANY = SEG7_CATHODE | SEG7_ANODE | SEG7_LETTERS | {~s & 0xFF for s in SEG7_LETTERS}
# patterns an LED chaser writes too: they say nothing about a 7-segment display
SEG7_AMBIGUOUS = {0x00, 0xFF} | {1 << b for b in range(8)} | {~(1 << b) & 0xFF for b in range(8)}
WAIT_MS = 3000           # real time to wait for one write; the simulator runs over 10 s of a 4 MHz PIC in it
RUN_WAIT_MS = 20000      # real time to reach a run_to line
BANK_SIZE = 4            # switches assumed on a port read as a whole, when TRIS does not say


def c_int(s):
    s = s.strip().lower().rstrip("ul")
    if s.startswith("0b"):
        return int(s[2:], 2)
    if s.startswith("0x"):
        return int(s[2:], 16)
    return int(s, 8) if len(s) > 1 and s.startswith("0") else int(s)


def blank_code(text):
    """Lines of the text with comments and the insides of string and character literals turned into
    spaces; the lines keep their numbers and lengths."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
        elif text[i] in "\"'":
            j = i + 1
            while j < n and text[j] not in (text[i], "\n"):
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            seg = text[i:j]
            out.append(seg[0] + re.sub(r"[^\n]", " ", seg[1:-1]) + seg[-1] if len(seg) > 1 else seg)
        else:
            j = i + 1
            out.append(text[i])
        i = j
    return "".join(out).split("\n")


def comment_of(orig, code):
    """The // comment of a line: the first // from which the blanked code is empty."""
    p = orig.find("//")
    while p >= 0 and code[p:].strip():
        p = orig.find("//", p + 2)
    return orig[p + 2:].strip() if p >= 0 else ""


def read_aliases(code):
    """Object-like #define names and their values (function-like macros are left out)."""
    out = {}
    for line in code:
        m = re.match(r"\s*#\s*define\s+([A-Za-z_]\w*)\s+(.+?)\s*$", line)
        if m and m.group(1) != "_XTAL_FREQ":
            out[m.group(1)] = m.group(2)
    return out


def expand(code, aliases):
    def sub(m):
        v = aliases.get(m.group(0))
        if v is None:
            return m.group(0)
        return v if re.fullmatch(r"[\w.]+", v) else f"({v})"
    for _ in range(4):           # an alias may name another alias
        new = re.sub(r"\b[A-Za-z_]\w*\b", sub, code)
        if new == code:
            break
        code = new
    # the old single-bit names (RA0, RC7) that XC8 still declares are port bits too
    return re.sub(r"(?<![.\w])R([A-E])([0-7])\b", r"PORT\1bits.R\1\2", code)


def conditions(code, next_code):
    """(start, end, pressed_is_true) for each if/while condition on a line: the text between the
    parentheses, and whether the value that makes it true means "pressed" (an if, or a while with a body)
    or the value that makes it false does (an empty while: waiting until the switch changes)."""
    out = []
    for m in re.finditer(r"\b(if|while)\s*\(", code):
        depth, j = 1, m.end()
        while j < len(code) and depth:
            depth += {"(": 1, ")": -1}.get(code[j], 0)
            j += 1
        if depth:
            continue                                   # the condition goes on past the line: skip it
        start, end = m.end(), j - 1
        rest = code[j:].strip()
        do_while = code[:m.start()].strip().endswith("}")
        empty = m.group(1) == "while" and not do_while and (
            rest.startswith(";") or re.fullmatch(r"\{\s*\}.*", rest) is not None
            or (rest == "{" and next_code.strip().startswith("}")))
        out.append((start, end, not empty))
    return out


def true_value(cond, s, e):
    """The value of the pin read at cond[s:e] that makes the condition true, or None if it does not decide."""
    expr = cond[:s] + " X " + cond[e:]
    expr = re.sub(r"\bPORT[A-E]bits\.R[A-E]\d\b", " O ", expr)
    if re.search(r"\b(PORT|LAT)[A-E]\b", expr):
        return None                                    # a whole port in the test: not a one-bit question
    expr = expr.replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"!(?!=)", " not ", expr).replace("~", " not ")
    expr = re.sub(r"\b(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)[uUlL]*\b", lambda m: str(c_int(m.group(1))), expr)
    if re.search(r"[A-Za-z_]\w*", re.sub(r"\b(X|O|not|and|or)\b", " ", expr)):
        return None                                    # other names (variables): the test is not about the pin
    found = set()
    for other in (0, 1):
        try:
            t = [bool(eval(expr, {"__builtins__": {}}, {"X": x, "O": other})) for x in (0, 1)]   # noqa: S307
        except Exception:                              # anything this simple translation cannot read
            return None
        if t[0] != t[1]:
            found.add(1 if t[1] else 0)
    return found.pop() if len(found) == 1 else None


def state_word(name):
    """'on' or 'off' when a macro's name says which switch state it stands for (ON, OFF, SW0_ON, sw1_OFF)."""
    m = re.search(r"(?:^|_)(on|off)$", name, re.I)
    return m.group(1).lower() if m else None


def analyze(text):
    """What the code says about the board: written ports, read pins and how they are tested, the first
    loop of main, the straight lines before it, and the line comments."""
    orig = text.split("\n")
    code = blank_code(text)
    aliases = read_aliases(code)
    info = {"main": None, "loop": None, "setup_end": None, "wait_loop": False, "fosc": None, "delay": False,
            "seg7": None, "writes": Counter(), "reads": {}, "votes": {}, "port_reads": {}, "tris": {},
            "names": {}, "notes": {}, "used": set(), "table_writes": Counter(), "bit_writes": {}, "digit_on": {},
            "int_edge": None, "shape_consts": {}}
    joined = "\n".join(code)
    m = re.search(r"#\s*define\s+_XTAL_FREQ\s+(\w+)", joined)
    if m:
        try:
            info["fosc"] = c_int(m.group(1))
        except ValueError:
            pass
    info["delay"] = bool(re.search(r"\b__delay_ms\s*\(", joined))
    body = "\n".join(c for c in code if not c.lstrip().startswith("#"))
    shapes = {c_int(v) for v in re.findall(r"\b0[xX][0-9a-fA-F]+\b|\b0[bB][01]+\b", body)}
    cathode, anode = len(shapes & SEG7_CATHODE), len(shapes & SEG7_ANODE)
    if max(cathode, anode) >= 5 or re.search(r"seg", body, re.I):
        info["seg7"] = "anode" if anode >= 5 and anode > cathode else "cathode"
    for name, value in aliases.items():
        pm = re.fullmatch(r"[!~]?\s*\(?\s*PORT([A-E])bits\.R[A-E](\d)\s*\)?", value)
        if pm:
            info["names"].setdefault(f"R{pm.group(1)}{pm.group(2)}", name)
    functions = set(re.findall(r"^\s*(?:static\s+)?(?:void|char|int|long|short|bit|__bit|unsigned\s+\w+|signed\s+\w+)"
                               r"\s+\**\s*([A-Za-z_]\w*)\s*\([^;]*$", joined, re.M)) - {"main"}
    if re.search(r"\bINTE\s*=\s*1\b|\bINTCONbits\.INTE\s*=\s*1\b", body):
        info["int_edge"] = 0 if re.search(r"\bINTEDG\s*=\s*0\b", body) else 1
    for i, c in enumerate(code, start=1):
        if re.match(r"\s*(?:void|int)\s+main\s*\(", c):
            info["main"] = i
            break
    lines = [(no, c) for no, c in enumerate(code, start=1) if c.strip() and not c.lstrip().startswith("#")]
    last_table = None
    for k, (no, c) in enumerate(lines):
        full = expand(c, aliases)
        nxt = expand(lines[k + 1][1], aliases) if k + 1 < len(lines) else ""
        info["used"] |= set(re.findall(r"\b(?:OSCCON|ADCON1|ANSEL[A-E]?|ANSELH)\b", full))
        info["used"] |= {f"{a}{p}" for a, p in re.findall(r"\b(PORT|TRIS|LAT)([A-E])(?:bits)?\b", full)}
        in_main = info["main"] is not None and no > info["main"]
        if in_main and info["loop"] is None and LOOP_RE.search(full):
            info["loop"] = no
            cond = re.search(r"\bwhile\s*\((.*)\)", full)
            info["wait_loop"] = bool(cond and re.search(r"\bPORT[A-E]", cond.group(1)))
        if in_main and info["setup_end"] is None:
            calls = {n for n in CALL_RE.findall(full) if n not in NOT_CALLS}
            if CONTROL_RE.search(full) or calls & functions or calls - functions - NOT_CALLS:
                info["setup_end"] = no
        note = comment_of(orig[no - 1], c)
        if note and re.search(r"[^\s=*/-]", note):
            info["notes"][str(no)] = note
        for m in re.finditer(rf"\b(PORT|LAT)([A-E])\s*{ASSIGN}\s*([^;]*)", full):
            if re.fullmatch(r"\(?\s*(?:0+|0[xX]0+|0[bB]0+)\s*\)?", m.group(3).strip()):
                continue        # clearing a port at start-up says nothing about what is on it
            info["writes"][m.group(1) + m.group(2)] += 1
            const = re.fullmatch(r"\(?\s*(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)\s*\)?", m.group(3).strip())
            shaped = const is not None and c_int(const.group(1)) in SEG7_ANY
            if shaped and c_int(const.group(1)) not in SEG7_AMBIGUOUS:
                info["shape_consts"].setdefault(m.group(1) + m.group(2), set()).add(c_int(const.group(1)))
            if "[" in m.group(3) or re.search(r"seg", m.group(3), re.I) or shaped:
                info["table_writes"][m.group(1) + m.group(2)] += 1      # a digit shape, from a table or as is
                last_table = m.group(1) + m.group(2)
        for m in re.finditer(rf"\b(PORT|LAT)([A-E])bits\.(?:R|LAT)[A-E](\d)\s*=\s*\(?\s*([01])\s*\)?\s*;", full):
            pin = f"R{m.group(2)}{m.group(3)}"
            info["bit_writes"].setdefault(pin, set()).add(int(m.group(4)))
            if last_table and last_table[-1] != m.group(2):
                # the first digit-pin write after a shape is written switches that digit on
                info["digit_on"].setdefault(pin, []).append(int(m.group(4)))
                last_table = None
        for m in re.finditer(rf"\b(PORT|LAT)([A-E])bits\.\w+\s*{ASSIGN}", full):
            info["writes"][m.group(1) + m.group(2)] += 1
        for m in re.finditer(r"\bTRIS([A-E])\s*=\s*(0[xXbB][0-9a-fA-F]+|\d+)\s*;", full):
            info["tris"][m.group(1)] = c_int(m.group(2))
        rhs = re.sub(rf"\b(?:PORT|LAT)[A-E](?:bits\.\w+)?\s*{ASSIGN}", lambda m: " " * len(m.group(0)), full)
        conds = conditions(rhs, nxt)
        raw_conds = conditions(c, lines[k + 1][1] if k + 1 < len(lines) else "")
        for m in re.finditer(r"([!~])?\s*\(?\s*\bPORT([A-E])bits\.R[A-E](\d)\b", rhs):
            pin = f"R{m.group(2)}{m.group(3)}"
            s = m.start() + len(m.group(0)) - len(f"PORT{m.group(2)}bits.R{m.group(2)}{m.group(3)}")
            info["reads"][pin] = info["reads"].get(pin, False) or bool(m.group(1))
            for n, (start, end, pressed_is_true) in enumerate(conds):
                if start <= s < end:
                    v = true_value(rhs[start:end], s - start, m.end() - start)
                    if v is None:
                        continue
                    # a test written with ON or OFF in a name says itself which state it is about;
                    # otherwise an if (or a loop with a body) acts when pressed, an empty loop waits for it
                    words = {state_word(w) for w in re.findall(r"[A-Za-z_]\w*", c[raw_conds[n][0]:raw_conds[n][1]])
                             if w in aliases} - {None} if n < len(raw_conds) else set()
                    if words == {"on"}:
                        pressed_is_true = True
                    elif words == {"off"}:
                        pressed_is_true = False
                    info["votes"].setdefault(pin, []).append(v if pressed_is_true else 1 - v)
        for m in re.finditer(r"([!~])?\s*\(?\s*\bPORT([A-E])\b(?!bits)", rhs):
            info["port_reads"][m.group(2)] = info["port_reads"].get(m.group(2), False) or bool(m.group(1))
    # digit or letter shapes written as constants (LATD = 0xC0; LATD = 0x89;): two different ones are enough
    if not info["seg7"]:
        for consts in info["shape_consts"].values():
            if len(consts) >= 2:
                anode = len(consts & (SEG7_ANODE | {~s & 0xFF for s in SEG7_LETTERS}))
                info["seg7"] = "anode" if anode * 2 > len(consts) else "cathode"
    return info


def bank_pins(port, bank, tris, names=()):
    """Pins of a port read as a whole: the given bank, else the pins the program names on that port
    (#define sw0 PORTAbits.RA0), else every input bit TRIS sets, else bits 0 to 3."""
    if bank:
        return [p for p in bank if p[1] == port]
    named = sorted(p for p in names if p[1] == port and (port not in tris or (tris[port] >> int(p[2])) & 1))
    if named:
        return named
    if port in tris:
        return [f"R{port}{b}" for b in range(8) if (tris[port] >> b) & 1]
    return [f"R{port}{b}" for b in range(BANK_SIZE)]


def pressed_low(pin, info, polarity):
    """True when the switch on the pin reads 0 while pressed."""
    if polarity != "auto":
        return polarity == "low"
    if pin == "RB0" and info["int_edge"] is not None:
        return info["int_edge"] == 0          # the press is the edge that raises the INT interrupt
    votes = info["votes"].get(pin, [])
    if votes and votes.count(0) != votes.count(1):
        return votes.count(0) > votes.count(1)
    return info["reads"].get(pin, False)            # no clear test: a read through ! or ~ means pressed = 0


def guess(text, table, device_regs, device_pins, polarity="auto", bank=None, writes=16, wait_ms=WAIT_MS):
    """The target part of picviewer.json (without id, device and source), and a few facts for the log."""
    info = analyze(text)
    if info["main"] is None:
        raise ProjectError("main が見つからない")
    lines = sorted({ln for _, ln in table if ln > info["main"]})
    if not lines:
        raise ProjectError("main の後に命令のある行が無い（XC8 の行の表が空）")
    first = lines[0]
    stop = min(x for x in (info["loop"], info["setup_end"], 10 ** 9) if x is not None)
    setup = [ln for ln in lines if ln < stop]
    out = out_reg = None
    if info["writes"]:
        port_count = Counter()
        for reg, n in info["writes"].items():
            port_count[reg[-1]] += n
        out = port_count.most_common(1)[0][0]
        out_reg = max((r for r in info["writes"] if r[-1] == out), key=lambda r: info["writes"][r])

    if info["table_writes"]:
        # the port written from a table of shapes carries the segments, however often other ports are written
        out_reg = info["table_writes"].most_common(1)[0][0]
        out = out_reg[-1]
    mux = None
    if info["seg7"] and info["table_writes"]:
        toggled = [p for p, vals in info["bit_writes"].items() if vals == {0, 1} and p[1] != out]
        if toggled:
            sel = Counter(p[1] for p in toggled).most_common(1)[0][0]
            pins = sorted(p for p in info["bit_writes"] if p[1] == sel)     # also digits the program keeps dark
            votes = {pin: info["digit_on"].get(pin, []) for pin in pins}
            known = [v for vs in votes.values() for v in vs]
            usual = max(set(known), key=known.count) if known else 0
            digits = [{"pin": pin, "active": "low" if (max(set(vs), key=vs.count) if vs else usual) == 0 else "high"}
                      for pin, vs in votes.items()]
            sel_reg = max((r for r in info["writes"] if r[-1] == sel), key=lambda r: info["writes"][r])
            mux = {"seg_reg": out_reg, "sel_reg": sel_reg, "digits": digits}

    def input_on_out_port(pin):
        return out in info["tris"] and (info["tris"][out] >> int(pin[2])) & 1 == 1
    digit_pins = {d["pin"] for d in mux["digits"]} if mux else set()
    reads = {p: low for p, low in info["reads"].items()
             if (p[1] != out or input_on_out_port(p)) and p not in digit_pins}
    for port, low in info["port_reads"].items():
        if port != out:
            for p in bank_pins(port, bank, info["tris"], info["names"]):
                reads[p] = reads.get(p, False) or low
    info["reads"] = reads
    switches = []
    for pin in sorted(p for p in reads if p in device_pins):
        low = pressed_low(pin, info, polarity)
        switches.append({"pin": pin, "active": "low" if low else "high", "label": info["names"].get(pin, pin)})

    def level(s, pressed):
        return int(pressed) ^ int(s["active"] == "low")

    plan = []
    if switches:
        plan.append({"set": {s["pin"]: level(s, False) for s in switches}, "note": "スイッチは全部離してある"})
    plan.append({"run_to": first})
    if setup:
        plan.append({"step": len(setup)})
    watch = [mux["seg_reg"], mux["sel_reg"]] if mux else out_reg
    if mux:
        writes = max(writes, 40)              # about three frames of a 4-digit display, every write a stop
    if out:
        if not switches:
            plan.append({"until_write": watch, "count": writes, "wait_ms": wait_ms})
        else:
            each = max(40 if mux else 2, writes // (2 * len(switches) + 1))
            if not info["wait_loop"]:
                plan.append({"until_write": watch, "count": each, "wait_ms": wait_ms})
            for s in switches:
                plan.append({"set": {s["pin"]: level(s, True)}, "note": f"{s['label']} を押す"})
                plan.append({"until_write": watch, "count": each, "wait_ms": wait_ms})
                plan.append({"set": {s["pin"]: level(s, False)}, "note": f"{s['label']} を離す"})
                plan.append({"until_write": watch, "count": each, "wait_ms": wait_ms})
    parse_plan(plan, "init")              # the same checks as a hand-written file

    ports = sorted({out} if out else set()) + sorted({s["pin"][1] for s in switches}) \
        + ([mux["sel_reg"][-1]] if mux else [])
    want = [r for r in SETUP_REGS if r in info["used"]]
    for kind in ("TRIS", "LAT", "PORT"):
        want += [f"{kind}{p}" for p in PORTS
                 if f"{kind}{p}" in info["used"] or (kind != "LAT" and p in ports) or f"{kind}{p}" == out_reg]
    registers = [r for r in want if r in device_regs] or ["STATUS"]

    if mux:
        circuit = {"type": "seg7mux", "port": out, "digits": mux["digits"],
                   **({"common": "anode"} if info["seg7"] == "anode" else {})}
    elif out and info["seg7"]:
        circuit = {"type": "seg7", "port": out, **({"common": "anode"} if info["seg7"] == "anode" else {})}
    elif out:
        circuit = {"type": "leds", "port": out}
    else:
        circuit = {"type": "pins"}
    if switches and circuit["type"] == "seg7mux":
        circuit = [circuit, {"type": "leds", "port": switches[0]["pin"][1], "bits": [], "switches": switches}]
    elif switches and circuit["type"] != "pins":
        circuit["switches"] = switches
    roles = []
    if mux:
        roles.append(f"PORT{out} に {len(mux['digits'])} 桁の 7 セグメント LED（桁は "
                     + "、".join(d["pin"] for d in mux["digits"]) + "）")
    elif out:
        roles.append(f"PORT{out} に {'7 セグメント LED' if circuit['type'] == 'seg7' else 'LED'}")
    if switches:
        roles.append("スイッチ " + "、".join(
            f"{s['label']}（{s['pin']}、押すと {level(s, True)}）" for s in switches))
    target = {}
    if info["fosc"]:
        target["fosc_hz"] = info["fosc"]
    target["summary"] = ("、".join(roles) or "ポートへの書き込みが見つからない") + "（init がソースから読み取った割り当て）"
    if info["delay"]:
        target["fast_forward"] = 1000
    target.update({"wait_ms": RUN_WAIT_MS, "registers": registers, "circuit": circuit, "trace": plan,
                   "notes": info["notes"]})
    kind = circuit[0]["type"] if isinstance(circuit, list) else circuit["type"]
    facts = {"first": first, "setup": len(setup), "out": "+".join(watch) if mux else out_reg, "seg7": kind.startswith("seg7"),
             "switches": [f"{s['pin']}({s['active']})" for s in switches], "wait_loop": info["wait_loop"]}
    return target, facts


def device_names(picdef):
    regs = set()
    for name in SETUP_REGS + [f"{k}{p}" for k in ("TRIS", "PORT", "LAT") for p in PORTS] + ["STATUS"]:
        try:
            if picdef.sfr(name)["width"] <= 8:
                regs.add(name)
        except KeyError:
            pass
    pins = {n.upper() for names in picdef.pins() for n in names}
    return regs, pins


def init_source(source, project_dir, device, tc, *, polarity="auto", bank=None, writes=16, force=False):
    """Compile once for the line table, then write <project_dir>/picviewer.json. Returns facts for the log."""
    source = Path(source).resolve()
    project_dir = Path(project_dir).resolve()
    out_file = project_dir / "picviewer.json"
    if out_file.exists() and not force:
        raise ProjectError(f"{out_file} はもうある（作り直すなら --force）")
    device = normalize_device(device)
    picdef = PicDef(find_device_file(device, tc.pack_dirs)[0])
    regs, pins = device_names(picdef)
    probe = Target(id="init", device=device, source=source, source_name=source.name, registers=[], trace=[])
    project_dir.mkdir(parents=True, exist_ok=True)
    xc8_args = []
    try:
        elf = compile_target(tc.require_xc8(), probe, project_dir / "build" / "init")
    except CompileError as first:
        # old code such as `dig3=3;` outside any function (implicit int) builds only as C90
        probe.xc8_args = ["-std=c90"]
        try:
            elf = compile_target(tc.require_xc8(), probe, project_dir / "build" / "init")
        except CompileError:
            raise first from None
        xc8_args = probe.xc8_args
    table = read_line_table(elf.with_suffix(".cmf"), compiled_name(probe))
    target, facts = guess("\n".join(read_source(source)), table, regs, pins, polarity, bank, writes)
    try:
        rel = Path(os.path.relpath(source, project_dir)).as_posix()
    except ValueError:                   # another drive
        rel = source.as_posix()
    if xc8_args:
        target["xc8_args"] = xc8_args
    project = {"title": source.stem, "output": f"{source.stem}_viewer.html",
               "targets": [{"id": device.lower(), "device": device, "source": rel, **target}]}
    out_file.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return facts
