"""Recruit preview and identity controls with same-route mirroring."""
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

import fefw_core as core
from fefw_save import chinese_name
import unit_entries as entries
from ui_util import ScrollableFrame
from zh_names import CHARACTERS


EVIDENCE_ZH = {
    'Data-derived; game test pending': '根据数据推断，尚待游戏验证',
    'Passed Leda Chapter 9 addition, deployment and progression checks; other contexts untested':
        '已在蕾达第9章验证添加、出击和后续推进，其他场景尚待验证',
    'Special case: passed Leda Chapter 9 with a helmeted portrait and an unhelmeted generic battle model. Custom appearance/name is not supplied':
        '已在蕾达第9章验证。头像戴头盔，战斗模型为未戴头盔的通用模型，自定义外观和姓名未写入',
    'Starts dismounted: passed Leda Chapter 9. Mounted Elephant Rider also passed Part 3 Chapter 2; earlier Leda deployment failed. Remounting and the map/unlock requirements remain unverified':
        '以解除骑乘的状态加入。已在蕾达第9章验证。骑乘状态的战象兵已在救世篇第2区分通过测试，但此前在蕾达路线出击失败。重新骑乘及地图和解锁条件尚待验证',
}


def recruit_name(person, row):
    return chinese_name("units", person) or CHARACTERS.get(row["name"], row["name"])


