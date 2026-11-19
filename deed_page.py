"""Single-counter Notable Deeds edits with normal save and undo controls."""
from tkinter import messagebox, ttk

import deeds
import fefw_core as core


EDITING_ZH = {"Read-only": "只读", "Inferred": "推断", "Editable": "可编辑"}


class DeedPageMixin:
    def _build_deeds(self):
        page = self.deeds_page
        top = ttk.Frame(page, style="Card.TFrame", padding=(14, 10))
        top.pack(fill="x")
        ttk.Label(top, text="伟业录", style="CardH3.TLabel").pack(anchor="w")
        self.deed_count = ttk.Label(top, style="CardMuted.TLabel")
        self.deed_count.pack(anchor="w", pady=(6, 0))
        self.deed_search = ttk.Entry(top)
        self.deed_search.pack(fill="x", pady=(8, 0))
        self.deed_search.bind("<KeyRelease>", lambda e: self.refresh_deeds())

        ttk.Label(page, style="CardFaint.TLabel", wraplength=980, justify="left", text=(
            "修改累计值会更新同一项目的所有目标进度。请在游戏内领取报酬。降低进度可能使游戏取消已领取状态。"
            "未列出的目标可能有额外的游戏条件。\n"
            "可按中文、英文、日文或哈希搜索。支持同路线镜像编辑。应用后按 Ctrl+S 写入存档，Ctrl+Z 撤销。"
        )).pack(side="bottom", anchor="w", pady=(8, 0))
        form = ttk.Frame(page, style="Card.TFrame", padding=(14, 10))
        form.pack(side="bottom", fill="x", pady=(8, 0))
        self.deed_detail = ttk.Label(form, style="CardMuted.TLabel", wraplength=960, justify="left")
        self.deed_detail.pack(anchor="w")
        controls = ttk.Frame(form, style="Card.TFrame")
        controls.pack(fill="x", pady=(8, 0))
        ttk.Label(controls, text="新累计值", style="Card.TLabel").pack(side="left")
        self.deed_value = ttk.Entry(controls, width=14)
        self.deed_value.pack(side="left", padx=(8, 8))
        self.deed_apply = ttk.Button(controls, text="应用进度", command=self.set_deed)
        self.deed_apply.pack(side="left")
        self.deed_target = ttk.Button(controls, text="填入所选目标值", command=self.fill_deed_target)
        self.deed_target.pack(side="left", padx=(8, 0))
        self.deed_value.bind("<Return>", lambda e: self.set_deed())

        frame = ttk.Frame(page, style="Card.TFrame")
        frame.pack(fill="both", expand=True, pady=(8, 0))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = self.deed_tree = ttk.Treeview(frame, columns=("progress", "reward", "claim", "access", "jp", "hash"),
                                           show="tree headings", selectmode="browse")
        for col, label, width in (("#0", "项目 · 目标值", 300), ("progress", "进度", 150),
                                  ("reward", "因果碎片", 90), ("claim", "存档领取状态", 155),
                                  ("access", "编辑状态", 90), ("jp", "目标原始键名", 300),
                                  ("hash", "目标哈希", 100)):
            tree.heading(col, text=label, anchor="w")
            tree.column(col, width=width, minwidth=width, stretch=col == "jp")
        vs = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        tree.bind("<<TreeviewSelect>>", self._deed_selected)
        self._deed_rows = {}
        self._deed_selected()

    def refresh_deeds(self):
        if not hasattr(self, "deed_tree"):
            return
        tree = self.deed_tree
        keep = tree.selection()
        view = tree.yview()
        tree.delete(*tree.get_children())
        self._deed_rows = {}
        try:
            cat = deeds.catalog()
            f = self.core_of()
            if f is None:
                self.deed_count.configure(text="打开存档后可查看伟业录。")
                self._deed_selected()
                return
            values = deeds.read(f)
        except core.SaveError as exc:
            self.deed_count.configure(text=str(exc))
            self._deed_selected()
            return
        q = self.deed_search.get().strip().casefold()
        claimed = unclaimed = total = 0
        for h, row in cat.items():
            value = values.get(h, 0)
            title = deeds.display_name(row)
            search_name = f"{title} {row['label']} {row['category']} {row['name']} {h:08x}"
            for tier in row["milestones"]:
                total += 1
                state = values.get(int(tier["hash"], 16))
                claimed += state == 4
                unclaimed += state == 2
                if q and q not in f"{search_name} {tier['name']} {tier['hash']} {tier['target']}".casefold():
                    continue
                iid = tier["hash"]
                self._deed_rows[iid] = (h, tier)
                reached = " · 已达标" if value >= tier["target"] else ""
                progress = f"{min(value, tier['target']):,}/{tier['target']:,}{reached}"
                tree.insert("", "end", iid=iid, text=f"{title} · {tier['target']:,}", values=(
                    progress, tier["reward"], deeds.claim_label(state, zh=True),
                    EDITING_ZH[deeds.editing_label(h, values)], tier["name"], tier["hash"]))
        tree.selection_set([i for i in keep if tree.exists(i)])
        if view:
            tree.yview_moveto(view[0])
        editable = sum(not deeds.read_only_reason(h, values) for h in cat)
        inferred = sum(bool(row.get('inferred')) for row in cat.values())
        self.deed_count.configure(text=(
            f"显示 {len(self._deed_rows)}/{total} 个目标 · 可编辑 {editable}/{len(cat)} 个计数项 · "
            f"推断 {inferred} 项 · 存档中已领取 {claimed} 项，已达成未领取 {unclaimed} 项"))
        self._deed_selected()

    def _deed_selected(self, _event=None):
        for widget in (self.deed_value, self.deed_apply, self.deed_target):
            widget.state(["disabled"])
        self.deed_detail.configure(text="选择一个目标可查看其累计值。报酬及领取状态只读。")
        picked = self.deed_tree.selection()
        if len(picked) != 1 or picked[0] not in self._deed_rows:
            return
        h, tier = self._deed_rows[picked[0]]
        try:
            f = self.core_of()
            if f is None:
                return
            values = deeds.read(f)
            row = deeds.catalog()[h]
            reason = deeds.read_only_reason(h, values, zh=True)
        except core.SaveError as exc:
            self.deed_detail.configure(text=str(exc))
            return
        value = values.get(h, 0)
        stored = f"{value:,}" if h in values else "0（未记录）"
        note = reason or (f"可输入 0 至 {deeds.maximum(h, values):,}，将更新该项目的全部 "
                          f"{len(row['milestones'])} 个目标。低于目标值时，游戏可能取消已领取状态。")
        if not reason:
            evidence = ("推断：此计数项尚未经游戏验证。编辑仅修改累计值，请在游戏内读取存档后确认进度。"
                        if row.get('inferred') else row.get('zh_editing_note', row.get('editing_note')))
            if evidence:
                note = evidence + '\n' + note
        self.deed_detail.configure(text=(
            f"{deeds.display_name(row)} · 当前累计值：{stored}\n{row['name']} · {h:08x}\n{note}"))
        self.deed_value.state(["!disabled"])
        self.deed_value.delete(0, "end")
        self.deed_value.insert(0, str(value))
        for widget in (self.deed_value, self.deed_apply, self.deed_target):
            widget.state(["disabled" if reason else "!disabled"])

    def fill_deed_target(self):
        picked = self.deed_tree.selection()
        if len(picked) == 1 and picked[0] in self._deed_rows and not self.deed_target.instate(["disabled"]):
            self.deed_value.delete(0, "end")
            self.deed_value.insert(0, str(self._deed_rows[picked[0]][1]["target"]))

    def _confirm_deed_unclaim(self, h, value, affected):
        targets = ", ".join(f"{tier['target']:,}" for tier in affected)
        return messagebox.askokcancel(
            "已领取的报酬",
            "新累计值低于部分已领取目标，仍要应用修改吗？",
            detail=(f"{deeds.display_name(deeds.catalog()[h])} · 新累计值：{value:,}\n"
                    f"受影响的已领取目标：{targets}\n\n"
                    "游戏读取修改后的存档时，可能会取消这些目标的领取状态。"
                    "编辑器只修改累计值，领取状态和因果碎片由游戏管理。"),
            icon="warning", default="cancel", parent=self)

    def set_deed(self):
        if not self.sv:
            return
        picked = self.deed_tree.selection()
        if len(picked) != 1 or picked[0] not in self._deed_rows:
            self.flash("请先选择一个目标。", "info")
            return
        h, _tier = self._deed_rows[picked[0]]
        try:
            value = int(self.deed_value.get().strip())
        except ValueError:
            self.flash("请输入整数作为累计值。", "error")
            return
        self._commit_form()
        try:
            before = self.core_of()
            after = deeds.edited(before, h, value)
        except core.SaveError as exc:
            self.flash(str(exc), "error")
            return
        try:
            risks = {}
            for sv in self._targets():
                for tier in deeds.claims_at_risk(h, value, deeds.read(self.core_of(sv))):
                    risks[tier['target']] = tier
            affected = list(risks.values())
        except core.SaveError as exc:
            from os.path import basename
            self.flash(f'未做更改：{basename(sv.path)} —— {exc}', 'error')
            return
        if affected and not self._confirm_deed_unclaim(h, value, affected):
            self.flash("已取消进度修改。", "info")
            return
        self._core_edit(lambda f: deeds.edited(f, h, value),
                        f"{deeds.display_name(deeds.catalog()[h])}的累计值 → {value:,}")
