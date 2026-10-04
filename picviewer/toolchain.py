"""Find MPLAB X (mdb), XC8 and the device packs on this machine."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ENV_MPLABX = "PICVIEWER_MPLABX"
ENV_XC8 = "PICVIEWER_XC8"
ENV_PACKS = "PICVIEWER_PACKS"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: keep child consoles from flashing on Windows
BELOW_NORMAL = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS: a long batch must not make the machine sluggish


class ToolNotFound(Exception):
    """A required program or device file is not installed (or not where we looked)."""


def version_key(name: str) -> tuple:
    """'v6.35' -> (6, 35), 'v3.0.0' -> (3, 0, 0). Names without digits sort first."""
    nums = re.findall(r"\d+", name)
    return tuple(int(n) for n in nums) if nums else (-1,)


def _newest(dirs):
    dirs = [d for d in dirs if d.is_dir()]
    return max(dirs, key=lambda d: version_key(d.name)) if dirs else None


def _install_roots(*parts):
    """Where Microchip installs tools on this OS."""
    if sys.platform == "win32":
        bases = [os.environ.get("ProgramFiles", "C:/Program Files"),
                 os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")]
        return [Path(b).joinpath("Microchip", *parts) for b in bases]
    if sys.platform == "darwin":
        return [Path("/Applications/microchip").joinpath(*parts)]
    return [Path("/opt/microchip").joinpath(*parts)]


def find_mplabx(override=None):
    """MPLAB X install directory (the one holding mplab_platform). The newest version wins."""
    given = override or os.environ.get(ENV_MPLABX)
    if given:
        return Path(given)
    name = "MPLABX" if sys.platform == "win32" else "mplabx"
    found = []
    for root in _install_roots(name):
        if root.is_dir():
            found += [d for d in root.iterdir() if (d / "mplab_platform").is_dir()]
    return _newest(found)


def mdb_path(mplabx):
    if mplabx is None:
        return None
    bin_dir = Path(mplabx) / "mplab_platform" / "bin"
    for name in ("mdb.bat", "mdb.sh", "mdb"):
        if (bin_dir / name).is_file():
            return bin_dir / name
    return None


def find_xc8(override=None):
    """xc8-cc. Looks in the normal install folders (including the winget one), then on PATH."""
    given = override or os.environ.get(ENV_XC8)
    if given:
        return Path(given)
    exe = "xc8-cc.exe" if sys.platform == "win32" else "xc8-cc"
    found = []
    for parts in (("xc8",), ("MPLABXC8",)):
        for root in _install_roots(*parts):
            if root.is_dir():
                found += [d for d in root.iterdir() if (d / "bin" / exe).is_file()]
    best = _newest(found)
    if best is not None:
        return best / "bin" / exe
    on_path = shutil.which("xc8-cc")
    return Path(on_path) if on_path else None


def find_pack_dirs(mplabx, extra=None):
    """Folders that hold device packs: the given one, PICVIEWER_PACKS, MPLAB X's own, and the user pack cache."""
    dirs = []
    for given in (extra, os.environ.get(ENV_PACKS)):
        if given:
            dirs.append(Path(given))
    if mplabx is not None:
        dirs.append(Path(mplabx) / "packs" / "Microchip")
    dirs.append(Path.home() / ".mchp_packs" / "Microchip")
    seen, out = set(), []
    for d in dirs:
        key = str(d).lower()
        if d.is_dir() and key not in seen:
            seen.add(key)
            out.append(d)
    return out


def ascii_path(path):
    """The path in ASCII only. XC8 cannot open a file whose path has other characters (it receives them
    mangled), so on Windows the short 8.3 name of an existing path is used; None when there is none."""
    path = Path(path)
    if str(path).isascii() or sys.platform != "win32":
        return path                     # elsewhere a path is bytes, and XC8 opens it as it is
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf)) and buf.value.isascii():
        return Path(buf.value)
    return None


def find_device_file(device, pack_dirs):
    """(path, pack name, pack version) of <device>.PIC. The newest pack version wins."""
    best = None
    for root in pack_dirs:
        for f in Path(root).glob(f"*/*/edc/{device}.PIC"):
            pack, ver = f.parent.parent.parent.name, f.parent.parent.name
            key = version_key(ver)
            if best is None or key > best[0]:
                best = (key, f, pack, ver)
    if best is None:
        where = ", ".join(str(d) for d in pack_dirs) or "(パックの置き場が見つからない)"
        raise ToolNotFound(f"{device} のデバイス定義ファイル（{device}.PIC）が無い。探した場所: {where}")
    return best[1], best[2], best[3]


def xc8_version(xc8):
    """'V3.00' from `xc8-cc --version`; falls back to the install folder name."""
    if xc8 is None:
        return None
    try:
        kw = {"creationflags": NO_WINDOW} if sys.platform == "win32" else {}
        out = subprocess.run([str(xc8), "--version"], capture_output=True, text=True, timeout=30, **kw).stdout
        m = re.search(r"Compiler\s+V(\d+\.\d+)", out)
        if m:
            return "v" + m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    for part in reversed(Path(xc8).parts):
        if re.fullmatch(r"v\d[\d.]*", part, re.I):
            return part
    return None


@dataclass
class Toolchain:
    mplabx: Path | None
    mdb: Path | None
    xc8: Path | None
    pack_dirs: list = field(default_factory=list)

    @classmethod
    def detect(cls, mplabx=None, xc8=None, packs=None):
        mx = find_mplabx(mplabx)
        return cls(mplabx=mx, mdb=mdb_path(mx), xc8=find_xc8(xc8), pack_dirs=find_pack_dirs(mx, packs))

    @property
    def mplabx_version(self):
        return self.mplabx.name if self.mplabx else None

    def require_mdb(self):
        if self.mdb is None or not self.mdb.exists():
            raise ToolNotFound("MPLAB X の mdb が見つからない。MPLAB X IDE を入れるか、"
                               f"--mplabx か環境変数 {ENV_MPLABX} で置き場所（例 C:/Program Files/Microchip/MPLABX/v6.35）を指定する。")
        return self.mdb

    def require_xc8(self):
        if self.xc8 is None or not self.xc8.exists():
            raise ToolNotFound(f"XC8（xc8-cc）が見つからない。XC8 を入れるか、--xc8 か環境変数 {ENV_XC8} で xc8-cc の場所を指定する。")
        return self.xc8

    def describe(self):
        packs = ", ".join(str(p) for p in self.pack_dirs) or "見つからない"
        return [
            f"MPLAB X : {self.mplabx or '見つからない'}",
            f"mdb     : {self.mdb or '見つからない'}",
            f"XC8     : {self.xc8 or '見つからない'}" + (f"（{xc8_version(self.xc8)}）" if self.xc8 else ""),
            f"パック  : {packs}",
        ]
