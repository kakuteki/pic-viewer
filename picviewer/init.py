"""picviewer init: write a picviewer.json for a C source by reading the code and XC8's line table.

The guess fits the usual exercise board: LEDs (or a one-digit 7-segment LED) on the port the program
writes most, push switches on the input pins it reads. The plan releases every switch, steps through the
lines before the first loop, then follows the writes to the LED port while it presses each switch in turn.
A switch is taken as active low (pressed = 0) when the program reads it through ! or ~, and as active high
otherwise, so that "pressed" on the page means what the program's author meant by it.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from .bundle import read_source
from .compiler import compile_target, compiled_name
from .linetab import read_line_table
from .picdef import PicDef
from .project import ProjectError, Target, normalize_device, parse_plan
from .toolchain import find_device_file

ASSIGN = r"(?:=|<<=|>>=|\|=|&=|\^=|\+=|-=)(?!=)"
LOOP_RE = re.compile(r"\b(while|for|do)\b")
SETUP_REGS = ["OSCCON", "ANSEL", "ANSELH", "ANSELA", "ANSELB", "ANSELC", "ANSELD", "ANSELE"]
PORTS = "ABCDE"
# a..g shapes of 0 to 9 with bit 0 = a: five of them among the constants mean a 7-segment table
SEG7_SHAPES = {0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x27, 0x7F, 0x6F, 0x67}
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


def blank_comments(text):
    """Lines of the text with /* */ and // comments removed; the line count does not change."""
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return [line.split("//", 1)[0] for line in text.split("\n")]


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
    return code


def analyze(text):
    """What the code says about the board: written ports, read pins, the first loop, notes."""
    orig = text.split("\n")
    code = blank_comments(text)
    aliases = read_aliases(code)
    info = {"main": None, "loop": None, "wait_loop": False, "fosc": None, "delay": False, "seg7": False,
            "writes": Counter(), "reads": {}, "port_reads": {}, "tris": {}, "names": {}, "notes": {},
            "used": set()}
    m = re.search(r"#\s*define\s+_XTAL_FREQ\s+(\w+)", "\n".join(code))
    if m:
        try:
            info["fosc"] = c_int(m.group(1))
        except ValueError:
            pass
    info["delay"] = bool(re.search(r"\b__delay_ms\s*\(", "\n".join(code)))
    body = "\n".join(c for c in code if not c.lstrip().startswith("#"))
    shapes = {c_int(v) for v in re.findall(r"\b0[xX][0-9a-fA-F]+\b|\b0[bB][01]+\b", body)}
    info["seg7"] = bool(re.search(r"seg", body, re.I)) or len(shapes & SEG7_SHAPES) >= 5
    for name, value in aliases.items():
        pm = re.fullmatch(r"[!~]?\s*\(?\s*PORT([A-E])bits\.R[A-E](\d)\s*\)?", value)
        if pm:
            info["names"].setdefault(f"R{pm.group(1)}{pm.group(2)}", name)
    for i, c in enumerate(code, start=1):
        if re.match(r"\s*(?:void|int)\s+main\s*\(", c):
            info["main"] = i
            break
    if info["main"] is None:
        return info
    for no in range(info["main"] + 1, len(code) + 1):
        c = code[no - 1]
        if not c.strip() or c.lstrip().startswith("#"):
            continue
        full = expand(c, aliases)
        info["used"] |= set(re.findall(r"\b(?:OSCCON|ANSEL[A-E]?|ANSELH)\b", full))
        info["used"] |= {f"{k}{p}" for k, p in re.findall(r"\b(PORT|TRIS|LAT)([A-E])(?:bits)?\b", full)}
        if info["loop"] is None and LOOP_RE.search(full):
            info["loop"] = no
            cond = re.search(r"\bwhile\s*\((.*)\)", full)
            info["wait_loop"] = bool(cond and re.search(r"\bPORT[A-E]", cond.group(1)))
        if "//" in orig[no - 1]:
            note = orig[no - 1].split("//", 1)[1].strip()
            if re.search(r"[^\s=*/-]", note):
                info["notes"][str(no)] = note
        for m in re.finditer(rf"\b(?:PORT|LAT)([A-E])\s*{ASSIGN}\s*([^;]*)", full):
            if re.fullmatch(r"\(?\s*(?:0+|0[xX]0+|0[bB]0+)\s*\)?", m.group(2).strip()):
                continue        # clearing a port at start-up says nothing about what is on it
            info["writes"][m.group(1)] += 1
        for m in re.finditer(rf"\b(?:PORT|LAT)([A-E])bits\.\w+\s*{ASSIGN}", full):
            info["writes"][m.group(1)] += 1
        for m in re.finditer(r"\bTRIS([A-E])\s*=\s*(0[xXbB][0-9a-fA-F]+|\d+)\s*;", full):
            info["tris"][m.group(1)] = c_int(m.group(2))
        rhs = re.sub(rf"\b(?:PORT|LAT)[A-E](?:bits\.\w+)?\s*{ASSIGN}", " ", full)
        for m in re.finditer(r"([!~])?\s*\(?\s*\bPORT([A-E])bits\.R[A-E](\d)\b", rhs):
            pin = f"R{m.group(2)}{m.group(3)}"
            info["reads"][pin] = info["reads"].get(pin, False) or bool(m.group(1))
        for m in re.finditer(r"([!~])?\s*\(?\s*\bPORT([A-E])\b(?!bits)", rhs):
            info["port_reads"][m.group(2)] = info["port_reads"].get(m.group(2), False) or bool(m.group(1))
    return info


