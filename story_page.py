"""Warning-gated chapter selector with route-specific validation evidence."""
import tkinter as tk
from tkinter import ttk, messagebox

import fefw_core as core
import fefw_layer as layer
import story
import story_start
import story_troubleshoot
from zh_names import CHARACTERS

WARNING_TITLE = '注意：更改剧情进度可能导致目标卡住'
WARNING_POINTS = (
    '当前是蕾达第1部存档，可选择第2至12章。第1章的首个目标无法继续，因此不可选。',
    '进入所选章节后，请按需另行补齐此前章节的设施、功能、教程、地图地点、支线任务及队伍成员。',
    '游戏测试覆盖章节开场及推进至下一段剧情或战斗。长期流程仍待验证。',
    '方尖塔大厅依赖章节存档文件。勾选「以当前状态创建章节起始存档」后，保存时会生成所选章节的文件。'
    '跳过的其他章节仍需各自的文件。替换现有文件前会先确认并备份。',
    '继续前，请将存档备份到新目录。',
)
EVIDENCE_NOTE = ('第2至12章已验证开场和后续剧情。编辑器生成的第8、9章文件已通过方尖塔大厅读取验证，'
                 '其他章节仍待验证。切换章节会重设游戏内时间和重开状态。')


def warning_points(route, part=1):
    if part == 3 and route == 9:
        return ('伊修玛尔第3部救世篇：第2至5区分已验证开场和后续推进，第6区分仅验证到开场战斗。'
                '第1区分不可选，请从方尖塔大厅的门进入。',
                '选择区分会沿用当前队伍、继承数据、标记和道具。所需的路线通关状态须在游戏内完成。',
                '进入时会重设游戏内时间和重开状态。游戏可能在开场重组队伍或重设角色属性。救世篇的解锁修复仍待确认。',
                *WARNING_POINTS[3:])
    if part == 2:
        if route not in story.PREFIXES:
            raise story.StoryError('战争篇仅支持四条主角路线。')
        if route != 1:
            return (f'{story.route_name(route)}的战争篇第1至6章已从经过测试的第1部存档推进至下一场战斗。'
                    '赛奥朵拉第6章也已通过自动开放中层通行后的测试。',
                    '第5章开放上层通行，第6章开放中层通行。赛奥朵拉第6章的自动修复已复测通过，'
                    '其余前置条件仍可能阻碍进度。',
                    '进入章节时沿用当前队伍、道具和已有进度。缺少的角色、报酬、设施和地图解锁需另行补齐。'
                    '从其他存档开始、通过方尖塔大厅读取及长期推进仍待验证。',
                    f'可选的章节文件只供所选章节使用。独立的篇章衔接文件 Part_{story.FILE_CODES[route]}_2 需由游戏生成。',
                    *WARNING_POINTS[3:])
        return ('凯伊战争篇第1至6章已使用游戏自然进入的战争篇存档，验证开场及推进至下一场战斗。',
                '第5章开放上层通行以抵达鞑古扎神殿。修复卡住的存档，以及重新进入该章，两种方式均已通过测试。',
                '从凯伊第1部第4章直接进入战争篇第1章，已验证开场和下一场战斗。'
                '其他起始存档、方尖塔大厅读取及长期推进仍待验证。沿用当前队伍和已有进度，缺少前置条件仍可能卡住。',
                *WARNING_POINTS[3:])
    if route == 4:
        return WARNING_POINTS
    if route == 1:
        return ('当前是凯伊第1部存档，第1至12章已验证开场和后续推进。',
                '第3章起会自动开放下水道。其他设施、功能、教程、地图地点、支线任务及此前应加入的角色需另行处理。',
                '应用下水道修复后，直接进入第4章已验证。编辑器生成的凯伊章节文件尚未通过方尖塔大厅测试，长期流程仍待验证。',
                *WARNING_POINTS[3:])
    if route == 2:
        return ('当前是迪托利希第1部存档，可选择第3至12章，均已通过短流程验证。第2章重玩会卡在救援目标，'
                '第1章也会进入该目标，因此两章均不可选。',
                '第5章解除上层行动限制。第6章解除下层行动限制，并开放上下层区域和旺达尔商会。'
                '第7章开放「秘传·不动贯枪」的传授。其他功能和队伍成员需另行补齐。',
                '应用这些条件后，第5、6章已验证直接进入和后续推进。第7章已验证传授和下一检查点。'
                '方尖塔大厅读取和长期流程仍待验证。',
                *WARNING_POINTS[3:])
    if route == 3:
        return ('当前是赛奥朵拉第1部存档，第1至12章已通过短流程验证，包括第4、6、8、9章的通行修复。',
                '第1、2章使用1446年，第3章起使用1449年。第4章开放中层通行，第6、8章开放中层、下层和街区通行，'
                '第9章显示特洛伊亚之祠出口的三个地图点。此前章节的其他解锁和队伍成员需另行补齐。',
                '第6章已通过城市绕行路线继续推进，NPC 路障可能仍在。第8章的路障已解除，两章差异的原因尚未确认。'
                '可在「功能」页调整城市通行。方尖塔大厅读取及长期流程仍待验证。',
                *WARNING_POINTS[3:])
    raise story.StoryError('不支持此剧情路线。')


