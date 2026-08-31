# official-export（去重说明）

这个目录原本保存 KiCad 10.0.6 官方导出的一整套制造文件（11 层 Gerber + PTH/NPTH 钻孔），
文件名是 KiCad 原生格式（`-F_Cu.gtl`、`-In1_Cu.g1` 等）。

经逐层比对，这 13 个文件与 `hardware/production/gerbers/` 中的发布件**几何完全一致**，
只有导出时间戳不同（本目录 13:08:30，发布件 13:23:31，同一份板文件的两次导出）。
为避免同一套制造数据在仓库里存两份，这 13 个重复文件已删除，
**以 `hardware/production/gerbers/` 为唯一权威制造数据**。

本目录保留的是发布件里没有、且仍有复核价值的文件：

| 文件 | 用途 |
|---|---|
| `HomeKey-Lock-RevA-PN7161-PTH-drl_map.svg` | PTH 钻孔位置图，用于人工核对孔位与长槽 |
| `HomeKey-Lock-RevA-PN7161-NPTH-drl_map.svg` | NPTH 钻孔位置图，用于核对 J2 定位柱孔与 M3 孔 |
| `HomeKey-Lock-RevA-PN7161-job.gbrjob` | Gerber X2 作业文件，记录 4 层叠构与层序 |
| `drill_report.txt` | 钻孔统计（PTH 240 圆孔 + 4 长槽，NPTH 6 圆孔） |

注意：`.gbrjob` 内引用的是 KiCad 原生文件名，与发布件的 `.GTL/.G2/.G3/.GBL` 命名不对应，
它只作为叠构/层序的说明文档，不要连同发布件一起上传给板厂。
