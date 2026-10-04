"""Tests that need neither MPLAB X nor XC8: parsing, planning, bundles, rendering and the server."""
import io
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
from picviewer.cli import _sources, build_parser
from picviewer.cli import main as cli_main
from picviewer.compiler import FAST_VAR, compiled_name, fast_define, fast_source, skipped_per_ms
from picviewer.index import ERROR_FILE, find_projects, index_html
from picviewer.init import analyze, guess
from picviewer.linetab import line_at, read_line_table, writer_address
from picviewer.mdb import MdbError, absolute_cycles, key_stimulus, parse_records, probe_errors, trace_commands
from picviewer.picdef import PicDef
from picviewer.project import ProjectError, Target, keypad_keys, keypad_of, load, parse_plan, pin_level
from picviewer.render import load_bundles, render_html
from picviewer.server import listing_html, make_server
from picviewer.toolchain import ENV_XC8, find_device_file, find_xc8, version_key
from picviewer.wave import parse_samples, summarize

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
EXAMPLE_NAMES = sorted(p.name for p in EXAMPLES.iterdir() if (p / "picviewer.json").is_file())

# part of the map file XC8 writes (proto.cmf): address, psect, class, >line:file
CMF = """\
%CMF
%LINETAB
$startup.o
0 end_init CODE >86:C:\\work\\build\\startup.s
$leds.o
7F8 cinit CODE >431:C:\\work\\build\\leds.s
7C0 maintext CODE >13:C:\\work\\build\\sim\\leds.c
7C3 maintext CODE >14:C:\\work\\build\\sim\\leds.c
7C3 maintext CODE >15:C:\\work\\build\\sim\\leds.c
7C7 maintext CODE >16:C:\\work\\build\\sim\\leds.c
7C7 maintext CODE >17:C:\\work\\build\\sim\\leds.c
7CB maintext CODE >18:C:\\work\\build\\sim\\leds.c
7DE maintext CODE >19:C:\\work\\build\\sim\\leds.c
7E1 maintext CODE >20:C:\\work\\build\\sim\\leds.c
# %SYMTAB Section
%SYMTAB
_main 7B3 0 CODE 0 maintext dist/leds.o
"""

# Watch PORTC W, then two writes: mdb reports the line after the write (the writer is one word back)
WRITE_LOG = """\
Watch PORTC W
Watchpoint 1.
Continue
Running
Wait 600000
Single breakpoint: @0x7
Simulator halted
Stop at
\taddress:0x7cb
\tsource line:18
Print PORTC
PORTC=
15
Print picviewer_delay_ms
picviewer_delay_ms=
0
Stopwatch
Stopwatch cycle count = 8 (8 \u00b5s)
Continue
Running
Wait 600000
Stop at
\taddress:0x7e1
\tsource line:20
Print PORTC
PORTC=
143
Print picviewer_delay_ms
picviewer_delay_ms=
500
Stopwatch
Stopwatch cycle count = 517 (517 \u00b5s)
Continue
Running
Wait 600000
Stop at
\taddress:0x7e1
\tsource line:20
Print PORTC
PORTC=
143
Print picviewer_delay_ms
picviewer_delay_ms=
1000
Stopwatch
Stopwatch cycle count = 520 (520 \u00b5s)
"""


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

# measured: a Wait that runs out prints nothing; our Halt then stops the program (no "Single breakpoint"),
# and the place is printed after the next command. A Halt on a halted target prints nothing.
TIMEOUT_LOG = """\
Continue
Running

Wait 3000

Halt
Simulator halted

Print PORTC
Stop at
\taddress:0x7f4
\tfile:C:/w/halt.c
\tsource line:15
PORTC=
1

Stopwatch
Stopwatch cycle count = 7131214 (7.131214 s)
Continue
Running

Wait 3000

Single breakpoint: @0x7
Simulator halted
Stop at
\taddress:0x7f1
\tfile:C:/w/halt.c
\tsource line:15

Halt

Print PORTC
PORTC=
2

Stopwatch
Stopwatch cycle count = 406 (406 \u00b5s)
"""

