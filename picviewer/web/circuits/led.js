/* led: one port pin drives an LED through a resistor. Shows the pin's output FETs and the current path. */
(() => {
  'use strict';

  const SVG = `
<svg id="cir" viewBox="0 0 740 410" role="img" aria-label="PIC の出力ピンと LED の回路">
  <defs><path id="ah" d="M-6 -6.5 L7 0 L-6 6.5 Z"/></defs>
  <g class="wire">
    <path d="M60 197 V44 H690"/>
    <path d="M60 211 V366 H690"/>
    <path d="M372 44 V96"/>
    <path d="M372 314 V366"/>
    <path d="M480 205 H530"/>
    <path d="M600 205 H660"/>
    <path class="w-source" d="M660 205 V262 M660 292 V366"/>
    <path class="w-sink" d="M660 44 V104 M660 134 V205"/>
  </g>
  <rect class="body" x="184" y="96" width="296" height="218" rx="6"/>
  <g class="wire">
    <path d="M372 96 V130 M372 164 V246 M372 280 V314 M372 205 H480"/>
    <path class="ctl" d="M346 190 H355 V147 H364 M346 220 H355 V263 H364"/>
  </g>
  <g id="gCur"></g>
  <line class="plate" x1="42" y1="197" x2="78" y2="197"/>
  <line class="plate thick" x1="50" y1="211" x2="70" y2="211"/>
  <text class="big" x="22" y="196">+</text>
  <text class="big" x="25" y="228">-</text>
  <text x="90" y="200">電源</text>
  <text x="90" y="222" id="lSupply">5 V</text>
  <text class="dim" x="150" y="32" id="lRailTop">+5 V の線</text>
  <text class="dim" x="150" y="391">GND (0 V) の線</text>
  <text class="big" x="196" y="121" id="lDev" font-weight="700">PIC</text>
  <rect class="box" x="194" y="164" width="152" height="82" rx="5"/>
  <text class="dim small" x="203" y="183">方向と出力データ</text>
  <text x="203" y="208" id="lTris">TRIS</text>
  <text x="203" y="233" id="lData">DATA</text>
  <g class="sw" id="swP">
    <line class="gate" x1="365" y1="137" x2="365" y2="157"/>
    <line class="lever" x1="372" y1="164" x2="372" y2="130" style="transform-origin:372px 164px"/>
    <circle class="dot" cx="372" cy="130" r="3"/><circle class="dot" cx="372" cy="164" r="3"/>
    <text x="402" y="145">Pch FET</text>
    <text x="402" y="166" id="lPch">オフ</text>
  </g>
  <g class="sw" id="swN">
    <line class="gate" x1="365" y1="253" x2="365" y2="273"/>
    <line class="lever" x1="372" y1="280" x2="372" y2="246" style="transform-origin:372px 280px"/>
    <circle class="dot" cx="372" cy="246" r="3"/><circle class="dot" cx="372" cy="280" r="3"/>
    <text x="402" y="261">Nch FET</text>
    <text x="402" y="282" id="lNch">オフ</text>
  </g>
  <circle class="dot" cx="372" cy="205" r="3"/>
  <circle class="dot" cx="372" cy="96" r="4.5"/>
  <circle class="dot" cx="372" cy="314" r="4.5"/>
  <circle class="dot" cx="480" cy="205" r="4.5"/>
  <text x="383" y="80" id="lVdd">VDD</text>
  <text x="383" y="347" id="lVss">VSS</text>
  <text x="488" y="239" id="lPin">PIN</text>
  <text class="dim" x="488" y="260" id="lVolt"></text>
  <rect class="comp" x="530" y="194" width="70" height="22" rx="2"/>
  <text x="565" y="181" text-anchor="middle" id="lR">抵抗</text>
  <g class="w-source">
    <circle class="halo" cx="660" cy="277" r="27"/>
    <path class="led-body" d="M644 262 H676 L660 290 Z"/>
    <line class="led-bar" x1="644" y1="292" x2="676" y2="292"/>
    <text class="big" x="688" y="272">LED</text>
    <text class="dim ledState" x="688" y="292">消灯</text>
  </g>
  <g class="w-sink">
    <circle class="halo" cx="660" cy="119" r="27"/>
    <path class="led-body" d="M644 104 H676 L660 132 Z"/>
    <line class="led-bar" x1="644" y1="134" x2="676" y2="134"/>
    <text class="big" x="688" y="114">LED</text>
    <text class="dim ledState" x="688" y="134">消灯</text>
  </g>
</svg>`;

  // current paths in the drawing's coordinates, with arrows placed between the parts
  const PATHS = {
    source: {
      pts: [[60, 197], [60, 44], [372, 44], [372, 205], [660, 205], [660, 366], [60, 366], [60, 211]],
      arrows: [[60, 120, -90], [215, 44, 0], [372, 72, 90], [430, 205, 0], [506, 205, 0], [632, 205, 0],
        [660, 332, 90], [520, 366, 180], [215, 366, 180], [60, 290, -90]],
    },
    sink: {
      pts: [[60, 197], [60, 44], [660, 44], [660, 205], [372, 205], [372, 366], [60, 366], [60, 211]],
      arrows: [[60, 120, -90], [215, 44, 0], [520, 44, 0], [660, 76, 90], [660, 172, 90], [630, 205, 180],
        [504, 205, 180], [428, 205, 180], [372, 342, 90], [215, 366, 180], [60, 290, -90]],
    },
  };

  const values = (t, cfg) => ({ vdd: t.vdd || 5, r: cfg.r_ohm || 330, vf: cfg.vf === undefined ? 2.0 : cfg.vf });
  const volts = (v, U) => U.num(v, Number.isInteger(v) ? 0 : 1);

  window.PicViewer.circuits.led = {
    title: '回路と電流',
    options: [{
      key: 'wire', legend: 'LED のつなぎ方（仮定）', default: 'source',
      choices: [{ value: 'source', label: '1 で点灯（ピンと GND の間）' }, { value: 'sink', label: '0 で点灯（電源とピンの間）' }],
    }],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const pb = U.portBit(cfg.pin);
      if (!pb) {
        box.append(U.el('p', 'hint', 'circuit.pin に LED をつなぐピンの名前（例 RB0）を書く。'));
        return {};
      }
      box.innerHTML = SVG;
      const svg = box.querySelector('#cir');
      const { vdd, r, vf } = values(t, cfg);
      const set = (id, s) => { svg.querySelector('#' + id).textContent = s; };
      set('lDev', t.device);
      set('lVdd', `VDD  ${U.pinsWith(t, 'VDD').join('、')} 番ピン`);
      set('lVss', `VSS  ${U.pinsWith(t, 'VSS').join('、')} 番ピン`);
      set('lPin', `${cfg.pin}  ${U.pinsWith(t, cfg.pin).join('、')} 番ピン`);
      set('lSupply', `${volts(vdd, U)} V`);
      set('lRailTop', `+${volts(vdd, U)} V の線`);
      set('lR', `抵抗 ${r} Ω`);
      const mA = Math.round(((vdd - vf) / r) * 1000);
      const limit = cfg.io_max_ma ? `I/O ピン 1 本の絶対最大定格は ${cfg.io_max_ma} mA${cfg.datasheet ? `（${cfg.datasheet}）` : ''}。` : '';
      return {
        sub: 'LED のまわりは仮定の回路',
        assume: `電流の見積もり: (電源 ${volts(vdd, U)} V - LED の順方向電圧 ${U.num(vf)} V) / 抵抗 ${r} Ω = 約 ${mA} mA（FET のオン抵抗は無視）。${limit}`,
        regHint: `TRIS${pb.port} は 1 が入力、0 が出力。太い枠のビットが LED をつなぐ ${cfg.pin}。`,
        marks: ['TRIS', 'PORT', 'LAT'].map((p) => ({ reg: p + pb.port, bit: pb.bit })),
        foot: [`LED と抵抗のつなぎ方、抵抗 ${r} Ω、順方向電圧 ${U.num(vf)} V は仮定。実際の基板に合わせて circuit の値を直す。電源電圧はデバイス定義ファイルの公称値。`],
      };
    },
    update(ctx) {
      const { t, cfg, box, R, U, opts } = ctx;
      const svg = box.querySelector('#cir');
      const pb = U.portBit(cfg.pin);
      if (!svg || !pb) return {};
      const trisReg = R['TRIS' + pb.port];
      const latch = U.latchOf(R, pb.port);
      if (trisReg === undefined || latch === undefined) {
        return { text: `TRIS${pb.port} と PORT${pb.port}（または LAT${pb.port}）を registers に入れると、ピンの状態が描ける。` };
      }
      const { vdd, r, vf } = values(t, cfg);
      const tris = (trisReg >> pb.bit) & 1;
      const data = (latch >> pb.bit) & 1;
      const drive = tris ? 'hiz' : (data ? 'high' : 'low');
      const wire = opts.wire;
      const flow = wire === 'source' && drive === 'high' ? 'source' : wire === 'sink' && drive === 'low' ? 'sink' : 'none';
      const lit = flow !== 'none';
      const mA = Math.round(((vdd - vf) / r) * 1000);
      const volt = drive === 'high' ? `約 ${volts(vdd, U)} V` : drive === 'low' ? '約 0 V' : 'ハイインピーダンス';

      svg.dataset.wire = wire;
      svg.dataset.lit = lit ? '1' : '0';
      const g = svg.querySelector('#gCur');
      g.replaceChildren();
      if (lit) U.drawPath(g, PATHS[flow].pts, '', { arrows: PATHS[flow].arrows });
      const sw = (id, label, on) => {
        svg.querySelector('#' + id).classList.toggle('on', on);
        const tx = svg.querySelector('#' + label);
        tx.textContent = on ? 'オン' : 'オフ';
        tx.setAttribute('class', on ? 'em' : 'dim');
      };
      sw('swP', 'lPch', drive === 'high');
      sw('swN', 'lNch', drive === 'low');
      const latchName = R['LAT' + pb.port] !== undefined ? `LAT${pb.port}${pb.bit}` : cfg.pin;
      const word = U.svgEl('tspan', { dx: '8', class: 'dim' });
      word.textContent = tris ? '入力' : '出力';
      svg.querySelector('#lTris').replaceChildren(`TRIS${pb.port}${pb.bit} = ${tris}`, word);
      svg.querySelector('#lData').textContent = `${latchName} = ${data}`;
      svg.querySelector('#lVolt').textContent = volt;
      svg.querySelectorAll('.ledState').forEach((tx) => {
        tx.textContent = lit ? '点灯' : '消灯';
        tx.setAttribute('class', lit ? 'em ledState' : 'dim ledState');
      });

      const pin = cfg.pin;
      const text = {
        'source/high': `電流が流れる。電源のプラス側から VDD、Pch FET、${pin}、抵抗、LED、GND の順に通って、電源のマイナス側へ戻る。`,
        'source/low': `電流は流れない。Nch FET がオンで ${pin} は約 0 V になり、LED の両端に電圧がかからない。`,
        'sink/low': `電流が流れる。電源のプラス側から LED、抵抗、${pin}、Nch FET、VSS、GND の順に通って、電源のマイナス側へ戻る。`,
        'sink/high': `電流は流れない。Pch FET がオンで ${pin} は約 ${volts(vdd, U)} V になり、LED の両端が同じ電圧になる。`,
      }[`${wire}/${drive}`] || `電流は流れない。FET は両方オフで、${pin} は PIC の中でどこにもつながっていない。`;
      return {
        status: [
          { label: 'Pch FET', value: drive === 'high' ? 'オン' : 'オフ', tone: drive === 'high' ? 'on' : '' },
          { label: 'Nch FET', value: drive === 'low' ? 'オン' : 'オフ', tone: drive === 'low' ? 'on' : '' },
          { label: `${pin} の電圧`, value: volt },
          { label: 'LED', value: lit ? '点灯' : '消灯', tone: lit ? 'on' : '' },
          { label: 'LED の電流', value: lit ? `約 ${mA} mA` : '0 mA', tone: lit ? 'on' : '' },
        ],
        text,
        probe: { drive, flow, lit },
      };
    },
  };
})();
