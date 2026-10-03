"""Tests that need neither MPLAB X nor XC8: parsing, planning, bundles, rendering and the server."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from picviewer import __version__
from picviewer.bundle import dumps, expand_plan, make_steps, register_info
from picviewer.cli import build_parser
from picviewer.mdb import MdbError, absolute_cycles, parse_records, probe_errors, trace_commands
from picviewer.picdef import PicDef
from picviewer.project import ProjectError, load, parse_plan
from picviewer.render import load_bundles, render_html
from picviewer.server import listing_html, make_server
from picviewer.toolchain import ENV_XC8, find_device_file, find_xc8, version_key
from picviewer.wave import parse_samples, summarize

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _fields(names):
    return "".join('<edc:SFRFieldDef edc:cname="%s" edc:nzwidth="0x%x"/>' % (n, w) for n, w in names)


TRISB_FIELDS = _fields([("TRISB%d" % i, 1) for i in range(8)])
OSCCON_FIELDS = _fields([("SCS", 1), ("LTS", 1), ("HTS", 1), ("OSTS", 1), ("IRCF", 3)])
OSCCON_LT_FIELDS = _fields([("IRCF0", 1), ("IRCF1", 1), ("IRCF2", 1)])
ANSELB_FIELDS = _fields([("ANSB4", 1), ("ANSB5", 1), ("ANSB6", 1), ("ANSB7", 1)])

MINI_PIC = """<?xml version='1.0' encoding='UTF-8'?>
<edc:PIC xmlns:edc="http://crownking/edc" edc:arch="16xxxx" edc:dsid="40001291" edc:name="PIC16F999">
  <edc:Power><edc:VDD edc:nominalvoltage="5.000"/></edc:Power>
  <edc:DataSpace>
    <edc:SFRDef edc:cname="TRISB" edc:impl="0xff" edc:nzwidth="0x8" edc:por="11111111" edc:_addr="0x86">
      <edc:SFRModeList><edc:SFRMode edc:id="DS.0">TRISB_FIELDS</edc:SFRMode></edc:SFRModeList>
    </edc:SFRDef>
    <edc:SFRDef edc:cname="OSCCON" edc:impl="0x7f" edc:nzwidth="0x8" edc:por="-110x000" edc:_addr="0x8F">
      <edc:SFRModeList>
        <edc:SFRMode edc:id="DS.0">OSCCON_FIELDS</edc:SFRMode>
        <edc:SFRMode edc:id="LT.0"><edc:AdjustPoint edc:offset="4"/>OSCCON_LT_FIELDS</edc:SFRMode>
      </edc:SFRModeList>
    </edc:SFRDef>
    <edc:SFRDef edc:cname="ANSELB" edc:impl="0xf0" edc:nzwidth="0x8" edc:por="--11----" edc:_addr="0x18D">
      <edc:SFRModeList><edc:SFRMode edc:id="DS.0"><edc:AdjustPoint edc:offset="4"/>ANSELB_FIELDS</edc:SFRMode></edc:SFRModeList>
    </edc:SFRDef>
    <edc:SFRDef edc:cname="WIDE" edc:nzwidth="0x10" edc:por="xxxxxxxxxxxxxxxx" edc:_addr="0x20"/>
  </edc:DataSpace>
  <edc:PinList>
    <edc:Pin><edc:VirtualPin edc:name="Vdd"/></edc:Pin>
    <edc:Pin><edc:VirtualPin edc:name="RB0"/><edc:VirtualPin edc:name="AN12"/></edc:Pin>
    <edc:Pin><edc:VirtualPin edc:name="Vss"/></edc:Pin>
    <edc:Pin><edc:VirtualPin edc:name="Vss"/></edc:Pin>
  </edc:PinList>
