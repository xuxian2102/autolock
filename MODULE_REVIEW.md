# Rev A 逐模块工程评审

复核日期 2026-08-31 · 对照厂商手册与应用指南逐条测量 · 只评审,未改动任何设计文件

## 方法

把每份厂商手册里的**布局规则原文**变成对实际 `.kicad_pcb` 的距离测量,而不是目测。
测量脚本读取封装的绝对焊盘坐标(含旋转),计算元件到它所服务的那个引脚的直线距离。

对照的文档:

| 模块 | 依据 |
|---|---|
| B / E 逻辑降压 | Diodes DS41326 Rev 3-2 §PCB Layout |
| I 舵机降压 | TI SLVSE71 §10.1 Layout Guidelines |
| G NFC 控制器 | NXP PN7160_PN7161 Rev 3.2 |
| F 主控 | Espressif ESP32-C6-MINI-1 §11 PCB Layout Recommendations |
| H RF/天线 | 本地 ngspice 扫频 + 封装几何 |

> 表格里的 target 值是通行工程实践,不是厂商给的数字 —— 厂商多数只写
> "as close as possible"。用它来排序,不要当作合格线。

---

## 功能分区

| | 区块 | 件数 | 占位 | 关键器件 |
|---|---|---|---|---|
| A | 电池输入与保护 | 8 | 162 mm² | J1 F1 Q1 D1 D2 |
| B | 5V 逻辑降压 | 6 | 87 mm² | U1 L1 |
| C | USB-C 与 ESD | 6 | 88 mm² | J2 U2 |
| D | 5V 合路 | 2 | 9 mm² | D3 D4 |
| E | 3V3 降压 | 6 | 87 mm² | U3 L2 |
| F | 主控与人机接口 | 26 | 205 mm² | U4 SW1-3 D5 + 12 测试点 |
| G | NFC 控制器 | 20 | 145 mm² | U5 X1 |
| H | RF 匹配与天线 | 24 | 118 mm² | L3 L4 AE1 |
| I | 舵机电源与输出 | 17 | 236 mm² | U6 L5 F2 J4 |

**区块间互连(不含 GND)—— 决定什么能挪:**

```
H ── G    4 根   PN_TX1/TX2, PN_RXP/RXN         刚性:RF 差分
G ── F    9 根   SPI×4 + IRQ + VEN + DWL_REQ    刚性:最粗的接口
F ── I    4 根   SERVO_EN/PWM + 2 根电源         半刚性:信号只有 2 根低速线
其余 33 对        全部 1-2 根,且几乎都是电源轨    自由
```

`H — G — F` 是一条必须保持相邻和顺序的刚性链。电源块 A→B→D→E 只靠单根电源轨串着,
布局上几乎可以任意折叠。

**被物理钉死的:** C(USB-C 贴板边)、A(J1 出线)、I(J4 出线)、H(天线位置)。

**结构缺陷:** 9 个区块两两 36 对里有 7 对包围盒相互重叠,其中 F 与 I 重叠 26.8 × 25.2 mm。
F 横跨 64 mm 把右半板全穿了,主因是 12 个测试点撒得到处都是。

---

## 🔴 I · 舵机降压 —— 全板最严重的布局问题

TI SLVSE71 规则 9 原文:*"The trace of the VFB node should be as small as possible to avoid noise coupling."*
规则 6:*"A separate VOUT path should be connected to the upper feedback resistor."*

| 测量项 | 实测 | 参考 | |
|---|---|---|---|
| R29 下分压 → U6 pin4 VFB | **20.12 mm** | <5 | 远 |
| R28 上分压 → U6 pin4 VFB | **13.31 mm** | <6 | 远 |
| C39 10µF 输入电容 → pin3 VIN | 7.41 mm | <3 | 远 |
| C40 100nF → pin3 VIN | 6.81 mm | <4 | 远 |
| C41 自举 → pin6 VBST | 7.51 mm | <3 | 远 |
| U6 pin2 SW → L5 | 4.75 mm | <5 | **达标** |
| C42 输出电容 → L5 | 5.44 mm | <6 | 达标 |
| C44 1000µF → L5 | 16.18 mm | <25 | 达标(储能,不需要近) |

5 A 开关电源旁边 20 mm 的反馈走线,是**会影响可用性**的一条,不是洁癖。
舵机动作时输出电压会跟着开关噪声抖。

