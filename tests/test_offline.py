"""Tests that need neither MPLAB X nor XC8: parsing, planning, bundles, rendering and the server."""
import io
import json
import os
import sys
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
from picviewer.compiler import (FAST_VAR, CompileError, compat_source, compiled_name, copy_includes, error_lines,
                                fast_define, fast_source, skipped_per_ms)
from picviewer.index import ERROR_FILE, find_projects, index_html, natural
from picviewer.init import analyze, guess, init_source
from picviewer.linetab import line_at, read_line_table, writer_address
from picviewer.mdb import (MdbError, _clean, absolute_cycles, key_stimulus, parse_records, plan_wait_seconds,
                           probe_errors, run_trace, skipped_stops, trace_chunks, trace_commands)
from picviewer.picdef import PicDef
from picviewer.pipeline import build_target
from picviewer.project import (ProjectError, Target, ascii_build_dir, keypad_keys, keypad_of, load, parse_plan,
                               pin_level)
from picviewer.render import load_bundles, render_html
from picviewer.server import listing_html, make_server
from picviewer.toolchain import ENV_XC8, find_device_file, find_xc8, version_key
from picviewer.wave import parse_samples, summarize

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
EXAMPLE_NAMES = sorted(p.name for p in EXAMPLES.iterdir() if (p / "picviewer.json").is_file())

