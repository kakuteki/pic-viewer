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


def goto(n):
    ab("eval", "(() => { const s = document.getElementById('slider'); s.value = '%d';"
               " s.dispatchEvent(new Event('input', {bubbles: true})); })()" % n)


def steps_of(example, target="pic16f886"):
    path = ROOT / "examples" / example / "bundles" / f"{target}.json"
    return json.loads(path.read_text(encoding="utf-8"))["steps"]


def text_of(element_id):
    return json.loads(ab("eval", f"document.getElementById('{element_id}').textContent"))


def expect(label, ok, detail=""):
    global failures
    if ok:
        print(f"ok    {label}")
    else:
        failures += 1
        print(f"FAIL  {label}: {detail}")


def click_timing_at_step(k):
    """Click the timing diagram where step k is drawn (its x is read from the cursor while standing on k)."""
    goto(k)
    x = state()["timing"]["cursor"]
    goto(0)
    ab("eval", "(() => { const svg = document.getElementById('tm'); const r = svg.getBoundingClientRect();"
               " const s = r.width / Number(svg.getAttribute('width'));"
               " svg.dispatchEvent(new MouseEvent('click', {clientX: r.left + %f * s, clientY: r.top + 40, bubbles: true})); })()" % x)


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


def buttons():
    steps = steps_of("buttons")
    note = {s["input_note"]: i for i, s in enumerate(steps) if s.get("input_note")}
    open_page("examples/buttons/buttons_viewer.html")
    check("buttons: start, every switch released", step=0, lit=0, pressed=False)
    expect("buttons: before ANSEL = 0 the released pins read 0, and the page says why",
           "アナログ入力のまま" in text_of("cirText"), text_of("cirText"))
    goto(3)
    expect("buttons: after ANSEL = 0 the pins read what is on them", "アナログ入力のまま" not in text_of("cirText"), text_of("cirText"))
    goto(note["SW0 を押す"])
    check("buttons: SW0 held lights RC0", kind="write", pattern="00000001", pressedNames=["SW0"])
    goto(note["SW3 を押す"] + 1)
    check("buttons: SW3 held also lights RC7", kind="write", pattern="10001000", pressedNames=["SW3"])
    timing = state()["timing"]
    expect("buttons: timing rows are the switch and LED pins",
           timing and all(n in timing["rows"] for n in ("RA0", "RA3", "RC0", "RC3", "RC7")), timing)
    k = note["SW2 を押す"]
    click_timing_at_step(k)
    check("buttons: a click on the timing diagram moves to that step", step=k, pressedNames=["SW2"])
    expect("buttons: the bits that changed are marked", "RA2" in state()["timing"]["changed"], state()["timing"])


def seg7_counter():
    steps = steps_of("seg7_counter")
    kinds = [s["kind"] for s in steps]
    first_wait = kinds.index("timeout")
    pressed = next(i for i, s in enumerate(steps) if s.get("input_note") == "SW を押す")
    open_page("examples/seg7_counter/seg7_counter_viewer.html")
    check("seg7: reset, dark", step=0, lit="")
    goto(first_wait - 1)
    check("seg7: setup shows 0", digit="0", lit="abcdef")
    goto(first_wait)
    check("seg7: waits for the switch without writing", kind="timeout", exec=None, digit="0", pressed=[])
    expect("seg7: the wait is explained", "書かないまま待った" in text_of("exNote"), text_of("exNote"))
    goto(pressed)
    check("seg7: held, counts to 1", kind="write", digit="1", pressed=["SW"])
    goto(pressed + 2)
    check("seg7: held, counts to 3", kind="write", digit="3", lit="abcdg")
    ab("click", "input[name=tmAxis][value=time]")
    expect("seg7: the timing diagram switches to the time axis", state()["timing"]["mode"] == "time", state()["timing"])
    ab("click", "#bLast")
    check("seg7: released again, waits", kind="timeout", pressed=[])


