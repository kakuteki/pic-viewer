/* buzzer: a piezo buzzer between one output pin and GND. The program switches the pin between 1 and 0; the last
   full cycle before this step (rising edge to rising edge, from the recorded stops) gives the pitch, shown with the
   nearest note of equal temperament (A4 = 440 Hz). Every write to the pin's port must be a stop (until_write). */
(() => {
  'use strict';

  const NOTES = ['ド', 'ド#', 'レ', 'レ#', 'ミ', 'ファ', 'ファ#', 'ソ', 'ソ#', 'ラ', 'ラ#', 'シ'];
  const LETTERS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  const SPAN = 0.1;          // seconds of recording searched for one full cycle
  const STARTED = 0.02;      // the pin changed this recently without a full cycle yet: it is just starting
  const FAR = 35;            // cents from the nearest note beyond which both neighbours are named

  function noteOf(hz, n = Math.round(12 * Math.log2(hz / 440)) + 57) {     // C0 = 0, A4 = 57
    const cents = Math.round(1200 * Math.log2(hz / (440 * 2 ** ((n - 57) / 12))));
    return { name: NOTES[((n % 12) + 12) % 12], letter: `${LETTERS[((n % 12) + 12) % 12]}${Math.floor(n / 12)}`, cents, n };
  }
  const signed = (c) => `${c > 0 ? '+' : c < 0 ? '−' : ''}${Math.abs(c)} セント`;

  // 1 while the pin is an output driven high
  const levelOf = (pin, U) => (R) => {
    const pb = U.portBit(pin);
    const v = U.latchOf(R, pb.port);
    const tris = R['TRIS' + pb.port];
    return v !== undefined && (tris === undefined || ((tris >> pb.bit) & 1) === 0) && ((v >> pb.bit) & 1) ? 1 : 0;
  };

  const pinOf = (cfg) => String(cfg.pin || 'RB0').toUpperCase();

  window.PicViewer.circuits.buzzer = {
    title: 'ブザー',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const pin = pinOf(cfg);
      const pb = U.portBit(pin);
      const name = cfg.label && String(cfg.label) !== pin ? String(cfg.label) : '';
      const svg = U.svgEl('svg', { id: 'cir', class: 'buzzer', viewBox: '0 0 300 320', role: 'img',
        'aria-label': `${pin} につないだブザー` });
      const add = (tag, attrs, text) => { const e = U.svgEl(tag, attrs); if (text !== undefined) e.textContent = text; svg.append(e); return e; };
      const defs = U.svgEl('defs');
      defs.append(U.svgEl('path', { id: 'ah', d: 'M-6 -6.5 L7 0 L-6 6.5 Z' }));
      svg.append(defs);
      const wires = add('g', { class: 'wire' });
      wires.append(U.svgEl('path', { d: 'M110 92 V150 M110 214 V286 M40 286 H260' }));
      add('g', { id: 'gCur' });
      add('rect', { class: 'body', x: 30, y: 20, width: 240, height: 72, rx: 6 });
      add('text', { x: 38, y: 40, class: 'small', 'font-weight': '700' }, t.device);
      add('text', { class: 'small', x: 110, y: 66, 'text-anchor': 'middle' }, pin);
      add('text', { class: 'dim small', x: 110, y: 84, 'text-anchor': 'middle' }, `${U.pinsWith(t, pin).join('、')} 番`);
      add('circle', { class: 'dot', cx: 110, cy: 92, r: 4 });
      // the buzzer: a round case with its sound opening, and the waves while it sounds
      add('circle', { class: 'comp', cx: 110, cy: 182, r: 32 });
      add('circle', { class: 'dot', cx: 110, cy: 182, r: 5 });
      add('text', { class: 'small', x: 152, y: 172 }, name || 'ブザー');
      add('text', { class: 'dim small', x: 152, y: 190 }, '圧電ブザー');
      add('path', { class: 'spin', id: 'waves', d: '' });
      add('text', { class: 'dim small', x: 200, y: 310 }, 'GND');
      box.append(svg);
      return {
        sub: `${pin}${name ? `（${name}）` : ''} から圧電ブザーを通って GND へ`,
        assume: 'ブザーを直接ピンにつないだと仮定（トランジスタで駆動する基板もある）。音の高さは、止めた所の時刻から測った直前の 1 周期（1 になってから次に 1 になるまで）。',
        regHint: '太い枠のビットがブザーのピン。',
        marks: ['TRIS', 'PORT', 'LAT'].map((p) => ({ reg: p + pb.port, bit: pb.bit })),
        foot: ['ブザーの音の高さは、ピンへの書き込みを全部止めて記録した時刻から求めたもの（until_write でそのポートへの書き込みを追う）。'],
      };
    },
    update(ctx) {
      const { cfg, box, R, U } = ctx;
      const svg = box.querySelector('#cir');
      if (!svg) return {};
      const pin = pinOf(cfg);
      const pb = U.portBit(pin);
      if (U.latchOf(R, pb.port) === undefined) return { text: `PORT${pb.port}（または LAT${pb.port}）を registers に入れるとブザーが描ける。` };
      const level = levelOf(pin, U);
      const now = level(R);
      const p = ctx.pulse(level, ctx.instrHz ? SPAN : Infinity);
      const sounding = p.period !== null && p.since !== null && p.since <= 2 * p.period;
      const g = svg.querySelector('#gCur');
      g.replaceChildren();
      if (now) U.drawPath(g, [[110, 92], [110, 150]], '', { arrows: [[110, 124, 90]] });
      if (now) U.drawPath(g, [[110, 214], [110, 286]], '', { arrows: [[110, 252, 90]] });
      svg.querySelector('#waves').setAttribute('d', sounding
        ? 'M150 214 a22 22 0 0 0 10 -16 M160 226 a36 36 0 0 0 16 -28 M170 238 a50 50 0 0 0 22 -40' : '');
      const status = [{ label: `${pin} の出力`, value: String(now), tone: now ? 'on' : '' }];
      let text;
      let probe = { level: now, hz: null, note: null };
      if (sounding && ctx.instrHz) {
        const hz = 1 / p.period;
        const n = noteOf(hz);
        // far from the nearest note: between it and the next one on that side
        const m = Math.abs(n.cents) > FAR ? noteOf(hz, n.n + (n.cents > 0 ? 1 : -1)) : null;
        const pair = m ? [n, m].sort((a, b) => a.n - b.n) : null;
        const near = pair ? `${pair[0].name}（${pair[0].letter}）と ${pair[1].name}（${pair[1].letter}）の間`
          : `${n.name}（${n.letter}）${Math.abs(n.cents) <= 15 ? '' : `から ${signed(n.cents)}`}`;
        status.push({ label: '音', value: `約 ${U.num(hz, 0)} Hz`, tone: 'on' });
        status.push({ label: '近い音', value: near });
        status.push({ label: '1 周期', value: ctx.fmtTime(p.period) });
        text = `${pin} を 1 と 0 に交互に切り替えて、ブザーを約 ${U.num(hz, 0)} Hz で振動させている（`
          + (pair ? `${near}。${n.name} から ${signed(n.cents)}、${m.name} から ${signed(m.cents)}` : `${n.name}、${n.letter} に近い${Math.abs(n.cents) <= 15 ? '' : `。${signed(n.cents)} ずれる`}`)
          + `）。1 周期 ${ctx.fmtTime(p.period)}${p.high !== null ? `、そのうち 1 の時間 ${ctx.fmtTime(p.high)}` : ''}。`;
        probe = { level: now, hz, note: n.letter };
      } else if (sounding) {
        status.push({ label: '1 周期', value: `${p.period} サイクル` });
        text = `${pin} を 1 と 0 に交互に切り替えている（1 周期 ${p.period} 命令サイクル）。fosc_hz を書くと音の高さ（Hz）が出る。`;
      } else if (p.period === null && p.edges > 0 && p.since !== null && p.since <= STARTED) {
        // switching, but not yet a full cycle since the input change or the start of the recording
        status.push({ label: '音', value: '（鳴り始め）', tone: 'on' });
        text = `${pin} を切り替え始めたところで、まだ 1 周期分の記録が無い（音の高さは次の 1 周期で分かる）。`;
      } else if (p.cut && p.period === null && p.edges === 0) {
        // the stretch before was run without stopping at the writes: the pitch is not in the recording
        status.push({ label: '音', value: '（記録が足りない）' });
        text = `${pin} は今 ${now}。この前は書き込みを止めずに走らせたので、音の高さはまだ分からない（1 周期分の記録が要る）。`;
      } else {
        status.push({ label: '音', value: '鳴っていない' });
        text = `${pin} は今 ${now} のまま。${p.input ? '入力を変えてから' : ''}切り替えていないので音は出ていない。`;
      }
      return { status, text, probe };
    },
  };
})();
