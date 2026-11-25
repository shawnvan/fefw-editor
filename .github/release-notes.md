## v1.4.2 更新内容 / What's New

### 新增功能 / Features
- **单位着装设置查看与编辑**：在「单位」列表新增「着装」列，并在角色名称旁显示当前着装状态（默认 / 职业 / 固有）；双击状态标签或点击 **「更改着装…」** 可切换为默认（移除标记）、职业外观（`兵種衣装優先 = 1`）或固有外观（`固有衣装優先 = 2`），支持读回校验与 Undo/Redo。
- **`Costume.bin` 服装限制检查**：内置 `costume_rules.json` 限制规则表，结合角色、当前兵种、骑乘/下马技能（`SID_乗り降り`）、剧情部数及全局禁换装标记（`衣装_着せ替え無効`），在修改时检测命中限制并在确认后才执行写入。
- **Flags 标记目录补全**：在 `flag_names.json` / `flag_labels.py` 中补齐 21 条 `衣装_*` 英文名称并保留原始日文键名，同时补充 `flag_display.py` 中文展示（包括 `衣装_着せ替え無効` → `禁止更改服装`）。

### 安全与镜像修复 / Fixes & Packaging
- **镜像同步逐槽核验**：镜像编辑时逐槽检查状态，缺少该角色的槽位自动跳过；当当前槽位已是目标着装时，仍会更新同路线下需要同步的其它槽位。
- **角色身份与存在性校验**：写入着装标记前核验角色内部 `PID` 与可玩角色身份（排除敌人、NPC 与未识别单位），并确认该单位存在于目标槽位后再写入。
- **发布打包包含与校验**：Windows 与 Linux 发布包已加入 `costume_rules.json`，CI 构建后会从可执行文件中提取该文件并与源码核对一致性。

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
