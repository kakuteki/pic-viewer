/* PIC viewer core: step controls, source, registers and explanation.
   A circuit view plugs in through PicViewer.circuits[type] = { title, options, setup(ctx), update(ctx) }. */
(() => {
  'use strict';
  const PV = window.PicViewer;
  const D = JSON.parse(document.getElementById('data').textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const PLAY_MS = 1400;
  const $ = (id) => document.getElementById(id);
  const S = { t: 0, step: 0, opts: {}, timer: null, probe: null, tmMode: 'step', tm: null };

  // ---------------- helpers, also used by the circuits
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const svgEl = (tag, attrs = {}) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
  };
  const num = (v, d = 1) => Number(v).toFixed(d);
  const hex = (v) => '0x' + v.toString(16).toUpperCase().padStart(2, '0');
  const pinsWith = (t, fn) => {
    const f = fn.toUpperCase();
    const out = [];
    t.pins.forEach((names, i) => { if (names.some((n) => n.toUpperCase() === f)) out.push(i + 1); });
    return out;
  };
  const portBit = (pin) => {
    const m = /^R([A-Z])(\d)$/i.exec(pin || '');
    return m ? { port: m[1].toUpperCase(), bit: Number(m[2]) } : null;
  };
  // the output latch of a port: LATx when it was read, PORTx otherwise
  const latchOf = (R, port) => (R['LAT' + port] !== undefined ? R['LAT' + port] : R['PORT' + port]);
  const levelBit = (v) => (v === 'high' ? 1 : v === 'low' ? 0 : Number.parseFloat(v) >= 2.5 ? 1 : 0);
  // circuit.switches (or the older single circuit.switch) with each one's state. The level put on the pin
  // (trace "set") decides whether it is pressed; the PORT bit is what the program reads, which differs while
  // the pin is still an analog input or an output. Without a set level, the PORT bit is all there is.
  const switchStates = (cfg, R, inputs = {}) => {
    const raw = Array.isArray(cfg.switches) ? cfg.switches : cfg.switch ? [cfg.switch] : [];
    return raw.filter((s) => s && portBit(s.pin)).map((s) => {
      const pb = portBit(s.pin);
      const pin = s.pin.toUpperCase();
      const reg = R['PORT' + pb.port];
      const read = reg === undefined ? null : (reg >> pb.bit) & 1;
      const set = inputs[pin] === undefined ? null : levelBit(inputs[pin]);
      const level = set !== null ? set : read;
      const low = s.active === 'low';
      return { pin, label: s.label || pin, low, level, read, down: level === null ? null : level === (low ? 0 : 1),
        mismatch: set !== null && read !== null && set !== read };
    });
  };
  // switch pins that read differently from their level, in words (empty when they all agree)
  const mismatchText = (sw) => {
    const groups = {};
    sw.filter((s) => s.mismatch).forEach((s) => { (groups[`${s.level}${s.read}`] ||= []).push(s); });
    return Object.values(groups).map((g) => `${g.map((s) => s.pin).join('、')} はピンが ${g[0].level} なのに、`
      + `プログラムには ${g[0].read} と読める（アナログ入力のままか、出力にしている）。`).join('');
  };
  // a current path: an orange line, moving dots, and arrows (explicit [x, y, angle] or along long segments)
  function drawPath(g, pts, cls, opt = {}) {
    const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0] + ' ' + p[1]).join(' ');
    g.append(svgEl('path', { class: 'cur ' + cls, d }));
    g.append(svgEl('path', { class: 'flow ' + cls, d }));
    const arrow = (x, y, ang) => g.append(svgEl('use', { href: '#ah', class: 'ah', transform: `translate(${x} ${y}) rotate(${ang})` }));
    if (Array.isArray(opt.arrows)) { opt.arrows.forEach(([x, y, a]) => arrow(x, y, a)); return; }
    if (opt.arrows === false) return;
    pts.slice(1).forEach((q, i) => {
      const p = pts[i];
      if (Math.hypot(q[0] - p[0], q[1] - p[1]) < 70) return;
      const mx = (p[0] + q[0]) / 2;
      const my = (p[1] + q[1]) / 2;
      if ((opt.skip || []).some(([x0, y0, x1, y1]) => mx >= x0 && mx <= x1 && my >= y0 && my <= y1)) return;
      arrow(mx, my, Math.atan2(q[1] - p[1], q[0] - p[0]) * 180 / Math.PI);
    });
  }
  // 7-segment helpers shared by the seg7 and seg7mux views: names by bit (bit 0 = a), the shapes of digits and a
  // few letters, and the outline of each segment for a digit w wide and h high at (x, y) with strokes t thick
  const seg7 = {
    NAMES: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'dp'],
    SHAPES: { 0x3f: '0', 0x06: '1', 0x5b: '2', 0x4f: '3', 0x66: '4', 0x6d: '5', 0x7d: '6', 0x07: '7', 0x27: '7',
      0x7f: '8', 0x6f: '9', 0x67: '9', 0x77: 'A', 0x7c: 'b', 0x39: 'C', 0x58: 'c', 0x5e: 'd', 0x79: 'E', 0x71: 'F',
      0x76: 'H', 0x38: 'L', 0x54: 'n', 0x08: '_', 0x40: '-', 0x73: 'P', 0x3e: 'U', 0x00: '（消灯）' },
    paths(x, y, w, h, t) {
      const hs = (yy) => `M${x + t * 0.67} ${yy} L${x + t * 1.22} ${yy - t / 2} H${x + w - t * 1.22} L${x + w - t * 0.67} ${yy} L${x + w - t * 1.22} ${yy + t / 2} H${x + t * 1.22} Z`;
      const vs = (xx, y0, y1) => `M${xx} ${y0 + t * 0.67} L${xx + t / 2} ${y0 + t * 1.22} V${y1 - t * 1.22} L${xx} ${y1 - t * 0.67} L${xx - t / 2} ${y1 - t * 1.22} V${y0 + t * 1.22} Z`;
      return { a: hs(y), g: hs(y + h / 2), d: hs(y + h), f: vs(x, y, y + h / 2), b: vs(x + w, y, y + h / 2),
        e: vs(x, y + h / 2, y + h), c: vs(x + w, y + h / 2, y + h) };
    },
  };
  // What the recording knows between two stops. The values read at a stop hold until the next one only when the
  // program could not change the circuit unseen in between: up to a write stop (every write to the watched
  // registers stops it), a timeout (no write came) or a step. Up to a run_to stop the program ran freely, and
  // after the end of an until_write the writes were no longer followed: those stretches are unknown.
  const knownAfter = (t, i) => {
    const next = t.steps[i + 1];
    return Boolean(next) && next.kind !== 'run' && next.kind !== 'start' && t.steps[i].kind !== 'end';
  };
  // the recorded stretches before step k, newest first, as far back as `span` (seconds, or instruction cycles
  // without a clock) or to the first unknown stretch: {spans: [{from, to, R, i}], covered, cut, end, top}. With
  // `ahead` they reach on to the next stop when this stop's values are known to hold until then (top = k + 1)
  function history(t, k, span, ahead) {
    const timeOf = (i) => { const s = secondsOf(t, t.steps[i]); return s === null ? t.steps[i].cycles : s; };
    // not across an input change: the inputs of step k + 1 were set right after stop k, so up to it is after the change
    const next = t.steps[k + 1];
    const top = ahead && knownAfter(t, k) && !next.inputs && next.key === undefined ? k + 1 : k;
    const end = timeOf(top);
    const spans = [];
    let cut = false;
    for (let i = top - 1; i >= 0; i--) {
      const b = timeOf(i + 1);
      if (end - b >= span) break;
      if (!knownAfter(t, i)) { cut = true; break; }
      const a = Math.max(timeOf(i), end - span);
      if (b > a) spans.push({ from: a, to: b, R: regsAt(t, t.steps[i]), i });
      if (end - timeOf(i) >= span) break;
    }
    return { spans, covered: spans.reduce((s, x) => s + x.to - x.from, 0), cut, end, top };
  }
  // a pin's last full cycle before step k, from its level at each stop (level(R) gives 0 or 1): the time from the
  // second last rising edge to the last, and how long it was 1 in it. null when it did not go round twice within
  // `span` of recorded time. `since` is how long ago its level last changed, `edges` how often it changed in the
  // recording looked at; `cut` says that recording starts at an unknown stretch (run_to), `input` at an input change.
  function pulse(t, k, level, span) {
    const h = history(t, k, span);
    // inputs changed by the plan (set, press) change what the program does; in the simulation they come only
    // a few writes apart, so a cycle must not reach back across one: start from the last change
    let since = 0;
    for (let i = k; i > 0; i--) if (t.steps[i].inputs || t.steps[i].key !== undefined) { since = i - 1; break; }
    const cutAtInput = h.spans.length > 0 && h.spans[h.spans.length - 1].i < since;
    h.spans = h.spans.filter((s) => s.i >= since);
    const now = level(regsAt(t, t.steps[k]));
    const seq = [{ at: h.end, v: now }, ...h.spans.map((s) => ({ at: s.from, v: level(s.R) }))];
    // seq: the level from each moment on, newest first; an edge where the level differs from the one before it
    const edges = [];
    for (let j = 0; j + 1 < seq.length; j++) {
      if (seq[j].v !== seq[j + 1].v) edges.push({ at: h.spans[j].to, rising: seq[j].v === 1 });
    }
    const ago = edges.length ? h.end - edges[0].at : null;
    const rises = edges.filter((e) => e.rising);
    const base = { since: ago, level: now, cut: h.cut, input: cutAtInput, edges: edges.length };
    if (rises.length < 2) return { ...base, period: null, high: null, duty: null };
    const [r1, r2] = rises;
    const fall = edges.find((e) => !e.rising && e.at > r2.at && e.at <= r1.at);
    const period = r1.at - r2.at;
    const high = fall ? fall.at - r2.at : null;
    return { ...base, period, high, duty: high === null || period <= 0 ? null : high / period };
  }
  PV.util = { el, svgEl, num, hex, pinsWith, portBit, latchOf, switchStates, mismatchText, drawPath, seg7 };

  // ---------------- data helpers
  const tgt = () => D.targets[S.t];
  const last = () => tgt().steps.length - 1;
  // a target's circuit is one part or a list of parts, each drawn by the plugin of its type
  const partsOf = (t) => (Array.isArray(t.circuit) ? t.circuit : [t.circuit]);
  const pluginOf = (cfg) => PV.circuits[cfg.type] || PV.circuits.pins;
  const optionsOf = (t) => partsOf(t).flatMap((cfg) => pluginOf(cfg).options || []);
  const isCounter = (t, name) => t.counters.indexOf(name) >= 0;
  const instrHz = (t) => (t.fosc_hz ? t.fosc_hz / 4 : null);
  const bitName = (r, b) => r.bits[b] || `${r.name}<${b}>`;
  const codeOf = (line) => { const i = line.indexOf('//'); return (i < 0 ? line : line.slice(0, i)).trim(); };
  const fmtTime = (s) => (s < 1e-3 ? `${num(s * 1e6, 1)} µs` : s < 1 ? `${num(s * 1e3, 3)} ms` : s < 60 ? `${num(s, 3)} 秒`
    : s < 3600 ? `${Math.floor(s / 60)} 分 ${num(s % 60, 1)} 秒` : `${Math.floor(s / 3600)} 時間 ${Math.floor((s % 3600) / 60)} 分`);

  // the levels put on input pins up to step k (trace "set"), as the switches really are
  function inputsAt(t, k) {
    const out = {};
    for (let i = 0; i <= k && i < t.steps.length; i++) Object.assign(out, t.steps[i].inputs || {});
    return out;
  }

  // the keypad key held at step k (trace "press" and "release"), or ''
  function keyAt(t, k) {
    let key = '';
    for (let i = 0; i <= k && i < t.steps.length; i++) if (t.steps[i].key !== undefined) key = t.steps[i].key;
    return key;
  }

  // what the plan changed just before this stop: input levels, a key pressed or released
  function changeText(st) {
    const parts = [];
    if (st.inputs && Object.keys(st.inputs).length) parts.push(`入力を変えた: ${Object.entries(st.inputs).map(([p, v]) => `${p} = ${levelText(v)}`).join('、')}`);
    if (st.key !== undefined) parts.push(st.key ? `キー ${st.key} を押した` : 'キーを離した');
    return parts.length ? `${parts.join('、')}${st.input_note ? `（${st.input_note}）` : ''}。` : '';
  }

  function regsAt(t, step) {
    const R = {};
    if (step) t.regs.forEach((r, i) => { R[r.name] = step.v[i]; });
    return R;
  }

  function changedBits(t, st, prev) {
    const out = [];
    if (!prev) return out;
    t.regs.forEach((r, i) => {
      if (isCounter(t, r.name)) return;
      const diff = (prev.v[i] ^ st.v[i]) & r.mask;
      for (let b = 7; b >= 0; b--) {
        if ((diff >> b) & 1) out.push({ name: bitName(r, b), from: (prev.v[i] >> b) & 1, to: (st.v[i] >> b) & 1 });
      }
    });
    return out;
  }

  // ---------------- one-time and per-target building
  function buildTargets() {
    const box = $('targetOpts');
    box.replaceChildren();
    D.targets.forEach((t, i) => {
      const label = el('label');
      const input = el('input');
      input.type = 'radio'; input.name = 'target'; input.value = String(i); input.checked = i === S.t;
      input.addEventListener('change', () => { S.t = i; stop(); buildTarget(); });
      label.append(input, el('span', '', t.device));
      box.append(label);
    });
  }

  function buildOptions(t) {
    document.querySelectorAll('#choices .seg.opt').forEach((n) => n.remove());
    optionsOf(t).forEach((o) => {
      if (!o.choices.some((ch) => ch.value === S.opts[o.key])) S.opts[o.key] = o.default || o.choices[0].value;
      const fs = el('fieldset', 'seg opt');
      fs.append(el('legend', '', o.legend));
      const box = el('div', 'opts');
      o.choices.forEach((ch) => {
        const label = el('label');
        const input = el('input');
        input.type = 'radio'; input.name = 'opt-' + o.key; input.value = ch.value; input.checked = S.opts[o.key] === ch.value;
        input.addEventListener('change', () => { S.opts[o.key] = ch.value; update(); });
        label.append(input, el('span', '', ch.label));
        box.append(label);
      });
      fs.append(box);
      $('choices').append(fs);
    });
  }

  function buildSource(t) {
    const ran = new Set(t.steps.map((s) => s.exec).filter((n) => n !== null));
    const code = $('code');
    code.replaceChildren();
    t.source.forEach((line, i) => {
      const no = i + 1;
      const li = el('li');
      li.dataset.line = String(no);
      const tx = el('span', 'tx');
      const cut = line.indexOf('//');
      if (cut < 0) tx.textContent = line;
      else tx.append(line.slice(0, cut), el('span', 'cm', line.slice(cut)));
      li.append(el('span', 'ln', String(no)), tx);
      if (ran.has(no)) {
        li.classList.add('run');
        li.tabIndex = 0;
        const jump = () => { stop(); setStep(t.steps.findIndex((s) => s.exec === no)); };
        li.addEventListener('click', jump);
        li.addEventListener('keydown', (e) => { if (e.key === 'Enter') { jump(); e.preventDefault(); } });
      }
      code.append(li);
    });
  }

  function buildRegs(t, marks) {
    const body = $('regBody');
    body.replaceChildren();
    t.regs.forEach((r) => {
      const tr = el('tr');
      const th = el('th', '', r.name);
      th.scope = 'row';
      tr.append(th, el('td', 'val'));
      for (let b = 7; b >= 0; b--) {
        const td = el('td');
        const bit = el('span', 'bit');
        if (!((r.mask >> b) & 1)) { bit.classList.add('none'); bit.textContent = '-'; bit.title = `${r.name} のビット ${b} は無い`; }
        else bit.title = `${bitName(r, b)}（ビット ${b}）`;
        if (marks.some((m) => m.reg === r.name && m.bit === b)) bit.classList.add('mark');
        td.append(bit);
        tr.append(td);
      }
      body.append(tr);
    });
  }

  function buildFoot(t, extra) {
    const hz = instrHz(t);
    const lines = [
      `レジスタの値と実行の順番は、MPLAB X ${t.tools.mplabx || ''} のシミュレータ（mdb）で ${t.source_name} を実行して読んだ値（コンパイラ XC8 ${t.tools.xc8 || ''}、記録 ${t.traced}）。`,
      `ピン番号とビットの有無は Microchip のデバイス定義ファイル（${t.pack.name} ${t.pack.version}）から。`,
      hz ? `時間は命令サイクル数を 1 秒あたり ${hz / 1e6} M 命令（Fosc ${t.fosc_hz / 1e6} MHz の 4 分の 1）で割った値。` : '',
      t.fast_forward ? `早送り: __delay_ms の待ちを 1/${t.fast_forward} に縮めた版をシミュレータで動かし、縮めた時間はプログラムの中で数えて足している。命令サイクル数は縮めた後の値。` : '',
      ...extra,
      `作成: ${D.tool}`,
    ].filter(Boolean);
    $('foot').replaceChildren(...lines.map((s) => el('li', '', s)));
  }

  function ctxBase(t) {
    return { t, opts: S.opts, U: PV.util };
  }

  function buildTarget() {
    const t = tgt();
    S.step = Math.min(S.step, last());
    $('slider').max = String(last());
    $('srcName').textContent = t.source_name;
    $('subtitle').textContent = t.summary
      ? `${t.device}: ${t.summary}`
      : `${t.device} の ${t.source_name} を MPLAB X のシミュレータで実行した記録。`;
    buildSource(t);
    buildOptions(t);
    const parts = partsOf(t);
    const box = $('cirBox');
    box.replaceChildren();
    const infos = parts.map((cfg) => {
      const part = el('div', 'cirpart');
      box.append(part);
      return pluginOf(cfg).setup({ ...ctxBase(t), cfg, box: part }) || {};
    });
    const joined = (key, sep) => infos.map((i) => i[key]).filter(Boolean).join(sep);
    $('cirTitle').textContent = [...new Set(parts.map((cfg) => pluginOf(cfg).title || '回路'))].join('、');
    buildRegs(t, infos.flatMap((i) => i.marks || []));
    $('cirSub').textContent = joined('sub', '。');
    $('cirAssume').textContent = joined('assume', ' ');
    const counterHint = t.counters.length ? `${t.counters.join('、')} は数え続けるカウンタなので、変化の印を付けない。` : '';
    $('regHint').textContent = ['オレンジの枠は直前のステップから変わったビット。- はそのマイコンに無いビット。', joined('regHint', ' '), counterHint]
      .filter(Boolean).join(' ');
    buildFoot(t, infos.flatMap((i) => i.foot || []));
    buildTiming(t);
    update();
  }

  // seconds since reset; with fast_forward, the waits cut from __delay_ms are added back
  const secondsOf = (t, st) => {
    const hz = instrHz(t);
    return hz ? st.cycles / hz + (st.skipped_us || 0) / 1e6 : null;
  };
  const levelText = (v) => (v === 'high' ? '1' : v === 'low' ? '0' : v);

  // ---------------- per-step painting
  function explain(t, st, prev) {
    const regsChanged = [];
    if (prev) t.regs.forEach((r, i) => { if (!isCounter(t, r.name) && prev.v[i] !== st.v[i]) regsChanged.push(`${r.name} ${hex(prev.v[i])} から ${hex(st.v[i])}`); });
    const inputs = changeText(st);
    if (st.kind === 'timeout') {
      const ran = st.cycles - (prev ? prev.cycles : 0);
      const s1 = secondsOf(t, st);
      const span = s1 === null ? `${ran} サイクル`
        : `${ran} サイクル、${t.fast_forward ? '縮めた待ちを足すと' : ''}約 ${fmtTime(s1 - (prev ? secondsOf(t, prev) : 0))}`;
      $('exNo').textContent = '';
      $('exCode').textContent = `${st.watch} への書き込みなし`;
      $('exNote').textContent = inputs + `${st.watch} に書かないまま待ったので、シミュレータを止めた（その間 ${span}）。`
        + (st.next ? `止めたときは ${st.next} 行目のあたりを実行していた。`
          : st.where ? `止めたときは ${st.where}（XC8 に付いてくる関数。割り算などで呼ばれる）の中を実行していた。` : '')
        + '入力が変わるのを待っているか、書き込みの無い所を回っている。待った長さは止め方で決まる（実機ならスイッチを押すまでの時間）。';
    } else if (st.exec === null && st.kind !== 'start') {
      // written by code without a line of ours: XC8's own routines (where), or code the compiler added
      $('exNo').textContent = '';
      $('exCode').textContent = st.where ? `${st.where} の中` : '行番号の無い所';
      $('exNote').textContent = inputs + (st.watch ? `${st.watch} に書いた所で止めた。` : '')
        + (st.where ? `書いたのは ${st.where}（XC8 に付いてくる関数）の中。` : 'ソースの行に当たらない所（コンパイラが足した命令）で止まった。');
    } else if (st.exec === null) {
      $('exNo').textContent = '';
      $('exCode').textContent = 'まだ無い';
      $('exNote').textContent = inputs + 'リセット直後。main の最初の行はまだ実行していない。';
    } else {
      $('exNo').textContent = `${st.exec} 行目`;
      $('exCode').textContent = codeOf(t.source[st.exec - 1] || '');
      const repeat = prev && prev.exec === st.exec && regsChanged.length === 0;
      const bits = changedBits(t, st, prev);
      const auto = bits.length ? '変わったビット: ' + bits.map((b) => `${b.name} が ${b.from} から ${b.to}`).join('、') : 'レジスタの変化は無い。';
      let note;
      if (st.kind === 'end') note = t.notes[String(st.exec)] || `${st.exec} 行目に来たので、${st.watch} への書き込みを追うのを終える。`;
      else if (repeat) note = '同じ行をもう一度実行した（ループの中）。レジスタは変わらない。';
      else note = t.notes[String(st.exec)] ? `${t.notes[String(st.exec)]}${bits.length ? `（${auto}）` : ''}` : auto;
      if (st.kind === 'write') note = `${st.watch} に書いた所で止めた。` + note;
      $('exNote').textContent = inputs + note;
    }
    $('exDelta').textContent = prev ? (regsChanged.length ? `変わったレジスタ: ${regsChanged.join('、')}` : '変わったレジスタ: なし') : '';
    $('exDelta').className = regsChanged.length ? 'delta' : '';
    const sec = secondsOf(t, st);
    // the waits that ran out last as long as the plan waited, not as the program decides: also the clock without
    const hz = instrHz(t);
    const waitedSec = hz && st.waited_cycles ? st.waited_cycles / hz + (st.waited_us || 0) / 1e6 : 0;
    const ff = t.fast_forward ? '早送りで縮めた待ちを足して、' : '';
    const time = sec === null ? ''
      : waitedSec > 0 ? `（${ff}書かないまま待った間を除くと ${fmtTime(sec - waitedSec)}。待った ${fmtTime(waitedSec)}も足すと ${fmtTime(sec)}）`
        : t.steps.slice(0, S.step + 1).some((s) => s.kind === 'timeout') ? `（${ff}${fmtTime(sec)}。書かないまま待った時間を含む）`
          : t.fast_forward ? `（早送りで縮めた待ちを足すと ${fmtTime(sec)}）` : `（${fmtTime(sec)}）`;
    const next = st.next === null ? '不明' : `${st.next} 行目`;
    $('exMeta').textContent = `次に実行する行: ${next}（アドレス ${st.addr}）　リセットからの命令サイクル数: ${st.cycles}${time}`;
  }

  function paintSource(st) {
    const code = $('code');
    code.querySelectorAll('li').forEach((li) => {
      const no = Number(li.dataset.line);
      li.classList.toggle('cur', no === st.exec);
      li.classList.toggle('at', st.kind === 'timeout' && no === st.next);   // where a wait was cut short
    });
    const cur = code.querySelector('li.cur') || code.querySelector('li.at');
    if (cur && code.scrollHeight > code.clientHeight) {   // keep the executed line inside the listing's own scroll box
      const box = code.getBoundingClientRect();
      const row = cur.getBoundingClientRect();
      const margin = row.height * 2;
      if (row.top < box.top + margin) code.scrollTop -= box.top + margin - row.top;
      else if (row.bottom > box.bottom - margin) code.scrollTop += row.bottom - (box.bottom - margin);
    }
  }

  function paintRegs(t, st, prev) {
    [...$('regBody').rows].forEach((tr, i) => {
      const r = t.regs[i];
      const v = st.v[i];
      const pv = prev ? prev.v[i] : v;
      const counter = isCounter(t, r.name);
      tr.classList.toggle('chg', pv !== v && !counter);
      tr.cells[1].textContent = hex(v);
      tr.cells[1].title = pv !== v ? `直前の値は ${hex(pv)}` : '';
      for (let b = 7; b >= 0; b--) {
        const bit = tr.cells[2 + (7 - b)].firstChild;
        if (!((r.mask >> b) & 1)) continue;
        const one = (v >> b) & 1;
        bit.textContent = String(one);
        bit.classList.toggle('one', one === 1);
        bit.classList.toggle('flip', ((pv ^ v) >> b & 1) === 1 && !counter);
      }
    });
  }

  function paintStatus(list) {
    $('cirStatus').replaceChildren(...list.map((s) => {
      const d = el('div', s.tone || '');
      d.append(el('dt', '', s.label), el('dd', s.mono ? 'mono' : '', s.value));   // mono keeps blank digits in place
      return d;
    }));
  }

  function waveCaption(w, hz) {
    const range = (a) => (Math.min(...a) === Math.max(...a) ? `${a[0]}` : `${Math.min(...a)}〜${Math.max(...a)}`);
    const avg = (a) => a.reduce((s, x) => s + x, 0) / a.length;
    const parts = [];
    if (w.periods.length) parts.push(`周期 ${range(w.periods)} サイクル` + (hz ? `（約 ${num(hz / avg(w.periods) / 1000)} kHz）` : ''));
    if (w.highs.length) parts.push(`H の長さ ${range(w.highs)} サイクル`);
    if (w.periods.length && w.highs.length) parts.push(`デューティ 約 ${Math.round((avg(w.highs) / avg(w.periods)) * 100)} %`);
    else parts.push(`この間ずっと ${w.edges[0][1] ? 'H' : 'L'}` );
    return parts.join('、') + '。1 命令は 1〜2 サイクルなので、変わり目の位置は 1〜2 サイクルずれうる。';
  }

  function paintWaves(t, st) {
    const box = $('waveBox');
    const list = (t.waves || []).filter((w) => w.at === st.next);
    box.hidden = list.length === 0;
    box.replaceChildren();
    const hz = instrHz(t);
    list.forEach((w) => {
      const W = 600, H = 40, hi = 5, lo = H - 5;
      const span = Math.max(1, w.end - w.start);
      const x = (c) => (((c - w.start) / span) * W).toFixed(1);
      const pts = [];
      w.edges.forEach(([c, lv], i) => {
        if (i > 0) pts.push(`${x(c)},${w.edges[i - 1][1] ? hi : lo}`);
        pts.push(`${x(c)},${lv ? hi : lo}`);
      });
      pts.push(`${W},${w.edges[w.edges.length - 1][1] ? hi : lo}`);
      const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', role: 'img', 'aria-label': `${w.pin} の波形` });
      svg.append(svgEl('polyline', { points: pts.join(' ') }));
      const card = el('div', 'wave');
      card.append(el('p', 'head', `${w.pin} の実際の波形: シミュレータで ${w.at} 行目に止めてから ${w.samples} 命令、1 命令ずつピンを読んだ（${w.end - w.start} サイクル分）`),
        svg, el('p', 'cap', waveCaption(w, hz)));
      box.append(card);
    });
  }

  // ---------------- timing diagram: the registers that change somewhere in the record.
  // Port pins get one row per bit that changes; other registers one band with the value in hex.
  const TM = { LW: 112, RH: 22, TOP: 30, FOOT: 26, MIN_DX: 9, MAX_ROWS: 40 };
  const PIN_REG = /^(PORT|LAT)[A-Z]$/;

  function timingRows(t) {
    const bits = [];
    const bands = [];
    t.regs.forEach((r, i) => {
      if (isCounter(t, r.name)) return;
      const vals = t.steps.map((s) => s.v[i] & r.mask);
      if (vals.every((v) => v === vals[0])) return;
      if (!PIN_REG.test(r.name)) { bands.push({ band: true, i, name: r.name, reg: r.name }); return; }
      for (let b = 7; b >= 0; b--) {
        if ((r.mask >> b) & 1 && vals.some((v) => ((v ^ vals[0]) >> b) & 1)) bits.push({ band: false, i, b, name: bitName(r, b), reg: r.name });
      }
    });
    return [...bits, ...bands];
  }

  // x of every step. 'step': evenly spaced. 'time': in proportion to time, except two kinds of gap that
  // are drawn as a break of fixed length: a gap next to a cut-short wait (its length is only how long we
  // waited), and a gap longer than three times the 90th percentile of the other gaps.
  function timingXs(t, width, mode) {
    const n = t.steps.length;
    const even = () => ({ xs: t.steps.map((_, k) => (n > 1 ? (k * width) / (n - 1) : 0)), breaks: [] });
    if (mode !== 'time' || n < 2) return even();
    const at = t.steps.map((s) => (instrHz(t) ? secondsOf(t, s) : s.cycles));
    const gaps = at.slice(1).map((v, k) => v - at[k]);
    const waits = gaps.map((_, k) => t.steps[k].kind === 'timeout' || t.steps[k + 1].kind === 'timeout');
    const pos = gaps.filter((g, k) => g > 0 && !waits[k]).sort((a, b) => a - b);
    if (!pos.length) return even();
    const cap = pos[Math.floor((pos.length - 1) * 0.9)] * 3;
    const cut = gaps.map((g, k) => waits[k] || g > cap);
    const used = gaps.map((g, k) => (cut[k] ? cap : g));
    const total = used.reduce((s, g) => s + g, 0);
    const xs = [0];
    const breaks = [];
    used.forEach((g, k) => {
      xs.push(xs[k] + (g / total) * width);
      if (cut[k]) breaks.push({ x: (xs[k] + xs[k + 1]) / 2, real: gaps[k] });
    });
    return { xs, breaks };
  }

  function buildTiming(t) {
    const box = $('tmBox');
    box.replaceChildren();
    const rows = timingRows(t).slice(0, TM.MAX_ROWS);
    const n = t.steps.length;
    S.tm = null;
    if (!rows.length || n < 2) {
      $('tmHint').textContent = 'この記録の中で値の変わったレジスタが無い。';
      return;
    }
    const avail = Math.max(240, box.clientWidth - TM.LW - 28);
    const width = S.tmMode === 'time' ? avail : Math.max(avail, (n - 1) * TM.MIN_DX);
    const { xs, breaks } = timingXs(t, width, S.tmMode);
    const X = (k) => TM.LW + 8 + xs[k];
    const H = TM.TOP + rows.length * TM.RH + TM.FOOT;
    const svg = svgEl('svg', { id: 'tm', width: String(TM.LW + width + 24), height: String(H), role: 'img',
      'aria-label': `値の変わった ${rows.length} 行を、ステップ 0 から ${n - 1} まで並べた図` });
    const add = (tag, attrs, text, parent = svg) => { const e = svgEl(tag, attrs); if (text !== undefined) e.textContent = text; parent.append(e); return e; };
    // input changes and cut-short waits along the top
    t.steps.forEach((st, k) => {
      if (st.kind === 'timeout') add('line', { class: 'tmwait', x1: X(k), x2: X(k), y1: TM.TOP - 6, y2: H - TM.FOOT });
      const change = changeText(st);
      if (change) {
        const g = add('g', { class: 'tmin' });
        add('path', { d: `M${X(k) - 5} ${TM.TOP - 16} H${X(k) + 5} L${X(k)} ${TM.TOP - 7} Z` }, undefined, g);
        add('title', {}, `ステップ ${k}: ${change}`, g);
      }
    });
    breaks.forEach((bk) => {
      add('path', { class: 'tmbreak', d: `M${TM.LW + 8 + bk.x - 6} ${TM.TOP - 2} l6 -8 M${TM.LW + 8 + bk.x} ${TM.TOP - 2} l6 -8` });
      add('title', {}, `ここは ${instrHz(t) ? fmtTime(bk.real) : `${bk.real} サイクル`} を縮めて描いた`,
        add('rect', { class: 'tmhit', x: TM.LW + 8 + bk.x - 8, y: TM.TOP - 12, width: 18, height: 12 }));
    });
    const labels = [];
    const end = X(n - 1) + 12;
    rows.forEach((row, r) => {
      const top = TM.TOP + r * TM.RH;
      labels.push(add('text', { class: 'tmlabel', x: TM.LW - 4, y: top + TM.RH / 2 + 4, 'text-anchor': 'end' }, row.name));
      if (row.band) {
        // one band per run of the same value, with slanted ends where the value changes
        const val = (k) => t.steps[k].v[row.i];
        const lo = top + 4;
        const hi = top + TM.RH - 5;
        const mid = (lo + hi) / 2;
        let from = 0;
        for (let k = 1; k <= n; k++) {
          if (k < n && val(k) === val(k - 1)) continue;
          const x0 = X(from);
          const x1 = k < n ? X(k) : end;
          const g = add('g', { class: 'tmband' });
          add('path', { d: x1 - x0 < 8 ? `M${x0} ${lo} V${hi}`
            : `M${x0} ${mid} L${x0 + 3} ${lo} H${x1 - 3} L${x1} ${mid} L${x1 - 3} ${hi} H${x0 + 3} Z` }, undefined, g);
          if (x1 - x0 >= 34) add('text', { x: (x0 + x1) / 2, y: mid + 4, 'text-anchor': 'middle' }, hex(val(from)), g);
          add('title', {}, `${row.reg} = ${hex(val(from))}（ステップ ${from} から）`, g);
          from = k;
        }
        return;
      }
      add('title', {}, `${row.reg} のビット ${row.b}`, labels[r]);
      const yOf = (lv) => (lv ? top + 4 : top + TM.RH - 5);
      const lv = (k) => (t.steps[k].v[row.i] >> row.b) & 1;
      let d = `M${X(0)} ${yOf(lv(0))}`;
      let fill = '';
      let from = lv(0) ? X(0) : null;
      for (let k = 1; k < n; k++) {
        if (lv(k) !== lv(k - 1)) {
          d += ` H${X(k)} V${yOf(lv(k))}`;
          if (lv(k)) from = X(k);
          else { fill += `M${from} ${top + 4} H${X(k)} V${top + TM.RH - 5} H${from} Z `; from = null; }
        }
      }
      d += ` H${end}`;
      if (from !== null) fill += `M${from} ${top + 4} H${end} V${top + TM.RH - 5} H${from} Z`;
      if (fill) add('path', { class: 'tmhi', d: fill });
      add('path', { class: 'tmwave', d });
    });
    // step numbers (or times) along the bottom, at least 70 px apart
    let lastX = -1e9;
    t.steps.forEach((st, k) => {
      if (X(k) - lastX < 70 && k !== n - 1) return;
      if (k === n - 1 && X(k) - lastX < 40) return;
      lastX = X(k);
      const sec = secondsOf(t, st);
      const text = S.tmMode === 'time' && sec !== null ? fmtTime(sec) : String(k);
      add('text', { class: 'tmtick', x: X(k), y: H - 8, 'text-anchor': k === 0 ? 'start' : k === n - 1 ? 'end' : 'middle' }, text);
    });
    const cursor = add('line', { class: 'tmcur', x1: X(0), x2: X(0), y1: TM.TOP - 4, y2: H - TM.FOOT + 2 });
    svg.addEventListener('click', (e) => {
      const rect = svg.getBoundingClientRect();
      const x = (e.clientX - rect.left) * (Number(svg.getAttribute('width')) / rect.width);
      let best = 0;
      xs.forEach((_, k) => { if (Math.abs(X(k) - x) < Math.abs(X(best) - x)) best = k; });
      stop();
      setStep(best);
    });
    box.append(svg);
    S.tm = { rows, X, cursor, labels };
    const more = timingRows(t).length > rows.length ? `（多いので最初の ${TM.MAX_ROWS} 本だけ）` : '';
    $('tmHint').textContent = `記録の中で値の変わったレジスタだけを並べた${more}。ポート（PORT、LAT）はビットごとの 0 と 1、ほかのレジスタは値（16 進）の帯。`
      + '各ステップで止めて読んだ値なので、ステップの間の細かい動きは描いていない。縦の点線は書かないまま待って止めた所、上の三角は入力を変えた所。'
      + (breaks.length ? '上の二本の斜線は、待った間と長すぎる間を詰めて描いた所（触れると本当の長さが出る）。' : '')
      + '図をクリックすると、そのステップへ移る。';
  }

  function paintTiming(t, st, prev) {
    if (!S.tm) return;
    const x = S.tm.X(S.step);
    S.tm.cursor.setAttribute('x1', x);
    S.tm.cursor.setAttribute('x2', x);
    S.tm.rows.forEach((row, r) => {
      const diff = prev ? prev.v[row.i] ^ st.v[row.i] : 0;
      S.tm.labels[r].classList.toggle('chg', row.band ? diff !== 0 : ((diff >> row.b) & 1) === 1);
    });
    const box = $('tmBox');
    if (box.scrollWidth > box.clientWidth && (x < box.scrollLeft + TM.LW || x > box.scrollLeft + box.clientWidth - 40)) {
      box.scrollLeft = Math.max(0, x - box.clientWidth / 2);
    }
  }

  function update() {
    const t = tgt();
    const st = t.steps[S.step];
    const prev = S.step > 0 ? t.steps[S.step - 1] : null;
    $('slider').value = String(S.step);
    $('stepLabel').textContent = `ステップ ${S.step} / ${last()}`;
    $('bFirst').disabled = $('bPrev').disabled = S.step === 0;
    $('bNext').disabled = $('bLast').disabled = S.step === last();
    explain(t, st, prev);
    paintSource(st);
    paintRegs(t, st, prev);
    const base = { ...ctxBase(t), st, prev, k: S.step, R: regsAt(t, st), P: prev ? regsAt(t, prev) : null,
      inputs: inputsAt(t, S.step), key: keyAt(t, S.step), regsAt: (step) => regsAt(t, step), fmtTime,
      instrHz: instrHz(t), seconds: secondsOf(t, st),
      // seconds since reset of any step (instruction cycles when the clock is not known)
      timeAt: (step) => { const s = secondsOf(t, step); return s === null ? step.cycles : s; },
      // the recorded stretches before this step, and a pin's last full cycle (see history and pulse above)
      history: (span, ahead) => history(t, S.step, span, ahead),
      pulse: (level, span) => pulse(t, S.step, level, span) };
    const boxes = $('cirBox').children;
    const results = partsOf(t).map((cfg, i) => pluginOf(cfg).update({ ...base, cfg, box: boxes[i] }) || {});
    paintStatus(results.flatMap((r) => r.status || []));
    $('cirText').textContent = results.map((r) => r.text).filter(Boolean).join(' ');
    // merged for the common one-part case; 'parts' keeps each part's own values where names collide
    S.probe = results.some((r) => r.probe)
      ? { ...Object.assign({}, ...results.map((r) => r.probe || {})), parts: results.map((r) => r.probe || null) } : null;
    paintWaves(t, st);
    paintTiming(t, st, prev);
    writeHash(t);
  }

  // ---------------- state, hash and controls
  function writeHash(t) {
    const p = new URLSearchParams({ target: t.id, step: String(S.step) });
    optionsOf(t).forEach((o) => p.set(o.key, S.opts[o.key]));
    try { history.replaceState(null, '', '#' + p.toString()); } catch (e) { /* file:// may refuse */ }
  }
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const i = D.targets.findIndex((t) => t.id === p.get('target'));
    if (i >= 0) S.t = i;
    const n = Number.parseInt(p.get('step'), 10);
    if (Number.isFinite(n)) S.step = Math.max(0, n);
    for (const [k, v] of p.entries()) if (k !== 'target' && k !== 'step') S.opts[k] = v;
  }
  function setStep(n) { S.step = Math.max(0, Math.min(last(), n)); update(); }
  function stop() {
    if (S.timer) clearInterval(S.timer);
    S.timer = null;
    $('bPlay').textContent = '再生';
    $('bPlay').setAttribute('aria-pressed', 'false');
  }
  function play() {
    if (S.step >= last()) setStep(0);
    S.timer = setInterval(() => { if (S.step >= last()) stop(); else setStep(S.step + 1); }, PLAY_MS);
    $('bPlay').textContent = '停止';
    $('bPlay').setAttribute('aria-pressed', 'true');
  }

  $('bFirst').addEventListener('click', () => { stop(); setStep(0); });
  $('bPrev').addEventListener('click', () => { stop(); setStep(S.step - 1); });
  $('bNext').addEventListener('click', () => { stop(); setStep(S.step + 1); });
  $('bLast').addEventListener('click', () => { stop(); setStep(last()); });
  $('bPlay').addEventListener('click', () => { if (S.timer) stop(); else play(); });
  $('slider').addEventListener('input', (e) => { stop(); setStep(Number(e.target.value)); });
  document.querySelectorAll('input[name="tmAxis"]').forEach((r) => r.addEventListener('change', () => {
    S.tmMode = r.value;
    buildTiming(tgt());
    update();
  }));
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { buildTiming(tgt()); update(); }, 200);
  });
  document.addEventListener('keydown', (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;   // the slider and the radios keep their own keys
    if (e.key === 'ArrowRight') { stop(); setStep(S.step + 1); }
    else if (e.key === 'ArrowLeft') { stop(); setStep(S.step - 1); }
    else if (e.key === 'Home') { stop(); setStep(0); }
    else if (e.key === 'End') { stop(); setStep(last()); }
    else if (e.key === ' ' && tag !== 'BUTTON' && tag !== 'LI') { if (S.timer) stop(); else play(); }
    else return;
    e.preventDefault();
  });

  // read-only view of the state, for checks run from a browser driver
  PV.state = () => {
    const t = tgt();
    const st = t.steps[S.step];
    return { target: t.id, step: S.step, last: last(), exec: st.exec, next: st.next, kind: st.kind,
      opts: { ...S.opts }, playing: Boolean(S.timer), probe: S.probe, key: keyAt(t, S.step),
      timing: S.tm ? { mode: S.tmMode, rows: S.tm.rows.map((r) => r.name), cursor: Number(S.tm.cursor.getAttribute('x1')),
        changed: S.tm.rows.filter((_, r) => S.tm.labels[r].classList.contains('chg')).map((row) => row.name) } : null };
  };

  readHash();
  document.title = D.title;
  $('title').textContent = D.title;
  buildTargets();
  buildTarget();
})();
