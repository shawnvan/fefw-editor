"""Single-god EXP and first-audience controls with selected-slot undo."""
import tkinter as tk
from tkinter import ttk

import audiences
import blessings
import fefw_core as core
from zh_names import CHARACTERS


def _god_name(god) -> str:
    """Official Chinese god name; the data-layer name stays English."""
    return CHARACTERS.get(god.name, god.name)


class BlessingPageMixin:
    def _build_blessings(self):
        page = self.blessings_page
        top = ttk.Frame(page, style="Card.TFrame", padding=(14, 12))
        top.pack(fill="x")
        ttk.Label(top, text="加护", style="CardH3.TLabel").pack(anchor="w")
        self.blessing_summary = ttk.Label(top, style="CardMuted.TLabel", wraplength=950)
        self.blessing_summary.pack(anchor="w", pady=(6, 0))

        frame = ttk.Frame(page, style="Card.TFrame")
        frame.pack(fill="both", expand=True, pady=(8, 0))
        tree = self.blessing_tree = ttk.Treeview(
            frame, columns=("exp", "level", "selected", "audience", "key"),
            show="tree headings", selectmode="browse", height=7)
        for col, title, width in (("#0", "神明", 125), ("exp", "保存的经验", 90),
                                  ("level", "等级预览", 140),
                                  ("selected", "所选加护（保存值）", 125),
                                  ("audience", "初次谒见", 175),
                                  ("key", "游戏内名称", 235)):
            tree.heading(col, text=title, anchor="w")
            tree.column(col, width=width, minwidth=width, stretch=col == "key")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb.set, xscrollcommand=hs.set)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        tree.bind("<<TreeviewSelect>>", self._blessing_selected)

        form = ttk.Frame(page, style="Card.TFrame", padding=(14, 12))
        form.pack(fill="x", pady=(8, 0))
        self.blessing_detail = ttk.Label(form, style="CardMuted.TLabel", wraplength=950)
        self.blessing_detail.pack(anchor="w")
        row = ttk.Frame(form, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="新经验值", style="Card.TLabel").pack(side="left")
        self.blessing_exp = ttk.Entry(row, width=12)
        self.blessing_exp.pack(side="left", padx=(8, 8))
        self.blessing_apply = ttk.Button(row, text="应用经验", style="Small.TButton",
                                         command=self.apply_blessing_exp)
        self.blessing_apply.pack(side="left", padx=(0, 14))
        self.blessing_presets = []
        for level in range(3):
            button = ttk.Button(row, text=f"{level + 1} 级经验", style="Small.TButton",
                                command=lambda i=level: self._blessing_preset(i))
            button.pack(side="left", padx=(0, 6))
            self.blessing_presets.append(button)
        audience_row = ttk.Frame(form, style="Card.TFrame")
        audience_row.pack(fill="x", pady=(12, 0))
        self.blessing_audience = tk.BooleanVar(value=False)
        self.blessing_audience_toggle = ttk.Checkbutton(
            audience_row, text="已记录初次谒见",
            variable=self.blessing_audience, command=self.toggle_blessing_audience)
        self.blessing_audience_toggle.pack(anchor="w")
        self.blessing_audience_detail = ttk.Label(
            audience_row, style="CardMuted.TLabel", wraplength=950, justify="left")
        self.blessing_audience_detail.pack(anchor="w", pady=(5, 0))
        ttk.Label(page, style="CardFaint.TLabel", wraplength=980, justify="left", text=(
            "一次编辑一位神明。等级预览使用游戏数据的阈值；芙托娜是特例。谒见开关只改动"
            "初次对话标记；其他进入条件可能仍然生效。谒见编辑不会改动经验、所选加护、"
            "神圣之沙和传送地点。\n使用同路线镜像选择。应用经验和开关只改动内存中的数据；"
            "Ctrl+S 保存，Ctrl+Z 撤销，Ctrl+Y 重做。"
        )).pack(anchor="w", pady=(8, 0))
        self._blessing_selected()

    def refresh_blessings(self):
        if not hasattr(self, "blessing_tree"):
            return
        tree = self.blessing_tree
        keep = tree.selection()
        tree.delete(*tree.get_children())
        self._blessing_records = {}
        self._audience_values = None
        self._audience_error = "请先打开存档。"
        try:
            f = self.core_of()
            if f is None:
                self.blessing_summary.configure(text="打开存档以查看其加护。")
                return
            table = blessings.read(f)
            self._blessing_records = {r.hash: r for r in table.records}
            try:
                self._audience_values = audiences.read(f)
            except core.SaveError as exc:
                self._audience_error = str(exc)
            for god in blessings.GODS:
                record = self._blessing_records[god.hash]
                level = blessings.level_preview(god.hash, record.exp)
                selected = {0: "否 (0)", 1: "是 (1)"}.get(
                    record.selected, f"未知（{record.selected}）")
                tree.insert("", "end", iid=f"{god.hash:016x}", text=_god_name(god),
                            values=(record.exp, "特殊 / 未知" if level is None else level,
                                    selected,
                                    audiences.state_label(god.hash, self._audience_values or {})
                                    if self._audience_values is not None or god.hash not in audiences.KEYS
                                    else "不可用",
                                    god.key))
            tree.selection_set([i for i in keep if tree.exists(i)])
            self.blessing_summary.configure(text=(
                f"全部 7 位神明 · 神圣之沙：{table.divine_sand:,}（只读）· "
                "初次谒见：已在蕾达第 9 章测试 · 芙托娜：无需谒见。"))
        except core.SaveError as exc:
            self.blessing_summary.configure(text=str(exc))
        finally:
            self._blessing_selected()

    def _blessing_selected(self, _event=None):
        selected = self.blessing_tree.selection()
        self.blessing_apply.state(["disabled"])
        self.blessing_audience_toggle.state(["disabled"])
        self.blessing_audience.set(False)
        self.blessing_audience_detail.configure(text="选择一位神明以查看其初次谒见条件。")
        self.blessing_exp.state(["!disabled"])
        self.blessing_exp.delete(0, "end")
        for i, button in enumerate(self.blessing_presets):
            button.configure(text=f"{i + 1} 级经验")
            button.state(["disabled"])
        records = getattr(self, "_blessing_records", {})
        if len(selected) != 1 or int(selected[0], 16) not in records:
            self.blessing_detail.configure(text="选择一位神明以编辑其保存的经验。")
            self.blessing_exp.state(["disabled"])
            return
        h = int(selected[0], 16)
        god, record = blessings.BY_HASH[h], records[h]
        values = getattr(self, "_audience_values", None)
        if h not in audiences.KEYS:
            audience_detail = "游戏数据中芙托娜没有初次谒见条件；不会写入任何标记。"
        elif values is None:
            audience_detail = getattr(self, "_audience_error", "谒见数据不可用。")
        else:
            value = values.get(audiences.HASHES[h])
            self.blessing_audience.set(value == 1)
            if value in (None, 1):
                self.blessing_audience_toggle.state(["!disabled"])
            audience_detail = (f"{audiences.state_label(h, values)} · 游戏内名称：{audiences.KEYS[h]} "
                               f"· {audiences.HASHES[h]:08x}\n"
                               "勾选记录初次谒见；取消则移除该记录。按 Ctrl+S 写入。")
        self.blessing_audience_detail.configure(text=audience_detail)
        self.blessing_exp.insert(0, str(record.exp))
        self.blessing_apply.state(["!disabled"])
        if god.thresholds:
            detail = "等级按钮会填入新经验值；点击“应用经验”使用该值。"
            for i, (button, value) in enumerate(zip(self.blessing_presets, god.thresholds)):
                button.configure(text=f"{i + 1} 级：{value}")
                button.state(["!disabled"])
        else:
            detail = "经验效果未知；经验为 1 或 65535 时加护画面均无变化。"
        self.blessing_detail.configure(text=f"{_god_name(god)} · 经验范围 0–65535。{detail}")

    def _blessing_preset(self, index):
        selected = self.blessing_tree.selection()
        if len(selected) != 1:
            return
        god = blessings.BY_HASH.get(int(selected[0], 16))
        if god and god.thresholds:
            self.blessing_exp.delete(0, "end")
            self.blessing_exp.insert(0, str(god.thresholds[index]))

    def apply_blessing_exp(self):
        if not self.sv:
            return
        selected = self.blessing_tree.selection()
        if len(selected) != 1:
            self.flash("请先选择一位神明。", "info")
            return
        try:
            raw = self.blessing_exp.get().strip()
            if not raw.isascii() or not raw.isdecimal():
                raise ValueError
            value = int(raw)
            h = int(selected[0], 16)
        except ValueError:
            self.flash("经验必须是 0 到 65535 的整数。", "error")
            return
        self._commit_form()
        try:
            before = self.core_of()
            after = blessings.edited(before, {h: value})
        except core.SaveError as exc:
            self.flash(str(exc), "error")
            return
        self._core_edit(lambda f: blessings.edited(f, {h: value}),
                        f"{_god_name(blessings.BY_HASH[h])}经验 → {value}")

    def toggle_blessing_audience(self):
        """Checkbox applies immediately in memory, like other page actions."""
        if not self.sv:
            self._blessing_selected()
            return
        selected = self.blessing_tree.selection()
        if len(selected) != 1:
            self.flash("请先选择一位神明。", "info")
            self._blessing_selected()
            return
        h = int(selected[0], 16)
        value = 1 if self.blessing_audience.get() else None
        self._commit_form()
        try:
            before = self.core_of()
            after = audiences.edited(before, {h: value})
        except core.SaveError as exc:
            self.flash(str(exc), "error")
            self._blessing_selected()
            return
        self._core_edit(lambda f: audiences.edited(f, {h: value}),
                        f"{_god_name(blessings.BY_HASH[h])}初次谒见 → "
                        f"{'已记录' if value else '未记录'}")
