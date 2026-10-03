"""Sample one pin instruction by instruction in the simulator and summarize the waveform."""
from __future__ import annotations

import re

from .mdb import header


def wave_commands(device, elf, source_name, at, pin, samples, wait_ms):
    cmds = header(device, elf) + [f"Break {source_name}:{at}", "Run", f"Wait {wait_ms}",
                                  "Stopwatch", f"print pin {pin}"]
    for _ in range(samples):
        cmds += ["Stepi 1", "Stopwatch", f"print pin {pin}"]
    cmds.append("Quit")
    return cmds


def parse_samples(text, pin):
    """(cycle, level) pairs: every stopwatch line is followed by the pin line (e.g. 'RC5  Dout  HIGH  ...')."""
    pts, cycle = [], None
    pin_line = re.compile(rf"^{re.escape(pin)}\s+\S+\s+(HIGH|LOW)\b", re.I)
    for raw in text.splitlines():
        s = raw.strip()
        m = re.match(r"Stopwatch cycle count = (\d+)", s)
        if m:
            cycle = int(m.group(1))
            continue
        m = pin_line.match(s)
        if m and cycle is not None:
            pts.append((cycle, 1 if m.group(1).upper() == "HIGH" else 0))
            cycle = None
    return pts


def summarize(points, pin, at, samples):
    if len(points) < 2:
        raise ValueError(f"{pin} の値が読めていない（{len(points)} 点）")
    edges = [[points[0][0], points[0][1]]]
    for cycle, level in points[1:]:
        if level != edges[-1][1]:
            edges.append([cycle, level])
    rises = [c for c, lv in edges[1:] if lv == 1]
    falls = [c for c, lv in edges[1:] if lv == 0]
    periods = [b - a for a, b in zip(rises, rises[1:])]
    highs = []
    for r in rises:
        f = next((x for x in falls if x > r), None)
        if f is not None:
            highs.append(f - r)
    start, end = points[0][0], points[-1][0]
    high_time = 0
    for i, (cycle, level) in enumerate(edges):
        nxt = edges[i + 1][0] if i + 1 < len(edges) else end
        if level:
            high_time += nxt - cycle
    return {"pin": pin, "at": at, "samples": samples, "start": start, "end": end, "edges": edges,
            "periods": periods, "highs": highs,
            "high_fraction": round(high_time / (end - start), 4) if end > start else float(points[0][1])}
