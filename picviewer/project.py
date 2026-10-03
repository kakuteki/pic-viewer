"""Load and check a project file (picviewer.json)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CIRCUIT_TYPES = ("pins", "led", "hbridge")
COUNTER_RE = re.compile(r"^(TMR\d+[LH]?|T\d+TMR[LH]?)$")
PROJECT_KEYS = {"title", "output", "bundles", "build", "circuit", "wait_ms", "targets"}
TARGET_KEYS = {"id", "device", "source", "fosc_hz", "summary", "registers", "counters", "trace",
               "circuit", "notes", "waves", "xc8_args", "wait_ms"}
DEFAULT_WAIT_MS = 600000


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


def normalize_device(name):
    n = str(name).strip().upper()
    if not n.startswith("PIC"):
        n = "PIC" + n
    if not re.fullmatch(r"PIC\d{2}[A-Z]{1,3}\d{2,5}[A-Z0-9]*", n):
        raise ProjectError(f"device の書き方が読めない: {name!r}（例 PIC16F886）")
    return n


def _pos_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def parse_plan(raw, where):
    """trace: [{"run_to": L}, {"step": N} | {"run_to": L, "show": L2}, ...]"""
    if not isinstance(raw, list) or not raw:
        raise ProjectError(f"{where}: trace が空")
    first = raw[0]
    if not (isinstance(first, dict) and set(first) == {"run_to"} and _pos_int(first["run_to"])):
        raise ProjectError(f'{where}: trace の最初は {{"run_to": 行番号}}（main の最初の行など）')
    plan = [{"run_to": first["run_to"]}]
    for i, a in enumerate(raw[1:], start=2):
        if isinstance(a, dict) and set(a) == {"step"} and _pos_int(a["step"]):
            plan.append({"step": a["step"]})
        elif (isinstance(a, dict) and "run_to" in a and set(a) <= {"run_to", "show"} and _pos_int(a["run_to"])
              and (a.get("show") is None or _pos_int(a["show"]))):
            plan.append({"run_to": a["run_to"], "show": a.get("show") or a["run_to"]})
        else:
            raise ProjectError(f'{where}: trace の {i} 番目が読めない: {a!r}（{{"step": 回数}} か {{"run_to": 行, "show": 行}}）')
    return plan


def plan_lines(plan):
    return sorted({a["run_to"] for a in plan if "run_to" in a})


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
    circuit = dict(defaults.get("circuit", {}))
    circuit.update(raw.get("circuit", {}))
    circuit.setdefault("type", "pins")
    if circuit["type"] not in CIRCUIT_TYPES:
        raise ProjectError(f"{where}: circuit.type は {CIRCUIT_TYPES} のどれか: {circuit['type']!r}")
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
    return Target(
        id=tid, device=normalize_device(raw["device"]), source=source,
        source_name=Path(raw["source"]).as_posix(), registers=list(regs),
        trace=parse_plan(raw["trace"], where), fosc_hz=fosc, summary=raw.get("summary", ""),
        notes=dict(notes), circuit=circuit, counters=list(counters), waves=waves,
        xc8_args=list(raw.get("xc8_args", [])), wait_ms=wait_ms,
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
        build_dir=base / raw.get("build", "build"),
        targets=targets,
    )
