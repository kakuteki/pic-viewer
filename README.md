# pic-viewer

PIC の C プログラムを MPLAB X のシミュレータで 1 行ずつ動かし、ソース・レジスタ・回路の電流を 1 枚の HTML で見る道具。
レジスタの値はシミュレータで実際に読んだもので、回路の絵（LED、LED の列とスイッチ、文字表示の LCD、H ブリッジのモータードライバ）はその値から描く。
スイッチなどの入力を途中で変える、ポートに書いた瞬間ごとに止める、`__delay_ms` の長い待ちを早送りする、もできる。
標準ライブラリのみ・Python 3.10 以上。

![モーターの例（PIC16F886、正転 30 % の PWM のオフ期間）](docs/images/motor.png)

## 仕組み

```mermaid
flowchart LR
  src["C のソース"] --> xc8["XC8 でコンパイル"]
  xc8 --> probe["下調べ<br>止める行に命令があるか<br>レジスタ名は正しいか"]
  probe --> mdb["MPLAB X のシミュレータ（mdb）<br>止めてレジスタを読む"]
  pic["デバイス定義ファイル（.PIC）<br>ピン番号・ビットの名前"] --> bundle
  mdb --> bundle["記録 bundles/*.json"]
  bundle --> html["HTML 1 枚"]
  cfg["picviewer.json<br>行の説明・回路の仮定"] --> html
```

- `build` はコンパイルからシミュレータでの実行、記録の書き出し、HTML まで通す。
- `render` は記録から HTML だけを作り直す。MPLAB X は要らないので、記録を git に入れておけばどこでも作り直せる。
- できる HTML は外部ファイルもネットも要らない 1 ファイル。ブラウザで開くだけで動く。

## 必要なもの

- MPLAB X IDE（mdb とデバイスパックを使う。v6.35 で確認）
- MPLAB XC8（v3.00 で確認）
- Python 3.10 以上

Windows で確認した。Linux と macOS でも標準の置き場所（`/opt/microchip`、`/Applications/microchip`）を探すが、試していない。
見つからないときは `--mplabx` `--xc8` `--packs` か、環境変数 `PICVIEWER_MPLABX` `PICVIEWER_XC8` `PICVIEWER_PACKS` で場所を指定する。

## 導入

```
git clone https://github.com/kakuteki/pic-viewer.git
cd pic-viewer
python -m pip install --user -e .
picviewer doctor          # 入らない場合は python -m picviewer doctor
```

## 使い方

```
picviewer doctor                          # MPLAB X・XC8・デバイスパックが見つかるか
picviewer build examples/led              # コンパイル、シミュレータで実行、HTML を作る（1 分ほど）
picviewer render examples/motor           # 記録（bundles/*.json）から HTML だけ作り直す
picviewer serve examples --open           # http://127.0.0.1:8765/ に一覧を出して開く
picviewer wave examples/motor -t pic16f1618 --pin RC5 --at 52   # ピンの波形を測る
```

`build` のおもな指定:

| 指定 | 意味 |
| --- | --- |
| `-t ID` | この target だけ作る（何度でも指定できる） |
| `--skip-compile` | コンパイルせず、前回の ELF を使う |
| `--reuse-logs` | `build/` に前回のシミュレータの記録があれば、実行せずに使う。ソースを変えていないときだけ使う |
| `--skip-waves` | 波形を測らない |

`serve` は 127.0.0.1 だけで待ち受ける。`index.html` が無いフォルダでは、中の HTML の一覧を出す。止めるときは Ctrl+C か `picviewer serve --stop`。

## 画面

