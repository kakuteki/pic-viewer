"""Program address to source line, from the %LINETAB section of the map file (.cmf) that XC8 writes."""
from __future__ import annotations

import re
from pathlib import Path


def read_line_table(cmf, source_name):
    """[(address, line), ...] for one source file, in file order. Addresses are in the units mdb prints."""
    entries, in_table = [], False
    base = source_name.lower()
    path = Path(cmf)
    if not path.is_file():
        return entries
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw.startswith("%"):
            in_table = raw.strip() == "%LINETAB"
            continue
        if not in_table or raw.startswith(("#", "$")):
            continue
        m = re.match(r"([0-9A-Fa-f]+)\s+\S+\s+\S+\s+>(\d+):(.+)$", raw.strip())
        if m and re.split(r"[\\/]", m.group(3).strip())[-1].lower() == base:
            entries.append((int(m.group(1), 16), int(m.group(2))))
    return entries


def line_at(table, address):
    """Source line of the code at address: the last entry at or below it (later entries win on equal addresses)."""
    best = None
    for addr, line in table:
        if addr <= address and (best is None or addr >= best[0]):
            best = (addr, line)
    return best[1] if best else None


def writer_address(device, stop_address):
    """A data breakpoint stops right after the writing instruction: one word back (two bytes on PIC18)."""
    return stop_address - (2 if device.upper().startswith("PIC18") else 1)
