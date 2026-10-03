"""Read facts about a PIC from Microchip's device definition file (<device>.PIC in a device pack)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{http://crownking/edc}"


def _a(el, name, default=None):
    return el.get(NS + name, default)


class PicDef:
    def __init__(self, path):
        self.path = Path(path)
        self.root = ET.parse(self.path).getroot()
        self.name = _a(self.root, "name")
        self.arch = _a(self.root, "arch")
        self.dsid = _a(self.root, "dsid")

    def nominal_vdd(self):
        vdd = self.root.find(f"{NS}Power/{NS}VDD")
        value = _a(vdd, "nominalvoltage") if vdd is not None else None
        return float(value) if value else None

    def pins(self):
        """Pins in package order; each item lists the function names on that pin."""
        plist = self.root.find(f"{NS}PinList")
        if plist is None:
            return []
        return [[_a(v, "name") for v in pin.findall(f"{NS}VirtualPin")] for pin in plist.findall(f"{NS}Pin")]

    def pin_numbers(self, func):
        """1-based pin numbers whose function list contains func (case-insensitive)."""
        f = func.lower()
        return [i for i, names in enumerate(self.pins(), start=1) if f in (n.lower() for n in names)]

    def sfr(self, cname):
        """Width, implemented-bit mask, reset value and per-bit names (index = bit) of one SFR."""
        for el in self.root.iter(NS + "SFRDef"):
            if _a(el, "cname") == cname:
                break
        else:
            raise KeyError(cname)
        width = int(_a(el, "nzwidth", "0x8"), 0)
        single, multi = {}, {}
        for mode in el.iter(NS + "SFRMode"):
            pos = 0
            for child in mode:
                if child.tag == NS + "AdjustPoint":
                    pos += int(_a(child, "offset"), 0)
                elif child.tag == NS + "SFRFieldDef":
                    w = int(_a(child, "nzwidth"), 0)
                    for i in range(w):
                        if w == 1:
                            single.setdefault(pos, _a(child, "cname"))
                        else:
                            multi.setdefault(pos + i, f"{_a(child, 'cname')}<{i}>")
                    pos += w
        bits = [single.get(b) or multi.get(b) for b in range(width)]
        por = _a(el, "por", "")
        if len(por) == width:      # '-' marks a bit that does not exist; more reliable than the impl attribute
            mask = sum(1 << (width - 1 - i) for i, c in enumerate(por) if c != "-")
        else:
            mask = int(_a(el, "impl", hex((1 << width) - 1)), 0)
        return {"name": cname, "addr": _a(el, "_addr"), "width": width, "mask": mask, "por": por, "bits": bits}
