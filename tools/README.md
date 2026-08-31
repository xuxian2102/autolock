# tools/

Rev A 的原理图/PCB 生成、布线、审计、制造导出与发布打包脚本。

## 路径约定

仓库在整理时把 `kicad/`、`docs/`、`production/` 收进了 `hardware/`，
脚本里对应引入了一个常量：

```python
ROOT     = HERE.parent        # 仓库根
HARDWARE = ROOT / "hardware"  # KiCad 工程 / 原理图 PDF / 制造文件
```

- 当前工作树的路径一律走 `HARDWARE`（`HARDWARE / "kicad"`、`HARDWARE / "production" / "gerbers"` …）。
- `reports/`、`design/`、`firmware/`、`tools/` 仍在仓库根，继续走 `ROOT`。
- `BOARD_PATH`、`LIB_OUT`、`SCHEMATIC_OUT` 都由 `generate_schematics.py` 的
  `OUT = PROJECT_ROOT / "hardware" / "kicad"` 派生，改工程位置只需要改这一行。

## 基线差分脚本的例外

`audit_*_delta.py` 会把一个**历史基线归档**解包后与当前文件逐字节比对。
那些归档是整理之前打的包，内部仍是旧布局（`kicad/…`、`production/…`）。
因此这些脚本里：

- `old_root` / `baseline` 一侧的相对路径**保持旧布局，不要改**；
- 当前工作树一侧走 `HARDWARE`，并用 `relative_to(HARDWARE)` 取键，
  这样两侧的比对键仍然都是 `kicad/…`、`production/…`，比对结果不受目录调整影响。

## 运行前提（整理之前就存在）

脚本依赖仓库之外的一套便携工具链，不随仓库分发：

- `../.tools/py`：`kiutils` 等 Python 依赖；
- `../.tools/kicad10-full-root`：KiCad 10.0.6 便携安装，供官方 DRC 和 Gerber 导出调用。

缺少这两者时脚本无法运行；这与目录整理无关。

## 发布链

`build_release.py` 按顺序跑完审计与导出，然后只生成一个上传用归档
`hardware/production/gerber.zip`，并刷新 `hardware/production/CHECKSUMS.sha256`。

整理前它还会额外打一个 `*_RevA_Review_Package.zip`，
而那个 zip 又会被外层 tar 再包一次，导致同一套 Gerber/BOM/报告在仓库里存三份。
现在仓库本身就是评审包，不再生成该 zip。