已核实无误:EN 使能网络算上 IC 内部 120–400 kΩ 下拉后仍有 2.79–2.93 V(阈值 1.6 V),
上电默认关断。

---

## 🔴 贯穿性问题 · 三个开关电源下方都没有地平面

Diodes:*"Make the bottom layer under the device as the GND layer for heat dissipation.
The GND layer should be as large as possible."*
TI:*"VIN and GND traces should be as wide as possible... also of advantage from the
view point of heat dissipation."* / *"Provide sufficient vias."*

实测半径 6 mm 内:

| | GND 铜面积 | 过孔数 |
|---|---|---|
| U1 (2 A) | 12.2 mm² | 5 |
| U3 (2 A) | 15.8 mm² | 3 |
| **U6 (5 A)** | **103.8 mm²** | **6** |

**根因:这块板没有任何敷铜(zone)。** 地是 7176 段走线画的网格 —— 也就是
`INDEPENDENT_REVIEW_KICAD10.md` 里 E3 那 199 个悬空线头的来源。

厂商的散热与低阻回流建议在当前结构下**无法满足**,而且敷铜不是能局部补的东西。
这条把 E3 从"可维护性问题"升级为**功能问题**。

另:Diodes 建议 2 oz 铜(针对 2 A 满载),本板为 1 oz。实际负载远低于 2 A,
此条优先级低于上面的地平面问题。

---

## 🟡 B / E · 逻辑降压 —— 去耦普遍偏远

Diodes:*"Place the VIN capacitors as close to the device as possible."*

| 测量项 | 实测 | 参考 |
|---|---|---|
| C3 输入电容 → U1 pin3 VIN | 6.96 mm | <3 |
| **C2 100nF → U1 pin3 VIN** | **18.00 mm** | <5 |
| U1 pin1 FB ← 输出取样点 | 14.40 mm | <8 |
| C4 自举 → U1 pin6 BST | 6.14 mm | <3 |
| U1 pin5 SW → L1 | 7.69 mm | <5 |
| C5 输出电容 → L1 | 2.79 mm | <6 ✓ |
| C7 输入电容 → U3 pin3 VIN | 6.96 mm | <3 |
| C8 自举 → U3 pin6 BST | 2.82 mm | <3 ✓ |
| U3 pin5 SW → L2 | 7.69 mm | <5 |
| C9 输出电容 → L2 | 2.60 mm | <6 ✓ |

~~**C2 在 18 mm 外,作为高频旁路等于不存在。** 单点最离谱,但影响面比 U6 小。~~

> **2026-08-31 已修复:C2 移到 U1 pin 3 VIN 旁,焊盘间距 18.00 → 1.47 mm。**
> 同批提案里 U5 的三颗去耦和 U6 的反馈分压被布线器否决,已回滚 —— 见
> `PREFAB_REVIEW_ACTIONS.md` J 节和 `tools/local_passive_placer.py` 顶部的逐条记录。

---

## 🟡 G · NFC 控制器 —— 去耦偏远

| 测量项 | 实测 | 参考 |
|---|---|---|
| **C21 2.2µF → U5 pin14 VDD(TX)** | **10.46 mm** | <5 |
| C17 100nF → U5 pin13 VDD(UP) | 8.77 mm | <3 |
| C23 100nF → U5 pin27 VDD | 7.83 mm | <3 |
| C26 100nF → U5 pin17 VMID | 4.67 mm | <3 |
| X1 晶振 → U5 pin30 XTAL1 | 8.78 mm | <8 |
| C15 负载电容 → X1 pin1 | 4.36 mm | <4 |

PN7161 发射时是脉冲大电流,10 mm 回路电感会让 TVDD 在发射瞬间塌陷,直接影响读距。
`C21`/`C22` 是最不该远的两个。

已核实无误:全部 40 脚 + 中心焊盘与 NXP 表 5 逐条吻合;供电范围全部在规格内。

---

## 🟢 H · RF 匹配与天线 —— 这块板做得最好的部分

端到端对称:

```
U5 TX1→L3   8.98 mm  │  U5 TX2→L4   9.20 mm      (差 0.22)
L3→C29      7.24 mm  │  L4→C30      7.24 mm      (完全一致)
C29→R18     1.90 mm  │  C30→R19     1.90 mm      (完全一致)
R22→ANT_P   1.88 mm  │  R23→ANT_N   1.88 mm      (完全一致)
```

