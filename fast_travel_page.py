"""Single-save fast-travel edits with the editor's normal undo/save controls."""
from tkinter import ttk

import fast_travel
import fefw_core as core


class FastTravelPageMixin:
    def _build_fast_travel(self):
        page = self.fast_travel_page
        top = ttk.Frame(page, style="Card.TFrame", padding=(14, 12))
        top.pack(fill="x")
        row = ttk.Frame(top, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="快速传送 · 鞑古席翁", style="CardH3.TLabel").pack(side="left")
        self.fast_travel_buttons = []
        for label, value in (("移除所选", None), ("设为可用", fast_travel.AVAILABLE)):
            button = ttk.Button(row, text=label, style="Small.TButton",
                                command=lambda v=value: self.set_fast_travel(v))
            button.pack(side="right", padx=(6, 0))
            self.fast_travel_buttons.append(button)
        self.fast_travel_count = ttk.Label(top, style="CardMuted.TLabel", wraplength=950)
        self.fast_travel_count.pack(anchor="w", pady=(8, 0))
        self.fast_travel_search = ttk.Entry(top)
        self.fast_travel_search.pack(fill="x", pady=(8, 0))
        self.fast_travel_search.bind("<KeyRelease>", lambda e: self.refresh_fast_travel())
        ttk.Label(page, style="CardFaint.TLabel", wraplength=980, justify="left", text=(
            "欧若拉的房间/庭园的传送定位器需要凯伊；萨拉弥斯迎宾馆需要赛奥朵拉。幽·法丝的"
            "传送/离开已在蕾达第 9 章测试通过；其正门仍受限。详见 功能 > 进入条件。"
            "可按名称、日文键或哈希搜索。"
            "编辑会更新所选存档及已启用镜像目标的传送点列表。能否传送仍取决于游戏条件；"
            "“厩舍前”传送点不会解锁厩舍设施。请至少保留三个原始传送点。\n"
            "使用同路线镜像选择。Ctrl+Z 撤销；Ctrl+S 保存。"
        )).pack(side="bottom", anchor="w", pady=(8, 0))
        frame = ttk.Frame(page, style="Card.TFrame")
        frame.pack(fill="both", expand=True, pady=(8, 0))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = self.fast_travel_tree = ttk.Treeview(frame, columns=("state", "evidence", "jp", "hash"),
                                                   show="tree headings", selectmode="extended")
        for col, label, width in (("#0", "传送点", 220), ("state", "状态", 175),
                                  ("evidence", "验证程度", 150),
                                  ("jp", "游戏内名称", 570), ("hash", "哈希", 95)):
            tree.heading(col, text=label, anchor="w")
            tree.column(col, width=width, minwidth=width, stretch=col == "jp")
        vs = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")

    def refresh_fast_travel(self):
        if not hasattr(self, "fast_travel_tree"):
            return
        tree = self.fast_travel_tree
        keep = tree.selection()
        tree.delete(*tree.get_children())
        for button in self.fast_travel_buttons:
            button.state(["disabled"])
        try:
            cat = fast_travel.catalog()
            f = self.core_of()
            if f is None:
                self.fast_travel_count.configure(text="打开存档以查看其传送点。")
                return
            values = fast_travel.read(f)
        except core.SaveError as exc:
            self.fast_travel_count.configure(text=str(exc))
            return
        q = self.fast_travel_search.get().strip().casefold()
        for h, row in cat.items():
            zh = fast_travel.title(row)
            if q and q not in f"{zh} {row['label']} {row['name']} {h:08x}".casefold():
                continue
            tree.insert("", "end", iid=f"{h:08x}", text=zh, values=(
                fast_travel.state_label(values.get(h)),
                fast_travel.coverage_label(row),
                row["name"], f"{h:08x}"))
        tree.selection_set([i for i in keep if tree.exists(i)])
        available = sum(values.get(h) == fast_travel.AVAILABLE for h in cat)
        unlisted = len(values.keys() - cat.keys())
        self.fast_travel_count.configure(text=(
            f"已知 {len(cat)} 个传送点中 {available} 个可用 · "
            f"{sum(row['coverage'] == 'Effect unknown' for row in cat.values())} 个效果未知 · "
            f"其他列表记录保留 {unlisted} 条"))
        for button in self.fast_travel_buttons:
            button.state(["!disabled"])

    def set_fast_travel(self, value):
        if not self.sv:
            return
        picked = self.fast_travel_tree.selection()
        if not picked:
            self.flash("请先选择一个或多个传送点。", "info")
            return
        edits = {int(i, 16): value for i in picked}
        self._commit_form()
        try:
            before = self.core_of()
            after = fast_travel.edited(before, edits)
        except core.SaveError as exc:
            self.flash(str(exc), "error")
            return
        what = f"{'已移除' if value is None else '已设为可用'} {len(edits)} 个传送点"
        self._core_edit(lambda f: fast_travel.edited(f, edits), what)
