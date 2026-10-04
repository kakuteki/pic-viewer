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
from bisect import bisect_right
from collections import Counter
from pathlib import Path

from .bundle import read_source
from .compiler import CompileError, compile_target, compiled_name
from .linetab import read_line_table
from .picdef import PicDef
from .project import ProjectError, Target, ascii_build_dir, normalize_device, parse_plan
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
# the shapes without bit 7 (the decimal point, lit or dark in a table): 1 lights (cathode) or 0 lights (anode)
SEG7_C7 = {v & 0x7F for v in SEG7_CATHODE | SEG7_LETTERS}
SEG7_A7 = {~v & 0x7F for v in SEG7_CATHODE | SEG7_LETTERS}
DIGITS_C7 = {v & 0x7F for v in SEG7_CATHODE}
DIGITS_A7 = {~v & 0x7F for v in SEG7_CATHODE} - {0}
# a body that only passes time: an empty loop with it is a wait
IDLE_RE = re.compile(r"\b(?:__delay_ms|__delay_us|_delay|NOP|CLRWDT|__nop|_nop)\s*\([^()]*\)")
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
    """(start, end, pressed_is_true, keyword) for each if/while condition on a line: the text between the
    parentheses, and whether the value that makes it true means "pressed" (an if, or a while with a body)
    or the value that makes it false does (an empty while: waiting until the switch changes). analyze
    replaces the guess for while from the statement structure (loop_shape), which sees past the line."""
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
        out.append((start, end, not empty, m.group(1)))
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


def one_run(v):
    """True when the 1 bits, or the 0 bits, of a byte are one unbroken run: a bar or a block of LEDs."""
    def run(x):
        x &= 0xFF
        if not x:
            return True
        while not x & 1:
            x >>= 1
        return x & (x + 1) == 0
    return run(v) or run(~v)


def seg7_kind(values):
    """(kind, strong, count) for the constants a program writes to one port: kind is 'cathode' or 'anode' when
    they (leaving out 0, 0xFF and one-bit patterns) are all shapes of that one polarity, else None; strong when one
    of them is not a bar (1, 7, 0 and 8 are bars, and so are LED blocks and bar graphs); count how many differ."""
    vals = {v & 0xFF for v in values} - SEG7_AMBIGUOUS
    if not vals:
        return None, False, 0
    cathode = all((v & 0x7F) in SEG7_C7 for v in vals)
    anode = all((v & 0x7F) in SEG7_A7 for v in vals)
    if not (cathode or anode):
        return None, False, len(vals)      # not shapes, or shapes of both kinds mixed (two LEDs moving along)
    if cathode and anode:                  # every one reads both ways: a dark point (bit 7 = 1) goes with anode
        anode = all(v & 0x80 for v in vals)
    return ("anode" if anode else "cathode"), not all(one_run(v) for v in vals), len(vals)


def place_of(index):
    """The place a digit table index stands for: /10 the tens (1), /100 the hundreds (2), % alone the ones (0),
    a number at the end of a name (dig2, keta3) that place. None when it does not say."""
    m = re.search(r"/\s*\(?\s*(10+)\b", index)
    if m:
        return len(m.group(1)) - 1
    m = re.search(r"\b[A-Za-z_]*?(\d)\s*\]?\s*$", index.strip().rstrip("]").strip())
    if m and re.search(r"[A-Za-z_]", index):
        return int(m.group(1))
    return 0 if "%" in index else None


def state_word(name):
    """'on' or 'off' when a macro's name says which switch state it stands for (ON, OFF, SW0_ON, sw1_OFF)."""
    m = re.search(r"(?:^|_)(on|off)$", name, re.I)
    return m.group(1).lower() if m else None


# statements of the C text, for the shape of main's loop: the text is blanked (no comments or strings)
KEYWORD_RE = re.compile(r"(if|for|while|do|switch)\b")
BRACKETS = {"(": ")", "[": "]", "{": "}"}


