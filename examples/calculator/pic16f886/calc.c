// calc.c  4 行 4 列のキーで「数 演算 数 =」と打つと、答えを文字表示の LCD に出す電卓（整数）
// PIC16F886  内部クロック 4 MHz  XC8
// 配線: キー  RB0-RB3 = 行（出力。調べる行だけ 0 にする）
//             RB4-RB7 = 列（入力。内部プルアップで、押していない時は 1）
//       LCD   RC0-RC3 = D4-D7、RC4 = RS、RC5 = E（R/W は GND）
// キーの並び  1 2 3 +
//             4 5 6 -
//             7 8 9 *
//             C 0 = /
#include <xc.h>

#pragma config FOSC = INTRC_NOCLKOUT, WDTE = OFF, PWRTE = ON, MCLRE = OFF
#pragma config CP = OFF, CPD = OFF, BOREN = OFF, IESO = OFF, FCMEN = OFF, LVP = OFF
#pragma config BOR4V = BOR40V, WRT = OFF

#define _XTAL_FREQ 4000000
#define RS PORTCbits.RC4
#define E  PORTCbits.RC5

const char KEYS[4][4] = {
    {'1', '2', '3', '+'},
    {'4', '5', '6', '-'},
    {'7', '8', '9', '*'},
    {'C', '0', '=', '/'},
};

// ---- LCD（4 ビット接続）
void lcd_nibble(unsigned char n)
{
    PORTC = (PORTC & 0xF0) | (n & 0x0F);    // D4-D7 に 4 ビットを出す
    E = 1;                                  // E を 1 から 0 に下げた時に LCD が読む
    E = 0;
}

void lcd_byte(unsigned char rs, unsigned char b)
{
    RS = rs;                                // 0 = 命令、1 = 文字
    lcd_nibble(b >> 4);                     // 上位 4 ビット
    lcd_nibble(b);                          // 下位 4 ビット
    __delay_us(50);
}

void lcd_cmd(unsigned char c)
{
    lcd_byte(0, c);
    if (c < 4) {
        __delay_ms(2);                      // 画面を消す命令は時間がかかる
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
    lcd_nibble(0x3);                        // 8 ビットのつもりの命令を 3 回
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

void lcd_number(long v)
{
    char buf[11];
    unsigned char i = 0;
    if (v < 0) {
        lcd_char('-');
        v = -v;
    }
    do {
        buf[i++] = (char)('0' + v % 10);    // 下の桁から
        v /= 10;
    } while (v > 0);
    while (i > 0) {
        lcd_char(buf[--i]);                 // 上の桁から書く
    }
}

// ---- キー
char scan(void)                             // 今押しているキー。無ければ 0
{
    unsigned char r, c, cols;
    for (r = 0; r < 4; r++) {
        PORTB = (unsigned char)~(1u << r);  // 行 r だけを 0 にする
        NOP();
        cols = PORTB >> 4;                  // 列を読む（押したキーの列だけ 0）
        for (c = 0; c < 4; c++) {
            if (!(cols & (1u << c))) {
                return KEYS[r][c];
            }
        }
    }
    return 0;
}

char get_key(void)                          // キーが押されて離されるのを待つ
{
    char k;
    while ((k = scan()) == 0) {             // 押されるまで待つ
    }
    __delay_ms(20);                         // チャタリングが収まるのを待つ
    while (scan() != 0) {                   // 離されるまで待つ
    }
    __delay_ms(20);
    return k;
}

// ---- 電卓
void main(void)
{
    long a = 0, b = 0;
    char op = 0, k;

    OSCCON = 0b01100000;                    // 内部発振器を 4 MHz に
    ANSEL  = 0b00000000;                    // アナログ入力を使わない
    ANSELH = 0b00000000;
    TRISB  = 0b11110000;                    // RB0-RB3 は行（出力）、RB4-RB7 は列（入力）
    TRISC  = 0b00000000;                    // PORTC は LCD
    OPTION_REGbits.nRBPU = 0;               // PORTB の内部プルアップを使う
    WPUB   = 0b11110000;                    // 列の 4 本だけ
    PORTC  = 0b00000000;
    lcd_init();

    while (1) {
        k = get_key();
        if (k >= '0' && k <= '9') {
            if (op == 0) {
                a = a * 10 + (k - '0');     // 1 つ目の数に桁を足す
            } else {
                b = b * 10 + (k - '0');     // 2 つ目の数に桁を足す
            }
            lcd_char(k);
        } else if (k == 'C') {
            a = 0;                          // 全部やり直す
            b = 0;
            op = 0;
            lcd_cmd(0x01);
        } else if (k == '=') {
            lcd_cmd(0xC0);                  // 2 行目の先頭へ
            lcd_char('=');
            if (op == '/' && b == 0) {
                lcd_char('E');              // 0 では割れない
            } else {
                if (op == '+') a = a + b;
                if (op == '-') a = a - b;
                if (op == '*') a = a * b;
                if (op == '/') a = a / b;
                lcd_number(a);              // 答えを出す（続けて演算もできる）
            }
            b = 0;
            op = 0;
        } else if (op == 0) {
            op = k;                         // 演算を覚える
            lcd_char(k);
        }
    }
}
