/* leds: one LED (with its resistor) on each pin of a port, and an optional push switch on an input pin. */
(() => {
  'use strict';

  const COL0 = 230;      // x of the first LED column
  const DX = 62;         // spacing of the columns
  const RAIL = 290;      // y of the common rail under the LEDs

  function columns(t, cfg) {
    const port = String(cfg.port || 'C').toUpperCase();
    if (Array.isArray(cfg.bits) && cfg.bits.length) return { port, bits: cfg.bits.map(Number) };
    const tris = t.regs.find((r) => r.name === 'TRIS' + port);
    const bits = [];
    for (let b = 7; b >= 0; b--) if (!tris || (tris.mask >> b) & 1) bits.push(b);
    return { port, bits };
  }

  function drawing(t, cfg, U) {
    const { port, bits } = columns(t, cfg);
    const high = cfg.active !== 'low';
    const sw = cfg.switch && U.portBit(cfg.switch.pin) ? cfg.switch : null;
    const right = COL0 + (bits.length - 1) * DX + 50;
    const svg = U.svgEl('svg', { id: 'cir', viewBox: `0 0 ${Math.max(right + 30, 560)} 330`, role: 'img', 'aria-label': `${port} の LED の回路` });
    const add = (tag, attrs, text) => { const e = U.svgEl(tag, attrs); if (text !== undefined) e.textContent = text; svg.append(e); return e; };
    const defs = U.svgEl('defs');
    defs.append(U.svgEl('path', { id: 'ah', d: 'M-6 -6.5 L7 0 L-6 6.5 Z' }));
    svg.append(defs);
    const wires = add('g', { class: 'wire' });
    const wire = (d) => wires.append(U.svgEl('path', { d }));
    add('g', { id: 'gCur' });
    add('rect', { class: 'body', x: COL0 - 40, y: 24, width: right - COL0 + 20, height: 68, rx: 6 });
    add('text', { x: COL0 - 32, y: 46, class: 'small', 'font-weight': '700' }, `${t.device}  PORT${port}`);
    wire(`M${(sw ? 30 : COL0 - 40)} ${RAIL} H${right}`);
    add('text', { class: 'dim small', x: right - 120, y: RAIL + 22 }, high ? 'GND (0 V) の線' : `+${t.vdd || 5} V の線`);
    bits.forEach((b, i) => {
      const cx = COL0 + i * DX;
      const name = `R${port}${b}`;
      add('text', { class: 'small', x: cx, y: 84, 'text-anchor': 'middle' }, name);
      add('text', { class: 'dim small', x: cx, y: 110, 'text-anchor': 'middle' }, `${U.pinsWith(t, name).join('、')} 番`);
      wire(`M${cx} 92 V118 M${cx} 162 V188 M${cx} 216 V${RAIL}`);
      add('circle', { class: 'dot', cx, cy: 92, r: 4 });
      add('rect', { class: 'comp', x: cx - 8, y: 118, width: 16, height: 44, rx: 2 });
      const g = add('g', { class: 'ledg', 'data-bit': String(b) });
      g.append(U.svgEl('circle', { class: 'halo', cx, cy: 202, r: 20 }));
      g.append(U.svgEl('path', { class: 'led-body', d: high ? `M${cx - 13} 190 H${cx + 13} L${cx} 212 Z` : `M${cx - 13} 214 H${cx + 13} L${cx} 192 Z` }));
      g.append(U.svgEl('line', { class: 'led-bar', x1: cx - 13, x2: cx + 13, y1: high ? 214 : 190, y2: high ? 214 : 190 }));
    });
    add('text', { class: 'dim small', x: COL0 - 32, y: RAIL + 22 }, `各 LED に抵抗 ${cfg.r_ohm || 330} Ω`);
    if (sw) {
      const pb = U.portBit(sw.pin);
      const up = sw.active !== 'low';      // switch to the supply with a pull-down, or to GND with a pull-up
      add('text', { class: 'small', x: 22, y: 20 }, up ? `+${t.vdd || 5} V` : 'GND');
      wire(`M60 26 V40 M60 72 V150 M60 150 H150 V60 H${COL0 - 40} M60 150 V180 M60 224 V${RAIL}`);
      add('circle', { class: 'dot', cx: COL0 - 40, cy: 60, r: 4 });
      const g = add('g', { class: 'sw', id: 'swBtn' });
      g.append(U.svgEl('line', { class: 'lever', x1: 60, y1: 72, x2: 60, y2: 40, style: 'transform-origin:60px 72px' }));
      g.append(U.svgEl('circle', { class: 'dot', cx: 60, cy: 40, r: 3.5 }));
      g.append(U.svgEl('circle', { class: 'dot', cx: 60, cy: 72, r: 3.5 }));
      add('circle', { class: 'dot', cx: 60, cy: 150, r: 4 });
      add('rect', { class: 'comp', x: 52, y: 180, width: 16, height: 44, rx: 2 });
      add('text', { class: 'small', x: 76, y: 60 }, 'スイッチ');
      add('text', { class: 'dim small', x: 76, y: 206 }, up ? 'プルダウン' : 'プルアップ');
      add('text', { class: 'small', x: 76, y: 142 }, `${sw.pin}  ${U.pinsWith(t, sw.pin).join('、')} 番`);
      svg.dataset.swPort = pb.port;
    }
    return svg;
  }

  window.PicViewer.circuits.leds = {
    title: '回路と電流',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const { port, bits } = columns(t, cfg);
      box.append(drawing(t, cfg, U));
      const high = cfg.active !== 'low';
      const marks = bits.flatMap((b) => ['TRIS', 'PORT', 'LAT'].map((p) => ({ reg: p + port, bit: b })));
      const sw = cfg.switch && U.portBit(cfg.switch.pin);
      if (sw) marks.push({ reg: 'PORT' + sw.port, bit: sw.bit });
      return {
        sub: 'LED と抵抗とスイッチのつなぎ方は仮定',
        assume: `LED は ${high ? 'ピンが 1 のとき（ピンから抵抗、LED を通って GND へ）' : 'ピンが 0 のとき（電源から LED、抵抗を通ってピンへ）'}点灯する。`
          + (sw ? `スイッチは押すと ${cfg.switch.pin} が ${cfg.switch.active === 'low' ? '0' : '1'} になる。` : ''),
        regHint: `太い枠のビットが LED${sw ? 'とスイッチ' : ''}のピン。`,
        marks,
        foot: ['LED、抵抗、スイッチのつなぎ方は仮定。実際の基板に合わせて circuit の値を直す。'],
      };
    },
    update(ctx) {
      const { t, cfg, box, R, U } = ctx;
      const svg = box.querySelector('#cir');
      if (!svg) return {};
      const { port, bits } = columns(t, cfg);
      const high = cfg.active !== 'low';
      const latch = U.latchOf(R, port);
      const tris = R['TRIS' + port];
      if (latch === undefined || tris === undefined) {
        return { text: `TRIS${port} と PORT${port}（または LAT${port}）を registers に入れると LED が描ける。` };
      }
      const g = svg.querySelector('#gCur');
      g.replaceChildren();
      const lit = [];
      bits.forEach((b, i) => {
        const out = ((tris >> b) & 1) === 0;
        const on = out && ((latch >> b) & 1) === (high ? 1 : 0);
        svg.querySelector(`.ledg[data-bit="${b}"]`).classList.toggle('lit', on);
        if (on) {
          lit.push(`R${port}${b}`);
          const cx = COL0 + i * DX;
          U.drawPath(g, [[cx, 92], [cx, RAIL]], '', { arrows: high ? [[cx, 174, 90], [cx, 252, 90]] : [[cx, 174, -90], [cx, 252, -90]] });
        }
      });
      let pressed = null;
      if (cfg.switch && svg.dataset.swPort) {
        const pb = U.portBit(cfg.switch.pin);
        const level = R['PORT' + pb.port] !== undefined ? (R['PORT' + pb.port] >> pb.bit) & 1 : null;
        if (level !== null) pressed = level === (cfg.switch.active === 'low' ? 0 : 1);
        svg.querySelector('#swBtn').classList.toggle('on', Boolean(pressed));
      }
      const pattern = bits.reduce((acc, b) => acc + (((latch >> b) & 1) ? '1' : '0'), '');
      const status = [
        { label: `PORT${port} の出力`, value: `${U.hex(latch)}（${pattern}）`, tone: lit.length ? 'on' : '' },
        { label: '点灯', value: `${lit.length} 個`, tone: lit.length ? 'on' : '' },
      ];
      if (pressed !== null) status.push({ label: 'スイッチ', value: pressed ? '押している' : '離している', tone: pressed ? 'on' : '' });
      const swText = pressed === null ? '' : pressed ? 'スイッチは押してある。' : 'スイッチは離してある。';
      return {
        status,
        text: (lit.length ? `点灯している LED: ${lit.join('、')}。` : 'LED は全部消えている。') + swText,
        probe: { pattern, lit: lit.length, pressed },
      };
    },
  };
})();
