# Rev A 独立复核报告（KiCad 10.0.6 实跑）

复核时间：2026-08-31
复核方式：在干净容器内安装 KiCad 10.0.6（PPA `kicad/kicad-10.0-releases`，版本号与工程发布链声称的完全一致），
**不使用工程自带的任何审计脚本**，直接用 `kicad-cli` 和官方导出结果与 `tools/design_data.py` 清单对拍。

---

## 一、独立复现通过的项

| 复核项 | 方法 | 结果 |
|---|---|---|
| PCB DRC | `kicad-cli pcb drc --severity-all` | **0 violations / 0 unconnected**，与 `KICAD10_DRC_FINAL_RELEASE.json` 一致 |
| 网表 vs 设计清单 | `kicad-cli pcb export ipcd356` 提取 KiCad 自己算出的网表，逐脚比对 `design_data.py` | **309 个焊盘、0 处不符**；PCB 上多出的 27 个焊盘全部是清单里显式声明的 NC |
| Gerber 可复现性 | 用 `.kicad_pcb` 重新导出，与仓库里的 `_JLCPCB_Gerber.zip` 逐字节比对 | **11 层全部逐字节一致**（GTL/G2/G3/GBL/GTS/GBS/GTP/GBP/GTO/GBO/GKO） |
| 钻孔 | 同上 | 内容一致；只有槽孔编码方式不同（发布件用 M15/M16 路铣式，默认导出用 G85），属导出选项差异，非缺陷 |
| Gerber X2 层序 | 读 `%TF.FileFunction` | L1 Top / L2 Inr / L3 Inr / L4 Bot，四层顺序正确 |
| 固件引脚表 | 比对 `PINMAP.md` × 模块符号引脚名 × `design_data.py` | 全部一致，含 U0TXD=GPIO16 / U0RXD=GPIO17、BOOT=GPIO9 |
| BOM 料号编码 | 抽查 UNI-ROYAL 编码规则并核对 LCSC | 一致（`270K`=2.7Ω、`220J`=22Ω、`1001`=1k、`2203`=220k），无阻值错配 |

**结论：PCB 这一层是可信的。** 板子在电气上确实等于 `design_data.py` 写的东西，交给嘉立创的 Gerber 也确实等于这块板子。

---

## 二、发现的问题

### P1（严重）工程没有根原理图，KiCad 打不开这个设计

```
$ kicad-cli sch erc HomeKey-Lock-RevA-PN7161.kicad_sch
Failed to load schematic
```

- 工程目录里 **不存在** `HomeKey-Lock-RevA-PN7161.kicad_sch`，只有 `schematics/` 下 5 个孤立页面；
- `.kicad_pro` 里 `sheets` 字段为 `null`，没有任何层次结构；
- 任何人双击 `.kicad_pro` 打开，Eeschema 里是**空白原理图**。

`FINAL_LOCAL_PRODUCTION_REVIEW.md` 写的"`.kicad_sch` 是权威原理图"，实际上不成立。
文档把 ERC/PDF 失败归因于"便携 KiCad 10 的 `bus error`"——在官方 10.0.6 上不是 bus error，
是根本没有可加载的工程原理图。

### P2（严重）5 个原理图页面不承载网表，只是图

对 5 页分别跑 ERC，共 **819 条**：

| 数量 | 类型 | 说明 |
|---|---|---|
| 416 | `endpoint_off_grid` | 生成器输出的端点不在栅格上 |
| 116+116 | `lib_symbol_issues` / `footprint_link_issues` | 缓存符号与库不一致 |
| 60 | `pin_to_pin` | 引脚直接相碰 |
| 37 | `isolated_pin_label` | 标签没落在引脚上 |
| **27** | **`pin_not_connected`（error）** | 清单里明明接了的脚，图上是断的 |
| 24 | `unconnected_wire_endpoint` | 0.0508 mm（2 mil）的碎线头，没接到任何引脚 |
| **6** | **`label_dangling`（error）** | 全局标签悬空 |
| 1 | `multiple_net_names` | 第 1 页 `GND` 和 `USB_5V` 挂在同一对象上 |

具体例子（这些脚在 `design_data.py` 里都是接了的）：

- `U4` pin 8 `EN/CHIP_PU` → 图上未连接，`ESP_EN` 标签悬空
- `U4` pin 12 `GPIO0` / pin 13 `GPIO1` → `PN_IRQ` / `PN_VEN` 悬空
- `U5` pin 21 `TX1` → `PN_TX1` 悬空
- `AE1` 天线两个脚 → 全部未连接
- 第 1 页 `GND` 与 `USB_5V` 同点：图上是一个短路

**影响**：原理图不能跑 ERC、不能导网表、不能作为评审依据、Rev B 改动无法从原理图入手。
Rev A 的电气正确性目前**只靠 `design_data.py` 这一份 Python 清单**兜底，没有第二道独立校验。

### P3（中）In1.Cu 有 199 个悬空铜线头，被 DRC 规则屏蔽掉了

`.kicad_pro` 里把 `track_dangling` 设成了 `ignore`。把它打开重跑：

```
Found 199 violations   —— 全部在 In1.Cu
Found 0 unconnected items
```

原因是地平面不是 KiCad 的敷铜（zone），而是用 **7176 段走线**画出来的"网格"。后果：

- 199 个铜线残桩，位置就在 NFC 天线正下方那一层，属于 RF 上不必要的不确定性；
- 板子在 KiCad 编辑器里基本改不动，任何改动都要重跑 `route_board.py`；
- 为了让 DRC 过，把一条真实规则永久关掉了。

