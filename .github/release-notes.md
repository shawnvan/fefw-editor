## v1.4.5 更新内容 / What's New

### 修复与改进 / Fixes & Improvements
- **安全移除军团兵与镜像保护**：重构资源池军团兵移除逻辑，移除前先安全卸下原持有者的军团引用，仅清零目标槽位而保留其余槽位位置；限制在自由行动与章节开始存档中操作，并仅作用于当前选中槽位，避免单位装备记录损坏或路线镜像误删其他槽位军团。
- **着装读写与镜像健壮性**：修复对象树异常时读取着装标记未捕获 `SaveError` 导致单位列表崩溃的问题；只读回退改为按锚点与着装键唯一识别通用标记表；同路线镜像更改着装时同时支持战前/战斗存档中的待机队员。
- **任务进度页（只读）判定修正**：修复外传支援前置点数判定未生效的问题；第 1 部外传过期判断纳入年份检查（避免莱达第 1 部第 1 章等早期存档误判过期）；补充仅含截止章节支线的期限文本，并补全西提司的招募锁定标记匹配。
- **弹窗、导航与高 DPI 改进**：所有模态弹窗改为隐藏状态下完成布局再居中显示，并在 Linux/X11 上对 `grab_set()` 增加可见性等待重试以防止 `TclError`；恢复 `Ctrl+Tab` / `Ctrl+Shift+Tab` 切页并隐藏非活动页面防止 `Tab` 焦点穿透；将左侧导航栏与任务列表豁免于多选及 `Ctrl+A`；修正高 DPI 下导航列宽与默认头像文字二次缩放，以及大尺寸自定义 PNG 图标缩放失效问题。
- **其他细节修复**：修复「添加到道具袋」不可堆叠道具添加失败后落入堆叠分支的报错覆盖问题，以及选择器搜索归零退格后的预览更新、世界地图「道路」英文地名搜索与军团归属非零判断。

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
