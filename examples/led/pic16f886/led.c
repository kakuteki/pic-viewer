// led.c  RB0 を出力にして 1 を出す（LED を点ける）
// PIC16F886  内部クロック 4 MHz  XC8
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000

void main(void)
{
    OSCCON = 0b01100000;        // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;        // AN0-AN4 をデジタルに
    ANSELH = 0b00000000;        // AN8-AN13 をデジタルに

    TRISA = 0b11111111;         // まず全ピンを入力に
    TRISB = 0b11111111;
    TRISC = 0b11111111;
    PORTA = 0b00000000;         // 出力データを 0 に
    PORTB = 0b00000000;
    PORTC = 0b00000000;

    TRISBbits.TRISB0 = 0;       // RB0 だけ出力に
    while (1) {
        PORTBbits.RB0 = 1;      // LED を点ける
    }
}
