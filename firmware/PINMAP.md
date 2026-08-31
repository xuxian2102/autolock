# Rev A 固件引脚表

```cpp
// ESP32-C6-MINI-1-N4
#define PIN_PN_IRQ       0
#define PIN_PN_VEN       1
#define PIN_BAT_ADC      2
#define PIN_SERVICE_BTN  3
#define PIN_USB_DM      12
#define PIN_USB_DP      13
#define PIN_STATUS_LED  14
#define PIN_UART_TX     16
#define PIN_UART_RX     17
#define PIN_PN_NSS      18
#define PIN_PN_SCK      19
#define PIN_PN_MOSI     20
#define PIN_PN_MISO     21
#define PIN_SERVO_PWM   22
#define PIN_SERVO_EN    23
```

启动顺序：先把 `PIN_SERVO_EN` 与 `PIN_SERVO_PWM` 配置为低，再初始化网络和 NFC。PN7161 `VEN` 置高后等待数据手册规定的启动时间，再开始 NCI/SPI 通信。

电池换算起始公式：

`VBAT = VADC × (1,000,000 + 220,000) / 220,000`

必须用实测 3.3 V 和电阻误差做两点校准，不把 ADC 数值作为 3S 过放保护；过放保护由电池包 BMS 负责。