</edc:PIC>
""".replace("OSCCON_LT_FIELDS", OSCCON_LT_FIELDS).replace("OSCCON_FIELDS", OSCCON_FIELDS) \
   .replace("TRISB_FIELDS", TRISB_FIELDS).replace("ANSELB_FIELDS", ANSELB_FIELDS)

# what mdb prints for: Run to 13, one Step, Delete + Continue to 20, one Step (paths trimmed)
TRACE_LOG = """\
Device PIC16F999
Hwtool SIM
Program "/work/led.elf"
Programming target...
Program succeeded.
Break led.c:13
Breakpoint 0 at file led.c, line 13.
Run
Running
Wait 600000
Stop at
\taddress:0x7de
\tfile:/work/led.c
\tsource line:13
Print TRISB
TRISB=
255
Print TMR2
TMR2=0
Stopwatch
Stopwatch cycle count = 9 (9 \u00b5s)
Step
Stop at
\taddress:0x7e2
\tsource line:14
Print TRISB
TRISB=
254
Print TMR2
TMR2=3
Stopwatch
Stopwatch cycle count = 13 (13 \u00b5s)
Delete 0
Break led.c:20
Breakpoint 1 at file led.c, line 20.
Continue
Running
Wait 600000
Print TRISB
Stop at
\taddress:0x7f0
\tsource line:20
TRISB=
254
Print TMR2
TMR2=7
Stopwatch
Stopwatch cycle count = 4 (4 \u00b5s)
Step
Stop at
\taddress:0x7f2
\tsource line:21
Print TRISB
TRISB=
-2
Print TMR2
TMR2=9
Stopwatch
Stopwatch cycle count = 8 (8 \u00b5s)
Quit
"""


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class PicDefTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.path = self.tmp / "PIC16F999.PIC"
        self.path.write_text(MINI_PIC, encoding="utf-8")
        self.pic = PicDef(self.path)

    def test_header(self):
        self.assertEqual(self.pic.name, "PIC16F999")
        self.assertEqual(self.pic.dsid, "40001291")
        self.assertEqual(self.pic.nominal_vdd(), 5.0)

    def test_pins(self):
        self.assertEqual(self.pic.pins()[1], ["RB0", "AN12"])
        self.assertEqual(self.pic.pin_numbers("vss"), [3, 4])
        self.assertEqual(self.pic.pin_numbers("RB0"), [2])

    def test_bits_and_masks(self):
        tris = self.pic.sfr("TRISB")
        self.assertEqual(tris["bits"][0], "TRISB0")
        self.assertEqual(tris["mask"], 0xFF)
        osc = self.pic.sfr("OSCCON")
        self.assertEqual(osc["mask"], 0x7F)                    # '-' in the reset value marks bit 7 as missing
        self.assertEqual(osc["bits"][4], "IRCF0")              # single-bit names win over IRCF<0>
        self.assertEqual(self.pic.sfr("ANSELB")["mask"], 0x30)  # the reset value beats the impl attribute
        with self.assertRaises(KeyError):
            self.pic.sfr("NOPE")

    def test_register_info(self):
        info = register_info(self.pic, ["ANSELB"])[0]
        self.assertEqual(info["bits"][4], "ANSB4")
        self.assertIsNone(info["bits"][7])                     # named in the file but not implemented
        with self.assertRaises(MdbError):
            register_info(self.pic, ["WIDE"])
        with self.assertRaises(MdbError):
            register_info(self.pic, ["NOPE"])


class ToolchainTest(TempDirTest):
    def test_version_key(self):
        names = ["v5.50", "v6.05", "v6.35", "v6.25"]
        self.assertEqual(max(names, key=version_key), "v6.35")
        self.assertGreater(version_key("1.10.2"), version_key("1.9.258"))

    def test_device_file_newest_pack(self):
        for ver in ("1.9.258", "1.10.2"):
            d = self.tmp / "PIC16Fxxx_DFP" / ver / "edc"
            d.mkdir(parents=True)
            (d / "PIC16F999.PIC").write_text(MINI_PIC, encoding="utf-8")
        path, pack, ver = find_device_file("PIC16F999", [self.tmp])
        self.assertEqual((pack, ver), ("PIC16Fxxx_DFP", "1.10.2"))

    def test_device_file_missing(self):
        from picviewer.toolchain import ToolNotFound
        with self.assertRaises(ToolNotFound):
            find_device_file("PIC16F999", [self.tmp])

    def test_xc8_from_env(self):
        with mock.patch.dict(os.environ, {ENV_XC8: "/opt/x/xc8-cc"}):
            self.assertEqual(find_xc8(), Path("/opt/x/xc8-cc"))


class ProjectTest(TempDirTest):
    def write(self, data):
        p = self.tmp / "picviewer.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def base(self, **target):
        t = {"id": "a", "device": "16F886", "source": "a.c", "registers": ["TRISB"], "trace": [{"run_to": 3}]}
        t.update(target)
        return {"targets": [t]}

    def test_examples_load(self):
        led = load(EXAMPLES / "led")
        self.assertEqual([t.id for t in led.targets], ["pic16f886", "pic16f1618"])
        self.assertEqual(led.targets[0].circuit["type"], "led")
        motor = load(EXAMPLES / "motor" / "picviewer.json")
        self.assertEqual(motor.targets[0].counters, ["TMR2"])
        self.assertEqual(motor.targets[1].counters, ["T2TMR"])
        self.assertEqual(motor.targets[1].trace[2], {"run_to": 50, "show": 49})

    def test_defaults(self):
        p = load(self.write(self.base()))
        t = p.targets[0]
        self.assertEqual(t.device, "PIC16F886")
        self.assertEqual(t.circuit, {"type": "pins"})
        self.assertEqual(p.output, self.tmp.resolve() / "viewer.html")

    def test_errors(self):
        bad = [
            self.base(trace=[{"step": 1}]),
            self.base(trace=[{"run_to": 3}, {"step": 0}]),
            self.base(colour="red"),
            self.base(notes={"x": "y"}),
            self.base(circuit={"type": "scope"}),
            self.base(registers=["TRISB", "TRISB"]),
            self.base(id="Bad Id"),
            {"targets": [self.base()["targets"][0], self.base()["targets"][0]]},
            {"targets": []},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ProjectError):
                load(self.write(data))

    def test_plan_show_defaults_to_line(self):
        plan = parse_plan([{"run_to": 5}, {"run_to": 9}], "t")
        self.assertEqual(plan[1], {"run_to": 9, "show": 9})


class MdbTest(unittest.TestCase):
    def test_parse_records(self):
        recs = parse_records(TRACE_LOG, ["TRISB", "TMR2"])
        self.assertEqual([r["line"] for r in recs], [13, 14, 20, 21])
        self.assertEqual([r["values"]["TMR2"] for r in recs], [0, 3, 7, 9])
        self.assertEqual(recs[2]["values"]["TRISB"], 254)        # Stop at printed in the middle of the prints
        self.assertEqual(recs[1]["addr"], "0x7e2")

    def test_absolute_cycles(self):
        recs = parse_records(TRACE_LOG, ["TRISB", "TMR2"])
        # Continue restarts the stopwatch; Step keeps counting from the last resume
        self.assertEqual(absolute_cycles(recs, ["start", "step", "run", "step"]), [9, 13, 17, 21])
        with self.assertRaises(MdbError):
            absolute_cycles(recs, ["start", "step", "step", "step"])
        with self.assertRaises(MdbError):
            absolute_cycles(recs, ["start", "step"])

    def test_missing_register(self):
        with self.assertRaises(MdbError):
            parse_records(TRACE_LOG, ["TRISB", "PORTB"])
        with self.assertRaises(MdbError):
            parse_records("Print FOO\nFOO=Symbol does not exist.\n", ["FOO"])

    def test_probe_errors(self):
        text = "Program succeeded.\nled.c:30 could not be resolved to an address\nFOO=Symbol does not exist.\n"
        errors = probe_errors(text, "led.c", "PIC16F999")
        self.assertEqual(len(errors), 2)
        self.assertIn("[30]", errors[0])
        self.assertTrue(probe_errors("Programming failed\n", "led.c", "PIC16F999"))
        self.assertEqual(probe_errors("Program succeeded.\n", "led.c", "PIC16F999"), [])

    def test_trace_commands(self):
        plan = [{"run_to": 13}, {"step": 2}, {"run_to": 20, "show": 19}, {"run_to": 22, "show": 22}]
        cmds, kinds = trace_commands("PIC16F999", Path("/w/a.elf"), "a.c", plan, ["TRISB"], 1000)
        self.assertEqual(kinds, ["start", "step", "step", "run", "run"])
        self.assertLess(cmds.index("Delete 0"), cmds.index("Break a.c:20"))
        self.assertLess(cmds.index("Delete 1"), cmds.index("Break a.c:22"))
        self.assertEqual(cmds[-1], "Quit")


class BundleTest(unittest.TestCase):
    REGS = [{"name": "TRISB", "mask": 0xFF, "bits": []}, {"name": "TMR2", "mask": 0xFF, "bits": []}]

    def test_make_steps(self):
        plan = [{"run_to": 13}, {"step": 1}, {"run_to": 20, "show": 19}, {"step": 1}]
        steps = make_steps(plan, parse_records(TRACE_LOG, ["TRISB", "TMR2"]), self.REGS)
        self.assertEqual([s["exec"] for s in steps], [None, 13, 19, 20])
        self.assertEqual([s["next"] for s in steps], [13, 14, 20, 21])
        self.assertEqual(steps[3]["v"], [254, 9])              # -2 printed as signed, masked to 8 bits

    def test_wrong_stop(self):
        plan = [{"run_to": 13}, {"step": 1}, {"run_to": 25}, {"step": 1}]
        with self.assertRaises(MdbError):
            make_steps(plan, parse_records(TRACE_LOG, ["TRISB", "TMR2"]), self.REGS)

    def test_expand_and_dump(self):
        self.assertEqual(len(expand_plan([{"run_to": 1}, {"step": 3}, {"run_to": 5, "show": 4}])), 5)
        b = {"schema": 1, "steps": [{"a": 1}, {"a": 2}], "source": ["x", "y"], "empty": []}
        self.assertEqual(json.loads(dumps(b)), b)


class WaveTest(unittest.TestCase):
    @staticmethod
    def log(levels, step=10):
        lines = []
        for i, lv in enumerate(levels):
            lines += [f"Stopwatch cycle count = {100 + i * step} (x)", "Pin\tMode\tValue\tOwner or Mapping",
                      f"RC5\tDout\t{'HIGH' if lv else 'LOW'}\t(RC5)"]
        return "\n".join(lines)

    def test_pwm(self):
        levels = ([1] * 3 + [0] * 7) * 3 + [1]
        w = summarize(parse_samples(self.log(levels), "RC5"), "RC5", 52, len(levels))
        self.assertEqual(w["periods"], [100, 100])
        self.assertEqual(w["highs"], [30, 30])
        self.assertEqual(w["edges"][0], [100, 1])

    def test_constant(self):
        w = summarize(parse_samples(self.log([0] * 5), "RC5"), "RC5", 1, 5)
        self.assertEqual((w["periods"], w["highs"], w["high_fraction"]), ([], [], 0.0))
        with self.assertRaises(ValueError):
            summarize([], "RC5", 1, 0)


class RenderTest(TempDirTest):
    def test_examples_are_up_to_date(self):
        for name in ("led", "motor"):
            with self.subTest(example=name):
                project = load(EXAMPLES / name)
                page = render_html(project, load_bundles(project))
                self.assertEqual(page, project.output.read_text(encoding="utf-8"),
                                 f"examples/{name} の HTML が古い: picviewer render examples/{name}")

    def test_page_shape(self):
        project = load(EXAMPLES / "motor")
        page = render_html(project, load_bundles(project))
        self.assertEqual(page.count("</script>"), 2)           # nothing in the data closes the script early
        self.assertIn("PicViewer.circuits.hbridge", page)
        self.assertIn("PicViewer.circuits.pins", page)         # the fallback view is always there
        self.assertIn(f"picviewer {__version__}", page)
        self.assertNotIn("C:/Users", page)
        self.assertNotIn("C:\\\\Users", page)

    def test_pins_view_from_led_bundles(self):
        data = json.loads((EXAMPLES / "led" / "picviewer.json").read_text(encoding="utf-8"))
        data["bundles"] = str((EXAMPLES / "led" / "bundles").resolve())
        data["circuit"] = {"type": "pins"}
        for t in data["targets"]:
            t.pop("circuit")
        p = self.tmp / "picviewer.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        project = load(p)
        page = render_html(project, load_bundles(project))
        self.assertNotIn("PicViewer.circuits.led", page)


class ServerTest(TempDirTest):
    def test_listing(self):
        (self.tmp / "a.html").write_text("<title>A page</title>", encoding="utf-8")
        (self.tmp / "build").mkdir()
        (self.tmp / "build" / "skip.html").write_text("<title>skip</title>", encoding="utf-8")
        page = listing_html(self.tmp)
        self.assertIn("A page", page)
        self.assertNotIn("skip", page)

    def test_serve(self):
        (self.tmp / "a.html").write_text("<title>A page</title>", encoding="utf-8")
        srv = make_server(self.tmp, 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            with urllib.request.urlopen(base + "/") as r:
                self.assertIn("A page", r.read().decode("utf-8"))
            with urllib.request.urlopen(base + "/a.html") as r:
                self.assertEqual(r.headers["Cache-Control"], "no-store")
            with mock.patch("sys.stderr", None):      # pythonw has no console
                with urllib.request.urlopen(base + "/a.html") as r:
                    self.assertEqual(r.status, 200)
        finally:
            srv.shutdown()
            srv.server_close()


class CliTest(unittest.TestCase):
    def test_parser(self):
        a = build_parser().parse_args(["build", "examples/led", "-t", "pic16f886", "--reuse-logs"])
        self.assertEqual((a.command, a.target, a.reuse_logs), ("build", ["pic16f886"], True))
        a = build_parser().parse_args(["serve", "--port", "9000", "--stop"])
        self.assertEqual((a.folder, a.port, a.stop), (".", 9000, True))


if __name__ == "__main__":
    unittest.main()