def calculator():
    steps = steps_of("calculator")
    note = {s["input_note"]: i for i, s in enumerate(steps) if s.get("input_note")}
    open_page("examples/calculator/calculator_viewer.html")
    goto(note["1 を押す"])
    check("calc: 1 held, row RB0 at 0, column RB4 reads 0", kind="run", exec=114, key="1", row=0, cols="0111")
    goto(note["= を押す"])
    check("calc: = held, row RB3 at 0, column RB6 reads 0", key="=", row=3, cols="1101", lines=["12+34", ""])
    expect("calc: the held key is what joins row and column", "列 RB6 が 0 と読める" in text_of("cirText"), text_of("cirText"))
    goto(note["C を押す"] - 1)
    check("calc: after = the answer is on the second line", kind="end", key="", lines=["12+34", "=46"])
    ab("click", "#bLast")
    check("calc: C clears the screen", key="", lines=["", ""])


def voltmeter():
    ends = [i for i, s in enumerate(steps_of("voltmeter")) if s["kind"] == "end"]
    open_page("examples/voltmeter/voltmeter_viewer.html")
    for k, volts, adc, shown in zip(ends, (1.25, 3.3, 0.5, 5), (255, 675, 102, 1023), ("1.25V", "3.30V", "0.50V", "5.00V")):
        goto(k)
        check(f"volt: {volts} V on AN0 reads {adc} and shows {shown}", volts=volts, adc=adc, lines=[shown, ""])


def stopwatch():
    steps = steps_of("stopwatch")
    runs = [i for i, s in enumerate(steps) if s["kind"] == "run"]
    open_page("examples/stopwatch/stopwatch_viewer.html")
    goto(runs[0] - 1)
    check("stopwatch: shows 0 and waits while stopped", kind="timeout", digit="0", pressed=False)
    # where a cut-short wait halts depends on timing: inside XC8's division routine, or on a line of ours
    st = steps[runs[0] - 1]
    if st.get("where"):
        expect("stopwatch: the wait stopped in XC8's routine, named by its file, not as a line of ours",
               st["where"] in text_of("exNote") and "行目" not in text_of("exNote"), text_of("exNote"))
    else:
        expect("stopwatch: the wait stopped on a line of ours, named in the text",
               f"{st['next']} 行目のあたり" in text_of("exNote"), text_of("exNote"))
    goto(runs[0])
    check("stopwatch: pressed, waits for the release", kind="run", exec=55, pressed=True)
    goto(runs[0] + 3)
    check("stopwatch: counts to 3", kind="write", exec=62, digit="3", pressed=False)
    ab("click", "#bLast")
    check("stopwatch: pressed again, stopped at 5", kind="timeout", digit="5")


def seg7_mux():
    steps = steps_of("seg7_mux")
    runs = [i for i, s in enumerate(steps) if s["kind"] == "run"]
    open_page("examples/seg7_mux/seg7_mux_viewer.html")
    goto(12)
    check("mux: early on only the first digit has been lit", shown="1   ")
    goto(runs[0] - 1)
    check("mux: after a few frames the eye sees 1234", shown="1234")
    expect("mux: the explanation says the eye sees an average", "平均" in text_of("cirText"), text_of("cirText"))
    goto(runs[0])
    # what is recorded after run_to is the stretch to the next stop, too short for a round: only this moment
    check("mux: after run_to the writes were not followed, so only this moment is drawn", cut=True)
    expect("mux: and the page says so", "走らせた" in text_of("cirText"), text_of("cirText"))
    # the first stop after run_to that is averaged again: whole rounds while less than the 20 ms window is recorded
    first = None
    for i in range(runs[0] + 1, min(runs[0] + 41, len(steps))):
        goto(i)
        p = state().get("probe") or {}
        if not p.get("cut"):
            first = (i, p)
            break
    expect("mux: a few writes after run_to the display is averaged again", first is not None)
    if first:
        i, p = first
        expect("mux: over whole rounds, as the page says", p["covered"] >= 0.02 or (
            p["round"] and abs(p["covered"] / p["round"] - round(p["covered"] / p["round"])) < 1e-6
            and "巡" in text_of("cirText")), (i, p, text_of("cirText")))
    k = next(i for i in range(runs[0] - 1, 0, -1) if steps[i].get("watch") == "PORTA" and steps[i]["v"][5] != 0x0F)
    goto(k)
    check("mux: at a digit switch-on, exactly one digit is on", now=[[0x0E, 0x0D, 0x0B, 0x07].index(steps[k]["v"][5])])
    ab("click", "#bLast")
    check("mux: half a second later the count is 1235", shown="1235")


