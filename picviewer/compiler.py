"""Compile one target with XC8 into an ELF that carries line information for the simulator."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .toolchain import NO_WINDOW


class CompileError(Exception):
    """XC8 failed, or the source is missing."""


def compile_target(xc8, target, work):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    if not target.source.is_file():
        raise CompileError(f"ソースが無い: {target.source}")
    elf = work / (target.source.stem + ".elf")
    cmd = [str(xc8), f"-mcpu={target.device[3:]}", "-O0", "-o", elf.name, str(target.source), *target.xc8_args]
    kw = {"creationflags": NO_WINDOW} if sys.platform == "win32" else {}
    r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, errors="replace", **kw)
    log = (r.stdout or "") + (r.stderr or "")
    (work / "compile.log").write_text(log, encoding="utf-8")
    if r.returncode != 0 or not elf.is_file():
        tail = "\n".join(log.strip().splitlines()[-15:])
        raise CompileError(f"XC8 が失敗した（終了コード {r.returncode}）:\n{tail}")
    return elf
