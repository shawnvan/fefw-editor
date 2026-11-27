## v1.4.4 更新内容 / What's New

### 新增功能 / Features
- **只读任务进度、前置条件与招募前置查看**：新增 `quest_catalog.json`、`quests.py` 与「任务」页面，只读展示全部 9 个外传、181 个支线及 67 位角色的招募前置状态。结合当前路线、剧情部数/章节、游戏内日期时间、名声等级与周目数，自动标注「可接取 / 进行中 / 可汇报 / 已完成 / 已过期失效 / 前置条件未满足 / 无法判定」等状态，支持按类型与状态筛选、状态优先排序，以及双击或按钮快捷跳转到前置/关联任务。

### 改进与打包 / Improvements & Packaging
- **左侧分组导航与清晰字号**：以左侧分组垂直导航栏与 `_PageStack` 替代拥挤的顶部横向页签，放大基础字号并消除切换页签时的白屏闪烁。
- **弹窗居中防闪烁**：所有模态弹窗改为先隐藏布局、计算居中坐标后再显示，消除从左上角闪现移动的问题。
- **发布包资源纳入**：`.github/workflows/release.yml` 已将 `quest_catalog.json` 打入 Windows 与 Linux 构建产物，并在打包阶段直接校验压缩包内资源完整性。

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
