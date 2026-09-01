# HomeKey Lock Rev A — PN7161 / ESP32-C6

本工程是面向嘉立创 4 层 PCB + 单面 PCBA 的第一版工程样机，目标是：

1. iPhone / Apple Watch 通过真正的 Apple HomeKey 认证；
2. ESP32-C6 驱动 180° DS3115 舵机压下室内门把手并立即复位；
3. 3S 保护电池供电，USB-C 可单独给逻辑与 NFC 供电、刷机和调试；
4. 门外不增加可见器件，不接门磁、不集成 3S 充电器。

## Rev A 设计边界

- 主控：ESP32-C6-MINI-1-N4，4 MB Flash。
- NFC：PN7161B1HN/C100E，SPI，27.12 MHz 晶振。
- 天线：PCB 顶层 40 mm × 40 mm、4 匝、0.4 mm 线宽、0.3 mm 间距；匹配值按 NXP AN13219 Rev.1.6 的首次迭代值。
- 电池输入：9.0–12.6 V（3S 保护电池或 12 V/5 A 台式适配器）。
- 舵机输出：约 5.94 V，TPS565201 额定 5 A；仅在动作时使能。
- USB-C：USB 2.0 D+/D− 连接 ESP32-C6 原生 USB Serial/JTAG；USB VBUS 只能带逻辑/NFC，不能带舵机。
- USB 与电池逻辑 5 V 用两颗 B5819W 肖特基二极管合路，互不反灌；`SYS_5V` 会比来源低约 0.2–0.4 V。
- 板层：4 层、1.6 mm、1 oz；所有器件位于顶层，降低 PCBA 成本。
- 不含：电池充电、门状态检测、机械限位检测、认证固件、外壳与舵机连杆。

## 必须先知道

这是 Rev A RF/机构验证板。40 mm 天线的匹配值只是在空气中的起点；门板、锁体、螺丝、铁氧体片和安装胶都会改变天线的电感、Q 值和谐振。首批应下单 5 块裸板、2 块 PCBA，先在家中厚门完成读取距离、HomeKey 成功率、待机电流和舵机堵转测试，再决定 Rev B。

HomeKey-ESP32 对 PN7161 的支持仍属于开发/实验路径。硬件把 IRQ 放在 ESP32-C6 的低功耗 GPIO0，并保留 VEN、SPI 和调试测试点；低功耗轮询与深睡唤醒仍需在固件中验证。

## 从源码重建

工程是生成出来的，不是手工画的。`tools/design_data.py` 是元件与网表的唯一真源，
`tools/generate_board.py` 里的 `FIXED_PLACEMENT` 是板上位置的唯一真源。

```bash
.claude/hooks/session-start.sh          # 装 KiCad 10.0.6 与依赖（幂等）
python3 tools/generate_board.py         # 摆放 → hardware/kicad/*.kicad_pcb
python3 tools/route_board.py            # 布线 + 接地敷铜
python3 tools/build_release.py          # 审计 + 官方 Gerber 导出 + 打包
python3 .claude/skills/kicad-verify/scripts/verify_release.py --strict
```

最后一步刻意不依赖 `tools/` 自带的审计脚本 —— 那套脚本既生成发布件又给发布件盖章。

## 当前生产状态

PCB 电气/几何审计已通过，原 21 个 LCSC 缺口已全部关闭；生产放行仍为 **HOLD**，等待在嘉立创预览器中确认 Gerber 和 CPL 旋转，并完成首板 RF/机构测试。详见 `reports/REV_A_RELEASE_STATUS.md` 与 `hardware/production/JLCPCB_ORDER_NOTES.md`。

## 目录

```
autolock/
├── README.md
├── hardware/
│   ├── kicad/                    KiCad 源工程：原理图、PCB、符号/封装库、3D 模型
│   ├── docs/                     原理图总 PDF + 分页 PDF
│   └── production/
│       ├── gerbers/              13 个制造文件（11 层 Gerber X2 + PTH/NPTH Excellon），平铺
│       ├── gerber.zip            嘉立创上传用，内容与 gerbers/ 逐字节一致
│       ├── assembly/             BOM_FULL / BOM_JLCPCB_DRAFT / CPL_JLCPCB_DRAFT / PROCUREMENT_GAPS
│       ├── JLCPCB_ORDER_NOTES.md 下单参数与预览确认清单
│       └── CHECKSUMS.sha256      上述制造文件的 SHA-256 清单
├── design/                       Rev A 设计冻结说明与 U4 板级片段
├── firmware/                     固定引脚表（不打包第三方固件二进制）
├── tools/                        原理图/PCB 生成、布线、审计、生产导出和发布打包脚本
├── reports/                      最终审查记录 + 手机可看的分层渲染图
├── .claude/
│   ├── hooks/                    SessionStart：装 KiCad 10.0.6、补齐未声明的 Python 依赖、
│   │                             搭好 tools/ 期望的便携 KiCad 目录
│   └── skills/kicad-verify/      独立验证：DRC、网表逐焊盘对 design_data.py、
│                                 引脚图对数据手册、Gerber 逐字节复现
├── INDEPENDENT_REVIEW_KICAD10.md 用独立安装的 KiCad 10 做的首轮复核
├── PREFAB_REVIEW_ACTIONS.md      投产前优化点与修改点全表（含已完成项）
├── MODULE_REVIEW.md              分模块复核：电源、NFC、RF、舵机
└── FINAL_LOCAL_PRODUCTION_REVIEW.md
```

- `hardware/kicad/`：`.kicad_sch` 是权威原理图（见 `hardware/docs/SCHEMATIC_PDF_STATUS.md` 的 PDF 限制说明）。
- `hardware/production/`：直接用于下单的一套文件；`gerber.zip` 就是上传给嘉立创的那个包。
- `reports/`：布线、几何、物理连通、CPL 定位和生产导出的最终审计，以及官方逐层 PNG/SVG。
  构建过程中产生的中间态 DRC/同步快照已不再入库，只保留最终版记录。

## 安全边界

- 必须保留原机械钥匙和室内手动开门能力；任何故障不得把人困在室内。
- 只使用带 BMS 的 3S 电池包和与该电池匹配的认证充电器；Rev A 不允许直接给裸电芯充电。
- 舵机固件必须设置动作超时和看门狗；调试时先拆下舵机臂。
- 逆向实现的 HomeKey 固件不等同于 MFi 认证商业锁，不应作为高风险场所唯一安防手段。
