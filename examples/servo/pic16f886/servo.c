// servo.c  サーボモーターを 3 つの角度へ 1 秒ずつ動かす（20 ms ごとに幅 1.0 / 1.5 / 2.0 ms のパルス）
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: RC0 = サーボの信号線（サーボの電源と GND は別につなぐ）
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000
#define SERVO PORTCbits.RC0

void main(void)
{
    unsigned char i;

    OSCCON = 0b01100000;        // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;        // アナログ入力を使わない
    ANSELH = 0b00000000;
    TRISC  = 0b11111110;        // RC0 だけ出力（サーボの信号）
    PORTC  = 0b00000000;

    while (1) {
        for (i = 0; i < 50; i++) {      // 幅 1.0 ms: 片側へ
            SERVO = 1;
            __delay_us(1000);
            SERVO = 0;
            __delay_us(19000);
        }
        for (i = 0; i < 50; i++) {      // 幅 1.5 ms: まん中
            SERVO = 1;
            __delay_us(1500);
            SERVO = 0;
            __delay_us(18500);
        }
        for (i = 0; i < 50; i++) {      // 幅 2.0 ms: 反対側へ
            SERVO = 1;
            __delay_us(2000);
            SERVO = 0;
            __delay_us(18000);
        }
    }
}
