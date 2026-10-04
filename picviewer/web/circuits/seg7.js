/* seg7: a one-digit 7-segment LED on a port. By default bit 0 is segment a ... bit 6 is g, bit 7 is dp,
   common cathode (a 1 lights the segment). */
(() => {
  'use strict';

  const NAMES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'dp'];
  // the usual a..g shapes of hexadecimal digits (bit 0 = a)
  const DIGITS = { 0x3f: '0', 0x06: '1', 0x5b: '2', 0x4f: '3', 0x66: '4', 0x6d: '5', 0x7d: '6', 0x07: '7', 0x27: '7',
    0x7f: '8', 0x6f: '9', 0x67: '9', 0x77: 'A', 0x7c: 'b', 0x39: 'C', 0x58: 'c', 0x5e: 'd', 0x79: 'E', 0x71: 'F', 0x00: '（消灯）' };

  // segment outlines in a 120 x 220 box at (70, 40)
  const X = 70, Y = 40, W = 120, H = 220, T = 18;
  const hseg = (y) => `M${X + 12} ${y} L${X + 22} ${y - T / 2} H${X + W - 22} L${X + W - 12} ${y} L${X + W - 22} ${y + T / 2} H${X + 22} Z`;
  const vseg = (x, y0, y1) => `M${x} ${y0 + 12} L${x + T / 2} ${y0 + 22} V${y1 - 22} L${x} ${y1 - 12} L${x - T / 2} ${y1 - 22} V${y0 + 22} Z`;
  const SHAPES = {
    a: hseg(Y), g: hseg(Y + H / 2), d: hseg(Y + H),
    f: vseg(X, Y, Y + H / 2), b: vseg(X + W, Y, Y + H / 2), e: vseg(X, Y + H / 2, Y + H), c: vseg(X + W, Y + H / 2, Y + H),
  };

  function settings(cfg) {
    const order = Array.isArray(cfg.segments) && cfg.segments.length === 8 ? cfg.segments.map(String) : NAMES;
    return { port: String(cfg.port || 'C').toUpperCase(), order, anode: cfg.common === 'anode' };
  }

  window.PicViewer.circuits.seg7 = {
    title: '7 セグメント LED',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const c = settings(cfg);
      const svg = U.svgEl('svg', { id: 'cir', viewBox: '0 0 560 320', role: 'img', 'aria-label': '7 セグメント LED' });
      const add = (tag, attrs, text) => { const e = U.svgEl(tag, attrs); if (text !== undefined) e.textContent = text; svg.append(e); return e; };
      add('rect', { class: 'body', x: 40, y: 14, width: 190, height: 290, rx: 14 });
      Object.entries(SHAPES).forEach(([name, d]) => add('path', { class: 'seg', 'data-seg': name, d }));
      add('circle', { class: 'seg', 'data-seg': 'dp', cx: X + W + 26, cy: Y + H + 4, r: 10 });
      Object.entries({ a: [X + W / 2, Y - 14], b: [X + W + 18, Y + 60], c: [X + W + 18, Y + 170], d: [X + W / 2, Y + H + 32],
        e: [X - 26, Y + 170], f: [X - 26, Y + 60], g: [X + W / 2, Y + H / 2 - 14] }).forEach(([n, [x, y]]) =>
        add('text', { class: 'dim small', x, y, 'text-anchor': 'middle' }, n));
      // the bit table: pin, pin number, segment, value
      add('text', { class: 'small dim', x: 270, y: 38 }, `${t.device}  PORT${c.port}`);
      for (let b = 0; b < 8; b++) {
        const y = 66 + b * 30;
        const pin = `R${c.port}${b}`;
        add('text', { class: 'small', x: 270, y }, pin);
        add('text', { class: 'small dim', x: 320, y }, `${U.pinsWith(t, pin).join('、')} 番`);
        add('text', { class: 'small', x: 390, y }, c.order[b]);
        add('text', { class: 'small', x: 440, y, 'data-bitval': String(b) }, '0');
      }
      box.append(svg);
      return {
        sub: `共通${c.anode ? 'アノード（0 で点灯）' : 'カソード（1 で点灯）'}、PORT${c.port} のビット 0 から ${c.order.join('、')}`,
        assume: '区画とビットの対応、共通端子の向きは仮定。実際の基板に合わせて circuit の値を直す。',
        regHint: `太い枠のビットが 7 セグにつないだ PORT${c.port}。`,
        marks: Array.from({ length: 8 }, (_, b) => [{ reg: 'PORT' + c.port, bit: b }, { reg: 'LAT' + c.port, bit: b }]).flat(),
        foot: ['7 セグの区画は、ポートのビットから決めて描いたもの（表示器はシミュレータに無い）。'],
      };
    },
    update(ctx) {
      const { cfg, box, R, U, inputs } = ctx;
      const c = settings(cfg);
      const svg = box.querySelector('#cir');
      const latch = U.latchOf(R, c.port);
      const tris = R['TRIS' + c.port];
      if (!svg || latch === undefined) return { text: `PORT${c.port}（または LAT${c.port}）を registers に入れると 7 セグが描ける。` };
      const lit = [];
      let shape = 0;
      for (let b = 0; b < 8; b++) {
        const level = (latch >> b) & 1;
        const out = tris === undefined || ((tris >> b) & 1) === 0;
        const on = out && level === (c.anode ? 0 : 1);
        const name = c.order[b];
        svg.querySelectorAll(`[data-seg="${name}"]`).forEach((s) => s.classList.toggle('on', on));
        const cell = svg.querySelector(`[data-bitval="${b}"]`);
        cell.textContent = String(level);
        cell.setAttribute('class', on ? 'small em' : 'small');
        if (on) {
          lit.push(name);
          const k = NAMES.indexOf(name);
          if (k >= 0 && k < 7) shape |= 1 << k;
        }
      }
      const digit = DIGITS[shape];
      const status = [
        { label: `PORT${c.port}`, value: `${U.hex(latch)}`, tone: lit.length ? 'on' : '' },
        { label: '点いている区画', value: lit.join(' ') || 'なし', tone: lit.length ? 'on' : '' },
        { label: '読める形', value: digit || '数字の形ではない' },
      ];
      // switches are not drawn here; their state is read from PORT like on the LED view
      const sw = U.switchStates(cfg, R, inputs).filter((s) => s.down !== null);
      const pressed = sw.filter((s) => s.down).map((s) => s.label);
      if (sw.length) status.push({ label: '押しているスイッチ', value: pressed.join('、') || 'なし', tone: pressed.length ? 'on' : '' });
      return {
        status,
        text: (lit.length ? `点いている区画: ${lit.join('、')}。${digit && digit !== '（消灯）' ? `数字の ${digit} の形。` : ''}` : '全部消えている。')
          + (sw.length ? (pressed.length ? `押しているスイッチ: ${pressed.join('、')}。` : 'スイッチはどれも離してある。') : '')
          + U.mismatchText(sw),
        probe: { lit: lit.join(''), digit: digit || null, pressed: sw.length ? pressed : null },
      };
    },
  };
})();
