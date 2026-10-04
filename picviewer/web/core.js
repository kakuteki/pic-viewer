/* PIC viewer core: step controls, source, registers and explanation.
   A circuit view plugs in through PicViewer.circuits[type] = { title, options, setup(ctx), update(ctx) }. */
(() => {
  'use strict';
  const PV = window.PicViewer;
  const D = JSON.parse(document.getElementById('data').textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const PLAY_MS = 1400;
  const $ = (id) => document.getElementById(id);
  const S = { t: 0, step: 0, opts: {}, timer: null, probe: null };

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
  PV.util = { el, svgEl, num, hex, pinsWith, portBit, latchOf, drawPath };

  // ---------------- data helpers
  const tgt = () => D.targets[S.t];
  const last = () => tgt().steps.length - 1;
  const circuitOf = (t) => PV.circuits[t.circuit.type] || PV.circuits.pins;
  const isCounter = (t, name) => t.counters.indexOf(name) >= 0;
  const instrHz = (t) => (t.fosc_hz ? t.fosc_hz / 4 : null);
  const bitName = (r, b) => r.bits[b] || `${r.name}<${b}>`;
  const codeOf = (line) => { const i = line.indexOf('//'); return (i < 0 ? line : line.slice(0, i)).trim(); };
  const fmtTime = (s) => (s < 1e-3 ? `${num(s * 1e6, 1)} µs` : s < 1 ? `${num(s * 1e3, 3)} ms` : `${num(s, 3)} 秒`);

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

  function buildOptions(c) {
    document.querySelectorAll('#choices .seg.opt').forEach((n) => n.remove());
    (c.options || []).forEach((o) => {
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
    return { t, cfg: t.circuit, box: $('cirBox'), opts: S.opts, U: PV.util };
  }

  function buildTarget() {
    const t = tgt();
    const c = circuitOf(t);
    S.step = Math.min(S.step, last());
    $('slider').max = String(last());
    $('srcName').textContent = t.source_name;
    $('subtitle').textContent = t.summary
      ? `${t.device}: ${t.summary}`
      : `${t.device} の ${t.source_name} を MPLAB X のシミュレータで実行した記録。`;
    buildSource(t);
    buildOptions(c);
    $('cirTitle').textContent = c.title || '回路';
    $('cirBox').replaceChildren();
    const info = c.setup(ctxBase(t)) || {};
    buildRegs(t, info.marks || []);
    $('cirSub').textContent = info.sub || '';
    $('cirAssume').textContent = info.assume || '';
    const counterHint = t.counters.length ? `${t.counters.join('、')} は数え続けるカウンタなので、変化の印を付けない。` : '';
    $('regHint').textContent = ['オレンジの枠は直前のステップから変わったビット。- はそのマイコンに無いビット。', info.regHint || '', counterHint]
      .filter(Boolean).join(' ');
    buildFoot(t, info.foot || []);
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
    const inputs = st.inputs
      ? `入力を変えた: ${Object.entries(st.inputs).map(([p, v]) => `${p} = ${levelText(v)}`).join('、')}${st.input_note ? `（${st.input_note}）` : ''}。`
      : '';
    if (st.exec === null) {
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
      else note = t.notes[String(st.exec)] || auto;
      if (st.kind === 'write') note = `${st.watch} に書いた所で止めた。` + note;
      $('exNote').textContent = inputs + note;
    }
    $('exDelta').textContent = prev ? (regsChanged.length ? `変わったレジスタ: ${regsChanged.join('、')}` : '変わったレジスタ: なし') : '';
    $('exDelta').className = regsChanged.length ? 'delta' : '';
    const sec = secondsOf(t, st);
    const time = sec === null ? '' : t.fast_forward
      ? `（早送りで縮めた待ちを足すと ${fmtTime(sec)}）`
      : `（${fmtTime(sec)}）`;
    $('exMeta').textContent = `次に実行する行: ${st.next} 行目（アドレス ${st.addr}）　リセットからの命令サイクル数: ${st.cycles}${time}`;
  }

  function paintSource(st) {
    const code = $('code');
    code.querySelectorAll('li').forEach((li) => li.classList.toggle('cur', Number(li.dataset.line) === st.exec));
    const cur = code.querySelector('li.cur');
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
      d.append(el('dt', '', s.label), el('dd', '', s.value));
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
    const res = circuitOf(t).update({ ...ctxBase(t), st, prev, k: S.step, R: regsAt(t, st), P: prev ? regsAt(t, prev) : null,
      regsAt: (step) => regsAt(t, step), fmtTime, instrHz: instrHz(t), seconds: secondsOf(t, st) }) || {};
    paintStatus(res.status || []);
    $('cirText').textContent = res.text || '';
    S.probe = res.probe || null;
    paintWaves(t, st);
    writeHash(t);
  }

  // ---------------- state, hash and controls
  function writeHash(t) {
    const p = new URLSearchParams({ target: t.id, step: String(S.step) });
    (circuitOf(t).options || []).forEach((o) => p.set(o.key, S.opts[o.key]));
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
      opts: { ...S.opts }, playing: Boolean(S.timer), probe: S.probe };
  };

  readHash();
  document.title = D.title;
  $('title').textContent = D.title;
  buildTargets();
  buildTarget();
})();