# shaped like the exercise programs: aliases with ! and without, a port alias, comments that must not count
COURSE_SRC = """\
#include <xc.h>
#define _XTAL_FREQ 4000000
#define SW0 !PORTAbits.RA0
#define sw1 PORTAbits.RA1
#define LED PORTC
#define ON 1
void main(void)
{
    OSCCON = 0b01100000;    // 4 MHz
    ANSEL = 0;
    TRISA = 0b00001111;
    TRISC = 0;
    PORTC = 0; /* while (1) is not here */
    while (1) {
        if (SW0) { LED = 0xFF; }
        else if (sw1 == ON) { LED = 0x0F; }
        else { LED = 0; }
        __delay_ms(10);
    }
}
"""
COURSE_TABLE = [(0x7C0, 9), (0x7C2, 10), (0x7C4, 11), (0x7C6, 12), (0x7C8, 13), (0x7CA, 15), (0x7D0, 16),
                (0x7D8, 17), (0x7E0, 18), (0x7F0, 19)]
DEVICE_REGS = {"OSCCON", "ANSEL", "ANSELH", "TRISA", "TRISB", "TRISC", "PORTA", "PORTB", "PORTC", "STATUS"}
DEVICE_PINS = {f"R{p}{b}" for p in "ABC" for b in range(8)}


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
        leds = load(EXAMPLES / "switch_leds").targets[0]
        self.assertEqual(leds.fast_forward, 1000)
        self.assertEqual(leds.trace[2], {"set": {"RB0": "high"}, "note": "スイッチを押す"})
        lcd = load(EXAMPLES / "lcd").targets[0]
        self.assertEqual(lcd.trace[2], {"until_write": "PORTB", "count": 120, "until": 73})

    def test_new_actions(self):
        plan = parse_plan([{"set": {"rb0": 0}}, {"run_to": 3}, {"set": {"AN0": "2.5 V"}, "note": "n"},
                           {"until_write": "PORTC", "count": 2, "until": 9, "wait_ms": 5}], "t")
        self.assertEqual(plan[0], {"set": {"RB0": "low"}, "note": ""})
        self.assertEqual(plan[2]["set"], {"AN0": "2.5V"})
        self.assertEqual(plan[3], {"until_write": "PORTC", "count": 2, "until": 9, "wait_ms": 5})
        self.assertEqual([pin_level(v) for v in (1, 0, True, "HIGH", "3.3v", "x", 2)], ["high", "low", "high", "high", "3.3V", None, None])

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
            self.base(trace=[{"run_to": 3}, {"set": {"RB0": 5}}]),
            self.base(trace=[{"run_to": 3}, {"set": {}}]),
            self.base(trace=[{"until_write": "PORTC", "count": 2}]),
            self.base(trace=[{"run_to": 3}, {"until_write": "PORTC"}]),
            self.base(trace=[{"set": {"RB0": 1}}]),
            self.base(fast_forward=7),
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ProjectError):
                load(self.write(data))

    KEYPAD = {"type": "keypad", "rows": ["RB0", "RB1"], "cols": ["RB4", "RB5"], "keys": [["1", "2"], ["3", "+"]]}

    def test_keys_and_circuit_lists(self):
        plan = [{"run_to": 3}, {"press": "+", "note": "押す"}, {"run_to": 5}, {"release": True}]
        p = load(self.write(self.base(trace=plan, circuit=[{"type": "lcd"}, self.KEYPAD])))
        t = p.targets[0]
        self.assertEqual([c["type"] for c in t.circuit], ["lcd", "keypad"])
        self.assertEqual(t.trace[1], {"press": "+", "note": "押す"})
        self.assertEqual(keypad_keys(keypad_of(t.circuit)), {"1": ("RB0", "RB4"), "2": ("RB0", "RB5"),
                                                            "3": ("RB1", "RB4"), "+": ("RB1", "RB5")})
        bad = [
            self.base(trace=plan),                                                  # no keypad in the circuit
            self.base(trace=[{"run_to": 3}, {"press": "9"}], circuit=[self.KEYPAD]),  # no such key
            self.base(circuit=[{**self.KEYPAD, "keys": [["1", "2"]]}]),              # 1 row of keys for 2 rows
            self.base(circuit=[{**self.KEYPAD, "keys": [["1", "1"], ["3", "4"]]}]),  # the same label twice
            self.base(circuit=[]),
            self.base(trace=[{"run_to": 3}, {"release": False}]),
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

    def test_inputs_and_watches(self):
        plan = [{"set": {"RB0": "low"}, "note": ""}, {"run_to": 13}, {"set": {"RB0": "high"}, "note": ""},
                {"until_write": "PORTC", "count": 2, "until": 40, "wait_ms": 50}, {"run_to": 45, "show": 45}]
        cmds, kinds = trace_commands("PIC16F999", Path("/w/a.elf"), "a.c", plan, ["PORTC"], 1000, [FAST_VAR])
        self.assertEqual(kinds, ["start", "write", "write", "run"])
        self.assertLess(cmds.index("write pin RB0 low"), cmds.index("Run"))
        self.assertLess(cmds.index("write pin RB0 high"), cmds.index("Watch PORTC W"))
        self.assertEqual(cmds[cmds.index("Watch PORTC W") - 1], "Delete 0")      # numbers are shared and never reused
        self.assertEqual(cmds[cmds.index("Watch PORTC W") + 1], "Break a.c:40")
        self.assertEqual(cmds[cmds.index("Break a.c:45") - 2:cmds.index("Break a.c:45")], ["Delete 1", "Delete 2"])
        self.assertIn("Wait 50", cmds)
        self.assertEqual(cmds[cmds.index("Wait 50") + 1], "Halt")      # a Wait that runs out leaves it running
        self.assertIn(f"Print {FAST_VAR}", cmds)

    def test_keypad_commands(self):
        keys = {"1": ("RB0", "RB4"), "+": ("RB1", "RB5")}
        pad = {"cols": ["RB4", "RB5"], "keys": keys, "scl": {"1": Path("/w/k0.scl"), "+": Path("/w/k1.scl")}}
        plan = [{"run_to": 13}, {"press": "1", "note": ""}, {"run_to": 20, "show": 20},
                {"press": "+", "note": ""}, {"run_to": 20, "show": 20}, {"release": True, "note": ""},
                {"run_to": 22, "show": 22}]
        cmds, kinds = trace_commands("PIC16F999", Path("/w/a.elf"), "a.c", plan, ["PORTB"], 1000, keypad=pad)
        self.assertEqual(kinds, ["start", "run", "run", "run"])
        self.assertLess(cmds.index("write pin RB5 high"), cmds.index("Run"))      # the columns start released
        first = cmds.index('Stim "/w/k0.scl"')
        second = cmds.index('Stim "/w/k1.scl"')
        self.assertEqual(cmds[second - 2:second], ["Stim", "write pin RB4 high"])  # the first key let go first
        last = len(cmds) - 1 - cmds[::-1].index("Stim")
        self.assertGreater(last, second)
        self.assertEqual(cmds[last + 1], "write pin RB5 high")                     # released: column back to 1
        self.assertLess(first, second)
        scl = key_stimulus("PIC16F886", "RB1", "RB5")
        self.assertIn('testbench for "pic16f886" is', scl)
        self.assertIn("wait until RB1 == '0';", scl)
        self.assertIn("RB5 <= '1';", scl)

    def test_stop_in_a_library(self):
        log = ("Halt\nSimulator halted\nPrint PORTC\nStop at\n\taddress:0xe2\n"
               "\tfile:C:/Program Files (x86)/Microchip/MPLABXC8/v3.0.0/pic/sources/c99/common/awdiv.c\n"
               "\tsource line:35\nPORTC=\n1\nStopwatch cycle count = 5\n")
        rec = parse_records(log, ["PORTC"], "stopwatch.c")[0]
        self.assertEqual((rec["line"], rec["where"], rec["timeout"]), (None, "awdiv.c", True))   # not line 35 of ours
        own = log.replace("C:/Program Files (x86)/Microchip/MPLABXC8/v3.0.0/pic/sources/c99/common/awdiv.c",
                          "C:/w/build/sim/stopwatch.c")
        self.assertEqual(parse_records(own, ["PORTC"], "stopwatch.c")[0]["line"], 35)

    def test_timeout_records(self):
        recs = parse_records(TIMEOUT_LOG, ["PORTC"])
        self.assertEqual([r["timeout"] for r in recs], [True, False])
        self.assertEqual([r["line"] for r in recs], [15, 15])
        self.assertEqual(recs[1]["values"]["PORTC"], 2)
        no_line = "Halt\nSimulator halted\nPrint PORTC\nStop at\n\taddress:0x10\nPORTC=\n1\nStopwatch cycle count = 5\n"
        self.assertIsNone(parse_records(no_line, ["PORTC"])[0]["line"])


class LineTableTest(TempDirTest):
    def test_read_and_lookup(self):
        p = self.tmp / "leds.cmf"
        p.write_text(CMF, encoding="utf-8")
        table = read_line_table(p, "leds.c")
        self.assertEqual(table[0], (0x7C0, 13))
        self.assertEqual(len(table), 8)                        # the .s and startup entries are left out
        self.assertEqual(line_at(table, 0x7CA), 17)             # 0x7C7 is listed for 16 and 17: the later one wins
        self.assertEqual(line_at(table, 0x7C3), 15)
        self.assertIsNone(line_at(table, 0x700))
        self.assertEqual(read_line_table(self.tmp / "none.cmf", "leds.c"), [])
        self.assertEqual(writer_address("PIC16F886", 0x7CB), 0x7CA)
        self.assertEqual(writer_address("PIC18F4550", 0x100), 0xFE)


class FastForwardTest(unittest.TestCase):
    def test_source_keeps_lines(self):
        src = "#include <xc.h>\nvoid main(void) {\n    __delay_ms(500); // __delay_ms( in a comment\n    __delay_us(10);\n}"
        new, count = fast_source(src)
        self.assertEqual(count, 2)
        old_lines, new_lines = src.splitlines(), new.splitlines()
        self.assertEqual(new_lines[:len(old_lines)][2].split(";")[0].strip(), "PICVIEWER_DELAY_MS(500)")
        self.assertEqual(new_lines[3], old_lines[3])            # __delay_us is left alone
        self.assertEqual(new_lines[-1], f"volatile unsigned long {FAST_VAR};")
        self.assertEqual(len(new_lines), len(old_lines) + 1)    # only one line is added, after the last one

    def test_define(self):
        d = fast_define(1000)
        self.assertTrue(d.startswith("-DPICVIEWER_DELAY_MS(x)="))
        self.assertIn(f"{FAST_VAR} += (unsigned long)(x);", d)    # milliseconds asked: 32 bits last 49 days
        self.assertIn("__delay_us((x) * 1UL)", d)
        self.assertIn("__delay_us((x) * 100UL)", fast_define(10))
        self.assertEqual([skipped_per_ms(f) for f in (10, 100, 1000)], [900, 990, 999])

    def test_compiled_name(self):
        def name(src):
            return compiled_name(Target(id="a", device="PIC16F886", source=Path(src), source_name=src,
                                        registers=[], trace=[]))
        self.assertEqual(name("/w/pic1-00.xc8"), "pic1-00.c")      # XC8 refuses .xc8 (error 894)
        self.assertEqual(name("/w/led.c"), "led.c")


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

    def test_write_stops(self):
        table = [(0x7C0, 13), (0x7C7, 17), (0x7CB, 18), (0x7DE, 19), (0x7E1, 20)]
        plan = [{"set": {"RB0": "high"}, "note": "スイッチ"},
                {"until_write": "PORTC", "count": 3, "until": 20}]
        start = "Stop at\n\taddress:0x7c0\n\tsource line:13\nPORTC=0\npicviewer_delay_ms=0\nStopwatch cycle count = 5\n"
        records = parse_records(start + WRITE_LOG, ["PORTC", FAST_VAR])
        plan = [{"run_to": 13}] + plan
        regs = [{"name": "PORTC", "mask": 0xFF, "bits": []}]
        steps = make_steps(plan, records, regs, writer_line=lambda a: line_at(table, a - 1),
                           delay_var=FAST_VAR, us_per_ms=skipped_per_ms(1000))
        self.assertEqual([s["kind"] for s in steps], ["start", "write", "end"])  # the third stop is dropped
        self.assertEqual(steps[1]["exec"], 17)                  # stopped at 18; the write was the word before
        self.assertEqual(steps[1]["inputs"], {"RB0": "high"})
        self.assertEqual(steps[1]["input_note"], "スイッチ")
        self.assertEqual(steps[2]["exec"], 20)
        self.assertEqual(steps[2]["skipped_us"], 499500)
        self.assertEqual([s["cycles"] for s in steps], [5, 13, 530])

    @staticmethod
    def rec(line, value, reading, timeout=False):
        return {"line": line, "addr": "0x7cb", "reading": reading, "values": {"PORTC": value}, "timeout": timeout}

    def test_timeout_steps(self):
        plan = [{"run_to": 13}, {"until_write": "PORTC", "count": 3},
                {"set": {"RA0": "high"}, "note": "押す"}, {"until_write": "PORTC", "count": 1}]
        records = [self.rec(13, 0, 5), self.rec(15, 0, 900, True), self.rec(15, 0, 900, True),
                   self.rec(18, 1, 40), self.rec(18, 3, 40)]
        regs = [{"name": "PORTC", "mask": 0xFF, "bits": []}]
        steps = make_steps(plan, records, regs, writer_line=lambda a: 17)
        self.assertEqual([s["kind"] for s in steps], ["start", "timeout", "write", "write"])  # the 2nd wait is dropped
        self.assertEqual((steps[1]["exec"], steps[1]["next"], steps[1]["watch"]), (None, 15, "PORTC"))
        self.assertEqual([s["cycles"] for s in steps], [5, 905, 1845, 1885])
        self.assertEqual(steps[3]["inputs"], {"RA0": "high"})
        with self.assertRaisesRegex(MdbError, "着かない"):
            make_steps([{"run_to": 13}, {"run_to": 20}], [self.rec(13, 0, 5), self.rec(15, 0, 9, True)], regs)

    def test_stops_without_a_line(self):
        regs = [{"name": "PORTC", "mask": 0xFF, "bits": []}]
        plan = [{"run_to": 13}, {"until_write": "PORTC", "count": 2}]
        records = [self.rec(13, 0, 5), self.rec(None, 1, 7), {**self.rec(None, 1, 900, True), "where": "awdiv.c"}]
        steps = make_steps(plan, records, regs, writer_line=lambda a: 62, line_of=lambda a: 62)
        self.assertEqual((steps[1]["exec"], steps[1]["next"]), (62, 62))       # compiler glue after our write
        self.assertEqual((steps[2]["next"], steps[2]["where"]), (None, "awdiv.c"))   # not guessed from our lines
        with self.assertRaisesRegex(MdbError, "行番号"):
            make_steps([{"run_to": 13}, {"step": 1}], [self.rec(13, 0, 5), self.rec(None, 0, 6)], regs)

    def test_key_events(self):
        plan = [{"run_to": 13}, {"press": "5", "note": "5 を押す"}, {"run_to": 20, "show": 20},
                {"release": True, "note": ""}, {"until_write": "PORTC", "count": 1}]
        regs = [{"name": "PORTC", "mask": 0xFF, "bits": []}]
        steps = make_steps(plan, [self.rec(13, 0, 5), self.rec(20, 0, 9), self.rec(18, 1, 9)], regs,
                           writer_line=lambda a: 17)
        self.assertEqual((steps[1]["key"], steps[1]["input_note"]), ("5", "5 を押す"))
        self.assertEqual(steps[2]["key"], "")                                   # released
        self.assertNotIn("key", steps[0])


class InitTest(unittest.TestCase):
    def test_analyze(self):
        info = analyze(COURSE_SRC)
        self.assertEqual((info["main"], info["loop"], info["fosc"]), (7, 14, 4000000))   # not the comment on 13
        self.assertEqual(info["writes"]["C"], 2)                 # LED = 0 and PORTC = 0 do not count
        self.assertEqual(info["reads"], {"RA0": True, "RA1": False})
        self.assertEqual(info["names"], {"RA0": "SW0", "RA1": "sw1"})
        self.assertEqual(info["tris"], {"A": 0x0F, "C": 0})
        self.assertEqual(info["notes"], {"9": "4 MHz"})
        self.assertFalse(info["seg7"])

    def test_guess(self):
        target, facts = guess(COURSE_SRC, COURSE_TABLE, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual((facts["first"], facts["setup"], facts["out"]), (9, 5, "C"))
        self.assertEqual(target["registers"], ["OSCCON", "ANSEL", "TRISA", "TRISC", "PORTA", "PORTC"])
        self.assertEqual(target["circuit"]["switches"], [{"pin": "RA0", "active": "low", "label": "SW0"},
                                                         {"pin": "RA1", "active": "high", "label": "sw1"}])
        plan = target["trace"]
        self.assertEqual(plan[0]["set"], {"RA0": 1, "RA1": 0})          # both released
        self.assertEqual(plan[1:3], [{"run_to": 9}, {"step": 5}])
        presses = [a["set"] for a in plan if "set" in a and "押す" in a["note"]]
        self.assertEqual(presses, [{"RA0": 0}, {"RA1": 1}])
        self.assertEqual(target["fast_forward"], 1000)
        load_check = parse_plan(plan, "t")
        self.assertEqual(sum(a.get("count", 0) for a in load_check), 3 * 5)

    def test_guess_other_shapes(self):
        seg = COURSE_SRC.replace("LED = 0x0F;", "LED = digits[3];").replace(
            "void main(void)", "const char digits[] = {0x3F, 0x06, 0x5B, 0x4F, 0x66};\nvoid main(void)")
        table = [(a, ln + 1) for a, ln in COURSE_TABLE]
        target, _ = guess(seg, table, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(target["circuit"]["type"], "seg7")
        bank = COURSE_SRC.replace("if (SW0) { LED = 0xFF; }", "if (0) { }").replace(
            "else if (sw1 == ON) { LED = 0x0F; }", "else if (1) { LED = ~PORTA; }")
        target, facts = guess(bank, COURSE_TABLE, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(facts["switches"], [f"RA{b}(low)" for b in range(4)])   # TRISA = 0b00001111
        wait = COURSE_SRC.replace("    while (1) {", "    while (SW0 == 0);\n    while (1) {")
        target, facts = guess(wait, COURSE_TABLE, DEVICE_REGS, DEVICE_PINS)
        self.assertTrue(facts["wait_loop"])
        self.assertIn("set", target["trace"][3])                  # pressed right after the start-up lines
        with self.assertRaises(ProjectError):
            guess("int x;\n", COURSE_TABLE, DEVICE_REGS, DEVICE_PINS)


class IndexTest(TempDirTest):
    def make(self, rel, error=None):
        d = self.tmp / rel
        d.mkdir(parents=True)
        (d / "picviewer.json").write_text(json.dumps({"title": d.name, "targets": [
            {"id": "a", "device": "PIC16F886", "source": "a.c", "registers": ["TRISB"], "trace": [{"run_to": 3}],
             "summary": f"{d.name} の説明"}]}), encoding="utf-8")
        if error:
            (d / "build").mkdir()
            (d / "build" / ERROR_FILE).write_text(error, encoding="utf-8")

    def test_index(self):
        self.make("PIC1/pic1-10")
        self.make("PIC1/pic1-2", error="mdb が 300 秒で終わらなかった")
        self.make("PIC1/pic1-2/build/x")                          # build folders are not searched
        found = [p.parent.name for p in find_projects(self.tmp)]
        self.assertEqual(found, ["pic1-2", "pic1-10"])            # natural order
        page = index_html(self.tmp, "授業")
        self.assertIn("2 本（見られる 0 本、失敗 1 本）", page)
        self.assertIn("mdb が 300 秒で終わらなかった", page)
        self.assertIn("<h2>PIC1</h2>", page)
        self.assertIn("pic1-10 の説明", page)


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
        self.assertEqual(EXAMPLE_NAMES, ["buttons", "calculator", "lcd", "led", "motor", "seg7_counter",
                                         "stopwatch", "switch_leds", "voltmeter"])
        for name in EXAMPLE_NAMES:
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

    def test_circuit_parts_bring_their_scripts(self):
        page = render_html(load(EXAMPLES / "calculator"), load_bundles(load(EXAMPLES / "calculator")))
        for name in ("lcd", "keypad", "pins"):
            self.assertIn(f"PicViewer.circuits.{name} =", page)
        self.assertNotIn("PicViewer.circuits.hbridge =", page)

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
        a = build_parser().parse_args(["build", "c/a", "c/b", "--keep-going", "--no-probe"])
        self.assertEqual((a.project, a.keep_going, a.no_probe), (["c/a", "c/b"], True, True))
        a = build_parser().parse_args(["init", "PIC1", "--switches", "low", "--switch-bank", "RA0,RA1", "-o", "out"])
        self.assertEqual((a.sources, a.switches, a.switch_bank, a.out, a.device), (["PIC1"], "low", "RA0,RA1", "out", "PIC16F886"))

    def test_build_keep_going(self):
        with tempfile.TemporaryDirectory() as tmp:
            course = Path(tmp) / "course"
            for name in ("a", "b"):
                (course / name).mkdir(parents=True)
                (course / name / "picviewer.json").write_text(json.dumps({"targets": [
                    {"id": "x", "device": "PIC16F886", "source": "x.c", "registers": ["TRISB"],
                     "trace": [{"run_to": 3}]}]}), encoding="utf-8")
            calls = []

            def fake_build(project, target, tc, **kw):
                calls.append((project.dir.name, kw["probe"]))
                if project.dir.name == "a":
                    raise MdbError("止まらなかった")

            with mock.patch("picviewer.pipeline.build_target", fake_build), \
                    mock.patch("picviewer.render.render_project", lambda p: p.output), \
                    mock.patch("picviewer.cli._toolchain", lambda args: None), \
                    mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                code = cli_main(["build", str(course), "--keep-going", "--no-probe"])
            self.assertEqual(code, 1)
            self.assertEqual(calls, [("a", False), ("b", False)])      # b is built although a failed
            self.assertIn("止まらなかった", (course / "a" / "build" / ERROR_FILE).read_text(encoding="utf-8"))
            self.assertFalse((course / "b" / "build" / ERROR_FILE).exists())

    def test_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("pic1-10.xc8", "pic1-2.xc8", "led.c", "._pic1-2.xc8", "build/x.c", "notes.txt"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("", encoding="utf-8")
            self.assertEqual([p.name for p in _sources([root])], ["led.c", "pic1-2.xc8", "pic1-10.xc8"])
            with self.assertRaises(ProjectError):
                _sources([root / "none.c"])


if __name__ == "__main__":
    unittest.main()
