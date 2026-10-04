/* pot: a potentiometer between the supply and GND whose wiper drives an analog input, and what the
   AD converter made of it. The voltage comes from the trace ({"set": {"AN0": "1.25V"}}), the result
   from ADRESH and ADRESL as the simulator read them. */
(() => {
  'use strict';

  const TOP = 50;          // y of the supply end of the resistor track
  const BOT = 250;         // y of the GND end
  const RX = 120;          // x of the resistor

  function settings(cfg, t) {
    const pin = String(cfg.pin || 'AN0').toUpperCase();
    const names = t.pins.find((n) => n.some((x) => x.toUpperCase() === pin)) || [];
    const port = names.find((x) => /^R[A-E]\d$/i.test(x));
    return { pin, port: port ? port.toUpperCase() : null, vdd: t.vdd || 5, bits: cfg.bits || 10 };
  }

  // the 10-bit result: ADFM (ADCON1 bit 7) set means right justified, as on the PIC16F88x
  function result(R, cfg) {
    if (R.ADRESH === undefined || R.ADRESL === undefined) return null;
    const right = R.ADCON1 !== undefined ? ((R.ADCON1 >> 7) & 1) === 1 : cfg.justify !== 'left';
    return right ? ((R.ADRESH & 0x03) << 8) | R.ADRESL : (R.ADRESH << 2) | (R.ADRESL >> 6);
  }

  window.PicViewer.circuits.pot = {
    title: '可変抵抗と AD 変換',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const c = settings(cfg, t);
      const svg = U.svgEl('svg', { id: 'cir', viewBox: '0 0 560 300', role: 'img', 'aria-label': `${c.pin} につないだ可変抵抗` });
      const add = (tag, attrs, text) => { const e = U.svgEl(tag, attrs); if (text !== undefined) e.textContent = text; svg.append(e); return e; };
      const wires = add('g', { class: 'wire' });
      wires.append(U.svgEl('path', { d: `M${RX} ${TOP - 26} V${TOP} M${RX} ${BOT} V${BOT + 26} M${RX - 16} ${BOT + 26} H${RX + 16}` }));
      add('text', { class: 'dim small', x: RX, y: TOP - 32, 'text-anchor': 'middle' }, `+${c.vdd}V`);
      add('text', { class: 'dim small', x: RX + 24, y: BOT + 30 }, 'GND');
      add('rect', { class: 'comp', x: RX - 12, y: TOP, width: 24, height: BOT - TOP, rx: 2 });
      wires.append(U.svgEl('path', { 'data-wiper-wire': '1', d: '' }));
      add('path', { class: 'wiper', 'data-wiper': '1', d: '' });
      add('rect', { class: 'body', x: 330, y: 80, width: 200, height: 120, rx: 6 });
      add('text', { class: 'small', x: 342, y: 102, 'font-weight': '700' }, t.device);
      add('text', { class: 'small', x: 342, y: 146 }, `${c.pin}${c.port ? `（${c.port}）` : ''}`);
      add('text', { class: 'dim small', x: 342, y: 168 }, `${U.pinsWith(t, c.pin).join('、')} 番ピン`);
      add('circle', { class: 'dot', cx: 330, cy: 140, r: 4 });
      add('text', { class: 'em', 'data-volts': '1', x: RX + 40, y: 0 }, '');
      box.append(svg);
      const marks = ['ADRESH', 'ADRESL'].flatMap((reg) => Array.from({ length: 8 }, (_, b) => ({ reg, bit: b })));
      return {
        sub: `${c.pin} の電圧を AD 変換で読む（${c.bits} ビット、基準 0〜${c.vdd} V）`,
        assume: `可変抵抗の両端は ${c.vdd} V と GND。つまみの電圧は trace の set で入れた値。`,
        regHint: '太い枠が AD 変換の結果（ADRESH と ADRESL）。',
        marks,
        foot: [`AD 変換の結果から求めた電圧は 結果 × ${c.vdd} / ${2 ** c.bits - 1}（基準を電源とした場合）。`],
      };
    },
    update(ctx) {
      const { t, cfg, box, R, inputs } = ctx;
      const c = settings(cfg, t);
      const svg = box.querySelector('#cir');
      if (!svg) return {};
      const raw = inputs[c.pin] !== undefined ? inputs[c.pin] : c.port ? inputs[c.port] : undefined;
      const volts = raw === undefined ? null : raw === 'high' ? c.vdd : raw === 'low' ? 0 : Number.parseFloat(raw);
      const y = volts === null ? (TOP + BOT) / 2 : BOT - (Math.max(0, Math.min(c.vdd, volts)) / c.vdd) * (BOT - TOP);
      svg.querySelector('[data-wiper]').setAttribute('d', `M${RX + 14} ${y} l14 -9 v18 Z`);
      svg.querySelector('[data-wiper-wire]').setAttribute('d', `M${RX + 28} ${y} H290 V140 H330`);
      const label = svg.querySelector('[data-volts]');
      label.setAttribute('y', String(y - 10));
      label.textContent = volts === null ? '電圧は入れていない' : `${volts.toFixed(2)} V`;
      const n = result(R, cfg);
      const full = 2 ** c.bits - 1;
      const measured = n === null ? null : (n * c.vdd) / full;
      const status = [
        { label: `${c.pin} に入れた電圧`, value: volts === null ? 'なし' : `${volts.toFixed(3)} V`, tone: volts === null ? '' : 'on' },
        { label: 'AD 変換の結果', value: n === null ? 'ADRESH と ADRESL が無い' : `${n} / ${full}` },
        { label: '結果から求めた電圧', value: measured === null ? '-' : `${measured.toFixed(3)} V` },
      ];
      const text = n === null ? 'ADRESH と ADRESL を registers に入れると、AD 変換の結果が出る。'
        : `AD 変換の結果は ${n}（${full} が ${c.vdd} V）。${n} × ${c.vdd} / ${full} = ${measured.toFixed(3)} V。`;
      return { status, text, probe: { volts, adc: n } };
    },
  };
})();
