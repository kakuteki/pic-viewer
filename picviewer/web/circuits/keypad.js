/* keypad: a matrix of keys scanned by the program. The rows are outputs, driven to 0 one at a time;
   the columns are inputs with pull-ups, so a column reads 0 only while the held key joins it to the row
   that is at 0. Which key is held comes from the trace ("press" and "release"). */
(() => {
  'use strict';

  const PIC_X = 20;
  const PIN_X = 130;      // right edge of the PIC box, where the wires leave
  const COL_X0 = 220;
  const COL_DX = 80;
  const COL_Y0 = 50;
  const COL_DY = 22;
  const ROW_Y0 = 170;
  const ROW_DY = 60;

  function settings(cfg) {
    return {
      rows: cfg.rows.map((p) => p.toUpperCase()),
      cols: cfg.cols.map((p) => p.toUpperCase()),
      keys: cfg.keys,
    };
  }

  const colX = (c) => COL_X0 + c * COL_DX;
  const colPinY = (c) => COL_Y0 + c * COL_DY;
  const rowY = (r) => ROW_Y0 + r * ROW_DY;

  function find(c, label) {
    for (let r = 0; r < c.rows.length; r++) {
      const col = c.keys[r].indexOf(label);
      if (col >= 0) return { r, col };
    }
    return null;
  }

  window.PicViewer.circuits.keypad = {
    title: 'キー',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const c = settings(cfg);
      const bottom = rowY(c.rows.length - 1) + 40;
      const width = colX(c.cols.length - 1) + 70;
      const svg = U.svgEl('svg', { id: 'cir', class: 'keypad', viewBox: `0 0 ${Math.max(width, 480)} ${bottom + 104}`, role: 'img',
        'aria-label': `${c.rows.length} 行 ${c.cols.length} 列のキー` });
      const add = (tag, attrs, text, parent = svg) => { const e = U.svgEl(tag, attrs); if (text !== undefined) e.textContent = text; parent.append(e); return e; };
      const defs = U.svgEl('defs');
      defs.append(U.svgEl('path', { id: 'ah', d: 'M-6 -6.5 L7 0 L-6 6.5 Z' }));
      svg.append(defs);
      const wires = add('g', { class: 'wire' });
      add('g', { id: 'gCur' });
      add('rect', { class: 'body', x: PIC_X, y: 20, width: PIN_X - PIC_X, height: bottom - 10, rx: 6 });
      add('text', { class: 'small', x: PIC_X + 8, y: 40, 'font-weight': '700' }, t.device);
      const pinText = (pin, y) => {
        add('text', { class: 'small', x: PIN_X - 6, y: y + 5, 'text-anchor': 'end' }, pin);
        add('text', { class: 'dim small', x: PIN_X + 6, y: y - 5 }, `${U.pinsWith(t, pin).join('、')} 番`);
      };
      c.cols.forEach((pin, k) => {
        const x = colX(k);
        pinText(pin, colPinY(k));
        wires.append(U.svgEl('path', { 'data-col': String(k), d: `M${PIN_X} ${colPinY(k)} H${x} V${bottom + 6}` }));
        add('rect', { class: 'comp', x: x - 7, y: bottom + 6, width: 14, height: 36, rx: 2 });
        add('text', { class: 'dim small', x, y: bottom + 62, 'text-anchor': 'middle' }, `+${t.vdd || 5}V`);
      });
      c.rows.forEach((pin, r) => {
        pinText(pin, rowY(r));
        wires.append(U.svgEl('path', { 'data-row': String(r), d: `M${PIN_X} ${rowY(r)} H${colX(c.cols.length - 1) + 34}` }));
        c.cols.forEach((_, k) => {
          const g = add('g', { class: 'key', 'data-key': c.keys[r][k] });
          add('rect', { x: colX(k) - 24, y: rowY(r) - 20, width: 48, height: 40, rx: 8 }, undefined, g);
          add('text', { x: colX(k), y: rowY(r) + 6, 'text-anchor': 'middle' }, c.keys[r][k], g);
        });
      });
      add('text', { class: 'dim small', x: colX(0) - 26, y: bottom + 92 }, '列のプルアップ抵抗（押していない列は 1）');
      box.append(svg);
      const marks = [...c.rows, ...c.cols].flatMap((pin) => {
        const pb = U.portBit(pin);
        return pb ? ['PORT', 'LAT', 'TRIS'].map((p) => ({ reg: p + pb.port, bit: pb.bit })) : [];
      });
      return {
        sub: `${c.rows.length} 行 ${c.cols.length} 列のキー（行 ${c.rows.join('、')}、列 ${c.cols.join('、')}）`,
        assume: '行は出力で、調べる行だけを 0 にする。列は入力で、プルアップにより押していない時は 1。押したキーは、その行が 0 の間だけ列を 0 にする。',
        regHint: '太い枠のビットがキーの行と列のピン。',
        marks,
        foot: ['押したキーは、シミュレータに読み込ませた刺激（SCL）で、そのキーの列のピンを行のピンに合わせて動かしたもの。シミュレータはプルアップを持たないので、列はほかの時 1 にしてある。'],
      };
    },
    update(ctx) {
      const { cfg, box, R, U, key } = ctx;
      const c = settings(cfg);
      const svg = box.querySelector('#cir');
      if (!svg) return {};
      const bit = (pin, reg) => {
        const pb = U.portBit(pin);
        const v = reg === 'TRIS' ? R['TRIS' + pb.port] : reg === 'LATCH' ? U.latchOf(R, pb.port) : R['PORT' + pb.port];
        return v === undefined ? null : (v >> pb.bit) & 1;
      };
      // a row is being scanned when it is an output at 0
      const low = c.rows.map((pin) => bit(pin, 'TRIS') === 0 && bit(pin, 'LATCH') === 0);
      const reads = c.cols.map((pin) => bit(pin, 'PORT'));
      svg.querySelectorAll('[data-row]').forEach((w) => w.classList.toggle('hot', low[Number(w.dataset.row)]));
      svg.querySelectorAll('[data-col]').forEach((w) => w.classList.toggle('hot', reads[Number(w.dataset.col)] === 0));
      svg.querySelectorAll('.key').forEach((g) => g.classList.toggle('held', g.dataset.key === key));
      const g = svg.querySelector('#gCur');
      g.replaceChildren();
      const at = key ? find(c, key) : null;
      const flowing = at && low[at.r];
      if (flowing) {
        // from the pull-up up the column to the key, then along the row into the pin that sinks it
        const x = colX(at.col);
        const bottom = rowY(c.rows.length - 1) + 40;
        U.drawPath(g, [[x, bottom + 6], [x, rowY(at.r)], [PIN_X, rowY(at.r)]], '', {});
      }
      const scanned = low.map((v, r) => (v ? c.rows[r] : null)).filter(Boolean);
      const zeros = c.cols.filter((_, k) => reads[k] === 0);
      const colText = c.cols.map((pin, k) => `${pin}=${reads[k] === null ? '?' : reads[k]}`).join(' ');
      let text = key ? `キー ${key} を押している。` : 'どのキーも押していない。';
      if (scanned.length) text += `今 0 にしている行は ${scanned.join('、')}。`;
      if (flowing) text += `押したキーがその行と列 ${c.cols[at.col]} をつなぐので、列 ${c.cols[at.col]} が 0 と読める。`;
      else if (zeros.length) text += `0 と読める列: ${zeros.join('、')}。`;
      else if (key && scanned.length) text += '押したキーの行ではないので、列はどれも 1 のまま。';
      return {
        status: [
          { label: '押しているキー', value: key || 'なし', tone: key ? 'on' : '' },
          { label: '0 にしている行', value: scanned.join('、') || 'なし' },
          { label: '列の読み', value: colText, tone: zeros.length ? 'on' : '' },
        ],
        text,
        probe: { key, row: low.indexOf(true), cols: reads.map((v) => (v === null ? '?' : String(v))).join('') },
      };
    },
  };
})();
