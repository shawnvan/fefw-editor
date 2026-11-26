## v1.4.3 更新内容 / What's New

### 新增功能 / Features
- **角色头像与单位列表图标**：内置 112 张具名角色透明底 PNG 头像（120×120，存于 `icons/units/`，覆盖 `unit_catalog.json` 中 66/67 个可添加角色及全部 61 个支援角色），在「单位」「支援情报」详情卡片与单位、支援、兵种、坐骑及「添加单位」列表中直接显示角色头像；泛用单位与未配图角色保留彩色圆点与文字徽章兜底。
- **按熟练度与兵种补全战技/特技与魔法**：新增 `learnset_catalog.json` 与 `learnsets.py`，在「单位 → 战技与特技」与「兵种」页支持按当前熟练度、等级及已精通兵种预览并补全应得战技、特技与魔法（支持单人/全队补全、魔法栏同步写入、高阶替换低阶、第 3 部开关及 Undo/Redo）。

### 改进与打包 / Improvements & Packaging
- **头像匹配与高分屏缩放修复**：扩展 `unit_image()` 支持十六进制 `uid`、显示名、英文原名 / `_slug` 及剥离章节前缀后的主角色 `PID` 查找，修复中文界面下英文文件名无法命中及 `_slug(中文)` 为空的问题；`_load_png()` 支持非整倍 DPI 缩放。
- **发布包资源纳入**：更新 `.github/workflows/release.yml` 与 `icons/README.md`，Windows 与 Linux 发布压缩包已随附 `icons/units/` 头像及 `learnset_catalog.json`（CI 构建阶段校验目录加载）。

## Download

| File | For |
|---|---|
| **FEFW-Save-Editor-…-windows.zip** | Windows — no Python needed. Unzip anywhere and run `FEFW Save Editor.exe`. |
| **FEFW-Save-Editor-…-linux-x86_64.tar.gz** | Linux (glibc 2.35+: Arch, Fedora, Debian 12, Ubuntu 22.04 and newer) — no Python needed. Extract and run `./fefw-save-editor`. |
| FEFW-Save-Editor-…-source.zip | macOS / anyone with Python 3.10+: `pip install -r requirements.txt`, then `python editor.py`. |

**Close the game and back up your save folder first.** The first save next to
each slot also keeps the untouched original as `<slot>.bak`, and every save
after that keeps the copy it replaced as `<slot>.bak1`, `.bak2` and so on.

The `.exe` isn't code-signed, so Windows SmartScreen may say "Windows protected
your PC" — click *More info → Run anyway*. Some antivirus tools also flag
PyInstaller-built programs by mistake; the source zip is the same program if
you'd rather run it with Python.

Settings are saved next to the `.exe` (or Linux binary), so keep it in a folder you can write to
(not `Program Files`).
