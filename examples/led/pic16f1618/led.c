// led.c  RB4 を出力にして 1 を出す（LED を点ける）
// PIC16F1618 には RB0 が無いので RB4 を使う。内部クロック 4 MHz  XC8
#include <xc.h>

#pragma config FOSC = INTOSC, WDTE = OFF, PWRTE = OFF, MCLRE = ON
#pragma config CP = OFF, BOREN = ON, CLKOUTEN = OFF, LVP = OFF, PLLEN = OFF

#define _XTAL_FREQ 4000000

void main(void)
{
    OSCCON = 0b01101000;        // 内部発振器を 4 MHz に（IRCF = 1101）
    ANSELA = 0b00000000;        // PORTA をデジタルに
    ANSELB = 0b00000000;        // PORTB をデジタルに
    ANSELC = 0b00000000;        // PORTC をデジタルに

    TRISA = 0b11111111;         // まず全ピンを入力に
    TRISB = 0b11111111;
    TRISC = 0b11111111;
    LATA  = 0b00000000;         // 出力ラッチを 0 に
    LATB  = 0b00000000;
    LATC  = 0b00000000;

    TRISBbits.TRISB4 = 0;       // RB4 だけ出力に
    while (1) {
        LATBbits.LATB4 = 1;     // LED を点ける
    }
}
