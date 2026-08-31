# Rev A 设计冻结

冻结日期：2026-08-27

## 1. 功能状态机

`待机 → PN7161 检测/IRQ → HomeKey 认证 → 打开 6V 舵机电源 → 压把手 → 回位 → 关闭舵机电源 → 待机`

固件约束：一次动作最长 2 s；ESP32 看门狗必须启用；上电默认 `SERVO_EN=0`、`SERVO_PWM=0`。

## 2. 电源树

| 电源 | 来源 | 负载 | 说明 |
|---|---|---|---|
| `BAT_RAW` | J1 | F1/TVS | 3S，9.0–12.6 V |
| `BAT_SYS` | F1 + Q1 | U1、U6、电量分压 | 反接保护后母线 |
| `5V_BAT` | AP63205 | D3 | 电池降压 5 V |
| `USB_5V` | USB-C | D4 | USB 5 V，仅逻辑/NFC |
| `SYS_5V` | 两颗 B5819W 肖特基 OR | PN7161、U3 | 无反灌逻辑轨，约 4.3–5.0 V（随负载与来源变化） |
| `3V3` | AP63203 | ESP32-C6、PN7161 VDD_PAD | 2 A 固定 3.3 V |
| `SERVO_6V` | TPS565201 | 输出电容、F2 | 5.94 V，GPIO 控制 EN |
| `SERVO_6V_OUT` | F2 2.5 A PPTC | DS3115 | 电缆/短路保护；不替代 2 s 软件堵转保护 |

输入保护：3 A/30 V PPTC、15 V TVS、-30 V P-MOS 反接保护、10 V 栅源钳位。舵机输出另有 2.5 A/16 V PPTC。

## 3. ESP32-C6 固定引脚

| 信号 | GPIO | 说明 |
|---|---:|---|
| `PN_IRQ` | GPIO0 | 低功耗/RTC GPIO，NFC 唤醒 |
| `PN_VEN` | GPIO1 | 低功耗/RTC GPIO，深睡保持高 |
| `BAT_ADC` | GPIO2 | 1 MΩ / 220 kΩ 分压，12.6 V → 2.27 V |
| `SERVICE_BTN` | GPIO3 | 配置/恢复按钮，低有效 |
| `STATUS_LED` | GPIO14 | 只在需要时点亮 |
| `USB_D-` | GPIO12 | 原生 USB Serial/JTAG |
| `USB_D+` | GPIO13 | 原生 USB Serial/JTAG |
| `PN_NSS` | GPIO18 | PN7161 SPI 片选 |
| `PN_SCK` | GPIO19 | PN7161 SPI 时钟 |
| `PN_MOSI` | GPIO20 | PN7161 SPI MOSI |
| `PN_MISO` | GPIO21 | PN7161 SPI MISO |
| `SERVO_PWM` | GPIO22 | DS3115 PWM，220 Ω 串联 |
| `SERVO_EN` | GPIO23 | TPS565201 EN，100 kΩ 下拉 |
| `UART_TX` | GPIO16 | 测试点 |
| `UART_RX` | GPIO17 | 测试点 |
| `BOOT` | GPIO9 | 10 kΩ 上拉，按键到 GND |
| `RESET` | EN | 10 kΩ 上拉/1 µF，按键到 GND |

避开 GPIO4/5/8/9/15 等启动配置脚作为普通外设；GPIO9 仅作官方 BOOT 功能。

## 4. PN7161 关键连接

- `VBAT`、`VBAT2`、`VDD_UP` → `SYS_5V`；`VDD_PAD` → `3V3`。
- `VDD`、`VDD_A`、`VDD_D` 相连并按 NXP 参考板去耦。
- `VDD_TX`、`TVDD_IN`、`TVDD_IN2` 相连并按 NXP 参考板去耦。
- `VDD_VMID` 独立 100 nF 去耦；`VDD_HF`、`ANT1`、`ANT2` 不连接。后三者是检测外部读卡器 RF 场的可选通道，不能用来发现作为被动卡模拟端的 iPhone；本机使用 PN7161 内部定时低功耗主动轮询。
- `DWL_REQ` 10 kΩ 下拉并设测试点；`WKUP_REQ`、`CLK_REQ` 仅留测试焊盘。
- 27.12 MHz、10 pF 晶振，两侧各 10 pF C0G；走线短、对称、无过孔。
- SPI 四线各串 0 Ω 调试位；`IRQ` 直达 GPIO0；`VEN` 直达 GPIO1并有 100 kΩ 下拉。

