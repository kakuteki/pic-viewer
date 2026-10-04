"""A bundle holds everything one page needs for one target, with no file path or tool dependency."""
from __future__ import annotations

import json
from pathlib import Path

from . import __version__
from .mdb import MdbError, absolute_cycles

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
    started, segment = False, 0
    for a in plan:
        if "set" in a:
            inputs.update(a["set"])
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
            new = [{"kind": "write", "want": None, "show": None, "watch": a["until_write"],
                    "until": a.get("until"), "segment": segment}] * a["count"]
        new = [dict(e) for e in new]
        if inputs or notes:
            new[0]["inputs"], new[0]["input_note"] = inputs, "、".join(notes)
            inputs, notes = {}, []
        out += new
    return out


def make_steps(plan, records, registers, writer_line=None, skipped_var=None):
    """Steps for the page. writer_line(address) gives the source line of the instruction that made a write stop."""
    expected = expand_plan(plan)
    if len(records) != len(expected):
        raise MdbError(f"止まった回数 {len(records)} が計画の {len(expected)} 回と合わない")
    cycles = absolute_cycles(records, [e["kind"] for e in expected])
    steps, ended, carry = [], set(), {}
    for i, (e, rec, cyc) in enumerate(zip(expected, records, cycles)):
        if e["kind"] == "write" and e["segment"] in ended:
            carry.update({k: e[k] for k in ("inputs", "input_note") if k in e})   # dropped stop: keep its inputs
            continue
        if e["want"] is not None and rec["line"] != e["want"]:
            raise MdbError(f"{i + 1} 回目は {e['want']} 行目で止まるはずが {rec['line']} 行目で止まった")
        kind = e["kind"]
        if kind == "start":
            executed = None
        elif kind == "step":
            executed = steps[-1]["next"]
        elif kind == "run":
            executed = e["show"]
        elif e.get("until") and rec["line"] == e["until"]:
            kind, executed = "end", e["until"]     # reached the closing line: no more writes are collected
            ended.add(e["segment"])
        else:
            line = writer_line(int(rec["addr"], 16)) if writer_line and rec["addr"] else None
            executed = line or rec["line"]
        step = {"kind": kind, "exec": executed, "next": rec["line"], "addr": rec["addr"], "cycles": cyc,
                "v": [rec["values"][r["name"]] & r["mask"] for r in registers]}
        if e["kind"] == "write":
            step["watch"] = e["watch"]
        inputs = {**carry, **{k: e[k] for k in ("inputs", "input_note") if k in e}}
        carry = {}
        if inputs.get("inputs"):
            step["inputs"] = inputs["inputs"]
            if inputs.get("input_note"):
                step["input_note"] = inputs["input_note"]
        if skipped_var and skipped_var in rec["values"]:
            step["skipped_us"] = rec["values"][skipped_var] & 0xFFFFFFFF
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
