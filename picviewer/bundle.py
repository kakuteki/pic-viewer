"""A bundle holds everything one page needs for one target, with no file path or tool dependency."""
from __future__ import annotations

import json
from pathlib import Path

from . import __version__
from .mdb import MdbError, absolute_cycles
from .project import watch_list

SCHEMA = 1


def register_info(picdef, names):
    out = []
    for n in names:
        try:
            s = picdef.sfr(n)
        except KeyError:
            raise MdbError(f"{picdef.name} に {n} というレジスタは無い（8 ビットの SFR の名前で書く）") from None
        if s["width"] > 8:
            raise MdbError(f"{n} は {s['width']} ビット。8 ビットのレジスタ（例 CCPR1L と CCPR1H）に分けて書く")
        bits = [b if (s["mask"] >> i) & 1 and b and "<" not in b else None for i, b in enumerate(s["bits"])]
        out.append({"name": n, "mask": s["mask"], "bits": bits})
    return out


def expand_plan(plan):
    """One entry per stop: kind, the line it must stop at (or None), the line to show (or None),
    the inputs set just before it, and for write stops the register and the line that ends the run."""
    out, inputs, notes = [], {}, []
    key = None                       # the key pressed ("5") or released ("") before the next stop
    started, segment = False, 0
    for a in plan:
        if "set" in a or "press" in a or "release" in a:
            if "set" in a:
                inputs.update(a["set"])
            else:
                key = a.get("press", "")
            if a.get("note"):
                notes.append(a["note"])
            continue
        if "step" in a:
            new = [{"kind": "step", "want": None, "show": None}] * a["step"]
        elif "run_to" in a:
            new = [{"kind": "run" if started else "start", "want": a["run_to"],
                    "show": a.get("show", a["run_to"]) if started else None}]
            started = True
        else:
            segment += 1
            new = [{"kind": "write", "want": None, "show": None, "watch": watch_list(a),
                    "until": a.get("until"), "segment": segment}] * a["count"]
        new = [dict(e) for e in new]
        if inputs or notes or key is not None:
            new[0]["inputs"], new[0]["input_note"] = inputs, "、".join(notes)
            if key is not None:
                new[0]["key"] = key
            inputs, notes, key = {}, [], None
        out += new
    return out


