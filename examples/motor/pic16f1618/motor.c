//***************************************************************
//	motor.c  DC モーターを H ブリッジ (IBT-2 / BTS7960) で動かす
//	PIC16F1618  内部クロック 32MHz  XC8
//***************************************************************
#include <xc.h>
#pragma config FOSC = INTOSC, WDTE = OFF, PWRTE = OFF, MCLRE = ON
#pragma config CP = OFF, BOREN = ON, CLKOUTEN = OFF, LVP = OFF, PLLEN = OFF

// 定数の宣言
#define _XTAL_FREQ	32000000	//32MHz for __delay_ms()

// IBT-2 モジュールとの接続
//	RC5 (CCP1) -> RPWM   正転の PWM
//	RC3 (CCP2) -> LPWM   逆転の PWM
//	RC4        -> R_EN と L_EN   1 で有効、0 で全オフ (惰性で停止)
#define MOTOR_EN	LATCbits.LATC4
#define DUTY_MAX	400			// 4 x (PR2 + 1) = 100%

//-----------------------------------------------------------
//	Main関数
//-----------------------------------------------------------
void main(void)
{
	// PICの初期化
	OSCCON = 0b11110000;	// 内部 8MHz x 4倍 PLL = 32MHz
	ANSELA = 0b00000000;	// PORTA のアナログ入力を無効
	ANSELB = 0b00000000;	// PORTB のアナログ入力を無効
	ANSELC = 0b00000000;	// PORTC のアナログ入力を無効

	// 入出力の設定(0:出力 1:入力)
	TRISA = 0b11111111;		// PORTAの入力設定
	TRISB = 0b11111111;		// PORTBの入力設定
	TRISC = 0b11000111;		// RC3, RC4, RC5 を出力
	LATC  = 0b00000000;		// 出力の初期値 (EN = 0, PWM = 0)

	// PWM の設定  周波数 = 32MHz / (4 x 4 x 100) = 20kHz
	T2CLKCON = 0b00000000;	// Timer2 のクロックは Fosc/4
	PR2      = 99;			// 周期 = (99 + 1) x 4 x 4 / 32MHz = 50us
	T2CON    = 0b10100000;	// Timer2 オン、プリスケール 1:4
	CCPTMRS  = 0b00000000;	// CCP1, CCP2 とも Timer2 を使う
	CCPR1    = 0;			// 正転の時間幅 0
	CCPR2    = 0;			// 逆転の時間幅 0
	CCP1CON  = 0b10001100;	// CCP1 有効、PWM モード、右詰め
	CCP2CON  = 0b10001100;	// CCP2 有効、PWM モード、右詰め
	RC5PPS   = 0b01100;		// RC5 に CCP1 の出力を出す
	RC3PPS   = 0b01101;		// RC3 に CCP2 の出力を出す

	// モーターの動作 (1秒ずつ)
	MOTOR_EN = 1;			// ドライバ有効 (両方の PWM が 0 なのでブレーキ)
	__delay_ms(1000);
	CCPR1 = 120;			// 正転 30% (120 / 400)
	__delay_ms(1000);
	CCPR1 = DUTY_MAX;		// 正転 100%
	__delay_ms(1000);
	CCPR1 = 0;				// ブレーキ (下側 2個がオン)
	__delay_ms(1000);
	CCPR2 = 200;			// 逆転 50% (200 / 400)
	__delay_ms(1000);
	CCPR2 = 0;				// ブレーキ
	__delay_ms(1000);
	MOTOR_EN = 0;			// ドライバ無効 (全オフ、惰性で停止)

	// メインループ
	while (1) {
		// ここにプログラムを記述
	}
}