网表连通性没问题（0 unconnected 已独立确认），所以这不是断线，是工艺和可维护性问题。

### P4（中）仓库里没有源文件，只有压缩包

`git` 里跟踪的是 40 MB 的 `.tar.gz` / `.zip`，工程源文件一个都没有版本化。而且
`production/..._RevA_Review_Package.zip` 本身又嵌在 `.tar.gz` 里，同一份内容存了约 3 份。

配套问题：

- 没有 `requirements.txt` / `pyproject.toml`；`tools/` 依赖 `kiutils`、`shapely`、`numpy`；
- 12 个脚本 `sys.path.insert` 到 `../.tools/py`，这个目录**不在压缩包里**——从零 clone 跑不起来发布链；
- 没有 CI，只有一个 `test_u4_generation_chain.py`；
- 打包里混进了 `~HomeKey-Lock-RevA-PN7161.kicad_pro.lck` 锁文件。

### P5（低）工艺参数贴着嘉立创下限

`kicad-cli pcb export stats`：

- 最小线宽 0.200 mm、**最小间距 0.100 mm（3.94 mil）**、最小钻孔 0.200 mm
- 231 个通孔过孔，其中 208 个是 0.2 mm 钻
- 120 个封装，其中 **17 个 component type 是 "Unspecified"**（所以 `footprint_type_mismatch` 也被列进了忽略清单）

0.1 mm 间距比嘉立创四层默认档位（5 mil）紧，会进更贵的工艺档或被 DFM 打回，下单前值得确认。

### P6（低）两处电路建议，需按数据手册确认

- **R4 / R5 = 22 Ω 串在 USB D+/D−**：ESP32-C6 是原生 USB Serial/JTAG，PHY 内部已含串阻，
  乐鑫硬件设计指南是直连。建议改 0 Ω（或保留焊盘、默认贴 0 Ω）。
- **GPIO8（模块 pin 22）悬空**：ESP32-C6 的 strapping 脚之一。建议加一个 10k 上拉位（可 DNP），
  避免上电时被外部走线拉低。

---

## 三、已核对无误、不需要改的部分

避免下一轮重复排查，这些我逐条看过是对的：

- **Q1 AO4407A 防反接方向正确**：漏极接电池侧 `BAT_FUSED`、源极接负载侧 `BAT_SYS`，
  R1 100k 栅极下拉，D2 齐纳阴极接栅极/阳极接源极，把 Vgs 钳在 −10 V。
- **USB-C**：CC1/CC2 各 5.1k 下拉正确；USBLC6-2SC6 引脚（1/6=D−、3/4=D+、2=GND、5=VBUS）正确。
- **U6 反馈分压**：68.1k / 10k，配 TPS565201 的 0.768 V 基准 → 6.0 V，与设计意图相符。
- **ESP32-C6 引脚分配**：`PINMAP.md` 与模块封装引脚名逐条对得上，包括 ADC1_CH2 落在 GPIO2、
  BOOT 落在 GPIO9 这类容易错的地方。
- **U4 引脚归类完备**：1–35 号脚在"已用 / GND / NC"三类里正好覆盖，无遗漏无重复。

---

## 四、建议的优化顺序

**第一批 —— 让工程可复核（不动铜箔，Gerber 逐字节不变）**

1. 补一张根原理图 `HomeKey-Lock-RevA-PN7161.kicad_sch`，把 5 页挂成层次表，写回 `.kicad_pro` 的 `sheets`；
2. 修 `generate_schematics.py`：标签/导线端点吸附到引脚坐标并对齐 1.27 mm 栅格，消掉 2 mil 碎线头；
3. 目标是原理图 ERC 干净，并且**从原理图导出的网表能和 `design_data.py`、和 PCB 三方对上**——
   这样 P2 里"只有一份 Python 兜底"的问题才算真正关掉。

**第二批 —— 清掉被屏蔽的 DRC**

4. In1.Cu 地平面改成真正的 KiCad zone（保留 NFC/U4 区域的禁铜），删掉 7176 段手绘走线；
5. 把 `track_dangling` 从 `ignore` 改回 `error`，让它自然归零，而不是关掉；
6. 补齐 17 个封装的 component type。

**第三批 —— 仓库与可复现性**

7. 把工程解包进 git 版本化，压缩包只作为 release artifact；`.gitignore` 掉生成物；
8. 加 `requirements.txt`（kiutils / shapely / numpy）+ 把 `../.tools/py` 的依赖收进工程内；
9. 加一个 CI：安装 KiCad 10 → 跑 DRC + ERC + Gerber 逐字节复现比对。本报告里这套流程已经验证可行。

**第四批 —— 电路小改（进 Rev B）**

10. R4/R5 改 0 Ω；GPIO8 加 DNP 上拉位；确认 0.1 mm 间距的工艺档位。

---

## 五、我无法在本地验证的（仍然是 HOLD 的真正原因）

以下四项需要外部环境，本次复核不涉及，工程原有的 HOLD 判断是对的：

1. 嘉立创 Gerber 查看器里的目视确认（层序、板框、长槽、禁铜、钢网）；
2. PCBA 预览里逐颗确认库模型零度定义（D5、J2、U4、U5、X1 的旋转）；
3. 下单当日的 LCSC 实时库存与可贴装状态；
4. 首板的 NFC 天线 VNA 调谐、HomeKey 穿门距离、待机功耗、舵机堵转与机构验证。

X1 的 10 pF 负载电容配 10 pF 晶振这一项也建议在首板上实测频偏后再定值。
