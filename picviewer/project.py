"""Load and check a project file (picviewer.json)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

CIRCUIT_TYPES = ("pins", "led", "hbridge", "leds", "lcd", "seg7", "seg7mux", "keypad", "pot")
COUNTER_RE = re.compile(r"^(TMR\d+[LH]?|T\d+TMR[LH]?)$")
PROJECT_KEYS = {"title", "output", "bundles", "build", "circuit", "wait_ms", "fast_forward", "targets"}
TARGET_KEYS = {"id", "device", "source", "fosc_hz", "summary", "registers", "counters", "trace",
               "circuit", "notes", "waves", "xc8_args", "wait_ms", "fast_forward"}
DEFAULT_WAIT_MS = 600000
FAST_FACTORS = (10, 100, 1000)


class ProjectError(Exception):
    """The project file is missing something or holds a wrong value."""


@dataclass
class Wave:
    pin: str
    at: int
    samples: int


@dataclass
class Target:
    id: str
    device: str
    source: Path
    source_name: str
    registers: list
    trace: list
    fosc_hz: int | None = None
    summary: str = ""
    notes: dict = field(default_factory=dict)
    circuit: dict = field(default_factory=lambda: {"type": "pins"})
    counters: list = field(default_factory=list)
    waves: list = field(default_factory=list)
    xc8_args: list = field(default_factory=list)
    wait_ms: int = DEFAULT_WAIT_MS
    fast_forward: int | None = None


@dataclass
class Project:
    path: Path
    dir: Path
    title: str
    output: Path
    bundle_dir: Path
    build_dir: Path
    targets: list

    def target(self, tid):
        for t in self.targets:
            if t.id == tid:
                return t
        raise ProjectError(f"target {tid!r} は {self.path.name} に無い（あるのは {', '.join(t.id for t in self.targets)}）")

    def bundle_path(self, tid):
        return self.bundle_dir / f"{tid}.json"


def ascii_build_dir(path):
    """XC8 cannot open files under a path with Japanese or other non-ASCII characters (it gets them mangled),
    and this PC has no 8.3 short names to fall back on. Such a build folder moves to an ASCII place."""
    if str(path).isascii():
        return path
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("PUBLIC"), tempfile.gettempdir()):
        if base and str(base).isascii():
            return Path(base) / "picviewer" / "build" / digest
    return path                        # nowhere better: compiling will say what is wrong


def normalize_device(name):
    n = str(name).strip().upper()
    if not n.startswith("PIC"):
        n = "PIC" + n
    if not re.fullmatch(r"PIC\d{2}[A-Z]{1,3}\d{2,5}[A-Z0-9]*", n):
        raise ProjectError(f"device の書き方が読めない: {name!r}（例 PIC16F886）")
    return n


def _pos_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def pin_level(value):
    """0 / 1 / 'high' / 'low' / '2.5V' -> what mdb's write pin takes ('high', 'low' or '2.5V'), or None."""
    if isinstance(value, bool) or value in (0, 1):
        return "high" if value else "low"
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("high", "low"):
            return s
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*v", s)
        if m:
            return f"{m.group(1)}V"
    return None


def parse_plan(raw, where):
    """trace: a list of actions.

    {"run_to": L}                       run (or continue) to line L and stop; the first stop must be one
    {"run_to": L, "show": S}            same, and show line S as the line that ran
    {"step": N}                         step N source lines, stopping at each
    {"set": {"RB0": 1, "AN0": "2.5V"}}  drive input pins before the next stop (optional "note")
    {"until_write": "PORTC", "count": N}  continue and stop after each write to the register, N times;
                                        a list (["PORTC", "PORTA"]) stops on a write to any of them;
                                        with "until": L, stop collecting when line L is reached
    {"press": "5"}                      hold a key of the keypad in the circuit (optional "note")
    {"release": true}                   let go of the held key (optional "note")
    """
    if not isinstance(raw, list) or not raw:
        raise ProjectError(f"{where}: trace が空")
    plan, started = [], False
    for i, a in enumerate(raw, start=1):
        bad = ProjectError(f"{where}: trace の {i} 番目が読めない: {a!r}")
        if not isinstance(a, dict):
            raise bad
        if set(a) == {"step"} and _pos_int(a["step"]):
            if not started:
                raise ProjectError(f'{where}: trace の最初の止め方は {{"run_to": 行番号}}（main の最初の行など）')
            plan.append({"step": a["step"]})
        elif "run_to" in a and set(a) <= {"run_to", "show"} and _pos_int(a["run_to"]) \
                and (a.get("show") is None or _pos_int(a["show"])):
            if not started:
                if "show" in a:
                    raise ProjectError(f"{where}: 最初の run_to に show は付けない")
                plan.append({"run_to": a["run_to"]})
                started = True
            else:
                plan.append({"run_to": a["run_to"], "show": a.get("show") or a["run_to"]})
        elif "set" in a and set(a) <= {"set", "note"} and isinstance(a["set"], dict) and a["set"]:
            levels = {}
            for pin, value in a["set"].items():
                level = pin_level(value)
                if not (isinstance(pin, str) and re.fullmatch(r"[A-Za-z]{1,6}\d{0,3}", pin)) or level is None:
                    raise ProjectError(f'{where}: trace の {i} 番目の set が読めない: {pin!r}: {value!r}'
                                       '（ピン名に 0、1、"high"、"low"、"2.5V" のどれか）')
                levels[pin.upper()] = level
            plan.append({"set": levels, "note": str(a.get("note", ""))})
        elif "press" in a and set(a) <= {"press", "note"} and isinstance(a["press"], str) and a["press"]:
            plan.append({"press": a["press"], "note": str(a.get("note", ""))})
        elif "release" in a and set(a) <= {"release", "note"} and a["release"] is True:
            plan.append({"release": True, "note": str(a.get("note", ""))})
        elif "until_write" in a and set(a) <= {"until_write", "count", "until", "wait_ms"} \
                and watch_list(a) and all(isinstance(r, str) and re.fullmatch(r"\w+", r) for r in watch_list(a)) \
                and _pos_int(a.get("count")) and (a.get("until") is None or _pos_int(a["until"])) \
                and (a.get("wait_ms") is None or _pos_int(a["wait_ms"])):
            if not started:
                raise ProjectError(f'{where}: until_write の前に {{"run_to": 行番号}} で一度止める')
            action = {"until_write": a["until_write"], "count": a["count"]}
            for key in ("until", "wait_ms"):
                if a.get(key) is not None:
                    action[key] = a[key]
            plan.append(action)
        else:
            raise bad
    if not started:
        raise ProjectError(f'{where}: trace に {{"run_to": 行番号}} が無い')
    return plan


