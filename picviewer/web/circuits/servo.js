/* servo: an RC servo motor with its signal wire on one output pin. The program sends a pulse about every 20 ms;
   the width of the last full pulse before this step sets the angle. 0.5 ms to 2.5 ms for -90 to +90 degrees is
   assumed (servos differ: many take 1.0 ms to 2.0 ms). Every write to the pin's port must be a stop (until_write). */
(() => {
  'use strict';

  const SPAN = 0.1;          // seconds of recording searched for one full pulse
  const STARTED = 0.04;      // the pin changed this recently without a full cycle yet: the pulses are just starting
  const CX = 196;            // the horn's pivot
  const CY = 214;
  const HORN = 46;

  const levelOf = (pin, U) => (R) => {
    const pb = U.portBit(pin);
    const v = U.latchOf(R, pb.port);
    const tris = R['TRIS' + pb.port];
    return v !== undefined && (tris === undefined || ((tris >> pb.bit) & 1) === 0) && ((v >> pb.bit) & 1) ? 1 : 0;
  };
  const pinOf = (cfg) => String(cfg.pin || 'RC0').toUpperCase();
  // the angle a pulse width stands for, between -90 and +90 degrees
  const angleOf = (width) => Math.max(-90, Math.min(90, ((width - 1.5e-3) / 1e-3) * 90));

  window.PicViewer.circuits.servo = {
    title: 'サーボモーター',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const pin = pinOf(cfg);
      const pb = U.portBit(pin);
      const name = cfg.label && String(cfg.label) !== pin ? String(cfg.label) : '';
      const svg = U.svgEl('svg', { id: 'cir', class: 'servo', viewBox: '0 0 330 300', role: 'img',
        'aria-label': `${pin} につないだサーボモーター` });
      const add = (tag, attrs, text) => { const e = U.svgEl(tag, attrs); if (text !== undefined) e.textContent = text; svg.append(e); return e; };
      const defs = U.svgEl('defs');
      defs.append(U.svgEl('path', { id: 'ah', d: 'M-6 -6.5 L7 0 L-6 6.5 Z' }));
      svg.append(defs);
      const wires = add('g', { class: 'wire' });
      wires.append(U.svgEl('path', { d: 'M70 92 V196 H120' }));
      add('g', { id: 'gCur' });
      add('rect', { class: 'body', x: 20, y: 20, width: 290, height: 72, rx: 6 });
      add('text', { x: 28, y: 40, class: 'small', 'font-weight': '700' }, t.device);
      add('text', { class: 'small', x: 70, y: 66, 'text-anchor': 'middle' }, pin);
      add('text', { class: 'dim small', x: 70, y: 84, 'text-anchor': 'middle' }, `${U.pinsWith(t, pin).join('、')} 番`);
      add('circle', { class: 'dot', cx: 70, cy: 92, r: 4 });
      add('text', { class: 'dim small', x: 78, y: 186 }, '信号');
      // the servo seen from above: its case, the range of the horn, and the horn itself
      add('rect', { class: 'comp', x: 120, y: 130, width: 152, height: 132, rx: 8 });
      const range = add('g', { class: 'wire' });     // over the case, not under it
      range.append(U.svgEl('path', { class: 'ctl', d: `M${CX - 56} ${CY} A56 56 0 0 1 ${CX + 56} ${CY}` }));
      add('text', { class: 'dim small', x: CX - 50, y: CY + 26, 'text-anchor': 'middle' }, '-90°');
      add('text', { class: 'dim small', x: CX + 50, y: CY + 26, 'text-anchor': 'middle' }, '+90°');
      add('line', { class: 'spin', id: 'horn', x1: CX, y1: CY, x2: CX, y2: CY - HORN, style: 'stroke-width: 9' });
      add('circle', { class: 'dot', cx: CX, cy: CY, r: 6 });
      add('text', { class: 'small', x: CX, y: 284, 'text-anchor': 'middle' }, name ? `サーボ（${name}）` : 'サーボ');
      box.append(svg);
      return {
        sub: `${pin}${name ? `（${name}）` : ''} からサーボの信号線へ（電源と GND は別につなぐ）`,
        assume: 'パルスの幅 0.5〜2.5 ms を −90〜+90 度と仮定（サーボの機種で違う。1.0〜2.0 ms のものも多い）。幅は、止めた所の時刻から測った直前の 1 周期の中の 1 の時間。',
        regHint: '太い枠のビットがサーボの信号のピン。',
        marks: ['TRIS', 'PORT', 'LAT'].map((p) => ({ reg: p + pb.port, bit: pb.bit })),
        foot: ['サーボの角度は、ピンへの書き込みを全部止めて記録した時刻から求めたパルスの幅による目安（until_write でそのポートへの書き込みを追う）。'],
      };
    },
    update(ctx) {
      const { cfg, box, R, U } = ctx;
      const svg = box.querySelector('#cir');
      if (!svg) return {};
      const pin = pinOf(cfg);
      const pb = U.portBit(pin);
      if (U.latchOf(R, pb.port) === undefined) return { text: `PORT${pb.port}（または LAT${pb.port}）を registers に入れるとサーボが描ける。` };
      const level = levelOf(pin, U);
      const now = level(R);
      const g = svg.querySelector('#gCur');
      g.replaceChildren();
      if (now) U.drawPath(g, [[70, 92], [70, 196], [120, 196]], '', { arrows: [[70, 146, 90]] });
      const status = [{ label: `${pin} の出力`, value: String(now), tone: now ? 'on' : '' }];
      if (!ctx.instrHz) {
        return { status, text: 'fosc_hz が無いので、パルスの幅（時間）が分からない。', probe: { level: now, width: null, angle: null } };
      }
      const p = ctx.pulse(level, SPAN);
      const fresh = p.period !== null && p.high !== null && p.since !== null && p.since <= 2 * p.period;
      if (!fresh && p.period === null && p.edges > 0 && p.since !== null && p.since <= STARTED) {
        status.push({ label: 'パルス', value: '（出し始め）', tone: 'on' });
        return { status, text: `${pin} からパルスを出し始めたところで、まだ 1 周期分の記録が無い（幅は次の 1 周期で分かる）。`,
          probe: { level: now, width: null, angle: null } };
      }
      if (!fresh && p.cut && p.period === null && p.edges === 0) {
        status.push({ label: 'パルス', value: '（記録が足りない）' });
        return { status, text: `この前は書き込みを止めずに走らせたので、パルスの幅はまだ分からない（1 周期分の記録が要る）。`,
          probe: { level: now, width: null, angle: null } };
      }
      if (!fresh) {
        status.push({ label: 'パルス', value: '来ていない' });
        return { status, text: `${pin} にパルスが来ていない（サーボは最後の角度のまま止まっている）。`, probe: { level: now, width: null, angle: null } };
      }
      const angle = angleOf(p.high);
      const deg = Math.round(angle) === 0 ? '0 度' : `${angle > 0 ? '+' : ''}${Math.round(angle)} 度`;
      const rad = (angle * Math.PI) / 180;
      const horn = svg.querySelector('#horn');
      horn.setAttribute('x2', String(CX + HORN * Math.sin(rad)));
      horn.setAttribute('y2', String(CY - HORN * Math.cos(rad)));
      const usual = p.period >= 0.015 && p.period <= 0.025;
      status.push({ label: 'パルスの幅', value: ctx.fmtTime(p.high), tone: 'on' });
      status.push({ label: '周期', value: `${ctx.fmtTime(p.period)}（${U.num(1 / p.period, 0)} Hz）` });
      status.push({ label: '角度の目安', value: deg });
      return {
        status,
        text: `${pin} から ${ctx.fmtTime(p.period)} ごとに幅 ${ctx.fmtTime(p.high)} のパルスを出している。`
          + `サーボはこの幅で角度を決める（目安 ${deg}）。`
          + (usual ? '' : '周期がふつうの 20 ms から離れているので、動かないサーボもある。'),
        probe: { level: now, width: p.high, period: p.period, angle },
      };
    },
  };
})();
