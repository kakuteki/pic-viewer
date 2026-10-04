"""Compile one target with XC8 into an ELF that carries line information for the simulator."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .toolchain import NO_WINDOW

FAST_VAR = "picviewer_skipped_us"      # microseconds of __delay_ms cut away so far, read at every stop


class CompileError(Exception):
    """XC8 failed, or the source is missing."""


def fast_source(text):
    """Source for the fast build: __delay_ms( renamed on the same line, the counter defined after the last line.

    Line numbers do not move, so breakpoints and the page refer to the original file.
    """
    new, count = re.subn(r"\b__delay_ms\s*\(", "PICVIEWER_DELAY_MS(", text)
    if not new.endswith("\n"):
        new += "\n"
    return new + f"volatile unsigned long {FAST_VAR};\n", count


def fast_define(factor):
    """-D option: wait 1/factor of the time and add the rest to the counter (a block-scope extern needs no header)."""
    real = 1000 // factor
    skipped = 1000 - real
    return (f"-DPICVIEWER_DELAY_MS(x)=do {{ extern volatile unsigned long {FAST_VAR}; "
            f"{FAST_VAR} += (unsigned long)(x) * {skipped}UL; __delay_us((x) * {real}UL); }} while (0)")


def compile_target(xc8, target, work):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    if not target.source.is_file():
        raise CompileError(f"ソースが無い: {target.source}")
    source, extra = target.source, []
    if target.fast_forward:
        sim = work / "sim"
        sim.mkdir(exist_ok=True)
        text = target.source.read_text(encoding="utf-8", errors="surrogateescape")
        new, _ = fast_source(text)
        source = sim / target.source.name
        source.write_text(new, encoding="utf-8", errors="surrogateescape", newline="\n")
        extra = [fast_define(target.fast_forward), f"-I{target.source.parent}"]
    elf = work / (target.source.stem + ".elf")
    cmd = [str(xc8), f"-mcpu={target.device[3:]}", "-O0", "-o", elf.name, str(source), *extra, *target.xc8_args]
    kw = {"creationflags": NO_WINDOW} if sys.platform == "win32" else {}
    r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, errors="replace", **kw)
    log = (r.stdout or "") + (r.stderr or "")
    (work / "compile.log").write_text(log, encoding="utf-8")
    if r.returncode != 0 or not elf.is_file():
        tail = "\n".join(log.strip().splitlines()[-15:])
        raise CompileError(f"XC8 が失敗した（終了コード {r.returncode}）:\n{tail}")
    return elf
