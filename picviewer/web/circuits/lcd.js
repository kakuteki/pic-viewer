/* lcd: a character LCD (HD44780 or compatible) on a 4-bit bus.
   Replays every value the port held up to this step, latches a nibble on each falling edge of E,
   and runs the controller's commands to draw the screen. Every write to the port must be a stop
   (use {"until_write": "PORTB", ...} in the trace), or edges are missed. */
(() => {
  'use strict';

  const ROW_START = [0x00, 0x40, 0x14, 0x54];

  function settings(cfg) {
    return {
      port: String(cfg.port || 'B').toUpperCase(),
      rs: cfg.rs === undefined ? 0 : cfg.rs,
      rw: cfg.rw === undefined ? 1 : cfg.rw,      // null: R/W is tied to GND (the program only writes)
      e: cfg.e === undefined ? 2 : cfg.e,
      high: cfg.data !== 'low',
      rows: cfg.rows || 2,
      cols: cfg.cols || 16,
    };
  }

  function command(s, byte) {
    if (byte & 0x80) { s.addr = byte & 0x7f; s.cgram = false; return `DDRAM のアドレスを 0x${(byte & 0x7f).toString(16).padStart(2, '0')} にする（文字を書く位置）`; }
    if (byte & 0x40) { s.cgram = true; return 'CGRAM のアドレスを決める（自作の文字）'; }
    if (byte & 0x20) {
      s.mode8 = Boolean(byte & 0x10);
      s.lines = byte & 0x08 ? 2 : 1;
      return `機能の設定: ${s.mode8 ? '8' : '4'} ビット、${s.lines} 行`;
    }
    if (byte & 0x10) {
      if (!(byte & 0x08)) s.addr = (s.addr + (byte & 0x04 ? 1 : 127)) & 0x7f;
      return byte & 0x08 ? '表示をずらす' : 'カーソルを動かす';
    }
    if (byte & 0x08) {
      s.display = Boolean(byte & 0x04); s.cursor = Boolean(byte & 0x02); s.blink = Boolean(byte & 0x01);
      return `表示 ${s.display ? 'オン' : 'オフ'}、カーソル ${s.cursor ? 'あり' : 'なし'}、点滅 ${s.blink ? 'あり' : 'なし'}`;
    }
    if (byte & 0x04) { s.inc = Boolean(byte & 0x02); return `書くたびにカーソルを${s.inc ? '右' : '左'}へ`; }
    if (byte & 0x02) { s.addr = 0; return 'カーソルを先頭へ'; }
    if (byte & 0x01) { s.ram.fill(0x20); s.addr = 0; s.inc = true; return '画面を消す'; }
    return '何もしない命令';
  }

  function data(s, byte) {
    if (s.cgram) return `CGRAM にデータ 0x${byte.toString(16).padStart(2, '0')} を書く`;
    s.ram[s.addr] = byte;
    const where = s.addr;
    s.addr = (s.addr + (s.inc ? 1 : 127)) & 0x7f;
    const ch = byte >= 0x20 && byte < 0x7f ? `'${String.fromCharCode(byte)}'` : '記号';
    return `文字 ${ch}（0x${byte.toString(16).padStart(2, '0')}）を DDRAM の 0x${where.toString(16).padStart(2, '0')} に書く`;
  }

  // the controller after the port values of steps 0..k; 'last' describes what step k did on the bus
  function replay(ctx, c) {
    const { t, k, U } = ctx;
    const s = { mode8: true, half: null, ram: new Array(128).fill(0x20), addr: 0, inc: true, cgram: false,
      display: false, cursor: false, blink: false, lines: 1, log: [], last: '' };
    let prevE = 0;
    let prevVal = null;
    for (let i = 0; i <= k; i++) {
      const R = ctx.regsAt(t.steps[i]);
      const val = U.latchOf(R, c.port);
      if (val === undefined) return null;
      const e = (val >> c.e) & 1;
      const rs = (val >> c.rs) & 1;
      const nib = c.high ? (val >> 4) & 0x0f : val & 0x0f;
      let what = '';
      if (prevE === 1 && e === 0) {
        if (s.mode8) {                       // 8-bit interface on a 4-bit bus: the low half reads as 0
          what = `E が下がった。LCD は 8 ビットのつもりで 0x${(nib << 4).toString(16)} を読んだ: ` + (rs ? data(s, nib << 4) : command(s, nib << 4));
        } else if (s.half === null) {
          s.half = nib;
          what = `E が下がった。上位 4 ビット 0x${nib.toString(16)} を読んだ（${rs ? '文字' : 'コマンド'}の前半）`;
        } else {
          const byte = (s.half << 4) | nib;
          s.half = null;
          what = `E が下がった。下位 4 ビット 0x${nib.toString(16)} を読み、1 バイト 0x${byte.toString(16).padStart(2, '0')} がそろった: ` + (rs ? data(s, byte) : command(s, byte));
        }
        s.log.push({ i, text: what });
      } else if (prevE === 0 && e === 1) {
        what = 'E を 1 にした。次に 0 に下げた時に、LCD がデータ線を読む';
      } else if (prevVal !== null && val !== prevVal) {
        const parts = [];
        if (((val ^ prevVal) >> c.rs) & 1) parts.push(`RS を ${rs} にした（${rs ? '文字' : 'コマンド'}を送る）`);
        const mask = c.high ? 0xf0 : 0x0f;
        if ((val ^ prevVal) & mask) parts.push(`データ線 D7-D4 に 0x${nib.toString(16)} を出した`);
        what = parts.join('、');
      }
      if (i === k) s.last = what;
      prevE = e;
      prevVal = val;
    }
    return s;
  }

  window.PicViewer.circuits.lcd = {
    title: 'LCD',
    options: [],
    setup(ctx) {
      const { t, cfg, box, U } = ctx;
      const c = settings(cfg);
      const screen = U.el('div', 'lcd');
      for (let r = 0; r < c.rows; r++) {
        const row = U.el('div', 'row');
        for (let col = 0; col < c.cols; col++) row.append(U.el('span', 'ch', ' '));
        screen.append(row);
      }
      const bus = U.el('div', 'bus');
      const lines = [['RS', c.rs], ...(c.rw === null ? [] : [['R/W', c.rw]]), ['E', c.e]];
      (c.high ? [7, 6, 5, 4] : [3, 2, 1, 0]).forEach((b, j) => lines.push([`D${7 - j}`, b]));
      lines.forEach(([label, bit]) => {
        const cell = U.el('span', 'line');
        cell.dataset.bit = String(bit);
        cell.append(U.el('b', '', label), U.el('span', 'bit', '0'), U.el('small', '', `R${c.port}${bit}`));
        bus.append(cell);
      });
      const log = U.el('ol', 'lcdlog');
      box.append(screen, bus, log);
      const pins = (b) => U.pinsWith(t, `R${c.port}${b}`).join('、');
      return {
        sub: `${c.cols} 文字 ${c.rows} 行、4 ビット接続（RS = R${c.port}${c.rs}、E = R${c.port}${c.e}、D4-D7 = R${c.port}${c.high ? '4-7' : '0-3'}${c.rw === null ? '、R/W は GND' : ''}）`,
        assume: `LCD の中身は、${t.source_name} がポートに書いた値を最初から順にたどり、E が 1 から 0 に下がるたびに 4 ビットを読んで HD44780 の命令として解いたもの。RS は ${pins(c.rs)} 番ピン、E は ${pins(c.e)} 番ピン。`,
        regHint: `太い枠のビットが LCD につないだピン（RS、${c.rw === null ? '' : 'R/W、'}E、データ線）。`,
        marks: [c.rs, c.rw, c.e, ...(c.high ? [4, 5, 6, 7] : [0, 1, 2, 3])].filter((b) => b !== null)
          .flatMap((b) => ['PORT', 'LAT'].map((p) => ({ reg: p + c.port, bit: b }))),
        foot: ['LCD の画面は、シミュレータが読んだポートの値から、このページの中で HD44780 の動きをまねて描いたもの（LCD 自体はシミュレータに無い）。書き込みを全部止めて記録していないと、E の変わり目を見落とす。'],
      };
    },
    update(ctx) {
      const { cfg, box, R, U } = ctx;
      const c = settings(cfg);
      const val = U.latchOf(R, c.port);
      if (val === undefined) return { text: `PORT${c.port}（または LAT${c.port}）を registers に入れると LCD が描ける。` };
      const s = replay(ctx, c);
      const screen = box.querySelector('.lcd');
      screen.classList.toggle('off', !s.display);
      const text = [];
      screen.querySelectorAll('.row').forEach((row, r) => {
        let line = '';
        row.querySelectorAll('.ch').forEach((cell, col) => {
          const a = (ROW_START[r] + col) & 0x7f;
          const code = s.ram[a];
          const ch = code >= 0x20 && code < 0x7f ? String.fromCharCode(code) : ' ';
          cell.textContent = s.display ? ch : ' ';
          cell.classList.toggle('cur', s.display && s.cursor && a === s.addr);
          line += ch;
        });
        text.push(line.replace(/\s+$/, ''));
      });
      box.querySelectorAll('.bus .line').forEach((cell) => {
        const one = (val >> Number(cell.dataset.bit)) & 1;
        const bit = cell.querySelector('.bit');
        bit.textContent = String(one);
        bit.classList.toggle('one', one === 1);
      });
      const log = box.querySelector('.lcdlog');
      log.replaceChildren(...s.log.slice(-5).map((e) => U.el('li', e.i === ctx.k ? 'now' : '', e.text)));
      return {
        status: [
          { label: 'つなぎ方', value: s.mode8 ? '8 ビットのつもり' : '4 ビット' },
          { label: '送る中', value: s.half === null ? '-' : `上位 0x${s.half.toString(16)} だけ受けた`, tone: s.half === null ? '' : 'on' },
          { label: '表示', value: s.display ? 'オン' : 'オフ', tone: s.display ? 'on' : '' },
          { label: '次に書く位置', value: `0x${s.addr.toString(16).padStart(2, '0')}` },
        ],
        text: s.last ? `このステップ: ${s.last}。` : 'このステップでは LCD への線は変わらない。',
        probe: { mode: s.mode8 ? 8 : 4, display: s.display, lines: text },
      };
    },
  };
})();
