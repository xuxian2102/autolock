# U5 / PN7161 SOT618-1 封装复核

复核日期：2026-08-30

## 结论

U5 使用的 `PN7161B1HN/C100E` 为 NXP `SOT618-1 / HVQFN40`，本体 6 × 6 mm、0.50 mm 引脚间距。Rev A 的 40 个外围铜焊盘与中心裸露铜焊盘尺寸正确；需要修正的是中心焊盘锡膏开窗和 courtyard。

| 项目 | NXP SOT618-1 | Rev A 定稿 |
|---|---:|---:|
| 外围间距 `P` | 0.500 mm | 0.500 mm |
| 外围焊盘 `D × C` | 0.290 × 0.900 mm | 0.290 × 0.900 mm |
| 中心铜焊盘 `SLx × SLy` | 4.100 × 4.100 mm | 4.100 × 4.100 mm |
| 锡膏阵列 `nSPx × nSPy` | 3 × 3 | 3 × 3 |
| 单个锡膏开窗 `SPx × SPy` | 0.600 × 0.600 mm | 0.600 × 0.600 mm |
| 锡膏总跨度 | 2.400 × 2.400 mm | 2.400 × 2.400 mm |
| courtyard / `Hx × Hy` | 7.250 × 7.250 mm | 7.250 × 7.250 mm |

九个开窗中心坐标为 `x,y ∈ {-0.90, 0, +0.90} mm`。总锡膏面积为 `9 × 0.60 × 0.60 = 3.24 mm²`，相对 4.10 × 4.10 mm 中心铜焊盘约为 19.3%。

## Rev A 修改范围

- 保持 U5 的 41 个铜焊盘、网络、位置与物理方向不变；
- 移除中心 pad 41 的整面 `F.Paste`；
- 新增九个仅位于 `F.Paste` 的 0.60 × 0.60 mm 开窗；
- courtyard 从 6.00 × 6.00 mm 更新为 7.25 × 7.25 mm；
- 保持此前已审核的八段角部丝印和 pin-1 标记，移除穿过焊盘的错误近整圆丝印弧；
- 把 U5 的文字与焊盘角度改为 KiCad 原生板坐标表示，不改变物理方向。

## 验证结果

- KiCad 10.0.6 DRC：0 violations、0 unconnected；
- project-library mismatch：0；
- 几何审计：0 errors、0 warnings；
- 68/68 网络物理连通；
- 相对 J2 检查点：U5 之外 PCB 逐字节一致，41 个铜焊盘完全一致；
- 13 个制造层中只有 `GTP` 顶层锡膏几何发生预期变化；
- 隔离干净重建仍得到相同 U5 和 0 条库不匹配。

## 权威来源

- NXP SOT618-1 package information：<https://www.nxp.com/docs/en/package-information/SOT618-1.pdf>
- NXP SOT618-1 package page：<https://www.nxp.com/packages/SOT618-1.html>
- NXP PN7160/PN7161 product and package page：<https://www.nxp.com/products/PN7160?tab=Package_Quality_Tab>