def bank_pins(port, bank, tris):
    """Pins of a port read as a whole: the given bank, else the input bits TRIS sets, else bits 0 to 3."""
    if bank:
        return [p for p in bank if p[1] == port]
    if port in tris:
        bits = [b for b in range(8) if (tris[port] >> b) & 1][:BANK_SIZE]
    else:
        bits = list(range(BANK_SIZE))
    return [f"R{port}{b}" for b in bits]


def guess(text, table, device_regs, device_pins, polarity="auto", bank=None, writes=16, wait_ms=WAIT_MS):
    """The target part of picviewer.json (without id, device and source), and a few facts for the log."""
    info = analyze(text)
    if info["main"] is None:
        raise ProjectError("main が見つからない")
    lines = sorted({ln for _, ln in table if ln > info["main"]})
    if not lines:
        raise ProjectError("main の後に命令のある行が無い（XC8 の行の表が空）")
    first = lines[0]
    loop = info["loop"] or 10 ** 9
    setup = [ln for ln in lines if ln < loop]
    out = info["writes"].most_common(1)[0][0] if info["writes"] else None

    reads = {p: low for p, low in info["reads"].items() if p[1] != out}
    for port, low in info["port_reads"].items():
        if port != out:
            for p in bank_pins(port, bank, info["tris"]):
                reads[p] = reads.get(p, False) or low
    switches = []
    for pin in sorted(p for p in reads if p in device_pins):
        low = reads[pin] if polarity == "auto" else polarity == "low"
        switches.append({"pin": pin, "active": "low" if low else "high", "label": info["names"].get(pin, pin)})

    def level(s, pressed):
        return int(pressed) ^ int(s["active"] == "low")

    plan = []
    if switches:
        plan.append({"set": {s["pin"]: level(s, False) for s in switches}, "note": "スイッチは全部離してある"})
    plan.append({"run_to": first})
    if setup:
        plan.append({"step": len(setup)})
    if out:
        watch = f"PORT{out}"
        if not switches:
            plan.append({"until_write": watch, "count": writes, "wait_ms": wait_ms})
        else:
            each = max(2, writes // (2 * len(switches) + 1))
            if not info["wait_loop"]:
                plan.append({"until_write": watch, "count": each, "wait_ms": wait_ms})
            for s in switches:
                plan.append({"set": {s["pin"]: level(s, True)}, "note": f"{s['label']} を押す"})
                plan.append({"until_write": watch, "count": each, "wait_ms": wait_ms})
                plan.append({"set": {s["pin"]: level(s, False)}, "note": f"{s['label']} を離す"})
                plan.append({"until_write": watch, "count": each, "wait_ms": wait_ms})
    parse_plan(plan, "init")              # the same checks as a hand-written file

    ports = sorted({out} if out else set()) + sorted({s["pin"][1] for s in switches})
    want = [r for r in SETUP_REGS if r in info["used"]]
    for kind in ("TRIS", "PORT"):
        want += [f"{kind}{p}" for p in PORTS if f"{kind}{p}" in info["used"] or p in ports]
    registers = [r for r in want if r in device_regs] or ["STATUS"]

    if out and info["seg7"]:
        circuit = {"type": "seg7", "port": out}
    elif out:
        circuit = {"type": "leds", "port": out}
    else:
        circuit = {"type": "pins"}
    if switches and circuit["type"] != "pins":
        circuit["switches"] = switches
    roles = []
    if out:
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
    facts = {"first": first, "setup": len(setup), "out": out, "seg7": circuit["type"] == "seg7",
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
    elf = compile_target(tc.require_xc8(), probe, project_dir / "build" / "init")
    table = read_line_table(elf.with_suffix(".cmf"), compiled_name(probe))
    target, facts = guess("\n".join(read_source(source)), table, regs, pins, polarity, bank, writes)
    try:
        rel = Path(os.path.relpath(source, project_dir)).as_posix()
    except ValueError:                   # another drive
        rel = source.as_posix()
    project = {"title": source.stem, "output": f"{source.stem}_viewer.html",
               "targets": [{"id": device.lower(), "device": device, "source": rel, **target}]}
    out_file.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return facts