## 5. NFC 天线和匹配（首次装配值）

依据 NXP AN13219 Rev.1.6 的 40 mm × 40 mm 天线例子：4 匝、0.4 mm 线宽、0.3 mm 间距、35 µm 铜；参考实测约 1.52 µH、1.37 Ω。

| 器件 | 首装值 | 要求 |
|---|---:|---|
| `L0P/L0N` | 160 nH | ±5%，13.56 MHz 电流额定 ≥400 mA |
| `C0P/C0N` | 330 pF | C0G/NP0，±2% |
| `C1P/C1N` | 68 pF | C0G/NP0，±2%，并联预留 DNP 调谐位 |
| `C2P/C2N` | 100 pF | C0G/NP0，±2%，并联预留 DNP 调谐位 |
| `RQ_P/RQ_N` | 2.7 Ω | 1% |
| `CRX_P/CRX_N` | 1 nF | C0G/NP0 |
| `RRX_P/RRX_N` | 2.2 kΩ | 1% |

天线区域所有内层和底层禁铜、禁走线、禁器件。板上天线通过 0 Ω 选择电阻连接；外接差分天线焊盘默认 DNP。门上调谐目标不是仅看 13.56 MHz 谐振点，还要测 TX 电流、场强、接收裕度和完整 HomeKey APDU 成功率。

## 6. PCB 与装配

- 4 层：F.Cu / In1.GND / In2.GND+电源 / B.Cu；1.6 mm、1 oz。
- 顶层单面贴装；仅电池和舵机螺丝端子为通孔件。
- NFC 区、PN7161 区、开关电源区、ESP32 2.4 GHz 天线区物理分区。
- TPS565201 输入回路与 SW 铜面积最小；舵机 6 V/GND 主电流路径至少 2.0 mm，优先整面铜。
- ESP32 模组天线贴板边，模块天线下方四层禁铜。
- 每条关键电源和 PN7161 控制线均有测试点。

## 7. Rev A 通过门槛

1. USB 与 3S 分别供电均能启动，无相互反灌；静态输入不异常发热。
2. `SYS_5V=4.3–5.0 V`、`3V3=3.20–3.40 V`、`SERVO_6V=5.80–6.10 V`。
3. 空气中 0–40 mm NFC 检测与完整 HomeKey 认证分别记录成功率。
4. 厚门最终位置完整 HomeKey 认证 ≥95/100；P95 完成时间 <800 ms。
5. 舵机 200 次压下/回位无复位、无过热；堵转 2 s 内断电。
6. 无手机 24 h 电池侧平均电流目标 <0.5 mA；记录误唤醒次数。

## 8. Rev A 设计修正记录

- 2026-08-27：删除原拟两颗 LM66100 的等压 5 V OR。LM66100 的双路 OR 参考连接需把每颗 CE 接到另一输入；对两个接近 5 V 的来源并不如简单二极管合路稳妥。Rev A 改为两颗 B5819W，允许约 0.2–0.4 V 压降，PN7161 和 AP63203 仍有足够输入余量。
- 2026-08-28：把 USB-C/ESP32 自定义多边形焊盘纳入精确铜皮审计；修正由原矩形近似漏检的 USB-C GND–VBUS 与 CC2–VBUS 局部接触，改为 CC/VBUS 顶层直出后全板重新布线。
- 2026-08-28：PN7161 4.1 × 4.1 mm 中心焊盘初步取消整面锡膏，改为 3 × 3 个 1.0 mm 开窗；该临时值随后由官方封装资料复核并替换。
- 2026-08-28：重新生成后通过精确几何检查（0 errors / 0 warnings）和 68/68 网络独立物理连通检查。
- 2026-08-30：依据 NXP SOT618-1 官方回流焊 footprint，把 PN7161 中心焊盘钢网定稿为 3 × 3 个 0.60 × 0.60 mm 开窗、总跨度 2.40 mm（锡膏面积约 19.3%）；外围 40 个铜焊盘和 4.10 × 4.10 mm 中心铜焊盘均不变，courtyard 更新为 7.25 × 7.25 mm。
