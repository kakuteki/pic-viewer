/* seg7mux: several 7-segment digits that share the segment lines and are lit one at a time (dynamic drive).
   The values read at a stop hold until the next stop wherever the recording followed every write (history in
   core.js), so the time each segment of each digit was lit can be added up over the recorded stretch before this
   step. What a person sees is that time averaged over a window: 20 ms by default, or one full round of the digits
   when the program takes longer to go round (then the display flickers). Every write to the segment port and to
   the digit pins must be a stop: {"until_write": ["PORTC", "PORTA"], ...}. */
(() => {
  'use strict';

  const DW = 64;        // digit width
  const DH = 116;       // digit height
  const T = 12;         // segment thickness
  const GAP = 44;       // between digits
  const X0 = 34;
  const Y0 = 34;
  const LOOK_BACK = 0.2;     // seconds of recording searched for one round of the digits
  const FLICKER_S = 0.025;   // a round slower than this (under 40 Hz) is seen to flicker
  const FAINT = 0.1;         // a digit less bright than this share of the brightest is a ghost, not a reading

  function settings(cfg) {
    const S = window.PicViewer.util.seg7;
    const port = String(cfg.port || 'C').toUpperCase();
    const digits = (Array.isArray(cfg.digits) ? cfg.digits : [])
      .filter((d) => d && typeof d === 'object' && /^R[A-Z][0-7]$/i.test(String(d.pin)))
      .map((d) => ({ pin: String(d.pin).toUpperCase(), low: d.active === 'low' }));
    return {
      port,
      digits,
      // a digit switched by a pin of the segment port: that bit is no segment
      digitBits: new Set(digits.filter((d) => d.pin[1] === port).map((d) => Number(d.pin[2]))),
      order: Array.isArray(cfg.segments) && cfg.segments.length === 8 ? cfg.segments.map(String) : S.NAMES,
      anode: cfg.common === 'anode',
      windowS: (Number(cfg.window_ms) > 0 ? Number(cfg.window_ms) : 20) / 1000,
      minShare: Number.isFinite(Number(cfg.min_share)) ? Number(cfg.min_share) : 0.01,
    };
  }

  // the segments driven and the digits switched on, from one set of register values
  function stateOf(c, R, U) {
    const latch = U.latchOf(R, c.port);
    const tris = R['TRIS' + c.port];
    const segs = {};
    for (let b = 0; b < 8; b++) {
      if (c.digitBits.has(b)) continue;
      const out = tris === undefined || ((tris >> b) & 1) === 0;
      segs[c.order[b]] = latch !== undefined && out && ((latch >> b) & 1) === (c.anode ? 0 : 1);
    }
    const on = c.digits.map((d) => {
      const pb = U.portBit(d.pin);
      const v = U.latchOf(R, pb.port);
      if (v === undefined) return false;
      const tr = R['TRIS' + pb.port];
      const out = tr === undefined || ((tr >> pb.bit) & 1) === 0;
      return out && ((v >> pb.bit) & 1) === (d.low ? 0 : 1);
    });
    // which digit pins are outputs at all: TRIS making a pin an output is no switch-on
    const outs = c.digits.map((d) => {
      const pb = U.portBit(d.pin);
      const tr = R['TRIS' + pb.port];
      return tr === undefined || ((tr >> pb.bit) & 1) === 0;
    });
    return { segs, on, outs };
  }

  // what the display shows at one set of register values, for telling stops that change nothing on it
  const look = (st) => `${st.on.join()}|${st.on.some(Boolean) ? Object.keys(st.segs).filter((n) => st.segs[n]).join() : ''}`;

  // the time each segment of each digit was lit in the window up to where this stop's picture changes (its values
  // hold over the stops after it that leave the display as it is), the window itself (one round of the digits when
  // that is longer than window_ms), and how much of it the recording covers. The average starts at the first write
  // stop (the setup before it lasts microseconds, with every pin in its reset state); an input change does not cut
  // it, as the eye keeps what it saw. A recording cut by run_to that holds one round or more is averaged over its
  // whole rounds: the display repeats itself, so that is what the eye sees
  function average(c, ctx, U) {
    const { t, k } = ctx;
    const now = look(stateOf(c, ctx.R, U));
    const h = ctx.history(LOOK_BACK, (i) => look(stateOf(c, ctx.regsAt(t.steps[i]), U)) === now);
    const firstLoop = t.steps.findIndex((st) => st.kind === 'write' || st.kind === 'timeout');
    const since = Math.max(0, firstLoop);
    const trimmed = h.spans.length > 0 && h.spans[h.spans.length - 1].i < since;
    const states = h.spans.filter((sp) => sp.i >= since).map((sp) => ({ ...sp, st: stateOf(c, sp.R, U) }));
    // one round: the time between the last two switch-ons of the digit switched on most recently, with another
    // digit switched on in between, or that digit blinking alone
    const onAt = c.digits.map(() => []);
    const atTop = stateOf(c, h.top === k ? ctx.R : ctx.regsAt(t.steps[h.top]), U);
    states.forEach((sp, j) => {
      const newer = j === 0 ? atTop : states[j - 1].st;
      sp.st.on.forEach((on, d) => { if (!on && sp.st.outs[d] && newer.on[d]) onAt[d].push(sp.to); });
    });
    let round = null;
    const order = onAt.map((ts, d) => ({ ts, d })).filter((x) => x.ts.length >= 2).sort((a, b) => b.ts[0] - a.ts[0]);
    if (order.length) {
      const [t0, t1] = order[0].ts;
      const between = onAt.some((ts, d) => d !== order[0].d && ts.some((x) => x > t1 && x < t0));
      const alone = onAt.every((ts, d) => d === order[0].d || ts.length === 0);
      if (between || alone || c.digits.length === 1) round = t0 - t1;
    }
    let win = Math.min(LOOK_BACK, Math.max(c.windowS, round || 0));
    let rounds = 0;
    const recorded = states.length ? h.end - states[states.length - 1].from : 0;
    if (h.cut && !trimmed && recorded < win * 0.999 && round && recorded >= round) {
      rounds = Math.floor(recorded / round + 1e-9);
      win = rounds * round;
    }
    const from = h.end - win;
    const sums = c.digits.map(() => ({}));
    const lit = new Set();
    let covered = 0;
    let switched = false;
    states.forEach((sp) => {
      const a = Math.max(sp.from, from);
      if (sp.to <= a) return;
      const dt = sp.to - a;
      covered += dt;
      sp.st.on.forEach((on, d) => {
        if (!on) return;
        Object.entries(sp.st.segs).forEach(([n, l]) => { if (l) { sums[d][n] = (sums[d][n] || 0) + dt; lit.add(d); } });
      });
    });
    onAt.forEach((ts) => { if (ts.some((x) => x > from)) switched = true; });
    // cut short only by a stretch the recording does not know; trimmed at the start of the loop, what is left is
    // all recorded and is averaged as it is
    return { sums, covered, win, round, rounds, lit, switched, cut: h.cut && !trimmed && covered < win * 0.999 };
  }

  const digitX = (d) => X0 + d * (DW + GAP);

  window.PicViewer.circuits.seg7mux = {
    title: '7 セグメント LED（ダイナミック点灯）',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const c = settings(cfg);
      if (!c.digits.length) {
        box.append(U.el('p', 'flowtext', 'circuit の digits が無いか、形が違う（[{"pin": "RA0", "active": "low"}, ...] の形で書く）。'));
        return {};
      }
      const width = Math.max(digitX(c.digits.length) + 10, 360);
      const svg = U.svgEl('svg', { id: 'cir', class: 'seg7mux', viewBox: `0 0 ${width} ${Y0 + DH + 96}`, role: 'img',
        'aria-label': `${c.digits.length} 桁の 7 セグメント LED` });
      const add = (tag, attrs, text) => { const e = U.svgEl(tag, attrs); if (text !== undefined) e.textContent = text; svg.append(e); return e; };
      add('rect', { class: 'body', x: 10, y: 10, width: digitX(c.digits.length) - GAP + 20, height: DH + 48, rx: 12 });
      c.digits.forEach((d, i) => {
        const paths = U.seg7.paths(digitX(i), Y0, DW, DH, T);
        Object.entries(paths).forEach(([name, p]) => {
          add('path', { class: 'seg', d: p });
          add('path', { class: 'seglit', 'data-digit': String(i), 'data-seg': name, d: p, 'fill-opacity': '0' });
        });
        add('circle', { class: 'seg', cx: digitX(i) + DW + 14, cy: Y0 + DH + 4, r: 6 });
        add('circle', { class: 'seglit', 'data-digit': String(i), 'data-seg': 'dp', cx: digitX(i) + DW + 14, cy: Y0 + DH + 4, r: 6, 'fill-opacity': '0' });
        add('circle', { class: 'nowdot', 'data-now': String(i), cx: digitX(i) + DW / 2, cy: Y0 + DH + 34, r: 5 });
        add('text', { class: 'small', 'data-pin': String(i), x: digitX(i) + DW / 2, y: Y0 + DH + 62, 'text-anchor': 'middle' }, d.pin);
        add('text', { class: 'dim small', x: digitX(i) + DW / 2, y: Y0 + DH + 80, 'text-anchor': 'middle' },
          `${d.low ? '0' : '1'} で点灯`);
      });
      box.append(svg);
      const marks = [];
      for (let b = 0; b < 8; b++) if (!c.digitBits.has(b)) ['PORT', 'LAT'].forEach((p) => marks.push({ reg: p + c.port, bit: b }));
      c.digits.forEach((d) => { const pb = U.portBit(d.pin); ['PORT', 'LAT'].forEach((p) => marks.push({ reg: p + pb.port, bit: pb.bit })); });
      const share = c.minShare * 100;
      return {
        sub: `区画は PORT${c.port}（ビット 0 から ${c.order.join('、')}、${c.anode ? '0' : '1'} で点灯）、桁は ${c.digits.map((d) => d.pin).join('、')}（左から）`,
        assume: `桁の左右の並び、区画とビットの対応、点灯の向きは仮定。明るさは、次に止める所までの ${c.windowS * 1000} ms（桁が 1 巡するのにもっとかかる時はその 1 巡。run_to の後で記録が足りない時は、記録のある何巡か分）の間に点いていた時間の割合（${share < 1 ? share.toFixed(1) : Math.round(share)} % 未満は消す）。桁を切り替えていない時は、今の表示をそのまま描く。`,
        regHint: '太い枠のビットが区画のポートと桁のピン。',
        marks,
        foot: ['7 セグの明るさは、止めた時のレジスタの値と時刻から、書き込みと書き込みの間の点灯時間を足して描いたもの。run_to で走らせた間のように書き込みを止めていない区間は分からないので、平均に入れない（区画のポートと桁のポートの両方を until_write で止める）。'],
      };
    },
    update(ctx) {
      const { t, cfg, box, U, R } = ctx;
      const c = settings(cfg);
      const svg = box.querySelector('#cir');
      if (!svg) return {};
      const missing = [c.port, ...c.digits.map((d) => d.pin[1])].filter((p, i, a) => a.indexOf(p) === i)
        .filter((p) => U.latchOf(R, p) === undefined);
      if (missing.length) {
        return { text: `${missing.map((p) => `PORT${p}（または LAT${p}）`).join('と')}を registers に入れると 7 セグが描ける。` };
      }
      const noTris = [c.port, ...c.digits.map((d) => d.pin[1])].filter((p, i, a) => a.indexOf(p) === i && R['TRIS' + p] === undefined);
      const here = stateOf(c, R, U);
      const avg = ctx.instrHz ? average(c, ctx, U) : { covered: 0, lit: new Set(), round: null, rounds: 0, cut: false, switched: false };
      // nothing (or not enough) recorded before this stop, or no clock to average over: this moment as it is. No
      // digit switched on in a recording shorter than the window: the display stands still, as it is now
      const still = avg.covered > 0 && !avg.cut && !avg.switched && avg.covered < avg.win * 0.999;
      const averaged = avg.covered > 0 && !avg.cut && !still;
      const share = averaged
        ? avg.sums.map((s) => Object.fromEntries(Object.entries(s).map(([n, v]) => [n, v / avg.covered])))
        : here.on.map((on) => (on ? Object.fromEntries(Object.entries(here.segs).filter(([, l]) => l).map(([n]) => [n, 1])) : {}));
      const peak = Math.max(0, ...share.flatMap((s) => Object.values(s)));
      // brightness against a full share for one digit in turn: an evenly dim display stays dim
      const full = Math.max(peak, 1 / c.digits.length);
      svg.querySelectorAll('.seglit').forEach((p) => {
        const v = share[Number(p.dataset.digit)][p.dataset.seg] || 0;
        p.setAttribute('fill-opacity', v >= c.minShare ? Math.min(1, v / full).toFixed(3) : '0');
      });
      here.on.forEach((on, d) => {
        svg.querySelector(`[data-now="${d}"]`).classList.toggle('on', on);
        svg.querySelector(`[data-pin="${d}"]`).classList.toggle('em', on);
      });
      // what can be read: each digit against its own brightest segment, leaving out faint ghosts
      const odd = [];
      const shown = share.map((s, d) => {
        const top = Math.max(0, ...Object.values(s));
        if (top < c.minShare || top < FAINT * peak) return ' ';
        const lit = Object.entries(s).filter(([, v]) => v >= top / 2).map(([n]) => n);
        let bits = 0;
        lit.forEach((n) => { const b = U.seg7.NAMES.indexOf(n); if (b >= 0 && b < 7) bits |= 1 << b; });
        const ch = bits === 0 ? ' ' : (U.seg7.SHAPES[bits] || '?');
        if (ch === '?') odd.push(`${d + 1} 桁目は ${lit.filter((n) => n !== 'dp').join('、')}`);
        return (ch.length > 1 ? ' ' : ch) + (lit.includes('dp') ? '.' : '');
      }).join('');
      // the setup lasts microseconds: what is on the pins then is no picture anyone sees
      const firstLoop = t.steps.findIndex((st) => st.kind === 'write' || st.kind === 'timeout');
      const setupNow = firstLoop > 0 && ctx.k < firstLoop;
      const nowPins = c.digits.filter((_, d) => here.on[d]).map((d) => d.pin);
      const ms = (s) => `${U.num(s * 1000, s < 0.01 ? 1 : 0)} ms`;
      const byRound = (avg.round && avg.win > c.windowS) || avg.rounds > 0;
      const roundsText = avg.rounds > 1 ? `${avg.rounds} 巡` : '1 巡';
      const winText = byRound ? `${roundsText}の ${ms(avg.win)}` : ms(avg.win || c.windowS);
      const texts = [];
      if (!ctx.instrHz) texts.push('fosc_hz が無いので時間で平均できず、この瞬間だけを描いている。');
      else if (avg.covered === 0) texts.push('この場面の前は書き込みを止めずに走らせた（run_to）ので、今の瞬間だけを描いている。');
      else if (avg.cut) {
        texts.push(`直前 ${winText} のうち記録があるのは ${ms(avg.covered)} だけ（その前は書き込みを止めずに走らせた）なので、`
          + '平均できず、今の瞬間だけを描いている。');
      }
      if (setupNow) texts.push('初期設定の途中。この出力は数 µs しか続かないので、目には見えない。');
      texts.push(nowPins.length ? `今は ${nowPins.join('、')} の桁を点けている。` : '今はどの桁も消えている。');
      if (still && nowPins.length) texts.push(`桁を切り替えていないので、今の表示がそのまま見える: ${shown.trim() || '（何も）'}。`);
      if (averaged) {
        texts.push(avg.lit.size >= 2 && avg.switched
          ? `桁を順に速く切り替えているので、目には直前${byRound ? `の ${roundsText}（${ms(avg.win)}）を平均したもの` : ` ${winText} の平均`}が見える: ${shown.trim() || '（何も）'}。`
          : avg.lit.size >= 2 ? `桁を切り替えず、${avg.lit.size} 桁を点けたままにしている: ${shown.trim() || '（何も）'}。`
            : `直前 ${winText} の間に点いていた桁は${avg.lit.size ? ' 1 つだけ' : '無い'}。`);
        if (avg.round && avg.round > FLICKER_S) texts.push(`1 巡に ${ms(avg.round)} かかる（40 Hz より遅い）ので、目にはちらついて見える。`);
        if (avg.rounds) texts.push('（その前は書き込みを止めずに走らせたので、記録のある分だけを平均した。）');
      }
      if (odd.length && shown.trim()) texts.push(`（? は字に当たらない形。${odd.join('、')} が点いている）`);
      if (noTris.length) texts.push(`TRIS${noTris.join('、TRIS')} を記録していないので、ピンは出力と仮定した。`);
      return {
        status: [
          { label: setupNow ? '今の出力（初期設定の途中）' : '目に見える表示', value: shown.trim() ? shown : '（全部消えている）',
            tone: shown.trim() && !setupNow ? 'on' : '', mono: true },
          { label: '今点いている桁', value: nowPins.join('、') || 'なし', tone: nowPins.length ? 'on' : '' },
          { label: '平均した時間', value: averaged ? `${ms(avg.covered)}（${byRound ? roundsText : `窓 ${ms(avg.win)}`}）`
            : still ? 'この場面だけ（切り替えていない）' : 'この場面だけ' },
          ...(avg.round ? [{ label: '1 巡', value: `${ms(avg.round)}（${U.num(1 / avg.round, 0)} Hz）` }] : []),
        ],
        text: texts.join(''),
        probe: { shown, now: here.on.map((on, d) => (on ? d : -1)).filter((d) => d >= 0), covered: avg.covered,
          round: avg.round, cut: avg.cut, still },
      };
    },
  };
})();
