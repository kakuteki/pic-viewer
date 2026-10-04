"""Compile one target with XC8 into an ELF that carries line information for the simulator."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .toolchain import BELOW_NORMAL, NO_WINDOW

# milliseconds asked of __delay_ms so far, read at every stop. Counting milliseconds rather than the
# microseconds cut away keeps 32 bits good for 49 days of program time (microseconds overflow in 71 minutes).
FAST_VAR = "picviewer_delay_ms"


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
    """-D option: wait 1/factor of the time and count the milliseconds asked (a block-scope extern needs no header)."""
    return (f"-DPICVIEWER_DELAY_MS(x)=do {{ extern volatile unsigned long {FAST_VAR}; "
            f"{FAST_VAR} += (unsigned long)(x); __delay_us((x) * {1000 // factor}UL); }} while (0)")


def skipped_per_ms(factor):
    """Microseconds cut away from each millisecond asked of __delay_ms."""
    return 1000 - 1000 // factor


def compiled_name(target):
    """File name XC8 and mdb see. XC8 takes only .c sources ('.xc8' gives error 894), so others become <stem>.c."""
    return target.source.name if target.source.suffix.lower() == ".c" else target.source.stem + ".c"


def compile_target(xc8, target, work):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    if not target.source.is_file():
        raise CompileError(f"ソースが無い: {target.source}")
    source, extra = target.source, []
    name = compiled_name(target)
    if target.fast_forward or name != target.source.name:
        # a copy under build/: the original is never touched, and #include "..." still finds its neighbours
        sim = work / "sim"
        sim.mkdir(exist_ok=True)
        text = target.source.read_text(encoding="utf-8", errors="surrogateescape")
        if target.fast_forward:
            text, _ = fast_source(text)
            extra.append(fast_define(target.fast_forward))
        source = sim / name
        source.write_text(text, encoding="utf-8", errors="surrogateescape", newline="\n")
        extra.append(f"-I{target.source.parent}")
    elf = work / (target.source.stem + ".elf")
    cmd = [str(xc8), f"-mcpu={target.device[3:]}", "-O0", "-o", elf.name, str(source), *extra, *target.xc8_args]
    kw = {"creationflags": NO_WINDOW | BELOW_NORMAL} if sys.platform == "win32" else {}
    r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, errors="replace", **kw)
    log = (r.stdout or "") + (r.stderr or "")
    (work / "compile.log").write_text(log, encoding="utf-8")
    if r.returncode != 0 or not elf.is_file():
        tail = "\n".join(log.strip().splitlines()[-15:])
        raise CompileError(f"XC8 が失敗した（終了コード {r.returncode}）:\n{tail}")
    return elf
