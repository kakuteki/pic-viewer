// drive.c  2 入力のモータードライバ（IN1・IN2）で DC モーターを回す
// SW0 を押している間は正転、SW1 は逆転、SW2 は半分の速さで正転（IN1 を 10 ms ごとに切り替える PWM）
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: RC0 = IN1、RC1 = IN2、RA0-RA2 = スイッチ（押すと 0。プルアップ抵抗つき）
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000
#define IN1 PORTCbits.RC0
#define IN2 PORTCbits.RC1
#define SW0 PORTAbits.RA0
#define SW1 PORTAbits.RA1
#define SW2 PORTAbits.RA2

void main(void)
{
    OSCCON = 0b01100000;        // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;        // アナログ入力を使わない
    ANSELH = 0b00000000;
    TRISA  = 0b11111111;        // スイッチは入力
    TRISC  = 0b11111100;        // RC0、RC1 は出力（IN1、IN2）
    PORTC  = 0b00000000;        // 止めておく

    while (1) {
        if (SW0 == 0) {             // 正転
            IN1 = 1;
            IN2 = 0;
        } else if (SW1 == 0) {      // 逆転
            IN1 = 0;
            IN2 = 1;
        } else if (SW2 == 0) {      // 半分の速さ: 5 ms 回して 5 ms 空転
            IN2 = 0;
            IN1 = 1;
            __delay_ms(5);
            IN1 = 0;
            __delay_ms(5);
        } else {                    // 離したら止める
            IN1 = 0;
            IN2 = 0;
        }
    }
}