def evidence_note(route, part=1):
    if part == 3 and route == 9:
        return ('伊修玛尔救世篇第2至5区分已验证开场及后续推进，第6区分仅验证到开场战斗。'
                '第1区分不可选，请从方尖塔大厅的门进入。方尖塔大厅读取和长期流程仍待验证。')
    if part == 2:
        if route != 1:
            return (f'{story.route_name(route)}战争篇第1至6章已从测试过的第1部存档推进至下一场战斗。'
                    '赛奥朵拉第6章的手动修复和带自动中层通行修复的直接进入均已验证。'
                    '其他起始存档、方尖塔大厅读取及长期流程仍待验证。')
        return ('凯伊战争篇第1至6章已通过测试，包括第5章修复和编辑器校验。'
                '第1部第4章直接进入战争篇第1章已通过短流程验证。'
                '其他起始存档、方尖塔大厅读取及长期流程仍待验证。')
    if route == 4:
        return EVIDENCE_NOTE
    if route == 1:
        return ('凯伊第1至12章已验证开场及后续推进，包括直接进入第4章。第3章起开放下水道。'
                '进入章节会重设游戏内时间和重开状态。方尖塔大厅读取仍待验证。')
    if route == 2:
        return ('迪托利希第3至12章和编辑器界面首轮测试均已通过。第2章仅游戏原生检查点可推进，重玩修复均未通过。'
                '第5至7章的修复只调整相关前置条件，沿用原有队伍和教程。')
    if route == 3:
        return ('赛奥朵拉第1至12章和编辑器界面首轮测试均已通过。第6章可绕行，第9章只需修复特洛伊亚之祠出口。'
                '第4、6、8、9章的修复只调整相关通行条件，其余队伍、教程、标记及地图点沿用现状。')
    raise story.StoryError('不支持此剧情路线。')


def chapter_evidence(route, chapter, part=1):
    if part == 3 and route == 9:
        if chapter == 1:
            return '不可选，请通过方尖塔大厅的门进入'
        if chapter == 6:
            return '已进入开场战斗，后续推进待验证'
        return '已在游戏内验证开场和下一场战斗'
    if part == 2:
        if route == 3 and chapter == 6:
            return '自动开放中层通行后，已验证开场和后续推进'
        return ('已在游戏内验证开场和后续推进' if chapter in story_start.PART2_TESTED_BY_ROUTE.get(route, ())
                else '推断，尚待游戏验证')
    if route == 3 and chapter in story_start.THEODORA_REPAIRS:
        return story_start.THEODORA_REPAIRS[chapter]
    if route == 2 and chapter in story_start.DIETRICH_BLOCKERS:
        return '不可选：' + story_start.DIETRICH_BLOCKERS[chapter]
    if route == 2 and chapter in (5, 6):
        return '已在游戏内验证直接进入和后续推进'
    if route == 2 and chapter == 7:
        return '修复传授权限后，已验证传授及下一检查点'
    if chapter in story_start.TESTED_BY_ROUTE[route]:
        return '已在游戏内验证开场和后续推进'
    return '推断，尚待游戏验证'