def plan_lines(plan):
    """Lines the simulator must be able to stop at."""
    lines = {a["run_to"] for a in plan if "run_to" in a}
    lines |= {a["until"] for a in plan if a.get("until")}
    return sorted(lines)


def plan_pins(plan):
    return sorted({p for a in plan if "set" in a for p in a["set"]})


def plan_watches(plan):
    return sorted({r for a in plan if "until_write" in a for r in watch_list(a)})


def watch_list(action):
    """The registers an until_write watches, as a list (it may be written as one name)."""
    w = action.get("until_write")
    return [w] if isinstance(w, str) else list(dict.fromkeys(w)) if isinstance(w, list) else []


def circuit_parts(circuit):
    """The circuit of a target as a list of parts (a single dict is one part)."""
    return circuit if isinstance(circuit, list) else [circuit]


def keypad_of(circuit):
    """The keypad part of a circuit, or None."""
    return next((p for p in circuit_parts(circuit) if p.get("type") == "keypad"), None)


def keypad_keys(part):
    """{label: (row pin, column pin)} of a keypad part; raises ProjectError when the part is malformed."""
    rows, cols, keys = part.get("rows"), part.get("cols"), part.get("keys")
    pin_list = lambda v: isinstance(v, list) and v and all(isinstance(p, str) and re.fullmatch(r"R[A-E][0-7]", p.upper()) for p in v)
    if not (pin_list(rows) and pin_list(cols)):
        raise ProjectError('keypad の rows と cols はピン名の並び（例 ["RB0", "RB1", "RB2", "RB3"]）')
    if not (isinstance(keys, list) and len(keys) == len(rows)
            and all(isinstance(r, list) and len(r) == len(cols) and all(isinstance(k, str) for k in r) for r in keys)):
        raise ProjectError(f"keypad の keys は {len(rows)} 行 {len(cols)} 列の文字の表")
    out = {}
    for r, row in enumerate(keys):
        for c, label in enumerate(row):
            if label in out:
                raise ProjectError(f"keypad の keys に {label!r} が 2 つある")
            out[label] = (rows[r].upper(), cols[c].upper())
    return out


def _circuit(raw, defaults, where):
    """The target's circuit: its own, else the project's; a dict of its own is laid over a project dict."""
    own, common = raw.get("circuit"), defaults.get("circuit")
    for value, name in ((own, "circuit"), (common, "全体の circuit")):
        if value is not None and not isinstance(value, (dict, list)):
            raise ProjectError(f"{where}: {name} は回路の設定（辞書）か、その並び: {value!r}")
    listed = own if isinstance(own, list) else common if own is None and isinstance(common, list) else None
    if listed is not None:
        if not listed or not all(isinstance(p, dict) for p in listed):
            raise ProjectError(f"{where}: circuit を並べるときは、回路の設定（辞書）を 1 つ以上")
        parts = [dict(p) for p in listed]
    elif isinstance(common, list):
        raise ProjectError(f"{where}: 全体の circuit が並びのときは、target の circuit も並びで書く")
    else:
        merged = dict(common or {})
        merged.update(own or {})
        parts = [merged]
    for p in parts:
        p.setdefault("type", "pins")
        if p["type"] not in CIRCUIT_TYPES:
            raise ProjectError(f"{where}: circuit.type は {CIRCUIT_TYPES} のどれか: {p['type']!r}")
        if p["type"] == "keypad":
            try:
                keypad_keys(p)
            except ProjectError as e:
                raise ProjectError(f"{where}: {e}") from None
    return parts if listed is not None else parts[0]


