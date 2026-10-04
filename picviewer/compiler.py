"""Compile one target with XC8 into an ELF that carries line information for the simulator."""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

from .toolchain import BELOW_NORMAL, NO_WINDOW, ascii_path

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


def compat_source(text):
    """Old HI-TECH C forms XC8 v3 refuses, rewritten on the same line: `void interrupt isr(void)` becomes
    `void __interrupt() isr(void)` (low_priority or high_priority, before or after `interrupt`, kept as the
    argument). Only spaces and tabs are matched, so no line is joined to the next."""
    prio = r"(?:(low_priority|high_priority)[ \t]+)?"
    return re.subn(rf"\bvoid[ \t]+{prio}interrupt[ \t]+{prio}([A-Za-z_]\w*)[ \t]*\(",
                   lambda m: f"void __interrupt({m.group(1) or m.group(2) or ''}) {m.group(3)}(", text)


def copy_includes(text, src_dir, dest_dir, seen=None):
    """Copy what the source takes in with #include "..." (and what that takes in) next to its copy, at the same
    relative places: for a source folder XC8 cannot be given with -I because its path is not ASCII."""
    seen = set() if seen is None else seen
    for rel in re.findall(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', text, re.M):
        rel = rel.replace("\\", "/")
        src = (Path(src_dir) / rel).resolve()
        if src in seen or not src.is_file():
            continue
        seen.add(src)
        dest = Path(dest_dir) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = src.read_text(encoding="utf-8", errors="surrogateescape")
        dest.write_text(body, encoding="utf-8", errors="surrogateescape", newline="\n")
        copy_includes(body, src.parent, dest.parent, seen)


def compiled_name(target):
    """File name XC8 and mdb see. XC8 takes only .c sources ('.xc8' gives error 894), so others become <stem>.c;
    a name that is not ASCII (XC8 cannot open it) becomes src_<hash>.c."""
    name = target.source.name if target.source.suffix.lower() == ".c" else target.source.stem + ".c"
    if not name.isascii():
        name = "src_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8] + ".c"
    return name


def elf_path(target, work):
    """Where compile_target puts the ELF (the .cmf map file is next to it)."""
    return Path(work) / (Path(compiled_name(target)).stem + ".elf")


def require_ascii(path):
    short = ascii_path(path)
    if short is None:
        raise CompileError(f"XC8 は日本語などの入ったパスを開けず、短い名前（8.3）も無い: {path}。"
                           "build の置き場所（picviewer.json の build）を英数字だけのフォルダにする")
    return short


def compile_target(xc8, target, work):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    if not target.source.is_file():
        raise CompileError(f"ソースが無い: {target.source}")
    source, extra = target.source, []
    name = compiled_name(target)
    text = target.source.read_text(encoding="utf-8", errors="surrogateescape")
    text, old_forms = compat_source(text)
    if target.fast_forward or name != target.source.name or old_forms or not str(target.source).isascii():
        # a copy under build/: the original is never touched, and #include "..." still finds its neighbours
        sim = work / "sim"
        sim.mkdir(exist_ok=True)
        if target.fast_forward:
            text, _ = fast_source(text)
            extra.append(fast_define(target.fast_forward))
        source = sim / name
        source.write_text(text, encoding="utf-8", errors="surrogateescape", newline="\n")
        include = ascii_path(target.source.parent)     # for #include "..." next to the source, when XC8 can open it
        if include:
            extra.append(f"-I{include}")
        else:
            copy_includes(text, target.source.parent, sim)
    elf = elf_path(target, work)
    cmd = [str(xc8), f"-mcpu={target.device[3:]}", "-O0", "-o", elf.name, str(require_ascii(source)),
           *extra, *target.xc8_args]
    kw = {"creationflags": NO_WINDOW | BELOW_NORMAL} if sys.platform == "win32" else {}
    r = subprocess.run(cmd, cwd=require_ascii(work), capture_output=True, text=True, errors="replace", **kw)
    log = (r.stdout or "") + (r.stderr or "")
    (work / "compile.log").write_text(log, encoding="utf-8")
    if r.returncode != 0 or not elf.is_file():
        tail = "\n".join(log.strip().splitlines()[-15:])
        raise CompileError(f"XC8 が失敗した（終了コード {r.returncode}）:\n{tail}")
    return elf