def buzzer():
    steps = steps_of("buzzer")
    open_page("examples/buzzer/buzzer_viewer.html")
    # the second rising edge of each note is the first stop that holds a full cycle
    notes = []
    for k, st in enumerate(steps):
        if st["kind"] == "write" and k + 1 < len(steps) and steps[k + 1]["kind"] != "write":
            notes.append(k)
    notes.append(len(steps) - 1)
    for k, letter in zip(notes, ("C4", "D4", "E4")):
        goto(k)
        check(f"buzzer: step {k} sounds {letter}", note=letter)
    runs = [i for i, st in enumerate(steps) if st["kind"] == "run"]
    goto(runs[1])
    check("buzzer: right after run_to the pitch is not known", hz=None)
    expect("buzzer: the text says the recording is too short", "分からない" in text_of("cirText"), text_of("cirText"))


def servo():
    steps = steps_of("servo")
    open_page("examples/servo/servo_viewer.html")
    ends = [k for k, st in enumerate(steps) if st["kind"] == "write" and (k + 1 == len(steps) or steps[k + 1]["kind"] != "write")]
    angles = []
    for k in ends:
        goto(k)
        angles.append(round(state()["probe"]["angle"]))
    expect("servo: 1.0, 1.5 and 2.0 ms read as -45, 0 and +45 degrees", angles == [-45, 0, 45], angles)


def dcmotor():
    steps = steps_of("dcmotor")
    open_page("examples/dcmotor/dcmotor_viewer.html")

    def first_after(note, kind=None):
        k = next(i for i, st in enumerate(steps) if st.get("input_note", "").startswith(note))
        return k
    goto(first_after("SW0 を押す") + 1)
    check("dcmotor: SW0 turns it forward", mode="正転", in1=1, in2=0)
    goto(first_after("SW1 を押す") + 2)
    check("dcmotor: SW1 turns it backward", mode="逆転", in1=0, in2=1)
    k = first_after("SW2 を離す") - 1
    goto(k)
    check("dcmotor: SW2 switches IN1 fast", mode="正転")
    speed = state()["probe"]["speed"]
    expect("dcmotor: half the time on is half speed", abs(speed - 0.5) < 0.02, speed)
    goto(first_after("SW2 を離す") + 1)
    check("dcmotor: released, it runs down", mode="止まっていく（空転）")


def seg7_mux_count():
    steps = steps_of("seg7_mux_count")
    runs = [i for i, s in enumerate(steps) if s["kind"] == "run"]
    open_page("examples/seg7_mux_count/seg7_mux_count_viewer.html")
    goto(runs[0] - 1)
    check("count: before the first jump the display reads 00", shown="00")
    seen = []
    for n, r in enumerate(runs):
        goto(r)
        check(f"count: jump {n + 1} stops where the count goes up, only this moment drawn", cut=True)
        end = runs[n + 1] - 1 if n + 1 < len(runs) else len(steps) - 1
        goto(end)
        seen.append(state()["probe"]["shown"])
    expect("count: the jumps show 01, 02, 03 and then 10", seen == ["01", "02", "03", "10"], seen)
    # the first averaged stop after the last jump: one 10 ms round recorded, less than the 20 ms window
    for i in range(runs[-1] + 1, len(steps)):
        goto(i)
        if not state()["probe"]["cut"]:
            break
    check("count: one round after the jump is averaged", shown="10")
    expect("count: and the page says it averaged a round", "1 巡" in text_of("cirText"), text_of("cirText"))


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
    buttons()
    seg7_counter()
    calculator()
    voltmeter()
    stopwatch()
    seg7_mux()
    seg7_mux_count()
    buzzer()
    servo()
    dcmotor()
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
