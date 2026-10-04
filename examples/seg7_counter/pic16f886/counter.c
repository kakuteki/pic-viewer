// counter.c  スイッチを押している間、7 セグメント LED の数字を 0.5 秒ごとに 0 から 9 まで進める
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: RB0 = スイッチ（押すと 0。プルアップ抵抗つきで、押すと GND につながる）
//       RC0-RC6 = 7 セグメント LED の a-g（カソード共通、1 で点灯）
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000
#define SW !PORTBbits.RB0           // 押すと 0 になるので ! で裏返す

const unsigned char SHAPE[10] = {   // 0-9 の形（ビット 0 が a、ビット 6 が g）
    0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F
};

void main(void)
{
    unsigned char n = 0;

    OSCCON = 0b01100000;        // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;        // アナログ入力を使わない
    ANSELH = 0b00000000;        // RB0 をデジタル入力に
    TRISB  = 0b00000001;        // RB0 はスイッチの入力
    TRISC  = 0b00000000;        // PORTC は全部 7 セグメント LED の出力
    PORTC  = SHAPE[n];          // 0 を出す

    while (1) {
        while (!SW) {           // 押されるまで待つ
        }
        __delay_ms(500);
        n = (n == 9) ? 0 : n + 1;
        PORTC = SHAPE[n];       // 次の数字を出す
    }
}