class StoryPageMixin:
    def _build_story(self, parent):
        card = ttk.Frame(parent, style='Card.TFrame', padding=(16, 8))
        card.pack(fill='x', pady=(10, 0))
        ttk.Label(card, text='剧情进度（实验功能）', style='CardH3.TLabel').pack(anchor='w')
        self.story_status = ttk.Label(card, style='CardMuted.TLabel', wraplength=690, justify='left')
        self.story_status.pack(anchor='w', pady=(4, 0))
        actions = ttk.Frame(card, style='Card.TFrame')
        actions.pack(fill='x', pady=(8, 0))
        self.story_button = ttk.Button(actions, text='选择剧情进度…',
                                       command=self.open_story_progression)
        self.story_button.pack(side='left')
        self.part2_button = ttk.Button(actions, text='尝试进入战争篇…',
                                      command=self.open_part2_entry)
        self.part2_button.pack(side='left', padx=12)
        self.troubleshoot_button = ttk.Button(actions, text='排查进度卡点…',
                                              command=self.open_progress_troubleshooter)
        self.troubleshoot_button.pack(side='left')
        self.refresh_story()

    def refresh_story(self):
        if not hasattr(self, 'story_button'):
            return
        self.story_button.state(['disabled'])
        if hasattr(self, 'part2_button'):
            self.part2_button.state(['disabled'])
        if hasattr(self, 'troubleshoot_button'):
            self.troubleshoot_button.state(['!disabled'] if self.sv else ['disabled'])
        if not self.sv:
            self.story_status.configure(text='请打开支持的第1、2部存档，或伊修玛尔的第3部救世篇存档（暂定支持）。')
            return
        route = layer.route_info(self.sv.payload)
        label = ''
        if route:
            lord, part, chapter = route
            name = CHARACTERS.get(lord, f'路线 {lord.removeprefix("route ")}')
            label = f'{name} · {story.part_name(part)} · {story.chapter_name(part, chapter)}\n'
        if route and (route[0], route[1]) not in (('Leda', 1), ('Cai', 1), ('Dietrich', 1), ('Theodora', 1), ('Cai', 2), ('Leda', 2), ('Dietrich', 2), ('Theodora', 2), ('Eshmel', 3)):
            self.story_status.configure(text=label + '此存档只读。支持四位主角的第1、2部，以及伊修玛尔的第3部救世篇。')
            return
        try:
            info = story_start.validate(self.core_of())
            story.check_outer(self.sv.raw, info, allow_part_change=
                              getattr(self.sv, "_allow_part_transition", False))
        except core.SaveError as exc:
            self.story_status.configure(text=label + str(exc)); return
        pending = getattr(self.sv, '_pending_story_chapter', None)
        note = f'\n保存时将生成 {story.chapter_filename(pending.route, pending.chapter, pending.part)}。' if pending else ''
        evidence = {4: '第2至12章已通过游戏验证，第1章不可选。',
                    1: '第1至12章已通过游戏验证，第3章起开放下水道。',
                    2: '第3至12章已通过游戏验证，包括直接进入第5、6章。第2章的救援目标会卡住，因此第1、2章不可选。',
                    3: '第1至12章已通过游戏验证，第4、6、8、9章有通行修复。第6章需绕行。'}.get(info.route, '')
        if info.part in (2, 3):
            evidence = evidence_note(info.route, info.part)
        if info.part == 2 and getattr(self.sv, '_allow_part_transition', False):
            evidence += ' 当前编辑会话使用了实验性的战争篇入口。'
        self.story_status.configure(text=info.label + '\n' + evidence + note)
        self.story_button.state(['!disabled'])
        if hasattr(self, 'part2_button') and info.route in story.PREFIXES and info.part == 1:
            self.part2_button.state(['!disabled'])

    def _story_ready(self):
        if not self.sv:
            self.flash('请先打开支持的第1、2部存档，或伊修玛尔的第3部救世篇存档。', 'warn'); return False
        if self.mirror.get():
            self.flash('请关闭「同时编辑此路线的其他存档」后再修改剧情进度。', 'warn')
            return False
        try:
            info = story_start.validate(core.SaveFile(self.sv.payload))
            story.check_outer(self.sv.raw, info, allow_part_change=
                              getattr(self.sv, "_allow_part_transition", False))
        except core.SaveError as exc:
            self.flash(str(exc), 'error'); return False
        return True

    def open_progress_troubleshooter(self):
        if not self.sv:
            self.flash('请先打开存档。', 'warn'); return
        slot, snapshot = self.sv, bytes(self.sv.payload)
        try:
            report = story_troubleshoot.diagnose(core.SaveFile(snapshot))
            story.check_outer(slot.raw, report.progress, allow_part_change=
                              getattr(slot, '_allow_part_transition', False))
        except core.SaveError as exc:
            messagebox.showerror('排查进度卡点', str(exc) + '\n\n暂无可安全应用的建议。'
                                 '请在下方地址反馈存档状态和卡住的目标：\n' +
                                 story_troubleshoot.ISSUES_URL, parent=self); return
        win = tk.Toplevel(self); win.withdraw(); win.title('排查进度卡点'); win.transient(self)
        win.configure(background=self.p['bg'])
        frame = ttk.Frame(win, padding=18); frame.pack(fill='both', expand=True)
        buttons = ttk.Frame(frame); buttons.pack(side='bottom', fill='x')
        ttk.Label(frame, text=report.progress.label, style='CardH3.TLabel').pack(anchor='w')
        ttk.Label(frame, text=report.note, wraplength=690, justify='left').pack(anchor='w', pady=(8, 10))
        table = ttk.Treeview(frame, columns=('state',), show='tree headings', height=5,
                             selectmode='none')
        table.heading('#0', text='已知前置条件', anchor='w'); table.column('#0', width=420)
        table.heading('state', text='当前状态与建议', anchor='w'); table.column('state', width=260)
        for row in report.checks:
            table.insert('', 'end', text=row.label, values=(row.state,))
        if report.checks:
            table.pack(fill='both', expand=True, pady=(0, 10))
        status = report.blocked
        if not status and report.checks and not report.actionable:
            status = '所有已知前置条件均已满足，仍可能有其他原因导致目标卡住。'
        if self.mirror.get():
            status += '\n请关闭「同时编辑此路线的其他存档」后，再修复当前槽位。'
        if status:
            ttk.Label(frame, text=status.strip(), wraplength=690, justify='left').pack(anchor='w', pady=(0, 10))
        ttk.Label(frame, text='应用后只补齐表中缺少的解锁条件，沿用当前章节、剧情检查点、日期、队伍和道具。'
                  'Ctrl+S 保存，Ctrl+Z 撤销。',
                  wraplength=690, justify='left', style='CardMuted.TLabel').pack(anchor='w', pady=(0, 10))
        if not report.actionable:
            ttk.Label(frame, text='如果仍卡在当前目标，可反馈角色、部数、章节、目标和受阻位置。'
                      '可附上截图，也可自行决定是否分享存档。',
                      wraplength=690, justify='left').pack(anchor='w', pady=(0, 4))
            def open_issues():
                import webbrowser
                webbrowser.open(story_troubleshoot.ISSUES_URL)
            ttk.Button(frame, text='打开上游项目的反馈页面',
                       command=open_issues).pack(anchor='w', pady=(0, 10))
        def apply():
            if self.apply_progress_repair(slot, snapshot):
                win.destroy()
        apply_button = ttk.Button(buttons, text='应用建议的解锁项', command=apply)
        apply_button.pack(side='right')
        if not report.actionable or self.mirror.get():
            apply_button.state(['disabled'])
        ttk.Button(buttons, text='关闭', command=win.destroy).pack(side='right', padx=8)
        win.bind('<Escape>', lambda e: win.destroy()); self.show_dialog(win, 740, 570); self.wait_window(win)

    def apply_progress_repair(self, slot, snapshot):
        if self.sv is not slot or bytes(slot.payload) != snapshot:
            self.flash('所选存档已变更，请重新打开「排查进度卡点」。', 'warn'); return False
        if not self._story_ready():
            return False
        self._commit_form()
        # Form edits can invalidate the diagnosis; never apply a stale plan.
        if self.sv is not slot or bytes(slot.payload) != snapshot:
            self.flash('待应用的编辑已改变存档，请重新打开「排查进度卡点」。', 'warn'); return False
        try:
            new = story_troubleshoot.edited(core.SaveFile(slot.payload))
        except core.SaveError as exc:
            self.flash(str(exc), 'error'); return False
        if new == slot.payload:
            self.flash('没有需要补齐的已知解锁项。', 'info'); return False
        self.push_undo()
        slot.payload = bytearray(new); slot.scan(); slot._core_cache = None
        self._touch([slot]); self.refresh_after_core()
        self.flash('已暂存建议的解锁项。Ctrl+S 保存，Ctrl+Z 撤销。', 'ok')
        return True

    def open_story_progression(self):
        if not self._story_ready():
            return
        slot = self.sv
        initial = story.read(core.SaveFile(slot.payload))
        route, part = initial.route, initial.part
        if not self._confirm_story_warning():
            return
        if self.sv is not slot or not self._story_ready():
            return
        current = story.read(core.SaveFile(slot.payload))
        if (current.route, current.part) != (route, part):
            self.flash('提示窗口打开期间，路线或部数已改变，请重新选择剧情进度。', 'warn'); return
        choice = self._choose_story_chapter()
        if choice is not None and self.sv is slot and self._story_ready():
            current = story.read(core.SaveFile(slot.payload))
            if (current.route, current.part) != (route, part):
                self.flash('选择章节期间，路线或部数已改变，请重新选择剧情进度。', 'warn'); return
            self.apply_story_chapter(*choice)

    def _confirm_story_warning(self):
        info = story.read(core.SaveFile(self.sv.payload))
        route, part = info.route, info.part
        win = tk.Toplevel(self); win.withdraw(); win.title(WARNING_TITLE); win.transient(self)
        win.configure(background=self.p['bg']); win.resizable(False, False)
        frame = ttk.Frame(win, padding=20); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=WARNING_TITLE, style='CardH3.TLabel', wraplength=670).pack(anchor='w', pady=(0, 12))
        for point in warning_points(route, part):
            ttk.Label(frame, text='• ' + point, wraplength=665, justify='left').pack(anchor='w', pady=(0, 10))
        ttk.Label(frame, text=evidence_note(route, part), wraplength=665, justify='left',
                  style='CardMuted.TLabel').pack(anchor='w', pady=(0, 8))
        result = [False]
        def finish(value):
            result[0] = value; win.destroy()
        buttons = ttk.Frame(frame); buttons.pack(side='bottom', fill='x', pady=(10, 0))
        ttk.Button(buttons, text='继续', command=lambda: finish(True)).pack(side='right')
        cancel = ttk.Button(buttons, text='取消', command=lambda: finish(False))
        cancel.pack(side='right', padx=8)
        win.bind('<Escape>', lambda e: finish(False)); win.protocol('WM_DELETE_WINDOW', lambda: finish(False))
        self.show_dialog(win, 720, 570, focus=cancel); self.wait_window(win)
        return result[0]

    def _choose_story_chapter(self):
        info = story_start.validate(core.SaveFile(self.sv.payload))
        lord = story.route_name(info.route)
        part = story.part_name(info.part)
        section = '区分' if info.part == 3 else '章节'
        chapters = story_start.chapters_for(info.route, info.part)
        win = tk.Toplevel(self); win.withdraw(); win.title(f'{lord} · {part} · 选择{section}'); win.transient(self)
        win.configure(background=self.p['bg'])
        frame = ttk.Frame(win, padding=18); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=f'选择起始{section}', style='CardH3.TLabel').pack(anchor='w')
        limit = {4: '第1章不可选。', 1: '凯伊第3章起也会开放下水道。',
                 2: '迪托利希第2章的救援目标会卡住，因此第1、2章不可选。第5至7章只调整相关前置条件，沿用原有队伍和教程。',
                 3: '赛奥朵拉第4、6、8、9章会调整相关通行条件。第6章可绕过 NPC 路障。'
                    '第1、2章使用1446年，第3章起使用1449年。沿用原有队伍和教程。'}.get(info.route, '')
        if info.part in (2, 3):
            limit = evidence_note(info.route, info.part)
        ttk.Label(frame, text=f'只编辑所选存档。应用后会暂存{section}起始状态及日期。Ctrl+S 保存，Ctrl+Z 撤销。{limit}',
                  wraplength=625, justify='left').pack(anchor='w', pady=(4, 10))
        buttons = ttk.Frame(frame); buttons.pack(side='bottom', fill='x', pady=(12, 0))
        options = ttk.Frame(frame); options.pack(side='bottom', fill='x', pady=(10, 0))
        create_file = tk.BooleanVar(value=getattr(self.sv, '_pending_story_chapter', None) is not None)
        ttk.Checkbutton(options, text='以当前状态创建章节起始存档',
                        variable=create_file).pack(anchor='w')
        ttk.Label(options, text='保存时才会在所选存档的目录中创建文件。若同名文件已存在，须先确认替换并保留 .bak 备份。',
                  wraplength=625, justify='left', style='CardMuted.TLabel').pack(anchor='w', pady=(4, 0))
        table = ttk.Frame(frame); table.pack(fill='both', expand=True)
        table.rowconfigure(0, weight=1); table.columnconfigure(0, weight=1)
        tree = ttk.Treeview(table, columns=('evidence',), show='tree headings', height=12, selectmode='browse')
        tree.heading('#0', text=section, anchor='w'); tree.column('#0', width=150, stretch=False)
        tree.heading('evidence', text='验证情况与限制', anchor='w'); tree.column('evidence', width=450)
        scroll = ttk.Scrollbar(table, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky='nsew'); scroll.grid(row=0, column=1, sticky='ns')
        for chapter in chapters:
            text = chapter_evidence(info.route, chapter, info.part)
            tree.insert('', 'end', iid=str(chapter), text=story.chapter_name(info.part, chapter), values=(text,))
        current = str(info.chapter if info.chapter in chapters else chapters[0])
        tree.selection_set(current); tree.see(current); tree.focus(current)
        result = [None]
        def apply():
            picked = tree.selection()
            if picked:
                result[0] = (int(picked[0]), create_file.get()); win.destroy()
        ttk.Button(buttons, text='应用起始进度', command=apply).pack(side='right')
        ttk.Button(buttons, text='取消', command=win.destroy).pack(side='right', padx=8)
        win.bind('<Escape>', lambda e: win.destroy()); self.show_dialog(win, 680, 540); self.wait_window(win)
        return result[0]

    def apply_story_chapter(self, chapter, create_file=False):
        if not self._story_ready():
            return
        try:
            info = story_start.validate(core.SaveFile(self.sv.payload))
            route, part = info.route, info.part
            story_start.target(chapter, route, part)
            self._commit_form()
            if not self._story_ready():
                return
            current = story.read(core.SaveFile(self.sv.payload))
            if (current.route, current.part) != (route, part):
                raise story.StoryError('路线或部数已改变，请重新选择剧情进度。')
            new = story_start.edited(core.SaveFile(self.sv.payload), chapter)
        except core.SaveError as exc:
            self.flash(str(exc), 'error'); return
        pending = story.ChapterFileRequest(route, chapter, part) if create_file else None
        if bytes(new) == bytes(self.sv.payload) and pending == getattr(self.sv, '_pending_story_chapter', None):
            self.flash('该起始进度已暂存。', 'info'); return
        self.push_undo()
        self.sv.payload = bytearray(new); self.sv.scan(); self.sv._core_cache = None
        self.sv._pending_story_chapter = pending
        self._touch([self.sv]); self.refresh_after_core()
        self.flash(f'已暂存{story.route_name(route)}{story.part_name(part)}{story.chapter_name(part, chapter)}的起始进度。'
                   'Ctrl+S 保存，Ctrl+Z 撤销。', 'ok')


    def open_part2_entry(self):
        if not self._story_ready():
            return
        info = story_start.validate(self.core_of())
        if info.route not in story.PREFIXES or info.part != 1:
            self.flash('请使用支持的第1部自由行动或章节开始存档。', 'warn'); return
        slot = self.sv
        snapshot = bytes(slot.payload)
        if not messagebox.askyesno('尝试进入战争篇（实验功能）',
                f'要从当前{story.route_name(info.route)}的第1部存档进入战争篇第1章吗？\n\n'
                '凯伊第4章、蕾达第9章、迪托利希第4章和赛奥朵拉第3章的存档已验证进入战争篇第1章及后续推进。'
                '其他起始存档和长期流程仍待验证。会沿用当前角色、道具、标记和世界状态。'
                '缺少的篇章衔接报酬或解锁条件须另行补齐，否则目标可能卡住。\n\n'
                f'所选存档将切换至战争篇。独立的篇章衔接文件 Part_{story.FILE_CODES[info.route]}_2 需由游戏生成，'
                f'另行创建的 Chapter_{story.FILE_CODES[info.route]}_2_N 章节文件无法代替它。\n\n'
                '请使用备份副本。应用只影响当前槽位。Ctrl+S 保存，Ctrl+Z 撤销。'
                '如需章节文件，请另行创建。', parent=self):
            return
        if self.sv is not slot or bytes(slot.payload) != snapshot:
            self.flash('所选存档已变更，请重新打开战争篇入口。', 'warn'); return
        self.apply_part2_entry()

    def apply_part2_entry(self):
        if not self._story_ready():
            return
        try:
            self._commit_form()
            if not self._story_ready():
                return
            new = story_start.enter_part2(self.core_of())
        except core.SaveError as exc:
            self.flash(str(exc), 'error'); return
        self.push_undo()
        # Session-only permission for the explicit conversion and its Undo/Redo.
        # Keep raw untouched: serialization derives the part mirror from payload.
        self.sv._allow_part_transition = True
        self.sv.payload = bytearray(new); self.sv.scan(); self.sv._core_cache = None
        self.sv._pending_story_chapter = None
        self._touch([self.sv]); self.refresh_after_core()
        lord = story.route_name(story.read(self.core_of()).route)
        self.flash(f'已暂存{lord}战争篇第1章的实验性起始进度。Ctrl+S 保存，Ctrl+Z 撤销。', 'ok')