- 上: マイコンの切り替えと、回路の見せ方の切り替え（LED のつなぎ方、PWM のオン期間とオフ期間など）
- ステップ操作: 最初・前へ・再生・次へ・最後、スライダー、左右キー、スペースキー。URL の `#target=...&step=...` に今の状態が入るので、見せたい場面をそのまま渡せる
- 起きたこと: 実行した行、その行の説明（`notes`。無ければ変わったビットを自動で書く）、変えた入力（`set`）、変わったレジスタ、次の行とアドレス、リセットからの命令サイクル数と時間（早送りのときは縮めた待ちを足した時間）
- ソースコード: 実行した行に色が付く。止まった行をクリックすると、その行を実行した直後へ移る
- 回路: `circuit.type` で選ぶ（下の表）。測った波形があれば、その行で止まっている場面に出る
- レジスタ: ビットごとに表示。直前のステップから変わったビットに枠が付く。`-` はその機種に無いビット

| `circuit.type` | 見せるもの |
| --- | --- |
| `pins`（既定） | TRISx と PORTx / LATx から見た各ピンの向きと値 |
| `led` | 1 本のピンにつないだ LED。ピンの中の Pch / Nch FET、LED の点灯、電流の経路 |
| `hbridge` | RPWM / LPWM / EN で動かす H ブリッジ（IBT-2 など）とモーター。4 個のスイッチ、PWM のオン期間とオフ期間の電流、デューティ、PWM 周波数、平均電圧（一番上の画像） |
| `leds` | 1 つのポートの各ピンにつないだ LED（8 個など）と、入力ピンの押しボタン。点灯と電流の経路、ポートの値、スイッチの状態 |
| `lcd` | 4 ビットでつないだ文字表示の LCD（HD44780 互換）。ポートに書いた値を最初から順にたどって画面を描き、E が下がるたびに読んだ 4 ビットと命令の意味を並べる |

`leds`（スイッチを押したあと、最初の模様 0x0F）:

![leds](docs/images/leds.png)

`lcd`（最後まで送ったところ）:

![lcd](docs/images/lcd.png)

`led`（PIC16F886、RB0 = 1 で点灯）:

![led](docs/images/led.png)

`pins`（PIC16F1618、出力にした RB4 だけが 1）:

![pins](docs/images/pins.png)

## プロジェクトファイル（picviewer.json）

```json
{
  "title": "LED を点ける",
  "output": "led_viewer.html",
  "circuit": { "type": "led", "r_ohm": 330, "vf": 2.0 },
  "targets": [
    {
      "id": "pic16f886",
      "device": "PIC16F886",
      "source": "pic16f886/led.c",
      "fosc_hz": 4000000,
      "registers": ["TRISB", "PORTB"],
      "trace": [{ "run_to": 13 }, { "step": 10 }, { "run_to": 26 }],
      "circuit": { "pin": "RB0" },
      "notes": { "24": "RB0 だけを出力に切り替える。" }
    }
  ]
}
```

| 項目 | 意味 |
| --- | --- |
| `title` / `output` | ページの題と、書き出す HTML（プロジェクトファイルからの相対） |
| `bundles` / `build` | 記録と作業ファイルの置き場所（既定は `bundles/` と `build/`） |
| `circuit` | 全 target に共通の回路の設定。target 側の `circuit` で上書きできる |
| `targets[].id` | 英小文字・数字・`_`・`-` |
| `targets[].device` | `PIC16F886` など。`PIC` は省ける |
| `targets[].source` | C のソース。1 ファイル |
| `targets[].fosc_hz` | 発振周波数。命令サイクル数を時間に直すのと、PWM 周波数の計算に使う |
| `targets[].registers` | 読む 8 ビットの SFR。16 ビットの組は `CCPR1L` と `CCPR1H` のように分ける |
| `targets[].trace` | 止め方の計画（下） |
| `targets[].notes` | 行番号ごとの説明 |
| `targets[].waves` | 測る波形 `{"pin": "RC5", "at": 52, "samples": 900}`。`at` 行で止めてから `samples` 命令、1 命令ずつピンを読む |
| `targets[].counters` | 数え続けるレジスタ（変化の印を付けない）。省くと `TMR2` `T2TMR` などを自動で選ぶ |
| `targets[].xc8_args` / `wait_ms` | XC8 に足す引数、`run_to` で止まるのを待つ上限（ミリ秒、既定 600000） |
| `fast_forward` | `10`、`100`、`1000` のどれか。`__delay_ms` の待ちをその分の 1 にしてシミュレータを動かす（下）。全体にも target ごとにも書ける |

