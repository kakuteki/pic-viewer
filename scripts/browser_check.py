"""Click through the example pages in a headless browser and check what the viewer shows.

Needs agent-browser (https://github.com/vercel-labs/agent-browser) on PATH.
usage: python scripts/browser_check.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "AGENT_BROWSER_SESSION": "picviewer-check"}
EXE = shutil.which("agent-browser")
failures = 0


def ab(*args, quiet=False):
    if quiet:   # the first call starts the browser daemon; it must not inherit our pipes or run() never returns
        subprocess.run([EXE, *args], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        return ""
    r = subprocess.run([EXE, *args], env=ENV, capture_output=True, text=True, encoding="utf-8", timeout=120)
    return r.stdout.strip()


def state():
    out = ab("eval", "JSON.stringify(PicViewer.state())")
    return json.loads(json.loads(out))


def check(label, **want):
    global failures
    got = state()
    flat = {**got, **(got.get("probe") or {})}
    bad = {k: (flat.get(k), v) for k, v in want.items() if flat.get(k) != v}
    if bad:
        failures += 1
        print(f"FAIL  {label}: " + ", ".join(f"{k} = {g!r}, expected {w!r}" for k, (g, w) in bad.items()))
    else:
        print(f"ok    {label}")


def open_page(rel):
    url = (ROOT / rel).resolve().as_uri()
    ab("open", url)
    ab("reload")
    ab("wait", "200")


def led():
    open_page("examples/led/led_viewer.html")
    check("led: reset", target="pic16f886", step=0, exec=None, drive="hiz", lit=False)
    ab("click", 'li[data-line="24"]')
    check("led: RB0 made an output, data 0", step=10, exec=24, drive="low", lit=False)
    ab("click", "#bNext")
    check("led: RB0 = 1 lights the LED", step=11, exec=26, drive="high", flow="source", lit=True)
    ab("click", "input[name=opt-wire][value=sink]")
    check("led: wired to the supply, a 1 turns it off", drive="high", lit=False)
    ab("click", "#bPrev")
    check("led: wired to the supply, a 0 lights it", step=10, drive="low", flow="sink", lit=True)
    ab("click", 'input[name=target][value="1"]')
    check("led: PIC16F1618 keeps the step", target="pic16f1618", step=10, exec=22, drive="hiz")
    ab("click", "#bNext")
    check("led: PIC16F1618 RB4 output", step=11, exec=24, drive="low", lit=True)
    ab("press", "ArrowRight")
    check("led: arrow key", step=12, exec=26)
    ab("press", "Home")
    check("led: Home key", step=0, exec=None)


def motor():
    open_page("examples/motor/motor_viewer.html")
    check("motor: reset, all off", target="pic16f886", step=0, state="coast", q="0000")
    ab("click", 'li[data-line="40"]')
    check("motor: end of setup, still off", step=13, exec=40, state="coast")
    ab("click", "#bNext")
    check("motor: EN = 1 brakes", step=14, exec=43, state="brake", q="0101")
    ab("click", "#bNext")
    check("motor: forward 30 % on period", step=15, exec=45, state="fwd", duty=30, q="1001")
    ab("click", "input[name=opt-phase][value=off]")
    check("motor: forward 30 % off period", state="fwdOff", q="0101")
    ab("click", "#bNext")
    check("motor: 100 % has no off period", step=16, exec=47, state="fwd", duty=100, q="1001")
    ab("click", "#bNext")
    ab("click", "#bNext")
    check("motor: reverse 50 % off period", step=18, exec=51, state="revOff", duty=50)
    ab("click", "input[name=opt-phase][value=on]")
    check("motor: reverse 50 % on period", state="rev", q="0110")
    ab("click", "#bLast")
    check("motor: EN = 0 coasts", step=20, exec=55, state="coast", q="0000")
    ab("click", 'input[name=target][value="1"]')
    check("motor: PIC16F1618 keeps the step", target="pic16f1618", step=20, exec=51, state="fwd", duty=30)
    wave = ab("eval", "document.getElementById('waveBox').hidden ? '' : document.getElementById('waveBox').textContent")
    if "RC5" not in wave or "20.0 kHz" not in wave:
        global failures
        failures += 1
        print(f"FAIL  motor: measured RC5 wave is shown: {wave[:120]!r}")
    else:
        print("ok    motor: measured RC5 wave is shown")
    ab("click", 'li[data-line="46"]')
    check("motor: PIC16F1618 after PPS, still off", step=18, state="coast")
    ab("click", "#bPlay")
    check("motor: play", playing=True)
    time.sleep(1.4 * 7 + 2)
    check("motor: play stops at the end", step=25, exec=61, playing=False, state="coast")


def switch_leds():
    open_page("examples/switch_leds/switch_leds_viewer.html")
    check("leds: reset, all off", step=0, pattern="00000000", lit=0, pressed=False)
    ab("click", "#bLast")
    check("leds: last write shifts the LED to RC7", step=18, exec=36, kind="write", pattern="10000000", lit=1, pressed=True)
    ab("click", 'li[data-line="33"]')
    check("leds: one LED on RC0", step=11, exec=33, pattern="00000001", lit=1)
    ab("click", 'li[data-line="30"]')
    check("leds: switch pressed, first pattern", step=7, exec=30, pattern="00001111", lit=4, pressed=True)
    ab("click", "#bPrev")
    check("leds: waiting for the switch", step=6, kind="step", lit=0, pressed=False)


def lcd():
    open_page("examples/lcd/lcd_viewer.html")
    check("lcd: reset", step=0, mode=8, display=False)
    ab("eval", "(() => { const s = document.getElementById('slider'); s.value = '16'; s.dispatchEvent(new Event('input', {bubbles: true})); })()")
    check("lcd: still the 8-bit start", step=16, mode=8)
    ab("click", "#bNext")
    check("lcd: function set 0x20 switches to 4 bits", step=17, exec=19, mode=4)
    ab("click", 'li[data-line="26"]')
    check("lcd: first character starts on a cleared, lit screen", exec=26, display=True, lines=["", ""])
    ab("click", "#bLast")
    check("lcd: HELLO and PIC on the screen", step=109, kind="end", exec=73, display=True, lines=["HELLO", "PIC"])


def main():
    global failures
    if EXE is None:
        print("agent-browser が無い（npm install -g agent-browser）")
        return 2
    ab("set", "viewport", "1536", "864", quiet=True)
    led()
    motor()
    switch_leds()
    lcd()
    errors = ab("errors")
    if errors:
        failures += 1
        print(f"FAIL  page errors: {errors}")
    else:
        print("ok    no page errors")
    ab("close")
    print(f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