def _target(raw, project_dir, defaults, index):
    where = f"targets[{index}]"
    if not isinstance(raw, dict):
        raise ProjectError(f"{where} が辞書でない")
    unknown = set(raw) - TARGET_KEYS
    if unknown:
        raise ProjectError(f"{where}: 知らない項目 {sorted(unknown)}（使えるのは {sorted(TARGET_KEYS)}）")
    for key in ("id", "device", "source", "registers", "trace"):
        if key not in raw:
            raise ProjectError(f"{where}: {key} が無い")
    tid = raw["id"]
    if not (isinstance(tid, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", tid)):
        raise ProjectError(f"{where}: id は英小文字・数字・_・- で書く: {tid!r}")
    where = f"target {tid}"
    regs = raw["registers"]
    if not (isinstance(regs, list) and regs and all(isinstance(r, str) and re.fullmatch(r"\w+", r) for r in regs)):
        raise ProjectError(f"{where}: registers はレジスタ名の並び")
    if len(set(regs)) != len(regs):
        raise ProjectError(f"{where}: registers に同じ名前が 2 回ある")
    notes = raw.get("notes", {})
    if not isinstance(notes, dict) or not all(re.fullmatch(r"[1-9]\d*", k) and isinstance(v, str) for k, v in notes.items()):
        raise ProjectError(f'{where}: notes は {{"行番号": "説明"}} の形')
    circuit = _circuit(raw, defaults, where)
    fosc = raw.get("fosc_hz")
    if fosc is not None and not _pos_int(fosc):
        raise ProjectError(f"{where}: fosc_hz は正の整数（Hz）")
    waves = []
    for w in raw.get("waves", []):
        if not (isinstance(w, dict) and set(w) <= {"pin", "at", "samples"} and isinstance(w.get("pin"), str)
                and _pos_int(w.get("at")) and _pos_int(w.get("samples", 800))):
            raise ProjectError(f'{where}: waves の要素は {{"pin": "RC5", "at": 行, "samples": 回数}}: {w!r}')
        waves.append(Wave(pin=w["pin"].upper(), at=w["at"], samples=w.get("samples", 800)))
    counters = raw.get("counters")
    if counters is None:
        counters = [r for r in regs if COUNTER_RE.match(r)]
    source = (project_dir / raw["source"]).resolve()
    wait_ms = raw.get("wait_ms", defaults.get("wait_ms", DEFAULT_WAIT_MS))
    if not _pos_int(wait_ms):
        raise ProjectError(f"{where}: wait_ms は正の整数（ミリ秒）")
    fast = raw.get("fast_forward", defaults.get("fast_forward"))
    if fast is not None and fast not in FAST_FACTORS:
        raise ProjectError(f"{where}: fast_forward は {FAST_FACTORS} のどれか（__delay_ms を何分の 1 にするか）")
    trace = parse_plan(raw["trace"], where)
    presses = [a["press"] for a in trace if "press" in a]
    if presses or any("release" in a for a in trace):
        pad = keypad_of(circuit)
        if pad is None:
            raise ProjectError(f'{where}: press と release には、circuit に {{"type": "keypad", ...}} が要る')
        unknown = sorted(set(presses) - set(keypad_keys(pad)))
        if unknown:
            raise ProjectError(f"{where}: keypad に無いキー: {unknown}")
    return Target(
        id=tid, device=normalize_device(raw["device"]), source=source,
        source_name=Path(raw["source"]).name, registers=list(regs),
        trace=trace, fosc_hz=fosc, summary=raw.get("summary", ""),
        notes=dict(notes), circuit=circuit, counters=list(counters), waves=waves,
        xc8_args=list(raw.get("xc8_args", [])), wait_ms=wait_ms, fast_forward=fast,
    )


def load(path):
    path = Path(path)
    if path.is_dir():
        path = path / "picviewer.json"
    if not path.is_file():
        raise ProjectError(f"プロジェクトファイルが無い: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProjectError(f"{path}: JSON として読めない: {e}") from None
    if not isinstance(raw, dict):
        raise ProjectError(f"{path}: 一番外側は辞書")
    unknown = set(raw) - PROJECT_KEYS
    if unknown:
        raise ProjectError(f"{path.name}: 知らない項目 {sorted(unknown)}（使えるのは {sorted(PROJECT_KEYS)}）")
    targets_raw = raw.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise ProjectError(f"{path.name}: targets が空")
    base = path.parent.resolve()
    targets = [_target(t, base, raw, i) for i, t in enumerate(targets_raw)]
    ids = [t.id for t in targets]
    if len(set(ids)) != len(ids):
        raise ProjectError(f"{path.name}: target の id が重なっている: {ids}")
    title = raw.get("title") or path.parent.name
    return Project(
        path=path.resolve(), dir=base, title=title,
        output=base / raw.get("output", "viewer.html"),
        bundle_dir=base / raw.get("bundles", "bundles"),
        build_dir=ascii_build_dir(base / raw.get("build", "build")),
        targets=targets,
    )
