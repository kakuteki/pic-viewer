// lcd.c  文字表示の LCD（HD44780 互換、16 文字 2 行）に 4 ビットでつないで、2 行の文字を出す
// PIC16F886  内部クロック 8 MHz  XC8
// 配線: RB0 = RS、RB1 = R/W（0 のまま）、RB2 = E、RB4-RB7 = D4-D7
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 8000000
#define LCD_RS PORTBbits.RB0
#define LCD_E  PORTBbits.RB2

// 上位 4 ビットを D4-D7 に出して、E を 1 から 0 にする（LCD は E が下がる時に読む）
void lcd_nibble(unsigned char value)
{
    PORTB = (PORTB & 0x0F) | (value & 0xF0);
    LCD_E = 1;
    LCD_E = 0;
}

// 1 バイトを上位、下位の順に 4 ビットずつ送る（rs が 0 ならコマンド、1 なら文字）
void lcd_byte(unsigned char rs, unsigned char value)
{
    if (rs) {
        LCD_RS = 1;
    } else {
        LCD_RS = 0;
    }
    lcd_nibble(value);
    lcd_nibble((unsigned char)(value << 4));
    __delay_ms(2);
}

void lcd_init(void)
{
    __delay_ms(50);             // 電源が入ってから待つ
    lcd_nibble(0x30);           // 8 ビットのつもりで 3 回送る
    __delay_ms(5);
    lcd_nibble(0x30);
    __delay_ms(1);
    lcd_nibble(0x30);
    __delay_ms(1);
    lcd_nibble(0x20);           // 4 ビットに切り替える
    __delay_ms(1);
    lcd_byte(0, 0x28);          // 4 ビット、2 行
    lcd_byte(0, 0x0C);          // 表示オン、カーソルなし
    lcd_byte(0, 0x01);          // 画面を消す
    lcd_byte(0, 0x06);          // 書くたびに右へ進む
}

void lcd_text(const char *s)
{
    while (*s) {
        lcd_byte(1, (unsigned char)*s);
        s++;
    }
}

void main(void)
{
    OSCCON = 0b01110000;        // 内部発振器を 8 MHz に
    ANSEL  = 0b00000000;
    ANSELH = 0b00000000;
    TRISB  = 0b00000000;        // PORTB は全部 LCD への出力
    PORTB  = 0b00000000;

    lcd_init();
    lcd_text("HELLO");
    lcd_byte(0, 0xC0);          // 2 行目の先頭へ
    lcd_text("PIC");
    while (1) {
    }
}