class UnitEntryPageMixin:
    def _unit_entry_ready(self):
        if not self.sv: return False
        self._commit_form()
        try: entries.require_context(self.core_of())
        except core.SaveError as exc:
            self.flash(str(exc), 'error');return False
        return True

    def apply_unit_entry(self, action, person, number=None, expected=None):
        if not self._unit_entry_ready():return False
        if expected is not None and (self.sv is not expected[0] or bytes(self.sv.payload)!=expected[1]):
            self.flash('所选存档已变更，请重新打开单位预览。', 'warn');return False
        def edit(f):
            entries.require_context(f)
            if action in ('add', 'restore'):
                state = entries.status(f, person)
                if state == 'Already in party':
                    return f.payload
                if state.startswith('Existing inactive'):
                    return entries.restore(f, person)
                return entries.add(f, person)  # Reject duplicates/unknown states.
            if action == 'renumber':
                return entries.renumber(f, person, number)
            raise core.SaveError('无法识别此单位操作。')
        # The preview action is exact for the selected slot. Peers may need
        # creation, flag restoration, or no change, and allocate numbers locally.
        try:
            if action == 'add': entries.add(self.core_of(), person)
            elif action == 'restore': entries.restore(self.core_of(), person)
        except core.SaveError as exc:
            self.flash(str(exc), 'error'); return False
        if action in ('add', 'restore'):
            try:
                targets=[self.core_of(sv) for sv in self._targets()]
                for f in targets:
                    entries.require_context(f)
                    entries.roster(f)
            except core.SaveError as exc:
                self.flash(f'未做更改：{exc}', 'error');return False
            if any(entries.context(f)=='pre-battle' for f in targets):
                if not messagebox.askokcancel('出击准备存档提示',
                        '至少一个目标存档处于出击准备阶段。\n\n'
                        '添加的单位可能以较低的 HP 或能力值开场，也可能暂时无法在编辑器中显示。'
                        '游戏初始化战斗时可能改变该单位。\n\n'
                        '仍要应用修改吗？', icon='warning', default='cancel', parent=self):
                    return False
        label = {'add':'已添加缺少的单位', 'restore':'已恢复单位加入状态',
                 'renumber':'已更新单位编号及已知装备归属记录'}.get(action, '已编辑单位')
        if not self._core_edit(edit, label):
            return False
        self.uid=person;self.show_unit()
        return True

    def open_add_unit(self):
        if not self._unit_entry_ready():return
        f=self.core_of();expected=(self.sv,bytes(self.sv.payload))
        try:
            rows=entries.roster(f);flags=entries.flag_values(f);cat=entries.catalog()
            states={h:entries.status(f,h,rows,flags) for h in cat}
            try:number=entries.next_number(f);allocation_error=''
            except core.SaveError as exc:number=None;allocation_error=str(exc)
        except core.SaveError as exc:self.flash(str(exc),'error');return
        from editor_advanced import class_label
        win=tk.Toplevel(self);win.title('添加单位预览');win.transient(self)
        win.configure(background=ttk.Style(win).lookup('TFrame','background'))
        win.columnconfigure(0,weight=1)
        win.rowconfigure(1,weight=3,minsize=100)
        win.rowconfigure(2,weight=2,minsize=110)
        top=ttk.Frame(win,padding=12);top.grid(row=0,column=0,sticky='ew')
        ttk.Label(top,text='选择单位',style='CardH3.TLabel').pack(anchor='w')
        context=entries.context(f)
        note = {'pre-battle': '出击准备：添加的单位可能以较低的 HP 或能力值开场，也可能暂时无法在编辑器中显示。应用前会再次提示。',
                'chapter-start': '已在测试过的章节开场验证添加单位。其他章节脚本仍可能重设或替换单位。'}.get(context, '')
        if note:
            context_note=ttk.Label(top,text=note,wraplength=805)
            context_note.pack(fill='x',pady=(6,0))
            top.bind('<Configure>',lambda event: context_note.configure(
                wraplength=max(180,event.width-24)))
        search=ttk.Entry(top);search.pack(fill='x',pady=(8,0))
        frame=ttk.Frame(win,padding=(12,0));frame.grid(row=1,column=0,sticky='nsew')
        frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
        tree=ttk.Treeview(frame,columns=('class','status'),show='tree headings',selectmode='browse',height=6)
        tree.heading('#0',text='单位');tree.column('#0',width=180,minwidth=140)
        tree.heading('class',text='初始兵种');tree.column('class',width=250,minwidth=230)
        tree.heading('status',text='状态');tree.column('status',width=340,minwidth=260)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=scroll.set)
        hscroll=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview);tree.configure(xscrollcommand=hscroll.set)
        tree.grid(row=0,column=0,sticky='nsew');scroll.grid(row=0,column=1,sticky='ns')
        hscroll.grid(row=1,column=0,sticky='ew')
        details=ScrollableFrame(win)
        details.grid(row=2,column=0,sticky='nsew',padx=12,pady=(8,0))
        detail=ttk.Label(details.body,wraplength=760,justify='left',padding=(0,6))
        detail.pack(fill='x',anchor='nw')
        details.canvas.bind('<Configure>',lambda event: detail.configure(wraplength=max(180,event.width-12)),add='+')
        bar=ttk.Frame(win,padding=12);bar.grid(row=3,column=0,sticky='ew')
        selected={'person':None,'action':None}
        def choose(*_):
            details.canvas.yview_moveto(0)
            picked=tree.selection();selected.update(person=None,action=None)
            apply.state(['disabled'])
            if not picked:detail.configure(text='选择单位后可预览修改。');return
            h=int(picked[0],16);r=cat[h];state=states[h]
            action=('add' if state.startswith('Not present') and number is not None else
                    'restore' if state.startswith('Existing inactive') else None)
            selected.update(person=h,action=action)
            if action == 'restore':
                preview = '恢复加入和会面标记，沿用现有单位及其编号。'
            else:
                preview = (f"LV 1 · {class_label(int(r['class'], 16))} · HP {r['hp']} · "
                           f"单位编号 {number if number is not None else '不可用'}\n"
                           f"{len(r['items'])} 件初始道具 · {len(r['magic'])} 项魔法 · "
                           f"{len(r['skills'][5])} 项已习得的能力。新单位以无坐骑和军团兵的状态加入。")
            if action == 'add':
                preview += '\n将创建新的单位记录。'
            preview += f"\n加入标记（原始键名）：{r['recruit_flag']}"
            mirror_note = ('\n启用镜像时，每个目标会保留现有单位或恢复其加入状态。'
                           '若缺少该单位，将分配本槽位的空闲编号。编辑前会校验所有目标。' if self.mirror.get() else '')
            evidence = EVIDENCE_ZH.get(r['evidence'], r['evidence'])
            scope = ('适用路线和部数尚待验证。单位通过编辑器加入，游戏内招募事件需另行触发。'
                     if r['route_scope'] == 'Route/part eligibility unverified; this is not a natural recruitment event.'
                     else r['route_scope'])
            detail.configure(text=f"{recruit_name(h, r)} · {entries.status_label(state)}\n{preview}\n"
                                  f"{evidence}。{scope}\n加入状态可能影响后续剧情。"
                                  "支持撤销和重做，按 Ctrl+S 写入存档。" + mirror_note
                                  + (f'\n{allocation_error}' if allocation_error and action != 'restore' else ''))
            apply.configure(text='恢复加入状态' if action=='restore' else '添加单位')
            if action:apply.state(['!disabled'])
        def fill(*_):
            keep=tree.selection();tree.delete(*tree.get_children());q=search.get().strip().casefold()
            for h,r in sorted(cat.items(),key=lambda item:recruit_name(*item).casefold()):
                label=class_label(int(r['class'],16))
                name = recruit_name(h, r)
                state = entries.status_label(states[h])
                if q and q not in f"{name} {r['name']} {r['pid']} {label} {state} {states[h]}".casefold():continue
                tree.insert('','end',iid=f'{h:08x}',text=name,values=(label,state))
            if keep and tree.exists(keep[0]):tree.selection_set(keep)
            choose()
        def commit():
            if selected['action'] and self.apply_unit_entry(selected['action'],selected['person'],expected=expected):win.destroy()
        apply=ttk.Button(bar,text='添加单位',style='Accent.TButton',command=commit);apply.pack(side='right')
        ttk.Button(bar,text='取消',command=win.destroy).pack(side='right',padx=8)
        search.bind('<KeyRelease>',fill);tree.bind('<<TreeviewSelect>>',choose)
        self.place_dialog(win,920,760)
        win.minsize(min(self.px(580),win.winfo_screenwidth()-40),
                    min(self.px(420),win.winfo_screenheight()-80))
        fill();search.focus_set();win.grab_set();self.wait_window(win)

    def open_unit_number(self):
        if not self._unit_entry_ready() or self.uid is None:return
        person=self.uid;expected=(self.sv,bytes(self.sv.payload))
        try:
            if entries.context(self.core_of()) != 'free-time':
                raise core.SaveError('请使用自由行动存档修改单位编号。此操作无法重写战斗中的单位引用。')
            rows=[g for u,g in entries.roster(self.core_of()) if u.person==person]
            if len(rows)!=1:raise core.SaveError('未选中唯一的单位记录。')
            old=rows[0].unit_number
        except core.SaveError as exc:self.flash(str(exc),'error');return
        number=simpledialog.askinteger('修改单位编号',
            f'当前编号：{old}\n请输入未使用的编号（1–254）。\n已知的坐骑与军团兵归属记录也会更新。\n此操作仍为实验功能，尚待游戏验证。',
            initialvalue=old,minvalue=1,maxvalue=254,parent=self)
        if number is not None:self.apply_unit_entry('renumber',person,number,expected)
