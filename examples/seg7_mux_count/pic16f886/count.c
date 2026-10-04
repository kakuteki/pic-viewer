// count.c  2 桁の 7 セグメント LED で 00 から 99 まで数える（ダイナミック点灯）。計画は picviewer init が作ったもの
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: RC0-RC6 = 区画 a-g（2 桁で共通。カソード共通、1 で点灯）
//       RA0 = 十の位、RA1 = 一の位（0 にした桁だけが点く）
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000

const unsigned char SHAPE[10] = {           // 0-9 の形（ビット 0 が a、ビット 6 が g）
    0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F
};

void count_up(void);

void main(void)
{
    OSCCON = 0b01100000;        // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;        // アナログ入力を使わない
    ANSELH = 0b00000000;
    TRISA  = 0b11111100;        // RA0、RA1 は桁の切り替え
    TRISC  = 0b00000000;        // RC0-RC6 は区画
    PORTA  = 0b00000011;        // 両方の桁を消しておく
    PORTC  = 0;
    while (1) {
        count_up();
    }
}

// 十の位 t と一の位 u を 5 ms ずつ交互に点ける。25 回（250 ms）点けたら一の位を 1 進める
void count_up(void)
{
    for (unsigned char t = 0; t < 10; t++) {
        for (unsigned char u = 0; u < 10; u++) {
            for (unsigned char k = 0; k < 25; k++) {
                PORTC = SHAPE[t];
                PORTAbits.RA0 = 0;      // 十の位を点ける
                __delay_ms(5);
                PORTAbits.RA0 = 1;
                PORTC = SHAPE[u];
                PORTAbits.RA1 = 0;      // 一の位を点ける
                __delay_ms(5);
                PORTAbits.RA1 = 1;
            }
        }
    }
}