def make_steps(plan, records, registers, writer_line=None, delay_var=None, us_per_ms=0, line_of=None, skipped=()):
    """Steps for the page. writer_line(address) gives the source line of the instruction that made a write stop,
    line_of(address) the line of a stop address the ELF gives no line for (from XC8's map file).
    delay_var is the fast-forward count of milliseconds asked of __delay_ms; us_per_ms of each were cut away.
    skipped: the stops of the plan (counted from 0) that were not made (mdb.skipped_stops), so have no record.

    A write stop made by Halt after the wait ran out becomes a 'timeout' step (nothing was written);
    a second one in a row within the same until_write is dropped, as it only repeats the first.
    """
    expected = [e for i, e in enumerate(expand_plan(plan)) if i not in skipped]
    if len(records) != len(expected):
        raise MdbError(f"止まった回数 {len(records)} が計画の {len(expected)} 回と合わない")
    cycles = absolute_cycles(records, [e["kind"] for e in expected])
    steps, ended, carry, last_kind = [], set(), {}, {}
    here = None                      # the line the program was at after the previous stop, kept or dropped
    values = None                    # the register values read at that stop
    for i, (e, rec, cyc) in enumerate(zip(expected, records, cycles)):
        # the absence of a breakpoint message means our Halt stopped it; only stops after Continue can be that
        timeout = rec.get("timeout", False) and e["kind"] != "step"
        next_line = rec["line"]
        if next_line is None and line_of and rec["addr"] and not rec.get("where"):
            next_line = line_of(int(rec["addr"], 16))     # code the compiler added inside the program
        before, here = here, next_line
        before_values, values = values, rec["values"]
        if e["kind"] == "write" and (e["segment"] in ended or (timeout and last_kind.get(e["segment"]) == "timeout")):
            carry.update({k: e[k] for k in ("inputs", "input_note", "key") if k in e})   # dropped stop: keep its inputs
            continue
        if timeout and e["kind"] != "write":
            where = f"{rec['line']} 行目のあたり" if rec["line"] else "行番号の無い所"
            raise MdbError(f"{i + 1} 回目は {e['want']} 行目で止まるはずが、着かないまま待ち時間が過ぎた"
                           f"（止めた所は {where}）。入力（set）か wait_ms を見直す")
        if e["want"] is not None and rec["line"] != e["want"]:
            raise MdbError(f"{i + 1} 回目は {e['want']} 行目で止まるはずが {rec['line']} 行目で止まった")
        if next_line is None and e["kind"] == "step" and not rec.get("where"):
            raise MdbError(f"{i + 1} 回目の停止で行番号が読めない（行番号の無いところで止まった）")
        kind = e["kind"]
        if kind == "start":
            executed = None
        elif kind == "step":
            executed = before
        elif kind == "run":
            executed = e["show"]
        elif timeout:
            kind, executed = "timeout", None       # nothing was written while we waited
        elif e.get("until") and rec["line"] == e["until"]:
            kind, executed = "end", e["until"]     # reached the closing line: no more writes are collected
            ended.add(e["segment"])
        elif rec.get("where"):
            executed = None                        # written by code in another file (XC8's or an included one)
        else:
            line = writer_line(int(rec["addr"], 16)) if writer_line and rec["addr"] else None
            executed = line or rec["line"]
        if e["kind"] == "write":
            last_kind[e["segment"]] = kind
        step = {"kind": kind, "exec": executed, "next": next_line, "addr": rec["addr"], "cycles": cyc,
                "v": [rec["values"][r["name"]] & r["mask"] for r in registers]}
        if rec.get("where"):
            step["where"] = rec["where"]
        if e["kind"] == "write":
            watched = e["watch"]

            def out_bits(values, reg):
                # a PORT value also moves when only an input changed: compare the output bits (TRIS 0) when known
                tris = values.get("TRIS" + reg[-1]) if reg.startswith("PORT") else None
                return values.get(reg, 0) & (~tris & 0xFF if tris is not None else 0xFFFF)
            changed = [r for r in watched if before_values and r in rec["values"]
                       and out_bits(rec["values"], r) != out_bits(before_values, r)]
            # one name when one register is watched, or when only one of several changed
            step["watch"] = changed[0] if len(watched) > 1 and len(changed) == 1 else " か ".join(watched)
        inputs = {**carry, **{k: e[k] for k in ("inputs", "input_note", "key") if k in e}}
        carry = {}
        if inputs.get("inputs"):
            step["inputs"] = inputs["inputs"]
        if inputs.get("input_note") and (inputs.get("inputs") or "key" in inputs):
            step["input_note"] = inputs["input_note"]
        if "key" in inputs:
            step["key"] = inputs["key"]
        if delay_var and delay_var in rec["values"]:
            step["skipped_us"] = (rec["values"][delay_var] & 0xFFFFFFFF) * us_per_ms
        steps.append(step)
    return steps


def make_bundle(target, picdef, pack, tools, source_lines, registers, steps, waves, traced):
    return {
        "schema": SCHEMA,
        "tool": f"picviewer {__version__}",
        "target": target.id,
        "device": target.device,
        "arch": picdef.arch,
        "datasheet_id": picdef.dsid,
        "pack": pack,
        "tools": tools,
        "traced": traced,
        "fast_forward": target.fast_forward,
        "vdd": picdef.nominal_vdd(),
        "source_name": target.source_name,
        "pins": picdef.pins(),
        "regs": registers,
        "steps": steps,
        "waves": waves,
        "source": source_lines,
    }


def read_source(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1").splitlines()


def dumps(bundle):
    """JSON with one list item per line, so that a change shows up as a small diff."""
    keys = list(bundle)
    lines = ["{"]
    for i, k in enumerate(keys):
        v = bundle[k]
        end = "," if i < len(keys) - 1 else ""
        if isinstance(v, list) and v:
            lines.append(f" {json.dumps(k)}: [")
            for j, item in enumerate(v):
                lines.append("  " + json.dumps(item, ensure_ascii=False) + ("," if j < len(v) - 1 else ""))
            lines.append(" ]" + end)
        else:
            lines.append(f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}{end}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write(path, bundle):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(bundle), encoding="utf-8", newline="\n")


def read(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise MdbError(f"{path}: 形式の版が違う（{data.get('schema')}、この版は {SCHEMA}）。build をやり直す")
    return data
