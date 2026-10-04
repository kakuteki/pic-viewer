// board_test.c  基板の点検: 4 桁の 7 セグを全部点けたままにして、ブザーを 1 秒ごとに 0.1 秒鳴らす
// 計画は picviewer init --seg7-board C:RA0,RA1,RA2,RA3 が作ったもの（基板の 7 セグの配線を渡す）
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: RC0-RC7 = 区画 a-g, dp（4 桁で共通。カソード共通、1 で点灯）
//       RA0-RA3 = 桁（左から。0 にした桁が点く）、RB0 = 圧電ブザー
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000
#define BZ PORTBbits.RB0

void main(void)
{
    OSCCON = 0b01100000;        // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;        // アナログ入力を使わない
    ANSELH = 0b00000000;
    TRISA  = 0b00000000;        // RA0-RA3 は桁
    TRISB  = 0b00000000;        // RB0 はブザー
    TRISC  = 0b00000000;        // RC0-RC7 は区画
    PORTC  = 0xFF;              // 区画を全部点ける
    PORTA  = 0b00000000;        // 4 桁とも点ける（切り替えない）
    PORTB  = 0;
    while (1) {
        for (unsigned char i = 0; i < 100; i++) {   // 1 kHz を 0.1 秒
            BZ = 1;
            __delay_us(500);
            BZ = 0;
            __delay_us(500);
        }
        __delay_ms(900);
    }
}
