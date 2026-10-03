/* pins: the default view. Direction and value of every port pin, from TRISx and PORTx / LATx. */
(() => {
  'use strict';

  function pinNames(t, cfg) {
    if (Array.isArray(cfg.pins) && cfg.pins.length) return cfg.pins.map((p) => String(p).toUpperCase());
    const out = [];
    t.regs.forEach((r) => {
      const m = /^TRIS([A-Z])$/.exec(r.name);
      if (!m) return;
      for (let b = 0; b < 8; b++) if ((r.mask >> b) & 1) out.push(`R${m[1]}${b}`);
    });
    return out;
  }

  window.PicViewer.circuits.pins = {
    title: 'ピン',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const names = pinNames(t, cfg).filter((n) => U.portBit(n));
      if (!names.length) {
        box.append(U.el('p', 'hint', 'ピンを出すには、TRISx と PORTx（または LATx）を registers に入れる。'));
        return {};
      }
      const grid = U.el('div', 'pins');
      names.forEach((name) => {
        const card = U.el('div', 'pin');
        card.dataset.pin = name;
        const no = U.pinsWith(t, name);
        card.append(U.el('b', '', name), U.el('span', 'no', no.length ? `${no.join('、')} 番` : ''),
          U.el('span', 'dir', ''), U.el('span', 'lv', ''));
        grid.append(card);
      });
      box.append(grid);
      return {
        sub: 'TRIS と PORT / LAT から見た、各ピンの向きと値',
        assume: 'TRIS のビットが 0 なら出力、1 なら入力。値は PORT のビット（PORT を読んでいなければ LAT）。オレンジの枠は直前のステップから変わったピン。',
      };
    },
    update(ctx) {
      const { box, R, P, U } = ctx;
      const outs = [];
      box.querySelectorAll('.pin').forEach((card) => {
        const pb = U.portBit(card.dataset.pin);
        const read = (regs, prefix) => (regs && regs[prefix + pb.port] !== undefined ? (regs[prefix + pb.port] >> pb.bit) & 1 : null);
        const level = (regs) => { const v = read(regs, 'PORT'); return v !== null ? v : read(regs, 'LAT'); };
        const dir = read(R, 'TRIS');
        const lv = level(R);
        card.classList.toggle('out', dir === 0);
        card.classList.toggle('hi', lv === 1);
        card.classList.toggle('chg', Boolean(P) && (read(P, 'TRIS') !== dir || level(P) !== lv));
        card.querySelector('.dir').textContent = dir === null ? '?' : (dir ? '入力' : '出力');
        card.querySelector('.lv').textContent = lv === null ? '?' : String(lv);
        if (dir === 0) outs.push(`${card.dataset.pin} = ${lv}`);
      });
      return { text: outs.length ? `出力のピン: ${outs.join('、')}` : '出力のピンは無い（すべて入力）。', probe: { outputs: outs } };
    },
  };
})();
