"""Tutorials page; mirrored edits with grouped Undo/Redo."""
from tkinter import ttk

import tutorials
import fefw_core as core
from ui_util import enable_multi_select


class TutorialPageMixin:
    def _build_tutorials(self):
        page = self.tutorials_page
        top = ttk.Frame(page, style="Card.TFrame", padding=(14, 12))
        top.pack(fill="x")
        row = ttk.Frame(top, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="教程", style="CardH3.TLabel").pack(side="left")
        self.tutorial_count = ttk.Label(top, style="CardMuted.TLabel", wraplength=950)
        self.tutorial_count.pack(anchor="w", pady=(8, 0))
        self.tutorial_buttons = []
        for label, value in (("移除所选", None), ("添加到存档", tutorials.AVAILABLE)):
            button = ttk.Button(row, text=label, style="Small.TButton",
                                command=lambda v=value: self.set_tutorials(v))
            button.pack(side="right", padx=(6, 0))
            self.tutorial_buttons.append(button)
        ttk.Button(row, text='选中当前列表', style='Small.TButton',
                   command=lambda: self.tutorial_tree.selection_set(self.tutorial_tree.get_children())).pack(side='right', padx=(6, 0))
        filters = ttk.Frame(top, style='Card.TFrame'); filters.pack(fill='x', pady=(8, 0))
        self.tutorial_category = ttk.Combobox(filters, state='readonly', width=19,
                                              values=('全部分类', *tutorials.CATEGORY_ZH.values()))
        self.tutorial_state = ttk.Combobox(filters, state='readonly', width=16,
                                         values=('全部状态', '未记录', '已保存 (2)', '存在 (1)', '未知'))
        self.tutorial_evidence = ttk.Combobox(filters, state='readonly', width=19,
                                            values=('全部名称', '游戏标题已核对', '暂定名'))
        for combo in (self.tutorial_category, self.tutorial_state, self.tutorial_evidence):
            combo.current(0); combo.pack(side='left', padx=(0, 6))
            combo.bind('<<ComboboxSelected>>', lambda e: self.refresh_tutorials())
        self.tutorial_search = ttk.Entry(filters)
        self.tutorial_search.pack(fill="x", expand=True)
        self.tutorial_search.bind("<KeyRelease>", lambda e: self.refresh_tutorials())
        ttk.Label(page, style="CardFaint.TLabel", wraplength=920, justify="left", text=(
            "可搜索中文、英文、日文或哈希。“添加到存档”保存所选记录；菜单是否显示由游戏控制，"
            "游戏也可能在游玩过程中再次解锁教程。“选中当前列表”按当前过滤器选中；"
            "未选中的条目保持已保存。未知状态会被保留。使用同路线镜像选择。"
            "Ctrl+Z 撤销编辑；Ctrl+S 写入。"
        )).pack(side="bottom", anchor="w", pady=(8, 0))
        self.tutorial_detail = ttk.Label(page, style='CardMuted.TLabel', wraplength=950,
                                         justify='left', padding=(12, 8), text='选择一个教程以查看其命名依据。')
        self.tutorial_detail.pack(side='bottom', fill='x', pady=(8, 0))
        frame = ttk.Frame(page, style="Card.TFrame")
        frame.pack(fill="both", expand=True, pady=(8, 0))
        tree = self.tutorial_tree = ttk.Treeview(frame, columns=("category", "state", "evidence", "jp", "hash"),
                                                show="tree headings", selectmode="extended")
        for col, label, width in (("#0", "名称", 285), ('category', '分类', 145),
                                  ("state", "保存的状态", 155), ('evidence', '名称依据', 145),
                                  ("jp", "游戏内名称", 310), ("hash", "哈希", 95)):
            tree.heading(col, text=label, anchor="w")
            tree.column(col, width=width, minwidth=width, stretch=False)
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=sb.set, xscrollcommand=hs.set)
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky='nsew'); sb.grid(row=0, column=1, sticky='ns')
        hs.grid(row=1, column=0, sticky='ew')
        enable_multi_select(tree)
        tree.bind('<<TreeviewSelect>>', lambda e: self._tutorial_selected(), add='+')

    def refresh_tutorials(self):
        if not hasattr(self, "tutorial_tree"):
            return
        tree = self.tutorial_tree
        keep = tree.selection(); view = tree.yview()
        tree.delete(*tree.get_children())
        for button in self.tutorial_buttons:
            button.state(["disabled"])
        try:
            cat = tutorials.catalog()
            f = self.core_of()
            if f is None:
                self.tutorial_count.configure(text="打开存档以查看其教程。")
                return
            values = tutorials.read(f)
        except core.SaveError as exc:
            self.tutorial_count.configure(text=str(exc))
            return
        q = self.tutorial_search.get().strip().casefold()
        category = self.tutorial_category.get(); state = self.tutorial_state.get()
        evidence = self.tutorial_evidence.get()
        for h, row in cat.items():
            label = tutorials.state_label(values.get(h)); name_evidence = tutorials.evidence_label(row)
            zh = tutorials.title(h, row)
            if category != '全部分类' and tutorials.CATEGORY_ZH[row['category_id']] != category: continue
            if evidence != '全部名称' and name_evidence != evidence: continue
            if state != '全部状态' and not (label.startswith('未知') if state == '未知' else label == state): continue
            if q and q not in f"{zh} {row['label']} {row['name']} {h:08x}".casefold():
                continue
            tree.insert("", "end", iid=f"{h:08x}", text=zh,
                        values=(tutorials.CATEGORY_ZH[row['category_id']], label, name_evidence,
                                row["name"], f"{h:08x}"))
        tree.selection_set([i for i in keep if tree.exists(i)])
        if any(tree.exists(i) for i in keep) and view: tree.yview_moveto(view[0])
        present = sum(h in values for h in cat)
        shown_present = sum(int(i, 16) in values for i in tree.get_children())
        unknown = len(values.keys() - cat.keys())
        self.tutorial_count.configure(text=(
            f"显示 {len(tree.get_children())} 条（已保存 {shown_present} 条）· "
            f"目录 {len(cat)} 条中已保存 {present} 条"
            f"（{present - shown_present} 条在过滤器之外）· "
            f"暂定名 {sum(r['evidence'] == 'provisional-translation' for r in cat.values())} 条 · "
            f"未列出的记录保留 {unknown} 条"))
        for button in self.tutorial_buttons:
            button.state(["!disabled"])
        self._tutorial_selected()

    def _tutorial_selected(self):
        picked = self.tutorial_tree.selection()
        if len(picked) == 1:
            row = tutorials.catalog()[int(picked[0], 16)]
            note = ('英文标题为暂定，未出现在所提供的截图中。若在正常游玩中出现，'
                    '一张标题截图有助于确认。'
                    if row['evidence'] == 'provisional-translation' else
                    '英文标题、分类和顺序与所提供的游戏内截图一致。这验证的是名称本身，'
                    '不代表功能解锁或在所有路线可用。')
            text = (tutorials.title(int(picked[0], 16), row) + ' · '
                    + tutorials.evidence_label(row) + '\n' + note
                    + '\n' + tutorials.visibility_note(row))
        elif picked:
            text = f'已选择 {len(picked)} 个教程。整批一起改动；若存在未知保存状态，将拒绝整批以保留原状。'
        else:
            text = '选择一个教程以查看其命名依据。新的英文标题会标为“暂定名”。'
        self.tutorial_detail.configure(text=text)

    def set_tutorials(self, value):
        if not self.sv:
            return
        picked = self.tutorial_tree.selection()
        if not picked:
            self.flash("请先选择一个或多个教程。", "info")
            return
        edits = {int(i, 16): value for i in picked}
        self._commit_form()
        try:
            before = self.core_of()
            after = tutorials.edited(before, edits)
        except core.SaveError as exc:
            self.flash(str(exc), "error")
            return
        count = len((tutorials.read(before).keys() - edits.keys()) |
                    {h for h, v in edits.items() if v is not None})
        what = f"{'已移除' if value is None else '已保存'} {len(edits)} 个教程；此存档还保留 {count} 条记录"
        self._core_edit(lambda f: tutorials.edited(f, edits), what)