### 止め方の計画（trace）

| 書き方 | 動き |
| --- | --- |
| `{"run_to": 行}` | そこまで走らせて止める。最初の止め方は必ずこれ（多くは main の最初の行） |
| `{"step": N}` | 1 行ずつ N 回進める。各行で止めてレジスタを読む |
| `{"run_to": 行, "show": 行}` | 次にその行に来るまで走らせて止める。`show` はその間に実行した行のうち、画面で「実行した行」として見せるもの |
| `{"set": {"RB0": 1}, "note": "スイッチを押す"}` | 入力ピンを変える（`0`、`1`、`"2.5V"` など）。止まらずに、次に止まった場面の説明に出る。最初の `run_to` の前にも書ける |
| `{"until_write": "PORTC", "count": N}` | そのレジスタに書くたびに止める（データブレークポイント）。N 回止める |
| `{"until_write": "PORTB", "count": N, "until": 行}` | 同じく書くたびに止めるが、`until` の行に来たらそこで終える。書く回数が分からないときに、N を多めにして使う |

止める行に命令が無い（空行、宣言だけの行）と止まれない。`build` は本番の前に下調べをして、そういう行、名前の違うレジスタ、無いピン名を先に知らせる。

書き込みで止めたときは、XC8 が書き出すマップファイル（`.cmf` の `%LINETAB`）でアドレスを行に直し、書いた命令の行を「実行した行」として見せる。

入力待ちのループ（`while (PORTBbits.RB0 == 0) { }` など）を `step` で抜けようとしない。条件が変わらない限り同じ行から出られず、`step` が返らない。先に `set` で入力を入れ、`run_to` か `until_write` で進める。

### 早送り（fast_forward）

シミュレータは `__delay_ms` の待ちにも実時間がかかる（モーターの例では 1 秒の待ちに約 1 分）。`fast_forward` を書くと、シミュレータ用のビルドでだけ `__delay_ms(n)` を n/F ミリ秒の待ちに置き換え、縮めた時間をプログラムの中の変数で数える。

- 置き換えは同じ行の中で行うので、行番号はずれない。元のソースは書き換えない（`build/<id>/sim/` に写しを作る）
- 画面の時間は、命令サイクル数から求めた時間に、縮めた時間を足したもの。数える命令の分だけ、わずかに長く出る
- 置き換えるのはプロジェクトの C のソースの中の `__delay_ms` だけ。`#include` した別のファイルの中の待ちはそのまま

### 回路の設定

| `type` | 項目 |
| --- | --- |
| `led` | `pin`（例 `RB0`）、`r_ohm`（既定 330）、`vf`（既定 2.0）、`io_max_ma` と `datasheet`（ピンの定格の注記） |
| `hbridge` | `rpwm` と `lpwm`（`{"pin": "RC5", "ccp": 1, "pps": {"reg": "RC5PPS", "code": 12}}`。`pps` は PPS のある機種だけ）、`en`（`{"pin": "RC4"}`）、`vm`（電池の電圧）、`driver` と `motor`（名前）、`r_ohm`（巻線抵抗。分かれば電流の目安を出す） |
| `pins` | `pins`（出すピンの並び。省くと読んだ TRISx の全ビット） |
| `leds` | `port`（例 `"C"`）、`bits`（並べるビット。省くとそのポートの全ビット）、`active`（`"high"` は 1 で点灯、`"low"` は 0 で点灯）、`r_ohm`、`switch`（`{"pin": "RB0", "active": "high"}`。押すと 1 になるスイッチ） |
| `lcd` | `port`（既定 `"B"`）、`rs` / `rw` / `e`（そのポートのビット番号、既定 0 / 1 / 2）、`data`（`"high"` は D4-D7 がビット 4-7、`"low"` はビット 0-3）、`rows` と `cols`（既定 2 と 16）。ポートへの書き込みを全部 `until_write` で止めて記録しておく |

