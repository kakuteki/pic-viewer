/* dcmotor: a DC motor on a driver with two inputs, IN1 and IN2 (TA7291P, DRV8833 and the like). IN1 = 1 and
   IN2 = 0 turns it one way, 0 and 1 the other way, 0 and 0 lets it run down, 1 and 1 brakes: the table of the
   usual drivers, to be checked against the data sheet of the one used. An input switched fast (software PWM) sets
   the speed by the share of time it is 1. */
(() => {
  'use strict';

  const SPAN = 0.1;          // seconds of recording searched for one PWM cycle
  const PWM_MAX = 0.05;      // a cycle shorter than this is speed control, not on and off
  const MX = 300;            // the motor
  const MY = 204;

  const levelOf = (pin, U) => (R) => {
    const pb = U.portBit(pin);
    const v = U.latchOf(R, pb.port);
    const tris = R['TRIS' + pb.port];
    return v !== undefined && (tris === undefined || ((tris >> pb.bit) & 1) === 0) && ((v >> pb.bit) & 1) ? 1 : 0;
  };
  const pinsOf = (cfg) => [String(cfg.in1 || 'RC0').toUpperCase(), String(cfg.in2 || 'RC1').toUpperCase()];
  const MODES = { '10': ['正転', 1], '01': ['逆転', -1], '00': ['止まっていく（空転）', 0], '11': ['ブレーキ', 0] };

  window.PicViewer.circuits.dcmotor = {
    title: 'モータードライバと DC モーター',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const [in1, in2] = pinsOf(cfg);
      const svg = U.svgEl('svg', { id: 'cir', class: 'dcmotor', viewBox: '0 0 400 290', role: 'img', 'aria-label': `${in1} と ${in2} で動かす DC モーター` });
      const add = (tag, attrs, text) => { const e = U.svgEl(tag, attrs); if (text !== undefined) e.textContent = text; svg.append(e); return e; };
      const defs = U.svgEl('defs');
      defs.append(U.svgEl('path', { id: 'ah', d: 'M-6 -6.5 L7 0 L-6 6.5 Z' }));
      svg.append(defs);
      const wires = add('g', { class: 'wire' });
      wires.append(U.svgEl('path', { d: `M60 92 V150 M110 92 V150 M170 190 H210 V${MY - 20} H${MX - 35}`
        + ` M170 218 H210 V${MY + 20} H${MX - 35}` }));
      add('g', { id: 'gCur' });
      add('rect', { class: 'body', x: 20, y: 20, width: 360, height: 72, rx: 6 });
      add('text', { x: 28, y: 40, class: 'small', 'font-weight': '700' }, t.device);
      add('rect', { class: 'comp', x: 30, y: 150, width: 140, height: 96, rx: 6 });     // under its labels
      [[60, in1, 'IN1'], [110, in2, 'IN2']].forEach(([x, pin, role]) => {
        add('text', { class: 'small', x, y: 66, 'text-anchor': 'middle' }, pin);
        add('text', { class: 'dim small', x, y: 84, 'text-anchor': 'middle' }, `${U.pinsWith(t, pin).join('、')} 番`);
        add('circle', { class: 'dot', cx: x, cy: 92, r: 4 });
        add('text', { class: 'small', x, y: 172, 'text-anchor': 'middle' }, role);
      });
      add('text', { class: 'small', x: 82, y: 226, 'text-anchor': 'middle' }, cfg.driver || 'ドライバ');
      add('text', { class: 'dim small', x: 164, y: 195, 'text-anchor': 'end' }, 'OUT1');
      add('text', { class: 'dim small', x: 164, y: 223, 'text-anchor': 'end' }, 'OUT2');
      add('circle', { class: 'motor', id: 'motor', cx: MX, cy: MY, r: 35 });
      add('text', { class: 'small', x: MX, y: MY + 6, 'text-anchor': 'middle', 'font-weight': '700' }, 'M');
      add('path', { class: 'spin', id: 'spin', d: '' });
      add('text', { class: 'dim small', x: MX, y: MY + 60, 'text-anchor': 'middle' }, cfg.motor || 'DC モーター');
      box.append(svg);
      const marks = [];
      [in1, in2].forEach((pin) => { const pb = U.portBit(pin); ['TRIS', 'PORT', 'LAT'].forEach((p) => marks.push({ reg: p + pb.port, bit: pb.bit })); });
      return {
        sub: `${in1}（IN1）と ${in2}（IN2）からモータードライバへ`,
        assume: 'IN1・IN2 の表は一般的なドライバのもの（IN1=1・IN2=0 で正転、0・1 で逆転、0・0 で空転して止まっていく、1・1 でブレーキ）。正転の向きはモーターのつなぎ方で決まる。',
        regHint: '太い枠のビットが IN1 と IN2 のピン。',
        marks,
        foot: ['モーターの動きは IN1・IN2 の値から一般的なドライバの表で読んだもの。速く切り替えている入力は、1 の時間の割合を速さの目安にする。'],
      };
    },
    update(ctx) {
      const { cfg, box, R, U } = ctx;
      const svg = box.querySelector('#cir');
      if (!svg) return {};
      const [in1, in2] = pinsOf(cfg);
      const ports = [...new Set([in1, in2].map((p) => U.portBit(p).port))];
      const missing = ports.filter((p) => U.latchOf(R, p) === undefined);
      if (missing.length) return { text: `${missing.map((p) => `PORT${p}（または LAT${p}）`).join('と')}を registers に入れるとモーターが描ける。` };
      const lv = [in1, in2].map((pin) => levelOf(pin, U));
      // an input switched fast: the share of time it is 1 is the speed
      const pwm = lv.map((level) => {
        if (!ctx.instrHz) return null;
        const p = ctx.pulse(level, SPAN);
        return p.period !== null && p.period < PWM_MAX && p.duty !== null && p.since <= 2 * p.period ? p : null;
      });
      const now = lv.map((level) => level(R));
      const driven = pwm.findIndex(Boolean);
      // while one input is switched fast, the motor turns the way that input on its own turns it
      const key = driven >= 0 ? (driven === 0 ? '10' : '01') : `${now[0]}${now[1]}`;
      const [mode, dir] = MODES[key];
      const speed = driven >= 0 ? pwm[driven].duty : dir !== 0 ? 1 : 0;
      svg.querySelector('#motor').classList.toggle('on', dir !== 0);
      svg.querySelector('#spin').setAttribute('d', dir > 0
        ? `M${MX - 27} ${MY + 22} A36 36 0 0 1 ${MX - 27} ${MY - 22} M${MX - 27} ${MY - 22} l-10 4 M${MX - 27} ${MY - 22} l2 10`
        : dir < 0 ? `M${MX + 27} ${MY - 22} A36 36 0 0 1 ${MX + 27} ${MY + 22} M${MX + 27} ${MY + 22} l10 -4 M${MX + 27} ${MY + 22} l-2 -10` : '');
      const g = svg.querySelector('#gCur');
      g.replaceChildren();
      if (dir !== 0) {
        const top = [[170, 190], [210, 190], [210, MY - 20], [MX - 35, MY - 20]];
        const bottom = [[MX - 35, MY + 20], [210, MY + 20], [210, 218], [170, 218]];
        // forward: out of OUT1, through the motor, back into OUT2; reverse: the other way round
        const legs = dir > 0 ? [top, bottom] : [[...bottom].reverse(), [...top].reverse()];
        legs.forEach((leg) => U.drawPath(g, leg, '', { arrows: false }));
      }
      const status = [
        { label: 'IN1・IN2', value: `${now[0]}・${now[1]}${driven >= 0 ? `（${driven === 0 ? 'IN1' : 'IN2'} を速く切り替え）` : ''}` },
        { label: 'モーター', value: mode, tone: dir !== 0 ? 'on' : '' },
      ];
      if (driven >= 0) status.push({ label: '速さの目安', value: `${Math.round(speed * 100)} %` });
      const text = driven >= 0
        ? `${driven === 0 ? 'IN1' : 'IN2'} を ${ctx.fmtTime(pwm[driven].period)} ごとに切り替え、1 の時間は ${Math.round(speed * 100)} %。`
          + `モーターは${mode}で、速さはその割合ほど（PWM）。`
        : `IN1 = ${now[0]}、IN2 = ${now[1]} なので、モーターは${mode}。`;
      return { status, text, probe: { in1: now[0], in2: now[1], mode, speed } };
    },
  };
})();
