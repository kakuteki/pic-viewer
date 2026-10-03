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
    """(kind, line the stop must be at or None, line to show as executed or None) per stop."""
    out = [("start", plan[0]["run_to"], None)]
    for a in plan[1:]:
        if "step" in a:
            out += [("step", None, None)] * a["step"]
        else:
            out.append(("run", a["run_to"], a.get("show", a["run_to"])))
    return out


def make_steps(plan, records, registers):
    expected = expand_plan(plan)
    if len(records) != len(expected):
        raise MdbError(f"止まった回数 {len(records)} が計画の {len(expected)} 回と合わない")
    cycles = absolute_cycles(records, [k for k, _, _ in expected])
    steps = []
    for i, ((kind, want, show), rec, cyc) in enumerate(zip(expected, records, cycles)):
        if want is not None and rec["line"] != want:
            raise MdbError(f"{i + 1} 回目は {want} 行目で止まるはずが {rec['line']} 行目で止まった")
        if kind == "start":
            executed = None
        elif kind == "step":
            executed = steps[-1]["next"]
        else:
            executed = show
        steps.append({"kind": kind, "exec": executed, "next": rec["line"], "addr": rec["addr"], "cycles": cyc,
                      "v": [rec["values"][r["name"]] & r["mask"] for r in registers]})
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
