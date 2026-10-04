// stopwatch.c  スイッチを押すたびに、計る・止めるを切り替えるストップウォッチ。経った秒（0〜9）を 7 セグメント LED に出す
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: RB0 = スイッチ（押すと 0。内部プルアップ）
//       RC0-RC6 = 7 セグメント LED の a-g（カソード共通、1 で点灯）
// 時間はタイマー 1 の割り込みで数える（1 MHz の命令クロックを 1/8 にして 62500 数えると 0.5 秒）
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000

const unsigned char SHAPE[10] = {           // 0-9 の形（ビット 0 が a、ビット 6 が g）
    0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F
};

volatile unsigned char halves = 0;          // 計っている間に過ぎた 0.5 秒の数
volatile unsigned char running = 0;         // 1 なら計っている

void __interrupt() tick(void)
{
    if (PIR1bits.TMR1IF) {                  // タイマー 1 があふれた（0.5 秒たった）
        PIR1bits.TMR1IF = 0;
        TMR1H = 0x0B;                       // 65536 - 62500 = 3036 = 0x0BDC から数え直す
        TMR1L = 0xDC;
        if (running) {
            halves++;
        }
    }
}

void main(void)
{
    unsigned char shown = 0xFF, sec;

    OSCCON = 0b01100000;                    // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;
    ANSELH = 0b00000000;                    // RB0 をデジタル入力に
    TRISB  = 0b00000001;                    // RB0 はスイッチ
    TRISC  = 0b00000000;                    // PORTC は 7 セグメント LED
    OPTION_REGbits.nRBPU = 0;               // RB0 の内部プルアップを使う
    WPUB   = 0b00000001;
    T1CON  = 0b00110001;                    // プリスケーラ 1:8、タイマー 1 を動かす
    TMR1H  = 0x0B;
    TMR1L  = 0xDC;
    PIE1bits.TMR1IE = 1;                    // タイマー 1 の割り込みを使う
    INTCONbits.PEIE = 1;
    INTCONbits.GIE = 1;                     // 割り込みを受け付ける

    while (1) {
        if (!PORTBbits.RB0) {               // スイッチを押した
            running = !running;             // 計る・止めるを切り替える
            __delay_ms(20);
            while (!PORTBbits.RB0) {        // 離されるまで待つ
            }
            __delay_ms(20);
        }
        sec = (unsigned char)((halves / 2) % 10);
        if (sec != shown) {                 // 秒が変わった時だけ書き直す
            shown = sec;
            PORTC = SHAPE[sec];
        }
    }
}