# stands in for mdb in a session: a prompt before each command it reads, the command echoed, and for each Run or
# Continue the outcome given on the command line (a source line, or null for a wait that runs out)
FAKE_MDB = r'''
import json, sys
outcomes = json.loads(sys.argv[1])
pending, cycles, runs = None, 0, 0
def say(text):
    sys.stdout.write(text)
    sys.stdout.flush()
say(">")
for raw in sys.stdin:
    cmd = raw.strip()
    say(cmd + "\n")
    if cmd in ("Run", "Continue"):
        pending, runs = outcomes[runs], runs + 1
    elif cmd.startswith("Wait") and pending is not None:
        say(f"Single breakpoint: @0x10\nStop at\n\taddress:0x10\n\tfile:C:/w/sim/a.c\n\tsource line:{pending}\n")
    elif cmd == "Halt" and pending is None:
        say("Simulator halted\nStop at\n\taddress:0x20\n\tfile:C:/w/sim/a.c\n\tsource line:99\n")
    elif cmd.startswith("Print "):
        say(f"{cmd[6:]}=\n{runs}\n")
    elif cmd == "Stopwatch" and outcomes != "hang":
        cycles += 10
        say(f"Stopwatch cycle count = {cycles}\n")
    elif cmd == "Quit":
        say(f"fake: {runs} runs\n")
        break
    say(">")
'''

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
Single breakpoint: @0x7
Simulator halted
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
Single breakpoint: @0x7
Simulator halted
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
Single breakpoint: @0x7de
Simulator halted
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
Single breakpoint: @0x7f0
Simulator halted
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

    def test_circuit_values_are_checked(self):
        t = load(self.write({**self.base(), "circuit": [{"type": "lcd"}]})).targets[0]
        self.assertEqual(t.circuit, [{"type": "lcd"}])                     # a project-level list is the default
        t = load(self.write({**self.base(circuit=None), "circuit": {"type": "led"}})).targets[0]
        self.assertEqual(t.circuit["type"], "led")                        # null: take the project's
        for data in ({**self.base(circuit={"type": "led"}), "circuit": [{"type": "lcd"}]},
                     self.base(circuit="led"), {**self.base(), "circuit": 3}):
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

    def test_watch_several_registers(self):
        plan = parse_plan([{"run_to": 3}, {"until_write": ["PORTC", "PORTA", "PORTC"], "count": 2}], "t")
        self.assertEqual(plan[1]["until_write"], ["PORTC", "PORTA", "PORTC"])
        cmds, kinds = trace_commands("PIC16F999", Path("/w/a.elf"), "a.c", plan, ["PORTA", "PORTC"], 1000)
        self.assertEqual([c for c in cmds if c.startswith("Watch")], ["Watch PORTC W", "Watch PORTA W"])
        regs = [{"name": "PORTA", "mask": 0xFF, "bits": []}, {"name": "PORTC", "mask": 0xFF, "bits": []}]
        rec = lambda line, a, c, reading: {"line": line, "addr": "0x7cb", "reading": reading, "timeout": False,
                                           "values": {"PORTA": a, "PORTC": c}}
        steps = make_steps(plan, [rec(3, 15, 0, 5), rec(9, 15, 6, 9), rec(10, 14, 6, 9)], regs, writer_line=lambda a: 8)
        self.assertEqual([s["watch"] for s in steps[1:]], ["PORTC", "PORTA"])   # the one that changed
        with self.assertRaises(ProjectError):
            parse_plan([{"run_to": 3}, {"until_write": [], "count": 2}], "t")

    def test_watch_name_compares_output_bits(self):
        plan = [{"run_to": 3}, {"set": {"RA4": 1}, "note": ""}, {"until_write": ["PORTC", "PORTA"], "count": 1}]
        regs = [{"name": r, "mask": 0xFF, "bits": []} for r in ("TRISA", "PORTA", "PORTC")]

        def rec(line, a, c):
            return {"line": line, "addr": "0x7cb", "reading": 5, "timeout": False,
                    "values": {"TRISA": 0x10, "PORTA": a, "PORTC": c}}
        # PORTC written again with the same value; PORTA moved only because the input RA4 changed
        steps = make_steps(plan, [rec(3, 0x00, 1), rec(9, 0x10, 1)], regs, writer_line=lambda a: 8)
        self.assertEqual(steps[1]["watch"], "PORTC か PORTA")
        with self.assertRaises(ProjectError):
            parse_plan([{"run_to": 3}, {"until_write": [["PORTC"]], "count": 2}], "t")

    def test_timeout_is_the_missing_hit(self):
        # the halt message may come after other output: only the absence of a hit decides
        log = ("Continue\nRunning\nWait 3000\nHalt\nPrint PORTC\nSimulator halted\nStop at\n\taddress:0x7f4\n"
               "\tsource line:15\nPORTC=\n1\nStopwatch\nStopwatch cycle count = 9\n")
        self.assertTrue(parse_records(log, ["PORTC"])[0]["timeout"])
        plan = [{"run_to": 3}, {"until_write": "PORTC", "count": 4, "wait_ms": 60000}, {"run_to": 9}]
        self.assertEqual(plan_wait_seconds(plan, 20000), 20 + 240 + 20)

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

    def test_chunks_follow_the_plan(self):
        plan = [{"run_to": 13}, {"step": 2}, {"until_write": "PORTC", "count": 3, "until": 30},
                {"set": {"RB0": "high"}, "note": ""}, {"until_write": ["PORTC", "PORTA"], "count": 2}]
        chunks = trace_chunks("PIC16F999", Path("/w/a.elf"), "a.c", plan, ["PORTC"], 1000)
        stops = [s for _, s in chunks if s]
        expected = expand_plan(plan)
        self.assertEqual([s["index"] for s in stops], list(range(len(expected))))
        self.assertEqual([s["kind"] for s in stops], [e["kind"] for e in expected])
        self.assertEqual([s.get("segment") for s in stops], [e.get("segment") for e in expected])
        self.assertEqual([s["until"] for s in stops if s["kind"] == "write"], [30, 30, 30, None, None])
        self.assertTrue(all(c[-1] == "Stopwatch" for c, s in chunks if s))
        self.assertTrue(all(not any(x in ("Run", "Continue", "Step") for x in c) for c, s in chunks if not s))
        self.assertEqual(_clean(">\x1b[1m> Single breakpoint: @0x10\r\n"), "Single breakpoint: @0x10\n")
        # the prompt printed in the middle of the halt report of the debugger's other thread (seen in a real log)
        self.assertEqual(_clean("\tfile:C:/w/sim/a.c>\n"), "\tfile:C:/w/sim/a.c\n")
        log = ("Stop at\n\taddress:0x7f8\n\tfile:C:/w/sim/a.c>\n\tsource line:42\nPORTC=\n1\n"
               "Stopwatch cycle count = 5\n")
        self.assertEqual(parse_records(log, ["PORTC"], "a.c")[0]["line"], 42)      # an old log read again

    def test_session_leaves_out_what_would_repeat(self):
        plan = [{"run_to": 13}, {"until_write": "PORTC", "count": 5}, {"set": {"RB0": "high"}, "note": "押す"},
                {"until_write": "PORTC", "count": 3, "until": 30}]
        # the line each Run or Continue reaches; null: the wait runs out with nothing written
        outcomes = [13, 20, None, None, 21, 30]
        regs = [{"name": "PORTC", "mask": 0xFF, "bits": []}]
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake_mdb.py"
            fake.write_text(FAKE_MDB, encoding="utf-8")
            chunks = trace_chunks("PIC16F999", Path("/w/a.elf"), "a.c", plan, ["PORTC"], 1000)
            text = run_trace([sys.executable, fake, json.dumps(outcomes)], chunks, Path(tmp) / "trace.log",
                             ["PORTC"], "a.c")
            self.assertEqual(text, (Path(tmp) / "trace.log").read_text(encoding="utf-8"))
            self.assertIn("Continue", (Path(tmp) / "trace.mdb").read_text(encoding="utf-8"))
        self.assertIn("fake: 6 runs", text)               # 9 stops in the plan, 6 made
        self.assertEqual(skipped_stops(text), {4, 5, 8})  # after two waits in a row ran out, after the until line
        self.assertNotIn(">", text)
        records = parse_records(text, ["PORTC"], "a.c")
        self.assertEqual(len(records), 6)
        steps = make_steps(plan, records, regs, writer_line=lambda a: 8, skipped=skipped_stops(text))
        self.assertEqual([s["kind"] for s in steps], ["start", "write", "timeout", "write", "end"])
        self.assertEqual(steps[3]["input_note"], "押す")
        with self.assertRaises(MdbError):              # fewer records than the plan, without the log saying why
            make_steps(plan, records, regs, writer_line=lambda a: 8)

    def test_session_gives_up_on_a_silent_mdb(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake_mdb.py"
            fake.write_text(FAKE_MDB, encoding="utf-8")
            chunks = trace_chunks("PIC16F999", Path("/w/a.elf"), "a.c", [{"run_to": 13}], ["PORTC"], 0)
            with self.assertRaises(MdbError):
                run_trace([sys.executable, fake, '"hang"'], chunks, Path(tmp) / "trace.log", ["PORTC"], margin=1)
            self.assertIn("Stopwatch", (Path(tmp) / "trace.log").read_text(encoding="utf-8"))


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

    def test_old_interrupt_form(self):
        text, n = compat_source("void interrupt intr(void)\n{\n}\nvoid interrupt low_priority tick(void) {}\n")
        self.assertEqual(n, 2)
        self.assertEqual(text.splitlines()[0], "void __interrupt() intr(void)")     # same line, line numbers kept
        self.assertIn("void __interrupt(low_priority) tick(void)", text)
        text, n = compat_source("void interrupt\nisr(void)\n{\n}\nvoid low_priority interrupt t2(void) {}\n")
        self.assertEqual(n, 1)
        self.assertEqual(text.splitlines()[:2], ["void interrupt", "isr(void)"])      # never joins two lines
        self.assertIn("void __interrupt(low_priority) t2(void)", text)

    def test_includes_copied_for_a_folder_xc8_cannot_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            (src / "main").mkdir(parents=True)
            (src / "main" / "melody.c").write_text('#include "notes.h"\nint m;\n', encoding="utf-8")
            (src / "main" / "notes.h").write_text("#define C4 1\n", encoding="utf-8")
            sim = Path(tmp) / "sim"
            sim.mkdir()
            copy_includes('#include <xc.h>\n#include "main\\melody.c"\n#include "absent.h"\n', src, sim)
            self.assertTrue((sim / "main" / "melody.c").is_file())
            self.assertTrue((sim / "main" / "notes.h").is_file())     # what the included file includes too

    def test_error_lines_put_the_error_first(self):
        warn = "a.c:5:22: warning: relational comparison result unused\n   5 | for(;;)\n     | ^\n"
        log = warn * 6 + "a.c:121:1: error: extraneous closing brace ('}')\n 121 | }\n     | ^\n4 warnings and 1 error generated.\n"
        text = error_lines(log)
        self.assertTrue(text.startswith("a.c:121:1: error: extraneous closing brace"))
        self.assertIn("compile.log", text)
        self.assertEqual(error_lines("just\nsome\noutput"), "just\nsome\noutput")   # no error line: the end

    def test_compiled_name(self):
        def name(src):
            return compiled_name(Target(id="a", device="PIC16F886", source=Path(src), source_name=src,
                                        registers=[], trace=[]))
        self.assertEqual(name("/w/pic1-00.xc8"), "pic1-00.c")      # XC8 refuses .xc8 (error 894)
        self.assertEqual(name("/w/led.c"), "led.c")
        self.assertRegex(name("/w/点滅.c"), r"^src_[0-9a-f]{8}\.c$")  # XC8 cannot open a name that is not ASCII

    def test_build_folder_with_japanese_path(self):
        self.assertEqual(ascii_build_dir(Path("/w/led/build")), Path("/w/led/build"))
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "/local"}):
            moved = ascii_build_dir(Path("/w/点滅/build"))
        self.assertEqual(moved.parent, Path("/local/picviewer/build"))
        self.assertTrue(str(moved).isascii())


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
        start = "Single breakpoint: @0x7c0\nStop at\n\taddress:0x7c0\n\tsource line:13\nPORTC=0\npicviewer_delay_ms=0\nStopwatch cycle count = 5\n"
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
        # the time spent waiting, the dropped second wait included, so the page can show the clock without it
        self.assertEqual([s.get("waited_cycles") for s in steps], [None, 900, 1800, 1800])
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

    def test_step_after_dropped_stops_and_writes_elsewhere(self):
        regs = [{"name": "PORTC", "mask": 0xFF, "bits": []}]
        plan = [{"run_to": 13}, {"until_write": "PORTC", "count": 3}, {"step": 1}]
        records = [self.rec(13, 0, 5), self.rec(18, 1, 9), self.rec(31, 1, 900, True), self.rec(33, 1, 900, True),
                   self.rec(34, 1, 905)]                        # Step keeps counting after the last resume
        steps = make_steps(plan, records, regs, writer_line=lambda a: 17)
        self.assertEqual([s["kind"] for s in steps], ["start", "write", "timeout", "step"])
        self.assertEqual(steps[3]["exec"], 33)                       # where the dropped stop left the program
        lcd = {**self.rec(None, 2, 9), "where": "lcd.h"}
        steps = make_steps([{"run_to": 13}, {"until_write": "PORTC", "count": 1}], [self.rec(13, 0, 5), lcd], regs,
                           writer_line=lambda a: 17, line_of=lambda a: 17)
        self.assertEqual((steps[1]["exec"], steps[1]["next"], steps[1]["where"]), (None, None, "lcd.h"))

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
        self.assertEqual(info["writes"], {"PORTC": 2})          # LED = 0 and PORTC = 0 do not count
        self.assertEqual(info["reads"], {"RA0": True, "RA1": False})
        self.assertEqual(info["names"], {"RA0": "SW0", "RA1": "sw1"})
        self.assertEqual(info["tris"], {"A": 0x0F, "C": 0})
        self.assertEqual(info["notes"], {"9": "4 MHz"})
        self.assertFalse(info["seg7"])

    def test_guess(self):
        target, facts = guess(COURSE_SRC, COURSE_TABLE, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual((facts["first"], facts["setup"], facts["out"]), (9, 5, "PORTC"))
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
        # a pass of the loop writes PORTC once at most (the if or the else if): 1 + 2 writes after each change
        self.assertEqual(sum(a.get("count", 0) for a in load_check), 5 * 3)

    def test_guess_other_shapes(self):
        seg = COURSE_SRC.replace("LED = 0x0F;", "LED = digits[3];").replace(
            "void main(void)", "const char digits[] = {0x3F, 0x06, 0x5B, 0x4F, 0x66};\nvoid main(void)")
        table = [(a, ln + 1) for a, ln in COURSE_TABLE]
        target, _ = guess(seg, table, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(target["circuit"]["type"], "seg7")
        bank = COURSE_SRC.replace("if (SW0) { LED = 0xFF; }", "if (0) { }").replace(
            "else if (sw1 == ON) { LED = 0x0F; }", "else if (1) { LED = ~PORTA; }")
        target, facts = guess(bank, COURSE_TABLE, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(facts["switches"], ["RA0(low)", "RA1(low)"])     # the pins the program names on PORTA
        unnamed = bank.replace("#define SW0 !PORTAbits.RA0", "").replace("#define sw1 PORTAbits.RA1", "")
        target, facts = guess(unnamed, COURSE_TABLE, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(facts["switches"], [f"RA{b}(low)" for b in range(4)])   # else TRISA = 0b00001111
        wait = COURSE_SRC.replace("    while (1) {", "    while (SW0 == 0);\n    while (1) {")
        target, facts = guess(wait, COURSE_TABLE, DEVICE_REGS, DEVICE_PINS)
        self.assertTrue(facts["wait_loop"])
        self.assertIn("set", target["trace"][3])                  # pressed right after the start-up lines
        with self.assertRaises(ProjectError):
            guess("int x;\n", COURSE_TABLE, DEVICE_REGS, DEVICE_PINS)


    @staticmethod
    def program(body, before=""):
        """A small program: the lines of `body` inside main after the start-up, with a line table for them."""
        head = ["#include <xc.h>", "#define _XTAL_FREQ 4000000", *before.split("\n"), "void main(void)", "{",
                "    ANSEL = 0;", "    TRISA = 0xFF;", "    TRISC = 0;"]
        lines = head + ["    " + b for b in body] + ["}"]
        main = head.index("void main(void)") + 1
        table = [(0x700 + n, n) for n in range(main + 2, len(lines))]
        return "\n".join(lines), table

    def switches(self, body, before=""):
        text, table = self.program(body, before)
        target, _ = guess(text, table, DEVICE_REGS | {"LATC"}, DEVICE_PINS)
        return target, [(s["pin"], s["active"]) for s in target["circuit"].get("switches", [])]

    def test_switch_read_by_the_int_interrupt(self):
        isr = ("void __interrupt() isr(void)\n{\n"
               "    if (PORTBbits.RB4 == 1 && PORTBbits.RB3 == 0) { n++; }\n"
               "    if (PORTBbits.RB4 == 0 && PORTBbits.RB3 == 1) { m++; }\n"
               "    INTCONbits.INTF = 0;\n}")
        text, table = self.program(["TRISB = 0xFF;", "INTCONbits.INTE = 1;", "INTCONbits.GIE = 1;", "while (1) {",
                                    "    PORTC = n + m;", "}"], before="unsigned char n, m;\n" + isr)
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        notes = [a["note"] for a in target["trace"] if "set" in a]
        # the interrupt reads RB4 when RB0 raises it: hold RB4 and press RB0 (three times: the program counts)
        self.assertIn("RB4 を押したままにする", notes)
        self.assertIn("RB4 を押したまま RB0 を押す（INT の割り込み）（3 回目）", notes)
        self.assertFalse(any("同時に押す" in n for n in notes))     # RB4 == 1 && RB3 == 0 is not "press both"

    def test_loops_that_blink_and_constants_from_variables(self):
        # PORTC = 0 inside the loop is a write too; with an if in the loop the few-writes shortcut does not apply
        body = ["while (1) {", "    if (n <= 3) {", "        PORTC = 0xFF;", "        __delay_ms(250);",
                "        PORTC = 0;", "        __delay_ms(250);", "        n++;", "    } else {",
                "        __delay_ms(2000);", "        n = 0;", "    }", "}"]
        target, _ = self.switches(body, before="unsigned char n;")
        self.assertIn({"until_write": "PORTC", "count": 16, "wait_ms": 3000}, target["trace"])
        target, _ = self.switches(["while (1) {", "    PORTC = 0xFF;", "    __delay_ms(500);", "    PORTC = 0;",
                                   "    __delay_ms(500);", "}"])
        self.assertIn({"until_write": "PORTC", "count": 4, "wait_ms": 3000}, target["trace"])    # two values, twice
        target, _ = self.switches(["a = 0x01;", "while (1) {", "    PORTC = a ^ 0x0F;", "}"], before="unsigned char a;")
        self.assertIn({"until_write": "PORTC", "count": 2, "wait_ms": 3000}, target["trace"])    # a never changes
        target, _ = self.switches(["a = 0x01;", "while (1) {", "    PORTC = a;", "    a = a << 1;", "}"],
                                  before="unsigned char a;")
        self.assertIn({"until_write": "PORTC", "count": 16, "wait_ms": 3000}, target["trace"])
        four = ["while (1) {"] + [f"    if (PORTAbits.RA{b} == 1) {{ PORTC = {1 << b}; __delay_ms(50); PORTC = 0; }}"
                                  for b in range(4)] + ["}"]
        target, _ = self.switches(four)
        counts = [a["count"] for a in target["trace"] if "until_write" in a]
        self.assertEqual(counts[:3], [4, 10, 4])      # idle 4, a press one pass (8 writes) + 2, the release 4

    def test_sections_of_the_loop(self):
        body = ["while (1) {",
                "    for (int i = 0; i < 100; i++) {", "        LED0 = 1;", "        __delay_ms(2);",
                "        LED0 = 0;", "        __delay_ms(8);", "    }",
                "    for (int i = 0; i < 100; i++) {", "        LED0 = 1;", "        __delay_ms(5);",
                "        LED0 = 0;", "        __delay_ms(5);", "    }", "}"]
        text, table = self.program(body, before="#define LED0 PORTCbits.RC0")
        target, facts = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        # each loop of the steps: stop at its first line (showing its for line), then four writes
        self.assertEqual(target["trace"], [{"run_to": 6}, {"step": 3},
                                           {"run_to": 11, "show": 10}, {"until_write": "PORTC", "count": 4, "wait_ms": 3000},
                                           {"run_to": 17, "show": 16}, {"until_write": "PORTC", "count": 4, "wait_ms": 3000}])
        self.assertEqual(facts["sections"], 2)
        self.assertEqual(target["circuit"], {"type": "leds", "port": "C", "bits": [0], "names": {"0": "LED0"}})

    def test_servo_buzzer_and_motor_driver(self):
        servo = ["while (1) {", "    for (int i = 0; i <= 50; i++) {", "        MO1 = 1;", "        __delay_us(900);",
                 "        MO1 = 0;", "        __delay_us(19100);", "    }",
                 "    for (int i = 0; i <= 50; i++) {", "        MO1 = 1;", "        __delay_us(2100);",
                 "        MO1 = 0;", "        __delay_us(17900);", "    }", "}"]
        text, table = self.program(servo, before="#define MO1 PORTCbits.RC0")
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(target["circuit"], {"type": "servo", "pin": "RC0", "label": "MO1"})   # 0.9 ms of 20 ms
        self.assertIn("サーボ", target["summary"])
        tone = ["while (1) {", "    if (SW0 == 1) {", "        BZ = 1;", "        __delay_us(1908);", "        BZ = 0;",
                "        __delay_us(1908);", "    }", "    else BZ = 0;", "}"]
        text, table = self.program(tone, before="#define SW0 !PORTAbits.RA0\n#define BZ PORTBbits.RB0")
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(target["circuit"][0], {"type": "buzzer", "pin": "RB0", "label": "BZ"})
        self.assertEqual(target["circuit"][1], {"type": "leds", "port": "A", "bits": [],
                                                "switches": [{"pin": "RA0", "active": "low", "label": "SW0"}]})
        self.assertIn("PORTB", target["registers"])
        # a pass writes BZ twice (the if) or once (the else): 2 + 2 writes after each change
        self.assertIn({"until_write": "PORTB", "count": 4, "wait_ms": 3000}, target["trace"])
        motor = ["while (1) {", "    if (SW0 == ON) { IN1 = 1; IN2 = 0; }", "    else if (SW1 == ON) { IN1 = 0; IN2 = 1; }",
                 "    else { IN1 = 0; IN2 = 0; }", "}"]
        text, table = self.program(motor, before="#define IN1 PORTCbits.RC0\n#define IN2 PORTCbits.RC1\n"
                                                 "#define SW0 PORTAbits.RA0\n#define SW1 PORTAbits.RA1\n#define ON 1")
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(target["circuit"][0], {"type": "dcmotor", "in1": "RC0", "in2": "RC1"})
        self.assertEqual(target["circuit"][1]["port"], "A")
        self.assertIn("モータードライバ", target["summary"])

    def test_presses_together_and_counted(self):
        both = ["while (1) {", "    if (SW0 == ON && SW1 == ON) { LED0 = 1; }", "    else { LED0 = 0; }", "}"]
        text, table = self.program(both, before="#define SW0 PORTAbits.RA0\n#define SW1 PORTAbits.RA1\n"
                                                "#define LED0 PORTCbits.RC0\n#define ON 1")
        target, facts = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        notes = [a["note"] for a in target["trace"] if "set" in a]
        self.assertEqual(notes[-2:], ["SW0とSW1 を同時に押す", "SW0とSW1 を離す"])
        self.assertIn({"set": {"RA0": 1, "RA1": 1}, "note": "SW0とSW1 を同時に押す"}, target["trace"])
        self.assertEqual(facts["groups"], 1)
        counter = ["while (1) {", "    for (int i = 0; i < 10; i++) {", "        while (SW0 == 0) { }",
                   "        PORTC = i;", "        while (SW0 == 1) { }", "    }", "}"]
        text, table = self.program(counter, before="#define SW0 PORTAbits.RA0")
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        presses = [a["note"] for a in target["trace"] if "set" in a and "押す" in a["note"]]
        self.assertEqual(presses, ["SW0 を押す（1 回目）", "SW0 を押す（2 回目）", "SW0 を押す（3 回目）"])
        whole = ["while (1) {", "    PORTC = PORTA;", "}"]
        target, _ = self.switches(whole)
        groups = [a["set"] for a in target["trace"] if "set" in a and "同時" in a["note"]]
        self.assertEqual(groups, [{"RA0": 1, "RA1": 1}, {f"RA{b}": 1 for b in range(8)}])   # two, then all

    def test_switch_left_as_output_and_bare_comments(self):
        text, table = self.program(["TRISA = 0x01;", "while (1) {", "    if (SW1 == 1) PORTC = 1;   //100",
                                    "    else PORTC = 0;   // 消す", "}"], before="#define SW1 PORTAbits.RA1")
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        self.assertIn("出力のまま", target["summary"])
        self.assertIn("出力のまま", target["notes"]["9"])            # on the TRISA line
        self.assertNotIn("100", " ".join(target["notes"].values()))   # a number alone is not an explanation
        self.assertIn("消す", target["notes"].values())

    def test_interrupt_that_writes_keeps_its_time(self):
        isr = "void __interrupt() isr(void)\n{\n    PORTC = PORTC + 1;\n    TMR0IF = 0;\n}"
        text, table = self.program(["while (1) {", "    __delay_ms(500);", "    PORTC = 2;", "}"], before=isr)
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        self.assertNotIn("fast_forward", target)     # shortened waits would change what the interrupt shows meanwhile
        text, table = self.program(["while (1) {", "    __delay_ms(500);", "    PORTC = PORTC + 2;", "}"])
        self.assertEqual(guess(text, table, DEVICE_REGS, DEVICE_PINS)[0]["fast_forward"], 1000)

    def seg(self, body, before=""):
        text, table = self.program(body, before)
        target, _ = guess(text, table, DEVICE_REGS | {"LATC"}, DEVICE_PINS)
        parts = [target["circuit"]] if isinstance(target["circuit"], dict) else target["circuit"]
        return target, parts[0]

    def test_led_patterns_are_not_seven_segments(self):
        def loop(*values):
            return ["while (1) {"] + [f"    PORTC = {v}; __delay_ms(100);" for v in values] + ["}"]
        for values in (("0x01", "0x03", "0x07", "0x0F", "0x1F", "0x3F", "0x7F", "0xFF"),      # a bar graph
                       ("0x80", "0xC0", "0xE0", "0xF0", "0xF8", "0xFC", "0xFE"),              # filling from the left
                       ("0x03", "0x06", "0x0C", "0x18", "0x30", "0x60", "0xC0"),              # two LEDs moving
                       ("0x07", "0x0E", "0x1C", "0x38", "0x70", "0xE0")):                     # three LEDs moving
            _, part = self.seg(loop(*values))
            self.assertEqual(part["type"], "leds", values)
        _, part = self.seg(["while (1) {", "    PORTC &= 0xF8; PORTC |= 0x07;", "    PORTC = 0x5B;", "}"])
        self.assertEqual(part["type"], "leds")                       # masks are not shapes
        # a buzzer written whole on another port does not take the LEDs' place
        target, _ = self.seg(["while (1) {", "    PORTC = n; n = n << 1;", "    PORTC = n + 1;", "    PORTB = 0x80;", "}"],
                             before="unsigned char n;")
        self.assertIn({"until_write": "PORTC", "count": 16, "wait_ms": 3000}, target["trace"])
        _, part = self.seg(loop("0x89", "0xC7"))                    # H and L on a common anode display
        self.assertEqual((part["type"], part.get("common")), ("seg7", "anode"))

    def test_anode_tables_with_the_point_clear(self):
        table = "const char t[] = {0b01000000, 0b01111001, 0b00100100, 0b00110000, 0b00011001, 0b00010010};"
        _, part = self.seg(["while (1) {", "    PORTC = t[i];", "}"], before=table + "\nunsigned char i;")
        self.assertEqual(part.get("common"), "anode")
        _, part = self.seg(["while (1) {", "    SEG = 0xC0; __delay_ms(5);", "    SEG = 0xF9; __delay_ms(5);",
                            "    SEG = 0xA4; __delay_ms(5);", "}"], before="#define SEG PORTC")
        self.assertEqual(part.get("common"), "anode")               # a name with seg in it does not force cathode
        onebit = "const char t[] = {0b00000001, 0b00000010, 0b00000100, 0b00001000, 0b00010000, 0b00100000};"
        _, part = self.seg(["while (1) {", "    _7seg = t[i];", "}"],
                           before=onebit + "\n#define _7seg PORTC\nunsigned char i;")
        self.assertEqual((part["type"], part.get("common", "cathode")), ("seg7", "cathode"))

    def test_digits_follow_the_segment_writes(self):
        body = ["while (1) {",
                "    PORTC = seg[n / 10 % 10]; PORTAbits.RA0 = 1; PORTAbits.RA1 = 0; __delay_ms(5);",
                "    PORTC = seg[n % 10]; PORTAbits.RA1 = 1; PORTAbits.RA0 = 0; __delay_ms(5);",
                "    BZ = 1; BZ = 0;", "}"]
        target, part = self.seg(body, before="const char seg[] = {0x3F, 0x06, 0x5B, 0x4F, 0x66};\nunsigned char n;\n"
                                              "#define BZ PORTAbits.RA5")
        self.assertEqual(part["type"], "seg7mux")
        # the last write before the wait lights the digit; the tens on the left; the buzzer is no digit
        self.assertEqual(part["digits"], [{"pin": "RA1", "active": "low"}, {"pin": "RA0", "active": "low"}])
        self.assertIn("表の添字の位", target["summary"])
        three = ["while (1) {",
                 "    PORTC = seg[a]; PORTBbits.RB0 = 1; PORTBbits.RB1 = 1; PORTBbits.RB2 = 0; __delay_ms(1);",
                 "    PORTC = seg[b]; PORTBbits.RB0 = 1; PORTBbits.RB1 = 0; PORTBbits.RB2 = 1; __delay_ms(1);", "}"]
        _, part = self.seg(three, before="const char seg[] = {0x3F, 0x06, 0x5B, 0x4F, 0x66};\nunsigned char a, b;")
        self.assertEqual({d["active"] for d in part["digits"]}, {"low"})     # the one 0 among 1s is the lit digit
        whole = ["while (1) {", "    for (i = 0; i < 4; i++) {", "        PORTC = seg[d[i]];",
                 "        PORTA = ~(1u << i) & 0x0F;", "        __delay_ms(5);", "    }", "}"]
        _, part = self.seg(whole, before="const char seg[] = {0x3F, 0x06, 0x5B, 0x4F, 0x66};\nunsigned char i, d[4];")
        self.assertEqual(part["type"], "seg7mux")                   # digits chosen by a whole-port write
        self.assertEqual([d["pin"] for d in part["digits"]], ["RA0", "RA1", "RA2", "RA3"])
        self.assertEqual({d["active"] for d in part["digits"]}, {"low"})

    def test_waits_decide_polarity_from_the_structure(self):
        cases = [["while (1) {", "    if (PORTAbits.RA0 == 0) { n++; while (PORTAbits.RA0 == 0); }", "}"],
                 ["while (1) {", "    while (PORTAbits.RA0 == 1);", "    n++;", "    while (PORTAbits.RA0 == 0);", "}"],
                 ["while (1) {", "    while (PORTAbits.RA0 == 1)", "    {", "    }", "    PORTC = 1;", "}"],
                 ["while (1) {", "    while (PORTAbits.RA0 == 1)", "        ;", "    PORTC = 1;", "}"],
                 ["while (1) {", "    do { } while (PORTAbits.RA0 == 1);", "    PORTC = 1;", "}"],
                 ["while (1) {", "    while (PORTAbits.RA0 == 1) { __delay_ms(1); }", "    PORTC = 1;", "}"]]
        for body in cases:
            _, sw = self.switches(body, before="unsigned char n;")
            self.assertEqual(sw, [("RA0", "low")], body)

    def test_int_from_whole_registers(self):
        _, sw = self.switches(["INTCON = 0b10010000;", "while (1) {", "    PORTC = n;", "}"], before="unsigned char n;")
        self.assertEqual(sw, [("RB0", "high")])                     # rising edge after a reset; RB0 never read
        _, sw = self.switches(["OPTION_REG = 0x00;", "INTE = 1;", "while (1) {", "    PORTC = n;", "}"],
                              before="unsigned char n;")
        self.assertEqual(sw, [("RB0", "low")])                      # INTEDG (bit 6) cleared: falling edge

    def test_main_forms_and_old_name_aliases(self):
        for head in ("main()", "void\nmain(void)"):
            text = (f"#include <xc.h>\n#define SW0 RA0\n{head}\n{{\n    TRISA = 0xFF;\n    while (1) {{\n"
                    "        if (SW0 == 1) PORTC = 1; else PORTC = 2;\n    }\n}\n")
            main = text.split("\n").index("{") + 1
            table = [(0x700 + n, n) for n in range(main + 1, text.count("\n"))]
            target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
            self.assertEqual(target["circuit"]["switches"][0]["label"], "SW0")     # #define SW0 RA0 keeps its name

    def test_conditions_decide_polarity(self):
        _, sw = self.switches(["while (1) {", "    if (PORTAbits.RA0 == 0) PORTC = 0xFF;", "}"])
        self.assertEqual(sw, [("RA0", "low")])                        # the action happens at 0
        _, sw = self.switches(["while (PORTAbits.RA1 == 0) {", "}", "PORTC = 1;", "while (1) {", "}"])
        self.assertEqual(sw, [("RA1", "high")])                       # waiting while 0: pressed ends it with 1
        _, sw = self.switches(["while (1) {", "    if (SW == OFF) { PORTC = 1; } else { PORTC = 2; }", "}"],
                              before="#define ON 1\n#define OFF 0\n#define SW PORTAbits.RA2")
        self.assertEqual(sw, [("RA2", "high")])                       # OFF names the released state
        target, sw = self.switches(["while (1) {", "    if (!RA3) RC0 = 1;", "}"])
        self.assertEqual(sw, [("RA3", "low")])                        # the old single-bit names
        self.assertEqual(target["circuit"]["port"], "C")

    def test_watch_lat_and_read_led_port_inputs(self):
        target, _ = self.switches(["while (1) {", "    LATC = 0x0F;", "}"])
        self.assertIn({"until_write": "LATC", "count": 2, "wait_ms": 3000}, target["trace"])  # one value over and over
        self.assertIn("LATC", target["registers"])
        target, _ = self.switches(["while (1) {", "    LATC = LATC + 1;", "}"])
        self.assertIn({"until_write": "LATC", "count": 16, "wait_ms": 3000}, target["trace"])
        target, sw = self.switches(["TRISB = 0x01;", "while (1) {", "    if (!PORTBbits.RB0) show(0xF0);", "}"],
                                   before="void show(unsigned char v)\n{\n    PORTB = v;\n}")
        self.assertEqual((target["circuit"]["port"], sw), ("B", [("RB0", "low")]))   # a write in a function before main

    def test_strings_calls_and_anode_tables(self):
        text, table = self.program(['lcd_puts("ready for input");', "PORTC = 1;", "while (1) {", "}"])
        info = analyze(text)
        self.assertEqual(info["loop"], text.split("\n").index("    while (1) {") + 1)   # not the "for" in the string
        target, facts = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(facts["setup"], 3)                           # stops before the call (it could wait inside)
        anode = "const unsigned char D[] = {0xC0, 0xF9, 0xA4, 0xB0, 0x99};"
        target, _ = self.switches(["while (1) {", "    PORTC = D[1];", "}"], before=anode)
        self.assertEqual(target["circuit"], {"type": "seg7", "port": "C", "common": "anode"})


    MUX_SRC = """\
#include <xc.h>
#define _XTAL_FREQ 4000000
const unsigned char _7seg[10] = {0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x27, 0x7F, 0x6F};
void __interrupt() intr(void)
{
    if (INTCONbits.INTF) { INTCONbits.INTF = 0; while (PORTBbits.RB0 == 1) { } }
}
void main(void)
{
    TRISA = 0;
    TRISB = 0x01;
    TRISC = 0;
    INTCONbits.INTE = 1;
    INTCONbits.GIE = 1;
    while (1) {
        PORTC = _7seg[1]; PORTAbits.RA0 = 0; __delay_ms(5); PORTAbits.RA0 = 1;
        PORTC = _7seg[2]; PORTAbits.RA1 = 0; __delay_ms(5); PORTAbits.RA1 = 1;
        PORTAbits.RA2 = 1;
    }
}
"""

    def test_multiplexed_display_and_int_pin(self):
        table = [(0x700 + n, n) for n in (10, 11, 12, 13, 14, 16, 17)]
        target, facts = guess(self.MUX_SRC, table, DEVICE_REGS, DEVICE_PINS)
        mux, switch = target["circuit"]
        self.assertEqual(mux["type"], "seg7mux")
        self.assertEqual(mux["digits"], [{"pin": "RA0", "active": "low"}, {"pin": "RA1", "active": "low"},
                                         {"pin": "RA2", "active": "low"}])   # RA2 stays dark but is a digit
        self.assertEqual(switch["switches"], [{"pin": "RB0", "active": "high", "label": "RB0"}])   # the INT edge
        watches = [a["until_write"] for a in target["trace"] if "until_write" in a]
        self.assertEqual(watches[0], ["PORTC", "PORTA"])
        counts = [a["count"] for a in target["trace"] if "until_write" in a]
        self.assertEqual(counts[0], 40)                 # five rounds of the digits to start with
        self.assertEqual(counts[1:3], [24, 16])         # three rounds after the press, two after the release
        target, _ = guess(self.MUX_SRC, table, DEVICE_REGS, DEVICE_PINS, digits="high")
        self.assertEqual({d["active"] for d in target["circuit"][0]["digits"]}, {"high"})   # --digits high

    def test_counting_display_moves_the_count_on(self):
        lines = ["#include <xc.h>", "#define _XTAL_FREQ 4000000",
                 "const unsigned char seg[10] = {0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x27, 0x7F, 0x6F};",
                 "void count(void);", "void main(void)", "{", "    TRISA = 0;", "    TRISC = 0;",
                 "    while (1) {", "        count();", "    }", "}", "void count(void)", "{",
                 "    for (int t = 0; t < 10; t++) {", "        for (int u = 0; u < 10; u++) {",
                 "            for (int k = 0; k < 25; k++) {",
                 "                PORTC = seg[t]; PORTAbits.RA1 = 0; __delay_ms(5); PORTAbits.RA1 = 1;",
                 "                PORTC = seg[u]; PORTAbits.RA0 = 0; __delay_ms(5); PORTAbits.RA0 = 1;",
                 "            }", "        }", "    }", "}"]
        no = {line.strip(): n for n, line in enumerate(lines, 1)}
        table = [(0x700 + n, n) for n in range(no["TRISA = 0;"], len(lines) + 1)]
        target, facts = guess("\n".join(lines), table, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(target["circuit"]["type"], "seg7mux")
        self.assertEqual(facts["counting"], ["u", "t"])        # the loop main's loop calls; k indexes nothing
        u_head, t_head = no["for (int u = 0; u < 10; u++) {"], no["for (int t = 0; t < 10; t++) {"]
        jumps = [(a["run_to"], a.get("show")) for a in target["trace"] if "run_to" in a][1:]
        self.assertEqual(jumps, [(21, u_head)] * 3 + [(22, t_head)])   # where u++ and t++ sit: the closing lines
        # a for that walks the digits writes one shape a pass: no count to move on
        walk = ["while (1) {", "    for (i = 0; i < 2; i++) {", "        PORTC = seg[i];",
                "        PORTA = ~(1u << i) & 0x03;", "        __delay_ms(5);", "    }", "}"]
        text, table = self.program(walk, before="const char seg[] = {0x3F, 0x06, 0x5B, 0x4F, 0x66};\nunsigned char i;")
        target, facts = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(target["circuit"]["type"], "seg7mux")
        self.assertEqual(facts["counting"], [])
        self.assertEqual(sum(1 for a in target["trace"] if "run_to" in a), 1)

    def test_board_known_to_have_digits(self):
        regs = DEVICE_REGS | {"LATB", "LATD", "TRISD", "PORTD"}
        pins = DEVICE_PINS | {f"RD{b}" for b in range(8)}
        # one segment written, then the digits switched off: on a board with digits (--digits) a 7-segment display
        once = ["TRISB = 0;", "TRISD = 0;", "PORTD = 0;", "LATBbits.LATB2 = 1;", "LATBbits.LATB3 = 1;", "while (1) {",
                "    LATD = 0b11011111;", "    LATBbits.LATB0 = 1;", "    LATBbits.LATB1 = 1;", "}"]
        text, table = self.program(once)
        target, _ = guess(text, table, regs, pins, digits="low")
        self.assertEqual(target["circuit"], {"type": "seg7mux", "port": "D", "common": "anode",
                                             "digits": [{"pin": f"RB{b}", "active": "low"} for b in range(4)]})
        target, _ = guess(text, table, regs, pins)
        self.assertNotEqual(target["circuit"]["type"], "seg7mux")          # nothing says 7 segments without it
        # the digits only switched off in the setup: every pin written bit by bit on another port
        setup_only = ["TRISB = 0;", "TRISD = 0;", *[f"LATBbits.LATB{b} = 1;" for b in range(4)], "while (1) {",
                      "    if (PORTAbits.RA6 == 0) { LATD = 0b11011111; } else { LATD = 0; }", "}"]
        text, table = self.program(setup_only)
        target, _ = guess(text, table, regs, pins, digits="low")
        mux, switch = target["circuit"]
        self.assertEqual((mux["type"], mux["port"], [d["pin"] for d in mux["digits"]]),
                         ("seg7mux", "D", ["RB0", "RB1", "RB2", "RB3"]))
        self.assertEqual(switch["switches"][0]["pin"], "RA6")

    def test_loops_one_after_another_on_a_display_and_after_a_press(self):
        shapes = "const char seg[] = {0x3F, 0x06, 0x5B, 0x4F, 0x66};"
        phase = ["    for (int i = 0; i < 100; i++) {",
                 "        PORTC = seg[{a}]; PORTAbits.RA0 = 0; __delay_ms(1); PORTAbits.RA0 = 1;",
                 "        PORTC = seg[{b}]; PORTAbits.RA1 = 0; __delay_ms(1); PORTAbits.RA1 = 1;", "    }"]
        body = ["TRISA = 0;", "while (1) {", *[ln.replace("{a}", "1").replace("{b}", "2") for ln in phase],
                *[ln.replace("{a}", "3").replace("{b}", "4") for ln in phase], "}"]
        text, table = self.program(body, before=shapes)
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(target["circuit"]["type"], "seg7mux")
        lines = text.split("\n")
        heads = [n for n, ln in enumerate(lines, 1) if ln.strip().startswith("for (")]
        self.assertEqual(target["trace"][2:], [x for h in heads for x in (
            {"run_to": h + 1, "show": h}, {"until_write": ["PORTC", "PORTA"], "count": 24, "wait_ms": 3000})])
        # a melody inside the if that tests the switch: press, let go, then the start of each later note
        note = ["    for (int i = 0; i < 10; i++) {", "        BZ = 1; __delay_us({d}); BZ = 0; __delay_us({d});", "    }"]
        body = ["while (1)", "if (SW0 == 1) {", *[ln.replace("{d}", d) for d in ("500", "400", "300") for ln in note], "}"]
        text, table = self.program(body, before="#define BZ PORTCbits.RC0\n#define SW0 !PORTAbits.RA0")
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        lines = text.split("\n")
        at_if = next(n for n, ln in enumerate(lines, 1) if ln.strip().startswith("if (SW0"))
        heads = [n for n, ln in enumerate(lines, 1) if ln.strip().startswith("for (")]
        trace = target["trace"]
        start = trace.index({"set": {"RA0": 0}, "note": "SW0 を押す"})
        self.assertEqual(trace[start + 1:], [
            {"until_write": "PORTC", "count": 4, "wait_ms": 3000},
            {"set": {"RA0": 1}, "note": "SW0 を離す（並んだループはスイッチを見ないので続く）"},
            {"run_to": heads[1] + 1, "show": heads[1]}, {"until_write": "PORTC", "count": 4, "wait_ms": 3000},
            {"run_to": heads[2] + 1, "show": heads[2]}, {"until_write": "PORTC", "count": 4, "wait_ms": 3000},
            {"run_to": at_if}, {"until_write": "PORTC", "count": 2, "wait_ms": 3000}])

    def test_swapped_for_is_noted_and_not_walked(self):
        body = ["while (1) {", "    for (int i = 0; i++; i < 500) {", "        PORTC = 1; __delay_ms(1);", "    }",
                "    for (int i = 0; i++; i < 500) {", "        PORTC = 2; __delay_ms(1);", "    }", "}"]
        text, table = self.program(body)
        target, facts = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        self.assertEqual(facts["sections"], 0)                       # a run_to into them would never arrive
        heads = [str(n) for n, ln in enumerate(text.split("\n"), 1) if ln.strip().startswith("for (")]
        for h in heads:
            self.assertIn("入れ替わっている", target["notes"][h])
            self.assertIn("1 度も動かない", target["notes"][h])

    def test_int_pressed_while_another_switch_holds_the_display(self):
        isr = ("void __interrupt() isr(void)\n{\n    PORTCbits.RC0 = 0;\n    while (PORTBbits.RB0 == 1) { }\n"
               "    INTCONbits.INTF = 0;\n}")
        text, table = self.program(["TRISB = 0xFF;", "INTCONbits.INTE = 1;", "INTCONbits.GIE = 1;", "while (1) {",
                                    "    if (PORTBbits.RB4 == 1) PORTCbits.RC0 = 1;", "}"], before=isr)
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        notes = [a["note"] for a in target["trace"] if "set" in a]
        # RB0 alone changes nothing (RC0 is dark unless RB4 is held): hold RB4, then press RB0
        self.assertEqual(notes[-4:], ["RB4 を押したままにする", "RB4 を押したまま RB0 を押す（INT の割り込み）",
                                      "RB0 を離す", "RB4 を離す"])

    def test_switch_left_as_output_is_pressed_once(self):
        text, table = self.program(["TRISA = 0x01;", "while (1) {", "    if (PORTAbits.RA1 == 0) { n++; PORTC = n; }",
                                    "    if (PORTAbits.RA0 == 0) { n++; PORTC = n; }", "}"], before="unsigned char n;")
        target, _ = guess(text, table, DEVICE_REGS, DEVICE_PINS)
        notes = [a["note"] for a in target["trace"] if "set" in a]
        self.assertEqual(sum(1 for n in notes if n.startswith("RA1 を押す")), 1)    # reads nothing: once is enough
        self.assertEqual(sum(1 for n in notes if n.startswith("RA0 を押す")), 3)    # counted presses

    def test_digit_order_from_places_only_when_they_differ(self):
        same = ["TRISA = 0;", "while (1) {", "    PORTC = seg[n % 10]; PORTAbits.RA0 = 0; __delay_ms(5); PORTAbits.RA0 = 1;",
                "    PORTC = seg[n % 10]; PORTAbits.RA1 = 0; __delay_ms(5); PORTAbits.RA1 = 1;", "}"]
        target, _ = self.seg(same, before="const char seg[] = {0x3F, 0x06, 0x5B, 0x4F, 0x66};\nunsigned char n;")
        self.assertIn("ピンの順と仮定", target["summary"])
        self.assertNotIn("表の添字の位", target["summary"])

    def test_old_c_builds_as_c90(self):
        calls = []

        def fake_compile(xc8, target, work):
            calls.append(list(target.xc8_args))
            if not target.xc8_args:
                raise CompileError("type specifier missing, defaults to 'int'")
            Path(work).mkdir(parents=True, exist_ok=True)
            (Path(work) / "a.cmf").write_text("%LINETAB\n7C0 maintext CODE >3:C:\\w\\a.c\n", encoding="utf-8")
            return Path(work) / "a.elf"

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.c"
            src.write_text("#include <xc.h>\nvoid main(void)\n{\n    PORTC = 1;\n}\n", encoding="utf-8")
            tc = mock.Mock(pack_dirs=[], require_xc8=lambda: "xc8")
            with mock.patch("picviewer.init.compile_target", fake_compile), \
                    mock.patch("picviewer.init.find_device_file", lambda d, p: ("x.PIC", "pack", "1")), \
                    mock.patch("picviewer.init.PicDef"), \
                    mock.patch("picviewer.init.device_names", lambda pd: (DEVICE_REGS, DEVICE_PINS)):
                init_source(src, Path(tmp) / "a", "PIC16F886", tc)
            written = json.loads((Path(tmp) / "a" / "picviewer.json").read_text(encoding="utf-8"))
        self.assertEqual(calls, [[], ["-std=c90"]])
        self.assertEqual(written["targets"][0]["xc8_args"], ["-std=c90"])

    def test_init_compiles_in_an_ascii_place_and_records_every_failure(self):
        works = []

        def fake_compile(xc8, target, work):
            works.append(Path(work))
            Path(work).mkdir(parents=True, exist_ok=True)
            (Path(work) / "a.cmf").write_text("%LINETAB\n7C0 maintext CODE >3:C:\\w\\a.c\n", encoding="utf-8")
            return Path(work) / "a.elf"

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.c"
            src.write_text("#include <xc.h>\nvoid main(void)\n{\n    PORTC = 1;\n}\n", encoding="utf-8")
            lib = Path(tmp) / "lib.c"
            lib.write_text("int twice(int x) { return 2 * x; }\n", encoding="utf-8")      # no main in it
            tc = mock.Mock(pack_dirs=[], require_xc8=lambda: "xc8")
            out = Path(tmp) / "点滅"
            with mock.patch("picviewer.init.compile_target", fake_compile), \
                    mock.patch("picviewer.init.find_device_file", lambda d, p: ("x.PIC", "pack", "1")), \
                    mock.patch("picviewer.init.PicDef"), \
                    mock.patch("picviewer.init.device_names", lambda pd: (DEVICE_REGS, DEVICE_PINS)), \
                    mock.patch("picviewer.cli._toolchain", lambda args: tc), \
                    mock.patch.dict(os.environ, {"LOCALAPPDATA": str(Path(tmp) / "appdata")}), \
                    mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                code = cli_main(["init", str(src), str(lib), "-o", str(out), "--keep-going"])
                self.assertEqual(code, 1)
                self.assertTrue(works and all(str(w).isascii() for w in works))     # never under 点滅
                failed = load(out / "lib")
                self.assertIn("main", failed.targets[0].init_error)
                self.assertTrue((failed.build_dir / ERROR_FILE).is_file())          # listed with its reason
                with self.assertRaises(ProjectError):                              # the placeholder is never run
                    build_target(failed, failed.targets[0], None)


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
        self.make("PIC1/①#a")                                    # a name int() and URLs need care with
        self.assertEqual(natural("1①2"), ["", 1, "①", 2, ""])
        page = index_html(self.tmp, "授業")
        self.assertIn("3 本（見られる 0 本、失敗 1 本）", page)
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
        self.assertEqual(EXAMPLE_NAMES, ["buttons", "buzzer", "calculator", "dcmotor", "lcd", "led", "motor",
                                         "seg7_counter", "seg7_mux", "seg7_mux_count", "servo", "stopwatch", "switch_leds",
                                         "voltmeter"])
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
