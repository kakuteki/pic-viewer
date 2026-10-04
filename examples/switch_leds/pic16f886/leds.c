// leds.c  スイッチを押すと、PORTC の LED 8 個の模様を 0.5 秒ごとに変える
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: RB0 = スイッチ（押すと 1。プルダウン抵抗つき）、RC0-RC7 = LED（1 で点灯）
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000

const unsigned char PATTERN[4] = { 0x0F, 0xF0, 0xAA, 0x55 };

void main(void)
{
    unsigned char i;

    OSCCON = 0b01100000;        // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;        // アナログ入力を使わない
    ANSELH = 0b00000000;
    TRISB  = 0b11111111;        // RB0 はスイッチの入力
    TRISC  = 0b00000000;        // PORTC は全部 LED の出力
    PORTC  = 0b00000000;        // LED を全部消す

    while (PORTBbits.RB0 == 0) {    // スイッチが押されるまで待つ
    }

    while (1) {
        for (i = 0; i < 4; i++) {
            PORTC = PATTERN[i];     // 模様を出す
            __delay_ms(500);
        }
        PORTC = 0b00000001;         // 1 個だけ点けて
        for (i = 0; i < 7; i++) {
            __delay_ms(500);
            PORTC = PORTC << 1;     // 左へ送る
        }
        __delay_ms(500);
    }
}
