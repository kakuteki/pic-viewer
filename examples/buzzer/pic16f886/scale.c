// scale.c  圧電ブザーでド・レ・ミを 0.5 秒ずつ鳴らし、0.5 秒休む
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: RB0 = 圧電ブザー（もう片方は GND）
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000
#define BZ PORTBbits.RB0

void main(void)
{
    unsigned char i;

    OSCCON = 0b01100000;        // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;        // アナログ入力を使わない
    ANSELH = 0b00000000;
    TRISB  = 0b11111110;        // RB0 だけ出力（ブザー）
    PORTB  = 0b00000000;

    while (1) {
        for (i = 0; i < 131; i++) {     // ド（262 Hz）: 1.908 ms ずつ 1 と 0
            BZ = 1;
            __delay_us(1908);
            BZ = 0;
            __delay_us(1908);
        }
        for (i = 0; i < 147; i++) {     // レ（294 Hz）
            BZ = 1;
            __delay_us(1701);
            BZ = 0;
            __delay_us(1701);
        }
        for (i = 0; i < 165; i++) {     // ミ（330 Hz）
            BZ = 1;
            __delay_us(1515);
            BZ = 0;
            __delay_us(1515);
        }
        __delay_ms(500);                // 休み
    }
}
