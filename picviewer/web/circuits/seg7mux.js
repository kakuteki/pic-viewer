/* seg7mux: several 7-segment digits that share the segment lines and are lit one at a time (dynamic drive).
   The register values are snapshots taken at each stop, and the outputs do not change between two stops, so the
   time each segment of each digit was on can be added up. What a person sees is that time averaged over the
   last window (20 ms by default) before this step, with the waits cut by fast_forward added back. Every write to
   the segment port and to the digit pins must be a stop: {"until_write": ["PORTC", "PORTA"], ...}. */
(() => {
  'use strict';

  const DW = 64;        // digit width
  const DH = 116;       // digit height
  const T = 12;         // segment thickness
  const GAP = 44;       // between digits
  const X0 = 34;
  const Y0 = 34;

  function settings(cfg) {
    const S = window.PicViewer.util.seg7;
    return {
      port: String(cfg.port || 'C').toUpperCase(),
      order: Array.isArray(cfg.segments) && cfg.segments.length === 8 ? cfg.segments.map(String) : S.NAMES,
      anode: cfg.common === 'anode',
      digits: (cfg.digits || []).map((d) => ({ pin: String(d.pin).toUpperCase(), low: d.active === 'low' })),
      windowS: (cfg.window_ms || 20) / 1000,
      minShare: cfg.min_share === undefined ? 0.01 : cfg.min_share,
    };
  }

  // the segments driven and the digits switched on, from one set of register values
  function stateOf(c, R, U) {
    const latch = U.latchOf(R, c.port);
    const tris = R['TRIS' + c.port];
    const segs = {};
    for (let b = 0; b < 8; b++) {
      const out = tris === undefined || ((tris >> b) & 1) === 0;
      segs[c.order[b]] = latch !== undefined && out && ((latch >> b) & 1) === (c.anode ? 0 : 1);
    }
    const on = c.digits.map((d) => {
      const pb = U.portBit(d.pin);
      const v = pb ? U.latchOf(R, pb.port) : undefined;
      if (v === undefined) return false;
      const tr = R['TRIS' + pb.port];
      const out = tr === undefined || ((tr >> pb.bit) & 1) === 0;
      return out && ((v >> pb.bit) & 1) === (d.low ? 0 : 1);
    });
    return { segs, on };
  }

  const digitX = (d) => X0 + d * (DW + GAP);

  window.PicViewer.circuits.seg7mux = {
    title: '7 セグメント LED（ダイナミック点灯）',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const c = settings(cfg);
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
      for (let b = 0; b < 8; b++) ['PORT', 'LAT'].forEach((p) => marks.push({ reg: p + c.port, bit: b }));
      c.digits.forEach((d) => { const pb = U.portBit(d.pin); if (pb) ['PORT', 'LAT'].forEach((p) => marks.push({ reg: p + pb.port, bit: pb.bit })); });
      return {
        sub: `区画は PORT${c.port}（ビット 0 から ${c.order.join('、')}、${c.anode ? '0' : '1'} で点灯）、桁は ${c.digits.map((d) => d.pin).join('、')}（左から）`,
        assume: `桁の左右の並び、区画とビットの対応、点灯の向きは仮定。明るさは、直前の ${c.windowS * 1000} ms の間に点いていた時間の割合（${Math.round(c.minShare * 100)} % 未満は消す）。`,
        regHint: `太い枠のビットが区画のポートと桁のピン。`,
        marks,
        foot: ['7 セグの明るさは、止めた時のレジスタの値と時刻から、書き込みと書き込みの間の点灯時間を足して描いたもの。止めていない間の書き込みがあると正しくない（区画のポートと桁のポートの両方を until_write で止める）。'],
      };
    },
    update(ctx) {
      const { t, cfg, box, U, k } = ctx;
      const c = settings(cfg);
      const svg = box.querySelector('#cir');
      if (!svg) return {};
      const timeOf = (i) => ctx.timeAt(t.steps[i]);
      const now = timeOf(k);
      const from = now - c.windowS;
      const sums = c.digits.map(() => ({}));
      let covered = 0;
      // without the clock (fosc_hz) the times are instruction cycles: no average over milliseconds then
      for (let i = ctx.instrHz ? k - 1 : -1; i >= 0; i--) {
        const b = timeOf(i + 1);
        if (b <= from) break;
        const dt = b - Math.max(timeOf(i), from);
        if (dt <= 0) continue;
        covered += dt;
        const st = stateOf(c, ctx.regsAt(t.steps[i]), U);
        st.on.forEach((on, d) => {
          if (on) Object.entries(st.segs).forEach(([n, lit]) => { if (lit) sums[d][n] = (sums[d][n] || 0) + dt; });
        });
      }
      const here = stateOf(c, ctx.R, U);
      // nothing recorded before this stop: show this moment as it is
      const share = covered > 0 ? sums.map((s) => Object.fromEntries(Object.entries(s).map(([n, v]) => [n, v / covered])))
        : here.on.map((on) => (on ? Object.fromEntries(Object.entries(here.segs).filter(([, l]) => l).map(([n]) => [n, 1])) : {}));
      const peak = Math.max(0, ...share.flatMap((s) => Object.values(s)));
      svg.querySelectorAll('.seglit').forEach((p) => {
        const v = share[Number(p.dataset.digit)][p.dataset.seg] || 0;
        p.setAttribute('fill-opacity', v >= c.minShare && peak > 0 ? (v / peak).toFixed(3) : '0');
      });
      here.on.forEach((on, d) => {
        svg.querySelector(`[data-now="${d}"]`).classList.toggle('on', on);
        svg.querySelector(`[data-pin="${d}"]`).classList.toggle('em', on);
      });
      // what can be read: segments at least half as bright as the brightest
      const shown = share.map((s) => {
        let bits = 0;
        const lit = Object.entries(s).filter(([, v]) => v >= c.minShare && v >= peak / 2).map(([n]) => n);
        lit.forEach((n) => { const b = U.seg7.NAMES.indexOf(n); if (b >= 0 && b < 7) bits |= 1 << b; });
        const ch = bits === 0 ? ' ' : (U.seg7.SHAPES[bits] || '?');
        return ch + (lit.includes('dp') ? '.' : '');
      }).join('');
      const nowPins = c.digits.filter((_, d) => here.on[d]).map((d) => d.pin);
      const span = covered > 0 ? ctx.fmtTime ? ctx.fmtTime(covered) : `${covered}` : '0';
      return {
        status: [
          { label: '目に見える表示', value: shown.trim() ? shown : '（全部消えている）', tone: shown.trim() ? 'on' : '' },
          { label: '今点いている桁', value: nowPins.join('、') || 'なし', tone: nowPins.length ? 'on' : '' },
          { label: '平均した時間', value: covered > 0 ? `${span}（窓 ${c.windowS * 1000} ms）` : 'この場面だけ' },
        ],
        text: (ctx.instrHz ? '' : 'fosc_hz が無いので時間で平均できず、この瞬間だけを描いている。')
          + (nowPins.length ? `今は ${nowPins.join('、')} の桁だけを点けている。` : '今はどの桁も消えている。')
          + (covered > 0 ? `桁を順に速く切り替えているので、目には直前 ${c.windowS * 1000} ms の平均が見える: ${shown.trim() || '（何も）'}。` : ''),
        probe: { shown, now: here.on.map((on, d) => (on ? d : -1)).filter((d) => d >= 0), covered },
      };
    },
  };
})();
