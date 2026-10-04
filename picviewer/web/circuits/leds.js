/* leds: push switches on input pins and one LED (with its resistor) on each pin of a port.
   Each switch is either to the supply with a pull-down (active "high": pressed reads 1)
   or to GND with a pull-up (active "low": pressed reads 0). */
(() => {
  'use strict';

  const X0 = 70;         // x of the first column
  const SW_DX = 74;      // width of a switch column
  const LED_DX = 62;     // width of an LED column
  const RAIL = 300;      // y of the GND rail
  const NODE = 172;      // y where a switch, its resistor and the pin meet

  function ledColumns(t, cfg) {
    const port = String(cfg.port || 'C').toUpperCase();
    if (Array.isArray(cfg.bits)) return { port, bits: cfg.bits.map(Number) };     // [] draws the switches only
    const tris = t.regs.find((r) => r.name === 'TRIS' + port);
    const bits = [];
    for (let b = 7; b >= 0; b--) if (!tris || (tris.mask >> b) & 1) bits.push(b);
    return { port, bits };
  }

  function layout(t, cfg, U) {
    const { port, bits } = ledColumns(t, cfg);
    const sws = U.switchStates(cfg, {});
    const ledStart = X0 + sws.length * SW_DX + (sws.length ? 16 : 0);
    const right = ledStart + (bits.length - 1) * LED_DX + 40;
    return { port, bits, sws, ledStart, right };
  }

  function drawing(t, cfg, U) {
    const L = layout(t, cfg, U);
    const high = cfg.active !== 'low';
    const vdd = `+${t.vdd || 5}V`;
    const narrow = !L.bits.length;          // switches only: draw it small instead of across the panel
    const svg = U.svgEl('svg', { id: 'cir', ...(narrow ? { class: 'narrow' } : {}),
      viewBox: `0 0 ${narrow ? L.right + 20 : Math.max(L.right + 40, 560)} 340`, role: 'img',
      'aria-label': narrow ? 'スイッチの回路' : `PORT${L.port} の LED とスイッチの回路` });
    const add = (tag, attrs, text, parent = svg) => { const e = U.svgEl(tag, attrs); if (text !== undefined) e.textContent = text; parent.append(e); return e; };
    const defs = U.svgEl('defs');
    defs.append(U.svgEl('path', { id: 'ah', d: 'M-6 -6.5 L7 0 L-6 6.5 Z' }));
    svg.append(defs);
    const wires = add('g', { class: 'wire' });
    const wire = (d) => wires.append(U.svgEl('path', { d }));
    add('g', { id: 'gCur' });
    add('rect', { class: 'body', x: X0 - 45, y: 20, width: L.right - X0 + 45, height: 72, rx: 6 });
    add('text', { x: X0 - 37, y: 40, class: 'small', 'font-weight': '700' }, t.device);
    const pinLabel = (x, name) => {
      add('text', { class: 'small', x, y: 66, 'text-anchor': 'middle' }, name);
      add('text', { class: 'dim small', x, y: 84, 'text-anchor': 'middle' }, `${U.pinsWith(t, name).join('、')} 番`);
      add('circle', { class: 'dot', cx: x, cy: 92, r: 4 });
    };
    wire(`M${X0 - 30} ${RAIL} H${L.right}`);
    add('text', { class: 'dim small', x: narrow ? X0 - 30 : L.right - 120, y: RAIL + 24 }, narrow ? 'GND' : 'GND (0 V) の線');
    L.sws.forEach((s, k) => {
      const x = X0 + k * SW_DX;
      pinLabel(x, s.pin);
      add('circle', { class: 'dot', cx: x, cy: NODE, r: 4 });
      const g = add('g', { class: 'sw', 'data-pin': s.pin });
      if (s.low) {
        // pull-up to the supply on the right, switch down to GND
        wire(`M${x} 92 V${NODE} H${x + 22} V158 M${x + 22} 114 V108 M${x} ${NODE} V198 M${x} 230 V${RAIL}`);
        add('rect', { class: 'comp', x: x + 15, y: 114, width: 14, height: 44, rx: 2 });
        add('text', { class: 'dim small', x: x + 22, y: 104, 'text-anchor': 'middle' }, vdd);
        g.append(U.svgEl('line', { class: 'lever', x1: x, y1: 230, x2: x, y2: 198, style: `transform-origin:${x}px 230px` }));
        g.append(U.svgEl('circle', { class: 'dot', cx: x, cy: 198, r: 3.5 }));
        g.append(U.svgEl('circle', { class: 'dot', cx: x, cy: 230, r: 3.5 }));
        add('text', { class: 'small', x: x + 10, y: 250 }, s.label);
      } else {
        // switch up to the supply on the right, pull-down to GND
        wire(`M${x} 92 V${NODE} H${x + 22} V150 M${x + 22} 118 V108 M${x} ${NODE} V196 M${x} 240 V${RAIL}`);
        add('text', { class: 'dim small', x: x + 22, y: 104, 'text-anchor': 'middle' }, vdd);
        g.append(U.svgEl('line', { class: 'lever', x1: x + 22, y1: 150, x2: x + 22, y2: 118, style: `transform-origin:${x + 22}px 150px` }));
        g.append(U.svgEl('circle', { class: 'dot', cx: x + 22, cy: 118, r: 3.5 }));
        g.append(U.svgEl('circle', { class: 'dot', cx: x + 22, cy: 150, r: 3.5 }));
        add('rect', { class: 'comp', x: x - 7, y: 196, width: 14, height: 44, rx: 2 });
        add('text', { class: 'small', x: x + 30, y: 140 }, s.label);
      }
    });
    L.bits.forEach((b, i) => {
      const cx = L.ledStart + i * LED_DX;
      pinLabel(cx, `R${L.port}${b}`);
      wire(`M${cx} 92 V118 M${cx} 162 V188 M${cx} 216 V${RAIL}`);
      add('rect', { class: 'comp', x: cx - 8, y: 118, width: 16, height: 44, rx: 2 });
      const g = add('g', { class: 'ledg', 'data-bit': String(b) });
      add('circle', { class: 'halo', cx, cy: 202, r: 20 }, undefined, g);
      add('path', { class: 'led-body', d: high ? `M${cx - 13} 190 H${cx + 13} L${cx} 212 Z` : `M${cx - 13} 214 H${cx + 13} L${cx} 192 Z` }, undefined, g);
      add('line', { class: 'led-bar', x1: cx - 13, x2: cx + 13, y1: high ? 214 : 190, y2: high ? 214 : 190 }, undefined, g);
    });
    if (L.bits.length) add('text', { class: 'dim small', x: L.ledStart - 20, y: RAIL + 24 }, `各 LED に抵抗 ${cfg.r_ohm || 330} Ω`);
    return svg;
  }

  window.PicViewer.circuits.leds = {
    title: '回路と電流',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const L = layout(t, cfg, U);
      box.append(drawing(t, cfg, U));
      const high = cfg.active !== 'low';
      const marks = L.bits.flatMap((b) => ['TRIS', 'PORT', 'LAT'].map((p) => ({ reg: p + L.port, bit: b })));
      L.sws.forEach((s) => { const pb = U.portBit(s.pin); marks.push({ reg: 'PORT' + pb.port, bit: pb.bit }); });
      const swText = L.sws.length
        ? `スイッチ: ${L.sws.map((s) => `${s.label}（${s.pin}、押すと ${s.low ? '0' : '1'}）`).join('、')}。`
        : '';
      if (!L.bits.length) {
        return { sub: 'スイッチのつなぎ方は仮定', assume: swText, regHint: '太い枠のビットがスイッチのピン。', marks,
          foot: ['スイッチのつなぎ方は仮定。シミュレータはプルアップを持たないので、離したスイッチの値も trace の set で入れる。'] };
      }
      return {
        sub: 'LED と抵抗とスイッチのつなぎ方は仮定',
        assume: `LED は ${high ? 'ピンが 1 のとき（ピンから抵抗、LED を通って GND へ）' : 'ピンが 0 のとき（電源から LED、抵抗を通ってピンへ）'}点灯する。${swText}`,
        regHint: `太い枠のビットが LED${L.sws.length ? 'とスイッチ' : ''}のピン。`,
        marks,
        foot: ['LED、抵抗、スイッチのつなぎ方は仮定。実際の基板に合わせて circuit の値を直す。シミュレータはプルアップを持たないので、離したスイッチの値も trace の set で入れる。'],
      };
    },
    update(ctx) {
      const { t, cfg, box, R, U, inputs } = ctx;
      const svg = box.querySelector('#cir');
      if (!svg) return {};
      const L = layout(t, cfg, U);
      const high = cfg.active !== 'low';
      const latch = U.latchOf(R, L.port);
      const tris = R['TRIS' + L.port];
      if (L.bits.length && (latch === undefined || tris === undefined)) {
        return { text: `TRIS${L.port} と PORT${L.port}（または LAT${L.port}）を registers に入れると LED が描ける。` };
      }
      const g = svg.querySelector('#gCur');
      g.replaceChildren();
      const lit = [];
      L.bits.forEach((b, i) => {
        const on = ((tris >> b) & 1) === 0 && ((latch >> b) & 1) === (high ? 1 : 0);
        svg.querySelector(`.ledg[data-bit="${b}"]`).classList.toggle('lit', on);
        if (on) {
          lit.push(`R${L.port}${b}`);
          const cx = L.ledStart + i * LED_DX;
          U.drawPath(g, [[cx, 92], [cx, RAIL]], '', { arrows: high ? [[cx, 174, 90], [cx, 258, 90]] : [[cx, 174, -90], [cx, 258, -90]] });
        }
      });
      const sw = U.switchStates(cfg, R, inputs).filter((s) => s.down !== null);
      sw.forEach((s) => svg.querySelector(`.sw[data-pin="${s.pin}"]`).classList.toggle('on', s.down));
      const pressedNames = sw.filter((s) => s.down).map((s) => s.label);
      const known = sw.length;
      const pattern = L.bits.reduce((acc, b) => acc + (((latch >> b) & 1) ? '1' : '0'), '');
      const status = L.bits.length ? [
        { label: `PORT${L.port} の出力`, value: `${U.hex(latch)}（${pattern}）`, tone: lit.length ? 'on' : '' },
        { label: '点灯', value: `${lit.length} 個`, tone: lit.length ? 'on' : '' },
      ] : [];
      if (known) status.push({ label: '押しているスイッチ', value: pressedNames.join('、') || 'なし', tone: pressedNames.length ? 'on' : '' });
      const swText = !known ? '' : pressedNames.length ? `押しているスイッチ: ${pressedNames.join('、')}。` : 'スイッチはどれも離してある。';
      const ledText = !L.bits.length ? '' : lit.length ? `点灯している LED: ${lit.join('、')}。` : 'LED は全部消えている。';
      return {
        status,
        text: ledText + swText + U.mismatchText(sw),
        probe: { pattern, lit: lit.length, pressed: known ? pressedNames.length > 0 : null, pressedNames },
      };
    },
  };
})();