`hbridge` は CCP の 2 つの型を見分ける。`CCPxCON` に FMT ビットがあれば新しい型（EN・FMT・MODE、例 PIC16F1618）、無ければ古い型（DCxB・CCPxM、例 PIC16F886）。Timer2 は `T2PR` を読んでいれば新しい型、`PR2` なら古い型として周期を計算する。

## 例

| フォルダ | 中身 |
| --- | --- |
| [examples/led](examples/led) | RB0（PIC16F1618 では RB4）を出力にして LED を点ける。PIC16F886 と PIC16F1618 |
| [examples/motor](examples/motor) | DC モーターを H ブリッジで正転・逆転・ブレーキ・停止し、PWM で速度を変える。実物の回路の設計は [hardware](examples/motor/hardware) |
| [examples/switch_leds](examples/switch_leds) | スイッチを押すと、PORTC の LED 8 個の模様を 0.5 秒ごとに変える。`set` でスイッチを押し、`until_write` で模様の変わり目を全部拾い、早送りで待ちを縮める。PIC16F886 |
| [examples/lcd](examples/lcd) | 文字表示の LCD を 4 ビットでつなぎ、HELLO と PIC を出す。PORTB への 104 回の書き込みを全部止めて記録し、LCD の画面を組み立てる。PIC16F886 |

記録（`bundles/*.json`）と HTML も入れてあるので、MPLAB X が無くても HTML を開けば見られる。

## シミュレータについて分かっていること

実際に動かして確かめたこと（MPLAB X v6.35 の mdb）:

- ストップウォッチは `Run` と `Continue` のたびに 0 から数え直し、`Step` では続けて数える。picviewer はこれを足し合わせて、リセットからの命令サイクル数にする
- ブレークポイントとデータブレークポイント（`Watch`）の番号は 0、1、2 と共通で増え、消しても使い回さない
- データブレークポイントは、書いた命令の直後で止まる。報告される行は次の文の行なので、1 語前（PIC18 は 2 バイト前）のアドレスの行を書いた行とする
- `write pin RB0 high` や `write pin AN0 2.5V` で入力ピンを変えられる。無いピン名を書くと mdb がそこで終わってしまうので、picviewer はデバイス定義ファイルで先に確かめる
- `Watch` は無いレジスタ名でも黙って受け付ける。これも picviewer が先に確かめる
- 走っている最中に `Step` を送ると mdb が終わってしまう
- 行番号の無いところ（ライブラリの中など）で止まると行が取れない。その行は `step` で通らず `run_to` で飛ばす
- 速さは中身によって大きく違う。何もしない待ちのループでは 1 秒に数百万命令進んだが、PWM を動かしているモーターの例では、1 秒の待ちに約 1 分かかった

## 限界

- 値は止めた瞬間のもの。PWM のように止めている間に動くものは `waves` で別に測る
- 回路の絵は、ピンの値から決めた模型。電流は仮定（抵抗値、順方向電圧、巻線抵抗）から計算した目安。`lcd` の画面も、ポートの値から HD44780 の動きをまねて描いたもので、LCD 自体はシミュレータに無い
- `lcd` は、ポートへの書き込みを全部止めて記録していないと、E の変わり目を見落とす
- シミュレータの周辺機能の再現度は機種による
- 読めるのは 8 ビットの SFR だけ。C の変数は読まない（早送りの数を数える変数だけは読む）

## 試験

```
python -m unittest discover -s tests          # MPLAB X も XC8 も要らない。CI でも走る
python scripts/browser_check.py               # 例のページをブラウザで操作して確かめる（agent-browser が要る）
```

`tests` には、入れてある例の HTML が `render` の結果と一致するかの確認も入っている。ひな形や記録を変えたら `picviewer render` で作り直す。

## ライセンス

MIT。デバイス定義ファイルとデータシートは Microchip のもので、このリポジトリには入っていない（手元の MPLAB X から読む）。
