// mux.c  4 桁の 7 セグメント LED を 1 桁ずつ順に点けて（ダイナミック点灯）、1234 から 0.5 秒ごとに 1 ずつ数える
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: RC0-RC7 = 区画 a-g, dp（4 桁で共通。カソード共通、1 で点灯）
//       RA0-RA3 = 桁（左から。0 にした桁だけが点く）
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000

const unsigned char SHAPE[10] = {           // 0-9 の形（ビット 0 が a、ビット 6 が g）
    0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F
};

void main(void)
{
    unsigned int count = 1234;
    unsigned char digit[4], i, frame;

    OSCCON = 0b01100000;        // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;        // アナログ入力を使わない
    ANSELH = 0b00000000;
    TRISA  = 0b00000000;        // RA0-RA3 は桁の切り替え
    TRISC  = 0b00000000;        // PORTC は区画
    PORTA  = 0b00001111;        // 全部の桁を消しておく
    PORTC  = 0b00000000;

    while (1) {
        digit[0] = (unsigned char)(count / 1000);   // 左の桁から
        digit[1] = (unsigned char)(count / 100 % 10);
        digit[2] = (unsigned char)(count / 10 % 10);
        digit[3] = (unsigned char)(count % 10);
        for (frame = 0; frame < 25; frame++) {        // 1 枠 20 ms を 25 回 = 0.5 秒
            for (i = 0; i < 4; i++) {
                PORTC = SHAPE[digit[i]];              // 区画を出してから
                PORTA = (unsigned char)(~(1u << i) & 0x0F);   // その桁だけ 0 にして点ける
                __delay_ms(5);
                PORTA = 0b00001111;                   // 全部消して次の桁へ
            }
        }
        count = (count + 1) % 10000;                  // 0.5 秒たったので 1 つ進める
    }
}