def closing(text, i):
    """The index of the bracket that closes text[i] ((, [ or {), or the end of the text."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == text[i]:
            depth += 1
        elif text[j] == BRACKETS[text[i]]:
            depth -= 1
            if depth == 0:
                return j
    return len(text) - 1


def statement_at(text, i):
    """(start, end) indexes of the statement at or after i: a block, a control statement with all it
    governs (else branches, the while of a do), or a simple statement up to its ;."""
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n:
        return n, n - 1
    if text[i] == "{":
        return i, closing(text, i)
    m = KEYWORD_RE.match(text, i)
    if m and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
        j = m.end()
        if m.group(1) != "do":
            p = text.find("(", j)
            j = closing(text, p) + 1 if p >= 0 else j
        _, end = statement_at(text, j)
        if m.group(1) == "if":
            other = re.compile(r"\s*else\b").match(text, end + 1)
            if other:
                _, end = statement_at(text, other.end())
        elif m.group(1) == "do":
            tail = re.compile(r"\s*while\s*\(").match(text, end + 1)
            if tail:
                semi = text.find(";", closing(text, tail.end() - 1))
                end = semi if semi >= 0 else n - 1
        return i, end
    depth = 0
    for j in range(i, n):
        if text[j] in "([":
            depth += 1
        elif text[j] in ")]":
            depth -= 1
        elif text[j] == ";" and depth <= 0:
            return i, j
    return i, n - 1


def statements_in(text, block_start, block_end):
    """(keyword or None, start, end) of each statement directly inside the block text[block_start:block_end + 1]."""
    out, i = [], block_start + 1
    while i < block_end:
        while i < block_end and (text[i].isspace() or text[i] == ";"):
            i += 1
        if i >= block_end:
            break
        s, e = statement_at(text, i)
        m = KEYWORD_RE.match(text, s)
        out.append((m.group(1) if m else None, s, e))
        i = e + 1
    return out


def pass_writes(text, s, e, count):
    """The most writes one pass through the statement text[s:e + 1] can make, count(a, b) being the writes in
    text[a:b + 1]: one side of each if, one case of a switch, one of a run of `if (x == 1)`, `if (x == 2)` ...
    on the same variable, and a loop body once."""
    while s <= e and text[s].isspace():
        s += 1
    if s > e:
        return 0
    if text[s] == "{":
        total, run, run_var = 0, 0, None
        for kw, a, b in statements_in(text, s, closing(text, s)):
            n = pass_writes(text, a, b, count)
            m = re.match(r"if\s*\(\s*(\w+)\s*==\s*\w+\s*\)", text[a:b + 1])
            alone = m and not re.search(r"\belse\b", text[a:b + 1])
            if alone and m.group(1) == run_var:
                run = max(run, n)          # x == 1, x == 2, ...: only one of them holds
                continue
            total += run
            run, run_var = (n, m.group(1)) if alone else (0, None)
            if not alone:
                total += n
        return total + run
    m = KEYWORD_RE.match(text, s)
    if not m:
        return count(s, e)
    kw = m.group(1)
    if kw == "do":
        body_s, body_e = statement_at(text, m.end())
        return pass_writes(text, body_s, body_e, count)
    p = text.find("(", m.end())
    close = closing(text, p)
    head = count(p, close)
    then_s, then_e = statement_at(text, close + 1)
    if kw == "if":
        other = re.compile(r"\s*else\b").match(text, then_e + 1)
        alt = pass_writes(text, other.end(), e, count) if other else 0
        return head + max(pass_writes(text, then_s, then_e, count), alt)
    if kw == "switch":
        body = text[then_s:then_e + 1]
        cuts = [mm.start() for mm in re.finditer(r"\b(?:case\b[^:]*|default\s*):", body)] + [len(body)]
        return head + max([count(then_s + a, then_s + b - 1) for a, b in zip(cuts, cuts[1:])] or [0])
    return head + pass_writes(text, then_s, then_e, count)


def loop_shape(code, aliases):
    """main's endless loop and the loops directly inside it, as line numbers:
    {"loop": (first, last), "sections": [(head, last), ...], "ifs": [(condition, (first, last)), ...]}."""
    plain = ["" if c.lstrip().startswith("#") else c for c in code]
    text = expand("\n".join(plain), aliases)
    starts, pos = [], 0
    for line in text.split("\n"):
        starts.append(pos)
        pos += len(line) + 1

    def line_of(i):
        return bisect_right(starts, i)

    shape = {"loop": None, "sections": [], "ifs": [], "isr": None, "fors": [], "waits": {}, "functions": [],
             "straight": False, "changed": set()}
    for f in re.finditer(r"\bif\s*\(", text):
        close = closing(text, f.end() - 1)
        bs, be = statement_at(text, close + 1)
        shape["ifs"].append((text[f.end():close], text[bs:be + 1]))
    for f in re.finditer(r"\bfor\s*\(", text):
        s, e = statement_at(text, f.start())
        shape["fors"].append((line_of(s), line_of(e)))
    isr = re.search(r"\b(?:__)?interrupt\b[^;{]*\{", text)
    if isr:
        shape["isr"] = (line_of(isr.end() - 1), line_of(closing(text, isr.end() - 1)))
    pin_re = re.compile(r"PORT([A-E])bits\.R[A-E](\d)")
    ifs = []
    for f in re.finditer(r"\bif\s*\(", text):
        close = closing(text, f.end() - 1)
        bs, be = statement_at(text, close + 1)
        ifs.append(({f"R{a}{b}" for a, b in pin_re.findall(text[f.end():close])}, bs, be))
    for w in re.finditer(r"\bwhile\s*\(", text):
        close = closing(text, w.end() - 1)
        before = text[:w.start()].rstrip()
        body = None
        if before.endswith("}"):                       # perhaps the while of a do { ... } while (...);
            depth, opening = 0, 0
            for j in range(len(before) - 1, -1, -1):
                depth += {"}": 1, "{": -1}.get(before[j], 0)
                if depth == 0:
                    opening = j
                    break
            if re.search(r"\bdo\s*$", before[:opening]):
                body = before[opening:]
        if body is None:
            bs, be = statement_at(text, close + 1)
            body = text[bs:be + 1]
        if not re.sub(r"[{}();\s]", "", IDLE_RE.sub("", body)):
            around = set().union(*[pins for pins, a, b in ifs if a <= w.start() <= b] or [set()])
            shape["waits"][line_of(w.start())] = around
    # function bodies: blocks at the outermost level after a parameter list (not `= { ... }` tables)
    depth, last = 0, 0
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0 and text[last:i].rstrip().endswith(")"):
                shape["functions"].append((line_of(i), line_of(closing(text, i))))
            depth += 1
        elif ch == "}":
            depth -= 1
            last = i + 1
        elif ch == ";" and depth == 0:
            last = i + 1
    m = re.search(r"\bmain\s*\(\s*(?:void)?\s*\)\s*\{", text)
    if not m:
        return shape
    brace = m.end() - 1
    if brace < 0:
        return shape
    loops = [(k, s, e) for k, s, e in statements_in(text, brace, closing(text, brace)) if k in ("while", "for", "do")]
    endless = [(k, s, e) for k, s, e in loops
               if re.match(r"(?:while\s*\(\s*(?:1|true|TRUE)\s*\)|for\s*\(\s*;\s*;\s*\)|do\b)", text[s:e + 1])]
    if not (endless or loops):
        return shape
    kind, s, e = (endless or loops)[-1]
    shape["loop"] = (line_of(s), line_of(e))
    shape["text"], shape["loop_span"] = text, (s, e)
    body = text.find("{", s)
    shape["straight"] = 0 <= body < e and not any(k for k, _, _ in statements_in(text, body, closing(text, body)))
    # names given a value inside the loop or in another function (an interrupt, a function the loop calls)
    elsewhere = text[s:e + 1] + "".join(text[starts[a - 1]:starts[b - 1] if b < len(starts) else len(text)]
                                        for a, b in shape["functions"] if not (a <= shape["loop"][0] <= b))
    assigned = re.findall(r"\b([A-Za-z_]\w*)\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)", elsewhere)
    stepped = [n for pair in re.findall(r"\b([A-Za-z_]\w*)\s*(?:\+\+|--)|(?:\+\+|--)\s*([A-Za-z_]\w*)", elsewhere)
               for n in pair if n]
    shape["changed"] = set(assigned) | set(stepped)
    if 0 <= body < e:
        shape["sections"] = [(line_of(bs), line_of(be)) for k, bs, be in statements_in(text, body, closing(text, body))
                             if k in ("while", "for", "do")]
    return shape


def analyze(text):
    """What the code says about the board: written ports, read pins and how they are tested, the first
    loop of main, the straight lines before it, and the line comments."""
    orig = text.split("\n")
    code = blank_code(text)
    aliases = read_aliases(code)
    info = {"main": None, "loop": None, "setup_end": None, "wait_loop": False, "fosc": None, "delay": False,
            "seg7": None, "writes": Counter(), "reads": {}, "votes": {}, "port_reads": {}, "tris": {},
            "names": {}, "notes": {}, "used": set(), "table_writes": Counter(), "bit_writes": {}, "digit_on": {},
            "int_edge": None, "line_writes": {}, "line_bits": {}, "whole_writes": Counter(), "const_lines": set(),
            "tris_line": {}, "delays_us": {}, "combos": [], "counted": set(), "shape": None, "isr_writes": Counter(),
            "test_lines": {}, "bit_targets": set(), "port_consts": {}, "events": [], "digit_pins": set(),
            "digit_place": {}, "digit_ports": {}, "zero_writes": [], "write_rhs": {}}
    joined = "\n".join(code)
    m = re.search(r"#\s*define\s+_XTAL_FREQ\s+(\w+)", joined)
    if m:
        try:
            info["fosc"] = c_int(m.group(1))
        except ValueError:
            pass
    info["delay"] = bool(re.search(r"\b__delay_ms\s*\(", joined))
    body = "\n".join(c for c in code if not c.lstrip().startswith("#"))
    # a table of digit shapes: five of one kind among all the constants, compared without the point (bit 7)
    shapes = {c_int(v) & 0x7F for v in re.findall(r"\b0[xX][0-9a-fA-F]+\b|\b0[bB][01]+\b", body)
              if c_int(v) & 0xFF not in SEG7_AMBIGUOUS} - {0, 0x7F}     # one-bit tables say nothing
    cathode, anode = len(shapes & DIGITS_C7), len(shapes & DIGITS_A7)
    if max(cathode, anode) >= 5 or re.search(r"seg", body, re.I):
        info["seg7"] = "anode" if anode > cathode else "cathode"
    for name, value in aliases.items():
        pm = re.fullmatch(r"[!~]?\s*\(?\s*PORT([A-E])bits\.R[A-E](\d)\s*\)?", expand(value, aliases))
        if pm:
            info["names"].setdefault(f"R{pm.group(1)}{pm.group(2)}", name)
    functions = set(re.findall(r"^\s*(?:static\s+)?(?:void|char|int|long|short|bit|__bit|unsigned\s+\w+|signed\s+\w+)"
                               r"\s+\**\s*([A-Za-z_]\w*)\s*\([^;]*$", joined, re.M)) - {"main"}
    # INT on RB0: enabled by INTE (INT0IE) = 1 or a whole INTCON with bit 4; the edge by INTEDG (INTEDG0) or bit 6
    # of OPTION_REG (INTCON2 on PIC18), rising after a reset
    lit = r"(0[xXbB][0-9a-fA-F]+|\d+)"
    inte = re.search(r"\b(?:INTCONbits\.)?(?:INTE|INT0IE|INT0E)\s*=\s*1\b", body)
    m = re.search(rf"\bINTCON\s*=\s*{lit}\s*;", body)
    if inte or (m and c_int(m.group(1)) & 0x10):
        edge = 1
        m = re.search(rf"\b(?:OPTION_REG|INTCON2)\s*=\s*{lit}\s*;", body)
        if m:
            edge = (c_int(m.group(1)) >> 6) & 1
        m = re.search(r"\b(?:OPTION_REGbits\.|INTCON2bits\.)?INTEDG0?\s*=\s*([01])\b", body)
        if m:
            edge = int(m.group(1))
        info["int_edge"] = edge
    m = re.search(r"\bmain\s*\(\s*(?:void)?\s*\)\s*(?:\{|$)", joined, re.M)
    if m:
        info["main"] = joined.count("\n", 0, m.start()) + 1
    info["shape"] = shape = loop_shape(code, aliases)
    lines = [(no, c) for no, c in enumerate(code, start=1) if c.strip() and not c.lstrip().startswith("#")]
    first_wait = {}                      # the value that ends the first wait on each pin: the press
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
        if note and re.search(r"[^\W\d_]", note):         # a comment with words; "100" or "----" says little
            info["notes"][str(no)] = note
        here = info["line_writes"].setdefault(no, Counter())
        events = []                      # (position, kind, ...) on this line, put in order below
        for m in re.finditer(r"__delay_(ms|us)\s*\(\s*(\d+)\s*[uUlL]*\s*\)", full):
            info["delays_us"][no] = info["delays_us"].get(no, 0) + int(m.group(2)) * (1000 if m.group(1) == "ms" else 1)
            events.append((m.start(), "delay"))
        for m in re.finditer(rf"\b(PORT|LAT)([A-E])\s*({ASSIGN})\s*([^;]*)", full):
            reg, value = m.group(1) + m.group(2), m.group(4).strip()
            const = re.fullmatch(r"\(?\s*(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)\s*\)?", value)
            if const and m.group(3) == "=":
                info["port_consts"].setdefault(reg, set()).add(c_int(const.group(1)))
            if re.fullmatch(r"\(?\s*(?:0+|0[xX]0+|0[bB]0+)\s*\)?", value):
                # clearing a port says nothing about what is on it (no vote for the LED port), but it is a write of
                # its line all the same (PORTC = 0 to switch LEDs off inside the loop)
                here[reg] += 1
                info["const_lines"].add(no)
                info["zero_writes"].append((no, reg))
                continue
            info["writes"][reg] += 1
            info["whole_writes"][reg] += 1
            here[reg] += 1
            info["write_rhs"].setdefault(no, []).append(value)
            if const:
                info["const_lines"].add(no)
            table = m.group(3) == "=" and ("[" in value or re.search(r"seg", value, re.I) is not None)
            if table:
                info["table_writes"][reg] += 1      # a digit shape from a table
            events.append((m.start(), "port", reg, value, table, c_int(const.group(1)) if const and m.group(3) == "=" else None))
        for m in re.finditer(rf"\b(PORT|LAT)([A-E])bits\.(?:R|LAT)[A-E](\d)\s*=\s*\(?\s*([01])\s*\)?\s*;", full):
            pin = f"R{m.group(2)}{m.group(3)}"
            info["bit_writes"].setdefault(pin, set()).add(int(m.group(4)))
            info["line_bits"].setdefault(no, []).append((pin, int(m.group(4))))
            events.append((m.start(), "bit", pin, int(m.group(4))))
        for m in re.finditer(rf"\b(PORT|LAT)([A-E])bits\.(\w+)\s*{ASSIGN}\s*([^;]*)", full):
            info["writes"][m.group(1) + m.group(2)] += 1
            here[m.group(1) + m.group(2)] += 1
            bit = re.fullmatch(r"(?:R|LAT)([A-E])(\d)", m.group(3))
            if bit:
                info["bit_targets"].add(f"R{bit.group(1)}{bit.group(2)}")
            info["write_rhs"].setdefault(no, []).append(m.group(4).strip())
            if re.fullmatch(r"\(?\s*(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)\s*\)?", m.group(4).strip()):
                info["const_lines"].add(no)
        info["events"] += [(no, *e[1:]) for e in sorted(events, key=lambda e: e[0])]
        for m in re.finditer(r"\bTRIS([A-E])\s*=\s*(0[xXbB][0-9a-fA-F]+|\d+)\s*;", full):
            info["tris"][m.group(1)] = c_int(m.group(2))
            info["tris_line"][m.group(1)] = no
        rhs = re.sub(rf"\b(?:PORT|LAT)[A-E](?:bits\.\w+)?\s*{ASSIGN}", lambda m: " " * len(m.group(0)), full)
        conds = conditions(rhs, nxt)
        raw_conds = conditions(c, lines[k + 1][1] if k + 1 < len(lines) else "")
        decided = {}                     # pins each condition on the line decides on
        for m in re.finditer(r"([!~])?\s*\(?\s*\bPORT([A-E])bits\.R[A-E](\d)\b", rhs):
            pin = f"R{m.group(2)}{m.group(3)}"
            s = m.start() + len(m.group(0)) - len(f"PORT{m.group(2)}bits.R{m.group(2)}{m.group(3)}")
            info["reads"][pin] = info["reads"].get(pin, False) or bool(m.group(1))
            for n, (start, end, pressed_is_true, keyword) in enumerate(conds):
                if start <= s < end:
                    v = true_value(rhs[start:end], s - start, m.end() - start)
                    if v is None:
                        continue
                    decided.setdefault(n, set()).add(pin)
                    waiting = keyword == "while" and no in shape["waits"]
                    if keyword == "while":
                        pressed_is_true = not waiting
                    # a test written with ON or OFF in a name says itself which state it is about;
                    # otherwise an if (or a loop with a body) acts when pressed, an empty loop waits for it
                    words = {state_word(w) for w in re.findall(r"[A-Za-z_]\w*", c[raw_conds[n][0]:raw_conds[n][1]])
                             if w in aliases} - {None} if n < len(raw_conds) else set()
                    if words == {"on"}:
                        vote = v
                    elif words == {"off"}:
                        vote = 1 - v
                    elif not waiting:
                        vote = v if pressed_is_true else 1 - v
                    elif pin in shape["waits"][no]:
                        vote = v        # waiting inside `if (pressed)` on the same pin: for the release
                    elif pin in first_wait:
                        vote = first_wait[pin]     # a later wait on the pin goes with the first (press, release)
                    else:
                        vote = first_wait[pin] = 1 - v
                    info["votes"].setdefault(pin, []).append(vote)
        for n, pins in decided.items():
            info["test_lines"].setdefault(no, set()).update(pins)
            # SW0 && SW1: something happens only while both are pressed
            cond = rhs[conds[n][0]:conds[n][1]]
            if len(pins) >= 2 and "&&" in cond and "||" not in cond and tuple(sorted(pins)) not in info["combos"]:
                info["combos"].append(tuple(sorted(pins)))
        for m in re.finditer(r"([!~])?\s*\(?\s*\bPORT([A-E])\b(?!bits)", rhs):
            info["port_reads"][m.group(2)] = info["port_reads"].get(m.group(2), False) or bool(m.group(1))
    # digit or letter shapes written as constants (LATD = 0xC0; LATD = 0x89;)
    shape_regs = set()
    said = re.search(r"7\s*seg|7\s*セグ|セグメント", text, re.I) is not None     # in a comment, too
    for reg, consts in info["port_consts"].items():
        kind, strong, count = seg7_kind(consts)

        def is_seg(e, r=reg):
            return e[1] == "port" and e[2] == r and e[5] is not None
        if not kind and said and writes_digits(info, is_seg):
            # the program says 7 segments and switches digits after these writes: single segments (0xDF, one
            # segment lit on an anode display) are shapes here too; more 1 bits than 0 bits means anode
            ones = sum(bin(v & 0x7F).count("1") for v in consts if v & 0xFF not in (0, 0xFF))
            zeros = sum(7 - bin(v & 0x7F).count("1") for v in consts if v & 0xFF not in (0, 0xFF))
            kind, strong, count = ("anode" if ones > zeros else "cathode"), True, 2
        if not kind:
            continue
        # two shapes with one that is no bar; or digit pins written right after a shape (a bar-like 0, 1 or 7 only
        # when they take turns); or the program saying it drives a 7-segment display
        if (strong and count >= 2) or said or switches_digits(info, is_seg) or (strong and writes_digits(info, is_seg)):
            shape_regs.add(reg)
            info["seg7"] = info["seg7"] or kind
    for no, kind, *rest in info["events"]:
        if kind == "port" and rest[0] in shape_regs and rest[3] is not None:
            info["table_writes"][rest[0]] += 1          # on a segment port, a blank or one segment is a shape too
    if info["seg7"] and info["table_writes"]:
        find_digits(info, shape_regs)
    for cond, body_text in shape["ifs"]:
        # a variable counted up or down where a switch is tested: the presses themselves are what it counts
        counting = re.sub(r"\bfor\s*\([^)]*\)", " ", body_text)
        if re.search(r"\+\+|--|[+-]=", counting):
            info["counted"] |= {f"R{p}{b}" for p, b in re.findall(r"PORT([A-E])bits\.R[A-E](\d)", cond)}
    for head, last in shape["fors"]:
        # a switch tested inside a for loop moves it on: for (i = 0; i < 10; i++) { wait for a press ... }
        if shape["loop"] is None or (head, last) != shape["loop"]:
            for no in range(head, last + 1):
                info["counted"] |= info["test_lines"].get(no, set())
    if shape["isr"]:
        for no in range(shape["isr"][0], shape["isr"][1] + 1):
            info["isr_writes"].update(info["line_writes"].get(no, {}))
    return info


def windows(info, is_seg):
    """(segment write, writes after it until the next wait, writes before it back to the last wait) for each
    segment write. Code that repeats goes round: main's loop, else the function the write is in, so the writes at
    the bottom of a loop come before the first segment write of the next round."""
    shape = info["shape"]
    blocks = ([shape["loop"]] if shape["loop"] else []) + shape["functions"]

    def block_of(line):
        return next((b for b in blocks if b[0] <= line <= b[1]), None)
    for e in info["events"]:
        if not is_seg(e):
            continue
        block = block_of(e[0])
        events = [f for f in info["events"] if block_of(f[0]) == block] if block else info["events"]
        at = events.index(e)
        after = []
        for f in events[at + 1:] + (events[:at] if block else []):
            if f[1] == "delay" or is_seg(f):
                break
            after.append(f)
        before = []
        for f in reversed(events[:at] if not block else events[at + 1:] + events[:at]):
            if f[1] == "delay" or is_seg(f):
                break
            before.append(f)
        yield e, after, before


def switches_digits(info, is_seg):
    """True when a pin on another port is set to 0 and to 1 around the segment writes: digits taking turns."""
    port = {e[2][-1] for e in info["events"] if is_seg(e)}
    seen = {}
    for _, after, before in windows(info, is_seg):
        for f in after + before:
            if f[1] == "bit" and f[2][1] not in port:
                seen.setdefault(f[2], set()).add(f[3])
    return any(v == {0, 1} for v in seen.values())


def writes_digits(info, is_seg):
    """True when a pin of another port is written right after the segment writes (before the next wait):
    a digit switched on, even if it is never switched off again."""
    port = {e[2][-1] for e in info["events"] if is_seg(e)}
    for _, after, _ in windows(info, is_seg):
        if any(f[1] == "bit" and f[2][1] not in port and not BUZZER_RE.fullmatch(info["names"].get(f[2], ""))
               and not MOTOR_IN_RE.fullmatch(info["names"].get(f[2], "")) for f in after):
            return True
    return False


def find_digits(info, shape_regs):
    """The pins that switch digits on: written after a segment write and before the next wait (or the next
    segment write), the last such write being the one that lights the digit. Whole-port writes there with
    a shift or a table (PORTA = ~(1 << i) & 0x0F) select digits by port. The place of the digit comes from the
    index of the table (/10 the tens ...)."""
    seg_ports = {r[-1] for r in info["table_writes"]}

    def is_seg(e):
        return e[1] == "port" and e[2] in info["table_writes"] and (e[4] or (e[2] in shape_regs and e[5] is not None))
    for e, after, before in windows(info, is_seg):
        bits = [(f[2], f[3]) for f in after if f[1] == "bit" and f[2][1] not in seg_ports]
        for f in after:
            if f[1] == "port" and f[2][-1] not in seg_ports and re.search(r"<<|>>|\[", f[3]):
                info["digit_ports"][f[2]] = f[3]
        if bits:
            # the lit digit: the one written with the value no other gets (one 0 among 1s), else the last written
            last = dict(bits)
            count = Counter(last.values())
            if len(last) >= 3 and len(count) == 2 and min(count.values()) == 1:
                value = min(count, key=count.get)
                pin = next(p for p, v in last.items() if v == value)
            else:
                pin, value = bits[-1]
            # a pin written the same value whenever it is written is not switched: its write says nothing of
            # the lit level (a program that writes 1 where the board lights at 0 keeps that digit dark)
            if info["bit_writes"].get(pin) == {0, 1}:
                info["digit_on"].setdefault(pin, []).append(value)
            place = place_of(re.sub(r"^.*?\[|\]\s*$", "", e[3]) if "[" in e[3] else "")
            if place is not None:
                info["digit_place"].setdefault(pin, []).append(place)
        info["digit_pins"] |= {p for p, _ in bits} | {f[2] for f in before if f[1] == "bit" and f[2][1] not in seg_ports}


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


# names a program gives the pins it drives, for what is on them
BUZZER_RE = re.compile(r"(?:BZ|BUZ|BUZZ|BUZZER|BEEP|BEEPER|SPK|SPEAKER|SOUND|PIEZO)\d*", re.I)
MOTOR_IN_RE = re.compile(r"A?IN([12])", re.I)


def pulse(info, first, last):
    """(pin, high µs, low µs) when the lines set one pin to 1 and back to 0 with waits in between:
    the waits after the 1 add up to the high time, the waits after the 0 to the low time."""
    level, pins, high, low = None, set(), 0, 0
    for no in range(first, last + 1):
        for pin, v in info["line_bits"].get(no, []):
            pins.add(pin)
            level = v
        wait = info["delays_us"].get(no, 0)
        if level == 1:
            high += wait
        elif level == 0:
            low += wait
    return (pins.pop(), high, low) if len(pins) == 1 and high and low else None


def guess(text, table, device_regs, device_pins, polarity="auto", bank=None, writes=16, wait_ms=WAIT_MS, digits="auto"):
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

    if info["seg7"] and info["table_writes"]:
        # the port written from a table of shapes carries the segments, however often other ports are written
        out_reg = info["table_writes"].most_common(1)[0][0]
        out = out_reg[-1]
    mux = None
    if info["seg7"] and info["table_writes"]:
        # digits: the pins written between a segment write and the next wait, without buzzers and motor inputs
        named = {p for p in info["digit_pins"] if BUZZER_RE.fullmatch(info["names"].get(p, ""))
                 or MOTOR_IN_RE.fullmatch(info["names"].get(p, ""))}
        found = (info["digit_pins"] - named) - {p for p in info["digit_pins"] if p[1] == out}
        # a port that switches digits: every pin written on it is a digit, also one set only at the start
        # (a digit the reset leaves lit, or one kept dark)
        pins = sorted(p for p in info["bit_writes"] if p[1] in {q[1] for q in found} and p not in named) or sorted(found)
        for reg, expr in info["digit_ports"].items():
            mask = re.search(r"&\s*\(?\s*(0[xXbB][0-9a-fA-F]+|\d+)", expr)
            bits = [b for b in range(8) if (c_int(mask.group(1)) >> b) & 1] if mask else list(range(4))
            pins += [f"R{reg[-1]}{b}" for b in bits if f"R{reg[-1]}{b}" not in pins]
        if pins and (len(pins) > 1 or any(info["bit_writes"].get(p) == {0, 1} for p in pins)):
            low_ports = {reg[-1] for reg, expr in info["digit_ports"].items() if "~" in expr}

            known = [v for p in pins for v in info["digit_on"].get(p, [])]

            def active(pin):
                if digits != "auto":
                    return 0 if digits == "low" else 1         # the board is known: --digits
                votes = info["digit_on"].get(pin, [])
                if votes:
                    return max(set(votes), key=votes.count)
                if pin[1] in low_ports:
                    return 0
                if known:                    # a digit the program keeps dark: lit the way the others are
                    return max(set(known), key=known.count)
                once = info["bit_writes"].get(pin, set())
                return next(iter(once)) if len(once) == 1 else 0      # always the same value: always lit
            places = {p: max(set(v), key=v.count) for p, v in info["digit_place"].items() if p in pins}
            if len(places) == len(pins) and len(set(places.values())) == len(pins):
                pins.sort(key=lambda p: -places[p])          # the highest place on the left
            digits = [{"pin": pin, "active": "low" if active(pin) == 0 else "high"} for pin in pins]
            sel_regs = []
            for port in dict.fromkeys(p[1] for p in pins):
                regs = [r for r in info["writes"] if r[-1] == port]
                if regs:
                    sel_regs.append(max(regs, key=lambda r: info["writes"][r]))
            mux = {"seg_reg": out_reg, "sel_regs": sel_regs, "digits": digits,
                   "ordered": len(places) == len(pins)}

    def input_on_out_port(pin):
        return out in info["tris"] and (info["tris"][out] >> int(pin[2])) & 1 == 1
    digit_pins = {d["pin"] for d in mux["digits"]} if mux else set()
    reads = {p: low for p, low in info["reads"].items()
             if (p[1] != out or input_on_out_port(p)) and p not in digit_pins}
    banks = {}
    for port, low in info["port_reads"].items():
        if port != out:
            banks[port] = bank_pins(port, bank, info["tris"], info["names"])
            for p in banks[port]:
                reads[p] = reads.get(p, False) or low
    if info["int_edge"] is not None and "RB0" in device_pins and "RB0" not in reads:
        reads["RB0"] = False             # the interrupt pin is the switch even when the program never reads it
    info["reads"] = reads
    switches = []
    for pin in sorted(p for p in reads if p in device_pins):
        low = pressed_low(pin, info, polarity)
        switches.append({"pin": pin, "active": "low" if low else "high", "label": info["names"].get(pin, pin)})
    by_pin = {s["pin"]: s for s in switches}
    # a switch on a pin TRIS leaves as an output reads what the PIC drives, not the switch
    stuck = [s for s in switches if s["pin"][1] in info["tris"] and not (info["tris"][s["pin"][1]] >> int(s["pin"][2])) & 1]

    def level(s, pressed):
        return int(pressed) ^ int(s["active"] == "low")

    watch = [mux["seg_reg"], *mux["sel_regs"]] if mux else out_reg
    watched = set(watch) if isinstance(watch, list) else {watch}
    loop = info["shape"]["loop"]

    def writes_in(a, b):
        return sum(n for no in range(a, b + 1) for reg, n in info["line_writes"].get(no, {}).items() if reg in watched)

    # loops one after another inside main's loop, each writing what we follow: a scale, steps of brightness
    sections = []
    if loop and out and not mux:
        for head, last in info["shape"]["sections"]:
            inside = [ln for ln in lines if head < ln <= last]
            if inside and writes_in(head, last):
                sections.append((head, last, inside[0]))
    per_pass = 0
    if loop and out:
        text_, (ls, le) = info["shape"]["text"], info["shape"]["loop_span"]
        write_re = re.compile(rf"\b((?:PORT|LAT)[A-E])(?:bits\.\w+)?\s*{ASSIGN}")
        per_pass = pass_writes(text_, ls, le, lambda a, b: sum(
            1 for m in write_re.finditer(text_, a, b + 1) if m.group(1) in watched))

    plan = []
    if switches:
        plan.append({"set": {s["pin"]: level(s, False) for s in switches}, "note": "スイッチは全部離してある"})
    plan.append({"run_to": first})
    if setup:
        plan.append({"step": len(setup)})
    if mux:
        writes = max(writes, 40)              # about three frames of a 4-digit display, every write a stop
    if out and not switches:
        if len(sections) >= 2:
            for head, last, at in sections:
                plan.append({"run_to": at, "show": head})
                plan.append({"until_write": watch, "count": 4, "wait_ms": wait_ms})
        else:
            count = writes
            written = [no for no in range(*loop) if any(r in watched for r in info["line_writes"].get(no, {}))] \
                if loop and not mux and info["shape"]["straight"] else []

            def fixed(no):
                # a literal, or names the program never changes in the loop (nor in other functions), no calls
                if no in info["const_lines"] and not info["write_rhs"].get(no):
                    return True
                for value in info["write_rhs"].get(no, []):
                    if re.search(r"\w\s*\(", value):
                        return False
                    names = set(re.findall(r"\b[A-Za-z_]\w*\b", value))
                    if names & info["shape"]["changed"] or any(re.match(r"(?:PORT|LAT|TMR|ADRES|CCPR)", n) for n in names):
                        return False
                return True
            if written and all(fixed(no) for no in written):
                count = max(2, min(writes, 2 * len(written)))    # the same few values over and over
            plan.append({"until_write": watch, "count": count, "wait_ms": wait_ms})
    elif out:
        # enough writes for one pass of the loop (both sides of every if counted) and the change it makes
        each = 40 if mux else max(2, min(per_pass + 2, 40))
        # before any press and after a release the program shows its idle state: a few writes are enough
        idle = 40 if mux else min(each, 4)
        if not info["wait_loop"]:
            plan.append({"until_write": watch, "count": idle, "wait_ms": wait_ms})
        # on a multiplexed display a round of the digits is 2 writes a digit: 3 rounds after a press, 2 after a release
        down, up, again_down = (24, 16, 16) if mux else (each, idle, each)

        def press(group, note_on, note_off, again=False):
            plan.append({"set": {s["pin"]: level(s, True) for s in group}, "note": note_on})
            plan.append({"until_write": watch, "count": again_down if again else down, "wait_ms": wait_ms})
            plan.append({"set": {s["pin"]: level(s, False) for s in group}, "note": note_off})
            plan.append({"until_write": watch, "count": up, "wait_ms": wait_ms})

        for s in switches:
            times = 3 if s["pin"] in info["counted"] else 1      # the program counts the presses
            for t in range(times):
                nth = f"（{t + 1} 回目）" if times > 1 else ""
                press([s], f"{s['label']} を押す{nth}", f"{s['label']} を離す", again=t > 0)
        groups = [c for c in info["combos"] if all(p in by_pin for p in c)]
        for port, pins in banks.items():
            pins = [p for p in pins if p in by_pin]
            groups += [tuple(pins[:2])] + ([tuple(pins)] if len(pins) > 2 else []) if len(pins) >= 2 else []
        for group in dict.fromkeys(groups):
            names = "と".join(by_pin[p]["label"] for p in group)
            press([by_pin[p] for p in group], f"{names} を同時に押す", f"{names} を離す")
    parse_plan(plan, "init")              # the same checks as a hand-written file

    # what is on the pins the program drives: names first (BZ, IN1/IN2), then the shape of a servo signal
    drive_pins = sorted({p for p in info["bit_targets"] if p in device_pins})
    roles_of = {}
    for pin in drive_pins:
        if BUZZER_RE.fullmatch(info["names"].get(pin, "")):
            roles_of[pin] = "buzzer"
    ins = {MOTOR_IN_RE.fullmatch(info["names"].get(p, "")).group(1): p for p in drive_pins
           if MOTOR_IN_RE.fullmatch(info["names"].get(p, "")) and p not in roles_of}
    motor = (ins["1"], ins["2"]) if len(ins) == 2 else None
    if loop:
        for head, last in ([(h, e) for h, e, _ in sections] or [loop]):
            found = pulse(info, head, last)
            if found and found[0] not in roles_of and 400 <= found[1] <= 2600 and 15000 <= found[1] + found[2] <= 25000:
                roles_of[found[0]] = "servo"

    def name_of(pin):
        return info["names"].get(pin, pin)

    parts = []
    if mux:
        parts.append({"type": "seg7mux", "port": out, "digits": mux["digits"],
                      **({"common": "anode"} if info["seg7"] == "anode" else {})})
    elif out and info["seg7"]:
        parts.append({"type": "seg7", "port": out, **({"common": "anode"} if info["seg7"] == "anode" else {})})
    else:
        for pin, role in roles_of.items():
            parts.append({"type": role, "pin": pin, "label": name_of(pin)})
        if motor:
            parts.append({"type": "dcmotor", "in1": motor[0], "in2": motor[1]})
        taken = set(roles_of) | set(motor or ())
        if out:
            whole = any(r[-1] == out for r in info["whole_writes"]) or any(
                r[-1] == out and loop and loop[0] < no <= loop[1] for no, r in info["zero_writes"])
            bits = sorted({int(p[2]) for p in drive_pins if p[1] == out and p not in taken})
            if whole or bits:
                led = {"type": "leds", "port": out}
                if not whole:
                    led["bits"] = sorted(bits, reverse=True)      # the highest bit on the left, as in binary
                # a name that only repeats the bit number (bit0, b3) tells nothing the pin name does not
                names = {str(b): name_of(f"R{out}{b}") for b in (range(8) if whole else bits)
                         if f"R{out}{b}" in info["names"] and f"R{out}{b}" not in taken
                         and not re.fullmatch(r"(?:bit|b|BIT)_?\d+", name_of(f"R{out}{b}"))}
                if names:
                    led["names"] = names
                parts.append(led)
    if switches:
        host = next((c for c in parts if c["type"] in ("leds", "seg7")), None)
        if host is None:
            parts.append({"type": "leds", "port": switches[0]["pin"][1], "bits": [], "switches": switches})
        else:
            host["switches"] = switches
    if not parts:
        parts.append({"type": "pins"})
    circuit = parts[0] if len(parts) == 1 else parts

    ports = sorted({out} if out else set()) + sorted({s["pin"][1] for s in switches}) \
        + ([r[-1] for r in mux["sel_regs"]] if mux else []) + sorted({p[1] for p in list(roles_of) + list(motor or ())})
    want = [r for r in SETUP_REGS if r in info["used"]]
    for kind in ("TRIS", "LAT", "PORT"):
        want += [f"{kind}{p}" for p in PORTS
                 if f"{kind}{p}" in info["used"] or (kind != "LAT" and p in ports) or f"{kind}{p}" == out_reg]
    registers = [r for r in want if r in device_regs] or ["STATUS"]

    roles = []
    if mux:
        roles.append(f"PORT{out} に {len(mux['digits'])} 桁の 7 セグメント LED（桁は左から "
                     + "、".join(d["pin"] for d in mux["digits"])
                     + ("。左右は表の添字の位から" if mux["ordered"] else "。左右はピンの順と仮定") + "）")
    elif out and info["seg7"]:
        roles.append(f"PORT{out} に 7 セグメント LED")
    for pin, role in roles_of.items():
        label = f"{pin}（{name_of(pin)}）" if name_of(pin) != pin else pin
        roles.append(f"{label}にブザー" if role == "buzzer" else f"{label}にサーボ（パルスの幅で角度が決まる）")
    if motor:
        roles.append(f"{motor[0]}（IN1）と {motor[1]}（IN2）にモータードライバ")
    led = next((c for c in parts if c["type"] == "leds" and c.get("bits") != []), None)
    if led:
        bits = led.get("bits")
        roles.append(f"PORT{out} に LED" + (f"（ビット {'、'.join(map(str, bits))}）" if bits is not None else ""))
    if switches:
        roles.append("スイッチ " + "、".join(
            f"{s['label']}（{s['pin']}、押すと {level(s, True)}）" for s in switches))
    notes = dict(info["notes"])
    for s in stuck:
        port = s["pin"][1]
        warn = (f"{s['label']}（{s['pin']}）は TRIS{port} で出力のまま。押してもピンは PIC が出す値のままで、"
                "スイッチは読めない")
        at = str(info["tris_line"].get(port, ""))
        if at:
            notes[at] = f"{notes[at]} / {warn}" if at in notes else warn
        roles.append(warn)
    target = {}
    if info["fosc"]:
        target["fosc_hz"] = info["fosc"]
    target["summary"] = ("、".join(roles) or "ポートへの書き込みが見つからない") + "（init がソースから読み取った割り当て）"
    # the interrupt keeps its own time: shortening the waits of main would change what it shows in between
    isr_drives = any(r in watched for r in info["isr_writes"])
    if info["delay"] and not isr_drives:
        target["fast_forward"] = 1000
    target.update({"wait_ms": RUN_WAIT_MS, "registers": registers, "circuit": circuit, "trace": plan,
                   "notes": notes})
    kind = parts[0]["type"]
    facts = {"first": first, "setup": len(setup), "out": "+".join(watch) if mux else out_reg, "seg7": kind.startswith("seg7"),
             "switches": [f"{s['pin']}({s['active']})" for s in switches], "wait_loop": info["wait_loop"],
             "parts": [c["type"] for c in parts], "sections": len(sections) if len(sections) >= 2 else 0,
             "groups": len(dict.fromkeys(groups)) if switches and out else 0}
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


def init_source(source, project_dir, device, tc, *, polarity="auto", bank=None, writes=16, force=False, digits="auto"):
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
    # the same ASCII place build uses: XC8 cannot open a path with Japanese in it, and a short 8.3 name would
    # also shorten the file name it writes into the line table
    work = ascii_build_dir(project_dir / "build") / "init"
    try:
        elf = compile_target(tc.require_xc8(), probe, work)
    except CompileError as first:
        # old code such as `dig3=3;` outside any function (implicit int) builds only as C90
        probe.xc8_args = ["-std=c90"]
        try:
            elf = compile_target(tc.require_xc8(), probe, work)
        except CompileError:
            raise first from None
        xc8_args = probe.xc8_args
    table = read_line_table(elf.with_suffix(".cmf"), compiled_name(probe))
    target, facts = guess("\n".join(read_source(source)), table, regs, pins, polarity, bank, writes, digits=digits)
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
