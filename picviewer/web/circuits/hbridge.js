/* hbridge: a DC motor on an H-bridge driver with RPWM / LPWM / EN inputs (IBT-2 style).
   Shows the four switches, the current path in the PWM on and off periods, and the duty from the CCP registers. */
(() => {
  'use strict';

  const SVG = `
<svg id="cir" viewBox="0 0 820 480" role="img" aria-label="H ブリッジのモータードライバと電流の経路">
  <defs><path id="ah" d="M-6 -6.5 L7 0 L-6 6.5 Z"/></defs>
  <g class="wire">
    <path d="M60 212 V40 H140 M190 40 H280 V110"/>
    <path d="M60 254 V445 H280 V400"/>
    <path d="M280 110 H560 M280 400 H560"/>
    <path d="M330 110 V135 M330 175 V305 M330 345 V400"/>
    <path d="M560 110 V135 M560 175 V305 M560 345 V400"/>
    <path d="M330 240 H410 M480 240 H560"/>
    <path class="ctl" d="M235 300 H315 V155 M315 300 V325 M235 330 H300 V412 H545 V155 M545 412 V325"/>
    <path class="ctl" d="M235 360 H262"/>
  </g>
  <rect class="mod" x="280" y="78" width="380" height="350" rx="8"/>
  <text class="small dim" x="292" y="97" id="hDriver">ドライバ</text>
  <g id="gCur"></g>
  <rect class="comp" x="140" y="31" width="50" height="18" rx="3"/>
  <line x1="140" y1="40" x2="190" y2="40" stroke="currentColor" stroke-width="1.5"/>
  <text class="small dim" x="128" y="24">ヒューズ</text>
  <line class="plate" x1="42" y1="212" x2="78" y2="212"/>
  <line class="plate thick" x1="50" y1="226" x2="70" y2="226"/>
  <line class="plate" x1="42" y1="240" x2="78" y2="240"/>
  <line class="plate thick" x1="50" y1="254" x2="70" y2="254"/>
  <text class="big" x="22" y="210">+</text>
  <text class="big" x="25" y="266">-</text>
  <text x="84" y="223">電池</text>
  <text x="84" y="245" id="hSupply">7.2 V</text>
  <text class="dim small" x="200" y="30">B+（モーター電源）</text>
  <text class="dim small" x="200" y="466">B-（GND）</text>
  <g class="sw" id="q1"><line class="gate" x1="322" y1="145" x2="322" y2="165"/><line class="lever" x1="330" y1="175" x2="330" y2="135" style="transform-origin:330px 175px"/><circle class="dot" cx="330" cy="135" r="3"/><circle class="dot" cx="330" cy="175" r="3"/><text class="small" x="340" y="150">Q1 上側</text><text class="small dim" x="340" y="168" id="q1t">オフ</text></g>
  <g class="sw" id="q2"><line class="gate" x1="322" y1="315" x2="322" y2="335"/><line class="lever" x1="330" y1="345" x2="330" y2="305" style="transform-origin:330px 345px"/><circle class="dot" cx="330" cy="305" r="3"/><circle class="dot" cx="330" cy="345" r="3"/><text class="small" x="340" y="320">Q2 下側</text><text class="small dim" x="340" y="338" id="q2t">オフ</text></g>
  <g class="sw" id="q3"><line class="gate" x1="552" y1="145" x2="552" y2="165"/><line class="lever" x1="560" y1="175" x2="560" y2="135" style="transform-origin:560px 175px"/><circle class="dot" cx="560" cy="135" r="3"/><circle class="dot" cx="560" cy="175" r="3"/><text class="small" x="570" y="150">Q3 上側</text><text class="small dim" x="570" y="168" id="q3t">オフ</text></g>
  <g class="sw" id="q4"><line class="gate" x1="552" y1="315" x2="552" y2="335"/><line class="lever" x1="560" y1="345" x2="560" y2="305" style="transform-origin:560px 345px"/><circle class="dot" cx="560" cy="305" r="3"/><circle class="dot" cx="560" cy="345" r="3"/><text class="small" x="570" y="320">Q4 下側</text><text class="small dim" x="570" y="338" id="q4t">オフ</text></g>
  <circle class="dot" cx="330" cy="240" r="3.5"/><circle class="dot" cx="560" cy="240" r="3.5"/>
  <text class="small dim" x="294" y="236">M+</text>
  <text class="small dim" x="568" y="236">M-</text>
  <circle class="dot" cx="280" cy="110" r="4.5"/><circle class="dot" cx="280" cy="400" r="4.5"/>
  <circle class="motor" id="motor" cx="445" cy="240" r="35"/>
  <text class="big" x="436" y="247">M</text>
  <path class="spin" id="spin" d=""/>
  <text class="small dim" x="410" y="296">モーター</text>
  <rect class="body" x="88" y="262" width="147" height="140" rx="6"/>
  <text x="96" y="283" id="hDev" font-weight="700">PIC</text>
  <text class="small" x="96" y="306" id="hRpwm">RPWM</text>
  <text class="small" x="96" y="336" id="hLpwm">LPWM</text>
  <text class="small" x="96" y="366" id="hEn">EN</text>
  <text class="small dim" x="96" y="392">5 V 系（別電源）</text>
  <text class="small dim" x="240" y="296">RPWM</text>
  <text class="small dim" x="240" y="326">LPWM</text>
  <text class="small dim" x="240" y="356">EN</text>
  <text class="small dim" x="266" y="372">R_EN, L_EN</text>
</svg>`;

  const PATHS = {
    fwdOn: [[60, 212], [60, 40], [280, 40], [280, 110], [330, 110], [330, 240], [560, 240], [560, 400], [280, 400], [280, 445], [60, 445], [60, 254]],
    fwdOff: [[330, 240], [560, 240], [560, 400], [330, 400], [330, 240]],
    revOn: [[60, 212], [60, 40], [280, 40], [280, 110], [560, 110], [560, 240], [330, 240], [330, 400], [280, 400], [280, 445], [60, 445], [60, 254]],
    revOff: [[560, 240], [330, 240], [330, 400], [560, 400], [560, 240]],
    gen: [[330, 240], [560, 240], [560, 400], [330, 400], [330, 240]],
  };
  const MOTOR_BOX = [[400, 230, 490, 250]];

  // duty in 1/(4 x (PR+1)) steps and whether the module is in PWM mode, for both CCP generations
  function readCcp(t, R, n) {
    const con = R[`CCP${n}CON`];
    const lo = R[`CCPR${n}L`];
    if (con === undefined || lo === undefined) return null;
    const reg = t.regs.find((r) => r.name === `CCP${n}CON`);
    const newStyle = Boolean(reg && reg.bits.some((b) => b && /FMT$/.test(b)));
    if (newStyle) {                       // EN, FMT, MODE<3:0> (e.g. PIC16F1618)
      const hi = R[`CCPR${n}H`];
      if (hi === undefined) return null;
      const duty = (con >> 4) & 1 ? (hi << 2) | (lo >> 6) : ((hi << 8) | lo) & 0x3ff;
      return { duty, pwm: (con & 0x80) !== 0 && (con & 0x0c) === 0x0c };
    }
    return { duty: (lo << 2) | ((con >> 4) & 3), pwm: (con & 0x0c) === 0x0c };   // DCxB<1:0>, CCPxM<3:0> (e.g. PIC16F886)
  }

  function readTimer2(R) {
    if (R.T2PR !== undefined && R.T2CON !== undefined) {   // Timer2 with ON / CKPS<2:0> / T2PR
      const clockOk = R.T2CLKCON === undefined || (R.T2CLKCON & 0x0f) === 0;
      return { on: (R.T2CON >> 7) & 1, presc: 1 << ((R.T2CON >> 4) & 7), pr: R.T2PR, clockOk };
    }
    if (R.PR2 !== undefined && R.T2CON !== undefined) {    // TMR2ON / T2CKPS<1:0> / PR2
      return { on: (R.T2CON >> 2) & 1, presc: [1, 4, 16, 16][R.T2CON & 3], pr: R.PR2, clockOk: true };
    }
    return null;
  }

  function bridge(ctx) {
    const { t, cfg, R, U, opts, instrHz } = ctx;
    const tm = readTimer2(R);
    const c1 = readCcp(t, R, cfg.rpwm.ccp);
    const c2 = readCcp(t, R, cfg.lpwm.ccp);
    const enPb = U.portBit(cfg.en.pin);
    const enReg = enPb ? U.latchOf(R, enPb.port) : undefined;
    if (!tm || !c1 || !c2 || enReg === undefined) return null;
    const routed = (side) => !side.pps || R[side.pps.reg] === undefined || R[side.pps.reg] === side.pps.code;
    const en = (enReg >> enPb.bit) & 1;
    const d1 = c1.pwm && tm.on && routed(cfg.rpwm) ? c1.duty : 0;
    const d2 = c2.pwm && tm.on && routed(cfg.lpwm) ? c2.duty : 0;
    const dmax = 4 * (tm.pr + 1);
    const q = { q1: false, q2: false, q3: false, q4: false };
    let state, path = null, duty = 0, dir = 0;
    if (!en) {
      state = 'coast';
    } else if (d1 > 0 && d2 > 0) {
      state = 'invalid'; q.q1 = q.q3 = true;
    } else if (d1 === 0 && d2 === 0) {
      state = 'brake'; q.q2 = q.q4 = true; path = 'gen';
    } else {
      dir = d1 > 0 ? 1 : -1;
      duty = Math.min(1, (d1 || d2) / dmax);
      if (opts.phase === 'on' || duty >= 1) {
        state = dir > 0 ? 'fwd' : 'rev';
        if (dir > 0) { q.q1 = q.q4 = true; path = 'fwdOn'; } else { q.q3 = q.q2 = true; path = 'revOn'; }
      } else {
        state = dir > 0 ? 'fwdOff' : 'revOff';
        q.q2 = q.q4 = true;
        path = dir > 0 ? 'fwdOff' : 'revOff';
      }
    }
    const freq = tm.on && tm.clockOk && instrHz ? instrHz / (tm.presc * (tm.pr + 1)) : 0;
    return { q, state, path, duty, dir, dmax, d1, d2, freq, timerOn: Boolean(tm.on), clockOk: tm.clockOk };
  }

  window.PicViewer.circuits.hbridge = {
    title: '回路と電流',
    options: [{
      key: 'phase', legend: 'PWM のどの期間を描くか', default: 'on',
      choices: [{ value: 'on', label: 'オン期間（電池から流れる）' }, { value: 'off', label: 'オフ期間（下側で還流）' }],
    }],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      if (!(cfg.rpwm && cfg.lpwm && cfg.en && U.portBit(cfg.en.pin))) {
        box.append(U.el('p', 'hint', 'circuit に rpwm（pin と ccp）、lpwm（pin と ccp）、en（pin）を書く。'));
        return {};
      }
      box.innerHTML = SVG;
      const svg = box.querySelector('#cir');
      const set = (id, s) => { svg.querySelector('#' + id).textContent = s; };
      const no = (pin) => U.pinsWith(t, pin).join('、');
      const vm = cfg.vm || 7.2;
      set('hDev', t.device);
      set('hRpwm', `${cfg.rpwm.pin} CCP${cfg.rpwm.ccp}  ${no(cfg.rpwm.pin)} 番`);
      set('hLpwm', `${cfg.lpwm.pin} CCP${cfg.lpwm.ccp}  ${no(cfg.lpwm.pin)} 番`);
      set('hEn', `${cfg.en.pin}  ${no(cfg.en.pin)} 番`);
      set('hSupply', `${U.num(vm)} V`);
      set('hDriver', cfg.driver || 'H ブリッジのドライバ');
      const enPb = U.portBit(cfg.en.pin);
      const current = cfg.r_ohm
        ? `巻線抵抗 ${cfg.r_ohm} Ω から、起動時と拘束時の電流は約 ${Math.round(vm / cfg.r_ohm)} A。`
        : `電流の大きさは出していない（${cfg.r_note || 'モーターの巻線抵抗が分かれば circuit.r_ohm に書く'}）。`;
      return {
        sub: [cfg.driver, `電池 ${U.num(vm)} V`, cfg.motor].filter(Boolean).join('、'),
        assume: `平均電圧はデューティと電池の電圧の積。${current}`,
        regHint: 'CCPRx が PWM の時間幅、PR2（新しい型では T2PR）が周期、T2CON が Timer2 の設定。太い枠のビットが EN のピン。',
        marks: ['PORT', 'LAT'].map((p) => ({ reg: p + enPb.port, bit: enPb.bit })),
        foot: [
          'H ブリッジの 4 個のスイッチは、ドライバの上側と下側の MOSFET。RPWM = 1 で Q1、LPWM = 1 で Q3 がオンになり、0 のときは同じ側の下側（Q2、Q4）がオン。EN = 0 で 4 個ともオフ。',
          'シミュレータが見ているのは PIC のピンまで。ドライバとモーターの振る舞いは、この入力から決めた想定。',
        ],
      };
    },
    update(ctx) {
      const { cfg, box, st, U, fmtTime, instrHz } = ctx;
      const svg = box.querySelector('#cir');
      if (!svg) return {};
      const B = bridge(ctx);
      if (!B) return { text: 'CCPxCON、CCPRxL（新しい型は CCPRxH も）、T2CON、PR2 か T2PR、EN のピンの PORT か LAT を registers に入れると描ける。' };
      ['q1', 'q2', 'q3', 'q4'].forEach((k) => {
        svg.querySelector('#' + k).classList.toggle('on', B.q[k]);
        const tx = svg.querySelector('#' + k + 't');
        tx.textContent = B.q[k] ? 'オン' : 'オフ';
        tx.setAttribute('class', B.q[k] ? 'small em' : 'small dim');
      });
      const g = svg.querySelector('#gCur');
      g.replaceChildren();
      if (B.path) {
        const cls = B.path === 'gen' ? 'gen' : B.path.endsWith('Off') ? 're' : '';
        U.drawPath(g, PATHS[B.path], cls, { arrows: B.path === 'gen' ? false : 'auto', skip: MOTOR_BOX });
      }
      svg.querySelector('#motor').classList.toggle('on', B.dir !== 0);
      svg.querySelector('#spin').setAttribute('d', B.dir > 0
        ? 'M418 262 A36 36 0 0 1 418 218 M418 218 l-10 4 M418 218 l2 10'
        : B.dir < 0 ? 'M472 218 A36 36 0 0 1 472 262 M472 262 l10 -4 M472 262 l-2 -10' : '');

      const vm = cfg.vm || 7.2;
      const pct = Math.round(B.duty * 100);
      const periodUs = B.freq ? 1e6 / B.freq : null;
      const share = (f) => (periodUs ? `1 周期 ${U.num(periodUs, 0)} µs のうち ${U.num(periodUs * f, 0)} µs がこの状態。` : '');
      const label = {
        coast: 'ドライバ無効（4 個ともオフ）', brake: 'ブレーキ（下側 2 個がオン）', invalid: '両方の PWM が入っている',
        fwd: `正転（${pct} %）`, rev: `逆転（${pct} %）`, fwdOff: `正転（${pct} %）のオフ期間`, revOff: `逆転（${pct} %）のオフ期間`,
      }[B.state];
      const tone = B.state === 'fwd' || B.state === 'rev' ? 'on' : B.state === 'fwdOff' || B.state === 'revOff' ? 're' : '';
      const text = {
        coast: 'EN = 0 なのでドライバは待機。4 個のスイッチが全部オフで、モーターには何もつながっていない。回っていれば惰性で止まる。',
        brake: 'RPWM と LPWM が両方 0 なので、下側の Q2 と Q4 がオン。電池からは流れない。モーターが回っていれば、発電した電流が Q2 と Q4 を通って回り、制動がかかる（点線）。',
        fwd: `RPWM = 1 の間は、電池のプラス側、ヒューズ、B+、Q1、M+、モーター、M-、Q4、B-、電池のマイナス側の順に流れる。LPWM = 0 なので Q4 がオン。この向きを正転とする。${pct >= 100 ? '100 % なのでオフ期間は無い。' : share(B.duty)}`,
        rev: `LPWM = 1 の間は、電池のプラス側、ヒューズ、B+、Q3、M-、モーター、M+、Q2、B-、電池のマイナス側の順に流れる。RPWM = 0 なので Q2 がオン。正転と逆向き。${pct >= 100 ? '100 % なのでオフ期間は無い。' : share(B.duty)}`,
        fwdOff: `RPWM = 0 の間は Q1 が切れて Q2 がオンになり、Q4 もオン。巻線はコイルなので電流は急に止まらず、M+、モーター、M-、Q4、Q2 と下側 2 個を通って回り続ける（還流。ブレーキと同じ経路）。${share(1 - B.duty)}`,
        revOff: `LPWM = 0 の間は Q3 が切れて Q4 がオンになり、Q2 もオン。電流は M-、モーター、M+、Q2、Q4 と下側 2 個を通って回り続ける（還流）。${share(1 - B.duty)}`,
        invalid: 'RPWM と LPWM が同時に 1。Q1 と Q3 がオンで M+ と M- が同じ電圧になり、モーターは止まる。使わない組み合わせ。',
      }[B.state];
      const freqText = !B.timerOn ? 'Timer2 停止' : !B.clockOk ? 'Timer2 のクロックが Fosc/4 以外' : B.freq ? `${U.num(B.freq / 1000)} kHz` : '-';
      return {
        status: [
          { label: '状態', value: label, tone },
          { label: 'デューティ', value: B.dir ? `${pct} %（${B.d1 || B.d2} / ${B.dmax}）` : '-', tone },
          { label: 'PWM 周波数', value: freqText },
          { label: 'モーターの平均電圧', value: B.dir ? `約 ${U.num(B.duty * vm)} V` : '0 V', tone },
          { label: 'リセットからの時間', value: ctx.seconds !== null ? fmtTime(ctx.seconds) : '-' },
        ],
        text,
        probe: { state: B.state, duty: pct, q: ['q1', 'q2', 'q3', 'q4'].map((k) => (B.q[k] ? '1' : '0')).join('') },
      };
    },
  };
})();
