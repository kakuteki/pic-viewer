// volt.c  AN0 の電圧を 0.01 V きざみで測り、文字表示の LCD に「1.25V」のように出す電圧計
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: AN0 (RA0) = 可変抵抗のつまみ（両端は 5 V と GND）
//       LCD  RC0-RC3 = D4-D7、RC4 = RS、RC5 = E（R/W は GND）
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000
#define RS PORTCbits.RC4
#define E  PORTCbits.RC5

// ---- LCD（4 ビット接続。calc.c と同じ）
void lcd_nibble(unsigned char n)
{
    PORTC = (PORTC & 0xF0) | (n & 0x0F);    // D4-D7 に 4 ビットを出す
    E = 1;                                  // E を 1 から 0 に下げた時に LCD が読む
    E = 0;
}

void lcd_byte(unsigned char rs, unsigned char b)
{
    RS = rs;                                // 0 = 命令、1 = 文字
    lcd_nibble(b >> 4);
    lcd_nibble(b);
    __delay_us(50);
}

void lcd_cmd(unsigned char c)
{
    lcd_byte(0, c);
    if (c < 4) {
        __delay_ms(2);
    }
}

void lcd_char(char ch)
{
    lcd_byte(1, (unsigned char)ch);
}

void lcd_init(void)
{
    __delay_ms(20);
    RS = 0;
    lcd_nibble(0x3);
    __delay_ms(5);
    lcd_nibble(0x3);
    __delay_us(150);
    lcd_nibble(0x3);
    __delay_us(50);
    lcd_nibble(0x2);                        // 4 ビット接続に切り替える
    __delay_us(50);
    lcd_cmd(0x28);                          // 4 ビット、2 行
    lcd_cmd(0x0C);                          // 表示オン、カーソルなし
    lcd_cmd(0x06);                          // 書くたびに右へ
    lcd_cmd(0x01);                          // 画面を消す
}

// ---- AD 変換
unsigned int adc_read(void)
{
    ADCON0bits.GO_nDONE = 1;                // 変換を始める
    while (ADCON0bits.GO_nDONE) {           // 終わるまで待つ（約 20 us）
    }
    return ((unsigned int)ADRESH << 8) | ADRESL;    // 右詰めの 10 ビット（0〜1023）
}

void main(void)
{
    unsigned int raw, cv, shown = 0xFFFF;

    OSCCON = 0b01100000;                    // 内部発振器を 4 MHz に
    ANSEL  = 0b00000001;                    // AN0 だけアナログ入力
    ANSELH = 0b00000000;
    TRISA  = 0b00000001;                    // RA0 (AN0) は入力
    TRISC  = 0b00000000;                    // PORTC は LCD
    ADCON1 = 0b10000000;                    // 結果は右詰め、基準は電源と GND
    ADCON0 = 0b01000001;                    // 変換クロック Fosc/8、AN0、AD 変換器を動かす
    PORTC  = 0b00000000;
    lcd_init();

    while (1) {
        raw = adc_read();
        cv = (unsigned int)(((unsigned long)raw * 500 + 511) / 1023);   // 0.01 V 単位に直す（四捨五入）
        if (cv != shown) {                  // 変わった時だけ書き直す
            shown = cv;
            lcd_cmd(0x80);                  // 1 行目の先頭へ
            lcd_char((char)('0' + cv / 100));
            lcd_char('.');
            lcd_char((char)('0' + cv / 10 % 10));
            lcd_char((char)('0' + cv % 10));
            lcd_char('V');
        }
        __delay_ms(100);                    // 1 秒に 10 回測る
    }
}
