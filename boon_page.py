"""Selected-save Boon tier and activation controls."""
from tkinter import ttk

import boons
import fefw_core as core


EVIDENCE_ZH = {"Game-tested": "游戏已验证", "Save-verified": "存档已验证", "Inferred": "推断"}


class BoonPageMixin:
    def _build_boons(self):
        page = self.boons_page
        top = ttk.Frame(page, style="Card.TFrame", padding=(14, 12))
        top.pack(fill="x")
        ttk.Label(top, text="救世的祝福", style="CardH3.TLabel").pack(anchor="w")
        self.boon_count = ttk.Label(top, style="CardMuted.TLabel", wraplength=900)
        self.boon_count.pack(anchor="w", pady=(6, 0))
        self.boon_observation = ttk.Label(top, style="CardMuted.TLabel", wraplength=900)
        self.boon_observation.pack(anchor="w", pady=(4, 0))
        self.boon_search = ttk.Entry(top)
        self.boon_search.pack(fill="x", pady=(8, 0))
        self.boon_search.bind("<KeyRelease>", lambda e: self.refresh_boons())
        ttk.Label(page, style="CardFaint.TLabel", justify="left", wraplength=900, text=(
            "修改等级仅更新祝福的解锁等级，全部设为最高级时也会保留各项祝福的启用状态。\n"
            "可按中文、英文或日文搜索。支持同路线镜像编辑。Ctrl+S 保存，Ctrl+Z 撤销，Ctrl+Y 重做。"
        )).pack(side="bottom", anchor="w", pady=(8, 0))
        form = ttk.Frame(page, style="Card.TFrame", padding=(14, 10))
        form.pack(side="bottom", fill="x", pady=(8, 0))
        self.boon_detail = ttk.Label(form, style="CardMuted.TLabel", wraplength=880, justify="left")
        self.boon_detail.pack(anchor="w")
        actions = ttk.Frame(form, style="Card.TFrame")
        actions.pack(fill="x", pady=(8, 0))
        buttons = (
            ('boon_set', '设为所选等级', self.set_boon_tier),
            ('boon_remove', '移除祝福', lambda: self.set_boon_tier(remove=True)),
            ('boon_on', '启用', lambda: self.set_boon_active(True)),
            ('boon_off', '停用', lambda: self.set_boon_active(False)),
            ('boon_max', '全部设为最高级', self.max_boons),
        )
        for name, label, action in buttons:
            button = ttk.Button(actions, text=label, style="Small.TButton", command=action, state="disabled")
            button.pack(side="left", padx=(0, 8))
            setattr(self, name, button)
        frame = ttk.Frame(page, style="Card.TFrame")
        frame.pack(fill="both", expand=True, pady=(8, 0))
        tree = self.boon_tree = ttk.Treeview(frame,
            columns=("tier", "state", "value", "evidence", "cost", "clears", "jp"),
            show="tree headings", selectmode="browse")
        for col, label, width in (("#0", "祝福效果", 240), ("tier", "等级", 50),
                ("state", "存档状态", 145), ("value", "数值", 55),
                ("evidence", "验证程度", 105), ("cost", "因果碎片", 90),
                ("clears", "所需通关次数", 115), ("jp", "原始组名", 145)):
            tree.heading(col, text=label, anchor="w")
            tree.column(col, width=width, minwidth=width, stretch=col in ("#0", "jp"))
        tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree.bind("<<TreeviewSelect>>", lambda e: self._select_boon())
        self._boon_rows = {}
        self._boon_state = None
        self._boon_error = "请先打开存档。"
        self._select_boon()

    def refresh_boons(self):
        if not hasattr(self, "boon_tree"):
            return
        tree = self.boon_tree
        keep = tree.selection()
        tree.delete(*tree.get_children())
        self._boon_rows = {}
        self._boon_state = None
        self._boon_error = "请先打开存档。"
        self.boon_observation.configure(text="打开存档后可查看祝福的解锁状态。")
        self._select_boon()
        try:
            groups = boons.catalog()
            rows = boons.matching_rows(self.boon_search.get())
        except core.SaveError as exc:
            self.boon_count.configure(text=str(exc))
            return
        summary = f"显示 {len(rows)}/{sum(len(g['tiers']) for g in groups)} 个等级 · 共 {len(groups)} 项祝福"
        try:
            f = self.core_of()
            if f is not None:
                self._boon_state = state = boons.read_state(f)
                summary += f" · 因果碎片：{state['karma_shards']:,}"
                owned = sum(not boons.group_edit_reason(g, state['groups'][g['name']])
                            and (state['groups'][g['name']]['level'] or 0) > 0 for g in groups)
                self.boon_observation.configure(text=(
                    f"已解锁 {owned}/{len(groups)} 项 · 游戏已验证：获得经验值增加第 1 级、武器购买金额折扣第 1 至 2 级。"
                    "存档已验证：格斗术经验值增加和黑魔术经验值增加第 1 级。其余等级为推断。"))
        except core.SaveError as exc:
            self._boon_error = str(exc)
            self.boon_observation.configure(text=str(exc))
        for iid, group, tier in rows:
            self._boon_rows[iid] = (group, tier)
            saved = (boons.row_state(group, tier, self._boon_state['groups'][group['name']], zh=True)
                     if self._boon_state else '不可用')
            evidence = EVIDENCE_ZH[boons.evidence_label(group['name'], tier['tier'])]
            tree.insert("", "end", iid=iid, text=boons.localized_name(group), values=(
                tier['tier'], saved, tier['value'], evidence, tier['cost'], tier['clear_count'], group['name']))
        self.boon_count.configure(text=summary)
        tree.selection_set([i for i in keep if tree.exists(i)])
        self._select_boon()

    def _selected_boon(self):
        selected = self.boon_tree.selection()
        return self._boon_rows.get(selected[0]) if len(selected) == 1 else None

    def _select_boon(self):
        for button in (self.boon_set, self.boon_remove, self.boon_on, self.boon_off, self.boon_max):
            button.configure(state="disabled")
        if self._boon_state:
            states = self._boon_state['groups']
            valid = all(not boons.group_edit_reason(g, states[g['name']]) for g in boons.catalog())
            already_max = all(states[g['name']]['level'] == len(g['tiers']) for g in boons.catalog())
            if valid and not already_max:
                self.boon_max.configure(state="normal")
        row = self._selected_boon()
        if not row:
            self.boon_detail.configure(text=(
                "选择一行后，可将祝福设为该等级。每项祝福有一个解锁等级和独立的启用状态。\n"
                "全部设为最高级也会修改被搜索隐藏的祝福，可一次撤销整批修改。"))
            return
        group, tier = row
        state = self._boon_state['groups'][group['name']] if self._boon_state else None
        reason = boons.group_edit_reason(group, state, zh=True) if state else self._boon_error
        if reason:
            note = '只读：' + reason
        else:
            level = state['level'] or 0
            self.boon_set.configure(state='normal' if level != tier['tier'] else 'disabled')
            self.boon_remove.configure(state='normal' if level else 'disabled')
            if level == tier['tier']:
                self.boon_on.configure(state='normal' if state['disabled'] == 1 else 'disabled')
                self.boon_off.configure(state='normal' if state['disabled'] is None else 'disabled')
            owned = f"已解锁第 {level} 级" if level else "未解锁"
            note = f"{owned}，最高第 {len(group['tiers'])} 级。选择当前解锁等级后可启用或停用。"
        if group.get('zh_effect_note'):
            note += '\n' + group['zh_effect_note']
        self.boon_detail.configure(text=(
            f"{boons.localized_name(group)} · 第 {tier['tier']} 级 · 数值 {tier['value']}\n"
            f"游戏数据中的解锁条件：{tier['cost']:,} 个因果碎片、所需通关次数 {tier['clear_count']}。"
            "编辑仅调整祝福，因果碎片和通关次数由游戏管理。\n" + note))

    def _change_boons(self, edit, label):
        if not self.sv:
            self._select_boon()
            return
        self._commit_form()
        try:
            before = self.core_of()
            after = edit(before)
        except core.SaveError as exc:
            self.flash(str(exc), "error")
            self.refresh_boons()
            return
        self._core_edit(edit, label)

    def set_boon_tier(self, remove=False):
        row = self._selected_boon()
        if not row:
            self.flash('请先选择祝福等级。', 'info')
            return
        group, tier = row
        level = 0 if remove else tier['tier']
        status = '未解锁' if remove else f'第 {level} 级'
        self._change_boons(lambda f: boons.edited_groups(f, levels={group['name']: level}),
                           f"{boons.localized_name(group)} → {status}")

    def max_boons(self):
        self._change_boons(lambda f: boons.edited_groups(f, levels=boons.max_levels()),
                           '全部祝福 → 最高级（保留启用状态）')

    def set_boon_active(self, active):
        row = self._selected_boon()
        if not row:
            self.flash('请先选择当前已解锁的祝福等级。', 'info')
            return
        group, tier = row
        def edit(f):
            state = boons.read_state(f)['groups'][group['name']]
            if state['level'] != tier['tier']:
                raise boons.BoonError('请选择当前已解锁的等级，再启用或停用祝福。')
            return boons.edited_groups(f, active={group['name']: active})
        self._change_boons(edit, f"{boons.localized_name(group)} → {'启用' if active else '停用'}")