ngspice 扫频:谐振 13.601 MHz,偏 +0.30%;换一档电容可拉回 4–5%,覆盖得住
从几何算出的天线电感(1.491 µH)的不确定度。天线区四层禁铜完整。

**这块不需要动。** 重新布线时应把它当作固定基准,其他模块围绕它排。

> ⚠️ 撤回一条误判:我最初量到 `R22→天线 17.21 mm` vs `R23→天线 1.88 mm`,以为差 15 mm。
> 那是测量假象 —— 螺旋天线内圈经 B.Cu 跳线引出,封装里 `pad 2` 出现两次,我取到了远端。
> 实际两侧都是 1.88 mm。

---

## 🟢 F · 主控与人机接口 —— 摆放正确

板上 ESP32 禁铜区 **13.2 × 5.4 mm**,与 Espressif 手册图 11-1 规定的 Antenna Area
**尺寸完全一致**,且贴板边(距边 0.2 mm),全四层。

唯一问题是 **12 个测试点把这个块摊成 64 mm 宽**,压在 B / E / G / I 上面 —— 这是
150 × 75 板框的直接成因之一。

---

## 🟢 A / C / D —— 未发现问题

- Q1 AO4407A 方向正确(漏极朝电池、源极朝负载),栅极下拉 + 齐纳钳位
- USB-C CC1/CC2 各 5.1 kΩ 下拉正确;USBLC6-2SC6 引脚正确
- D3/D4 肖特基合路,互不反灌

一条待确认:D1 是 **SMBJ15A 单向** TVS,反接时正向导通、靠 F1 (3 A PPTC) 限流。
PPTC 是触发后保持限流而非熔断,所以反接状态下 TVS 会持续承受约 0.3 W。
SMB 封装扛得住,但板子会一直"热着"直到断开。属于有意的 crowbar 设计,记录备查。

---

## 网上的参考案例

**没有可直接对比的开源 PN7161 硬件。** 找到的都是固件侧:

| 项目 | 内容 |
|---|---|
| [rednblkx/HomeKey-ESP32](https://github.com/rednblkx/HomeKey-ESP32) | 本工程依赖的固件。PN7161 驱动在 main 分支(`components/pn7160` + `Pn7160Reader.cpp`) |
| [NXPNFCLinux/nxpnfc](https://github.com/NXPNFCLinux/nxpnfc) | NXP 官方 PN7160 内核驱动 |
| [Strooom/PN7160](https://github.com/Strooom/PN7160) | 独立 PN7160 驱动 |

硬件基准只能是 NXP 自己的 **PNEV7160B 评估板**与 **AN12988(硬件设计指南)/
AN13219(天线设计与匹配指南)**。AN13219 需注册下载,本次未取得;不过匹配值已仿到
+0.30%,说明原设计本来就是照它做的。

**固件兼容性核实结果(撤回一条误判):**
搜索摘要称该固件不支持 ESP32-C6。直接查仓库 CI 构建矩阵:

```
target: [esp32, esp32c3, esp32c6, esp32s3]
release 产物含 esp32c6.firmware.bin / esp32c6.firmware.factory.bin
```

**ESP32-C6 是一等构建目标**,wiki 上的说法已过时。NFC 引脚为运行时可配
(`nfcGpioPins` / `nfcIrqPin` / `nfcVenPin`),使用 SPI2 —— 本板 GPIO 分配无冲突。
PN7161 路径仍标 dev,成熟度低于 PN532。

---

## 优先级

按"影响可用性"而非"看着难受"排:

| | 问题 | 性质 |
|---|---|---|
| 1 | U6 反馈走线 20 mm | 直接影响舵机供电稳定性 |
| 2 | 三个开关电源无地平面(无敷铜) | 散热与回流;也是 199 悬空线头的根 |
| 3 | NFC 去耦 8–10 mm | 影响读距与发射稳定性 |
| 4 | U1 的 100nF 在 18 mm 外 | 单点最离谱,影响面小 |
| 5 | 测试点摊开、板框过大 | 成本与体积 |

**前三条都指向同一个结论:需要重新布线,而不是打补丁。** 第 2 条尤其 ——
敷铜是全局结构,不能局部添加。

重新布线时的固定基准:H(RF/天线)保持不动,`H—G—F` 保持相邻与顺序,
电源块 A/B/D/E 可自由重排。
