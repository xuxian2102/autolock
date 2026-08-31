# Rev A LCSC 料号复核

## 结论

- 原 21 个未确认位号已经全部获得精确的 LCSC、MPN 和制造商字段。
- 前 20 个实例只补采购字段；D5 另经专门修正，将标准 LED 的 1 脚阴极接 GND、2 脚阳极经 R9 接驱动，并在原坐标旋转 180°。
- C7/C11 的设计额定值为 10 V，实际统一采用 25 V 的 C9807；容量、介质和封装不变，额定电压只升不降。
- C27/C28 与 C29/C30 采用 ±1% C0G/NP0，满足并优于设计要求的 ±2%。
- D5 使用 C12624 / KT-0603G。基线差分确认全部铜走线和过孔未变，两个焊盘的物理中心原位交换针脚身份。

## 已写入设计的料号

| 组 | 位号 | LCSC | MPN | 制造商 | 规格 |
|---|---|---|---|---|---|
| 1206 MLCC | C3, C7, C11, C39 | C9807 | CL31A106KAHNNNE | Samsung Electro-Mechanics | 10 µF, 25 V, X5R, 1206 |
| USB 串阻 | R4, R5 | C23345 | 0603WAF220JT5E | UNI-ROYAL | 22 Ω, ±1%, 0603 |
| 轻触开关 | SW1, SW2, SW3 | C2886899 | TL3305AF160QG | E-Switch | SPST-NO, 4.5 × 4.5 mm, SMD gull-wing |
| LED 串阻 | R9 | C21190 | 0603WAF1001T5E | UNI-ROYAL | 1 kΩ, ±1%, 0603 |
| ADC 分压 | R11 | C22961 | 0603WAF2203T5E | UNI-ROYAL | 220 kΩ, ±1%, 0603 |
| RF 电容 | C27, C28 | C882521 | GRM1885C1H331FA01D | Murata Electronics | 330 pF, C0G/NP0, ±1%, 50 V, 0603 |
| RF 电容 | C29, C30 | C237335 | GRM1885C1H680FA01D | Murata Electronics | 68 pF, C0G/NP0, ±1%, 50 V, 0603 |
| RF 阻尼 | R18, R19 | C22946 | 0603WAF270KT5E | UNI-ROYAL | 2.7 Ω, ±1%, 0603 |
| RX 支路 | R20, R21 | C4190 | 0603WAF2201T5E | UNI-ROYAL | 2.2 kΩ, ±1%, 0603 |
| 舵机信号 | R30 | C22962 | 0603WAF2200T5E | UNI-ROYAL | 220 Ω, ±1%, 0603 |
| 状态灯 | D5 | C12624 | KT-0603G | Hubei KENTO Elec | Green LED, 0603, standard polarity 1=K / 2=A |

## 主要核对来源

- C9807: https://www.lcsc.com/product-detail/C9807.html
- C23345: https://www.lcsc.com/product-detail/C23345.html
- C2886899: https://www.lcsc.com/product-detail/C2886899.html
- TL3305 系列机械图: https://sten-eswitch-13110800-production.s3.amazonaws.com/system/asset/product_line/data_sheet/213/TL3305.pdf
- C21190: https://www.lcsc.com/product-detail/C21190.html
- C22961: https://www.lcsc.com/product-detail/C22961.html
- C882521: https://jlcpcb.com/partdetail/946628-GRM1885C1H331FA01D/C882521
- C237335: https://jlcpcb.com/partdetail/236779-GRM1885C1H680FA01D/C237335
- C22946: https://www.lcsc.com/product-detail/C22946.html
- C4190: https://www.lcsc.com/product-detail/C4190.html
- C22962: https://www.lcsc.com/product-detail/C22962.html
- C12624: https://www.lcsc.com/product-detail/C12624.html

库存为动态信息。最终是否可装配以提交 BOM 当天嘉立创页面为准。
