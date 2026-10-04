// buttons.c  スイッチを押している間、そのスイッチと同じ番号の LED を点ける。SW3 を押している間は RC7 も点ける
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: RA0-RA3 = スイッチ SW0-SW3（押すと 0。プルアップ抵抗つきで、押すと GND につながる）
//       RC0-RC7 = LED（1 で点灯）
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000
#define SW0 !PORTAbits.RA0          // 押すと 0 になるので ! で裏返す
#define SW1 !PORTAbits.RA1
#define SW2 !PORTAbits.RA2
#define SW3 !PORTAbits.RA3

void main(void)
{
    unsigned char held;

    OSCCON = 0b01100000;        // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;        // RA0-RA3 をデジタル入力に
    ANSELH = 0b00000000;
    TRISA  = 0b00001111;        // RA0-RA3 はスイッチの入力
    TRISC  = 0b00000000;        // PORTC は全部 LED の出力
    PORTC  = 0b00000000;        // LED を全部消す

    while (1) {
        held = ~PORTA & 0x0F;   // 押しているスイッチのビットだけ 1 になる
        if (SW3) {
            held = held | 0x80; // SW3 を押している間は RC7 も点ける
        }
        PORTC = held;           // 押しているスイッチの LED を点ける
    }
}
