"""Read-only quest progress, prerequisites, expiration, and recruitment viewer tab."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import fefw_core as core
import quests


KIND_FILTER_MAP = {
    "全部": None,
    "外传": "sidestory",
    "支线": "subquest",
    "招募前置": "recruitment",
}

KIND_LABELS = {
    "sidestory": "外传",
    "subquest": "支线",
    "recruitment": "招募",
}

STATUS_FILTER_MAP = {
    "全部": None,
    "可接取/进行中/可汇报": {"available", "in_progress", "reportable"},
    "已过期失效": {"expired"},
    "未解锁/前置不足": {"locked"},
    "已完成": {"cleared"},
    "非当前路线": {"wrong_route"},
    "无法判定": {"unknown"},
}

STATUS_SORT_ORDER = {
    "reportable": 0,
    "in_progress": 1,
    "available": 2,
    "expired": 3,
    "locked": 4,
    "unknown": 5,
    "cleared": 6,
    "wrong_route": 7,
}

KIND_SORT_ORDER = {
    "sidestory": 0,
    "subquest": 1,
    "recruitment": 2,
}


class QuestPageMixin:
    def _build_quests(self):
        page = self.quests_page
        top = ttk.Frame(page, style="Card.TFrame", padding=(14, 10))
        top.pack(fill="x")
        ttk.Label(top, text="任务进度、前置条件与招募前置（只读）", style="CardH3.TLabel").pack(anchor="w")
        self.quest_summary = ttk.Label(top, style="CardMuted.TLabel", wraplength=980, justify="left")
        self.quest_summary.pack(anchor="w", pady=(6, 0))

        filters = ttk.Frame(top, style="Card.TFrame")
        filters.pack(fill="x", pady=(8, 0))
        ttk.Label(filters, text="搜索", style="Card.TLabel").pack(side="left")
        self.quest_search_var = tk.StringVar()
        self.quest_search_var.trace_add("write", lambda *_args: self.refresh_quests())
        self.quest_search = ttk.Entry(filters, textvariable=self.quest_search_var)
        self.quest_search.pack(side="left", fill="x", expand=True, padx=(6, 12))

        ttk.Label(filters, text="类型", style="Card.TLabel").pack(side="left")
        self.quest_kind_cb = ttk.Combobox(
            filters, state="readonly", width=10, values=list(KIND_FILTER_MAP.keys()))
        self.quest_kind_cb.set("全部")
        self.quest_kind_cb.pack(side="left", padx=(6, 12))
        self.quest_kind_cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_quests())

        ttk.Label(filters, text="状态", style="Card.TLabel").pack(side="left")
        self.quest_status_cb = ttk.Combobox(
            filters, state="readonly", width=20, values=list(STATUS_FILTER_MAP.keys()))
        self.quest_status_cb.set("全部")
        self.quest_status_cb.pack(side="left", padx=(6, 12))
        self.quest_status_cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_quests())

        ttk.Label(filters, text="排序", style="Card.TLabel").pack(side="left")
        self.quest_sort_cb = ttk.Combobox(
            filters, state="readonly", width=10, values=("状态优先", "默认顺序", "名称"))
        self.quest_sort_cb.set("状态优先")
        self.quest_sort_cb.pack(side="left", padx=(6, 0))
        self.quest_sort_cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_quests())

        ttk.Label(
            page,
            style="CardFaint.TLabel",
            wraplength=980,
            justify="left",
            text="只读视图：展示外传、支线与角色招募前置状态。双击行或点击下方按钮可快速跳转到前置/关联任务。",
        ).pack(side="bottom", anchor="w", pady=(8, 0))

        detail_card = ttk.Frame(page, style="Card.TFrame", padding=(14, 10))
        detail_card.pack(side="bottom", fill="x", pady=(8, 0))
        self.quest_detail = ttk.Label(
            detail_card,
            text="选择一个任务或角色以查看达成条件、描述与前置检查详情。",
            style="CardMuted.TLabel",
            wraplength=960,
            justify="left",
        )
        self.quest_detail.pack(anchor="w")
        detail_actions = ttk.Frame(detail_card, style="Card.TFrame")
        detail_actions.pack(fill="x", pady=(8, 0))
        self.quest_jump_btn = ttk.Button(
            detail_actions,
            text="跳转到前置/关联任务",
            command=self._jump_to_prereq,
        )
        self.quest_jump_btn.pack(side="left")
        self.quest_jump_btn.state(["disabled"])

        frame = ttk.Frame(page, style="Card.TFrame")
        frame.pack(fill="both", expand=True, pady=(8, 0))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = self.quest_tree = ttk.Treeview(
            frame,
            columns=("kind", "status", "window", "prereq", "key"),
            show="tree headings",
            selectmode="browse",
        )
        for col, label, width in (
            ("#0", "名称", 240),
            ("kind", "类型", 70),
            ("status", "当前状态", 145),
            ("window", "期限 / 关联任务", 220),
            ("prereq", "前置条件 / 招募条件", 280),
            ("key", "原始键名", 210),
        ):
            tree.heading(col, text=label, anchor="w")
            tree.column(col, width=width, minwidth=width, stretch=col in ("prereq", "key"))

        vs = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        tree.bind("<<TreeviewSelect>>", self._quest_selected)
        tree.bind("<Double-1>", lambda _e: self._jump_to_prereq())
        self._quest_rows: dict[str, dict] = {}

    def refresh_quests(self):
        if not hasattr(self, "quest_tree"):
            return
        tree = self.quest_tree
        keep = tree.selection()
        view = tree.yview()
        tree.delete(*tree.get_children())
        self._quest_rows = {}
        try:
            f = self.core_of()
            if f is None:
                self.quest_summary.configure(text="打开存档后可查看任务进度、前置条件与招募前置。")
                self._quest_selected()
                return
            data = quests.read(f)
        except core.SaveError as exc:
            self.quest_summary.configure(text=str(exc))
            self._quest_selected()
            return

        ctx = data["context"]
        ss_list = data["sidestories"]
        sq_list = data["subquests"]
        rec_list = data.get("recruitments", [])
        all_items = ss_list + sq_list + rec_list

        q = self.quest_search_var.get().strip().casefold()
        kind_filter = KIND_FILTER_MAP.get(self.quest_kind_cb.get())
        status_filter = STATUS_FILTER_MAP.get(self.quest_status_cb.get())
        sort_mode = self.quest_sort_cb.get() if hasattr(self, "quest_sort_cb") else "状态优先"

        filtered = []
        for idx, item in enumerate(all_items):
            if kind_filter and item["kind"] != kind_filter:
                continue
            if status_filter and item["status_code"] not in status_filter:
                continue
            row = item["row"]
            haystack = " ".join([
                row.get("title", ""),
                row.get("key", ""),
                row.get("client", ""),
                row.get("goal", ""),
                row.get("target_name", ""),
                row.get("jp_name", ""),
                item["status_text"],
                item["prereq_text"],
            ]).casefold()
            if q and q not in haystack:
                continue
            filtered.append((idx, item))

        if sort_mode == "状态优先":
            filtered.sort(key=lambda pair: (
                STATUS_SORT_ORDER.get(pair[1]["status_code"], 99),
                KIND_SORT_ORDER.get(pair[1]["kind"], 9),
                pair[0],
            ))
        elif sort_mode == "名称":
            filtered.sort(key=lambda pair: (pair[1]["row"].get("title") or pair[1]["row"]["key"]).casefold())

        for _, item in filtered:
            row = item["row"]
            iid = row["key"]
            self._quest_rows[iid] = item
            kind_label = KIND_LABELS.get(item["kind"], item["kind"])
            tree.insert(
                "",
                "end",
                iid=iid,
                text=row.get("title") or row["key"],
                values=(kind_label, item["status_text"], item["window"], item["prereq_text"], row["key"]),
            )

        tree.selection_set([i for i in keep if tree.exists(i)])
        if view:
            tree.yview_moveto(view[0])

        ss_clr = sum(1 for x in ss_list if x["status_code"] == "cleared")
        ss_act = sum(1 for x in ss_list if x["status_code"] in ("available", "in_progress"))
        ss_exp = sum(1 for x in ss_list if x["status_code"] == "expired")
        sq_clr = sum(1 for x in sq_list if x["status_code"] == "cleared")
        sq_act = sum(1 for x in sq_list if x["status_code"] in ("available", "in_progress", "reportable"))
        sq_exp = sum(1 for x in sq_list if x["status_code"] == "expired")
        rec_in = sum(1 for x in rec_list if x["status_code"] == "cleared")
        fame_str = f" · 名声 Lv.{ctx.fame_level} ({ctx.fame_points:,})" if ctx.fame_level is not None else ""

        self.quest_summary.configure(text=(
            f"当前进度：{ctx.route_label} · {ctx.part_label} · {ctx.chapter_label} · "
            f"游戏内时间 {ctx.date_label}{fame_str}\n"
            f"显示 {len(filtered)}/{len(all_items)} 项 · "
            f"外传（已完成 {ss_clr} / 可接或进行中 {ss_act} / 已过期 {ss_exp} / 共 {len(ss_list)}） · "
            f"支线（已完成 {sq_clr} / 可接或进行中 {sq_act} / 已过期 {sq_exp} / 共 {len(sq_list)}） · "
            f"招募（已入队 {rec_in}/{len(rec_list)}）"
        ))
        self._quest_selected()

    def _quest_selected(self, _event=None):
        picked = self.quest_tree.selection()
        if len(picked) != 1 or picked[0] not in self._quest_rows:
            self.quest_detail.configure(text="选择一个任务或角色以查看达成条件、描述与前置检查详情。")
            self.quest_jump_btn.state(["disabled"])
            return
        item = self._quest_rows[picked[0]]
        row = item["row"]
        kind_label = KIND_LABELS.get(item["kind"], item["kind"])
        lines = [f"[{kind_label}] {row.get('title', row['key'])}  —  {item['status_text']}"]
        if row.get("client"):
            lines.append(f"委托人：{row['client']}")
        if row.get("target_name") and item["kind"] != "recruitment":
            lines.append(f"关联角色：{row['target_name']}  |  地点：{row.get('spot') or '—'}")
        if row.get("goal"):
            lines.append(f"达成条件 / 说明：{row['goal']}")
        lines.append(f"期限 / 关联：{item['window']}  |  前置条件：{item['prereq_text']}")
        if item["kind"] == "sidestory":
            lines.append(f"存档原始状态：state={item['raw_state']}, flag={item['raw_flag']}" + (f", ts={item['ts']}" if item.get("ts") else ""))
        elif item["kind"] == "subquest":
            lines.append(f"存档原始状态：state={item['raw_state']}, sub={item['raw_sub']}")
        else:
            lines.append(
                f"单位状态：in_pool={item['in_pool']}, 加入旗标={item['rec_flag']}, "
                f"面识={item['met_flag']}, 可招募={item['scout_flag']}"
            )
        if row.get("detail"):
            lines.append(f"任务说明：{row['detail']}")
        self.quest_detail.configure(text="\n".join(lines))
        if item.get("prereq_quest_keys"):
            self.quest_jump_btn.state(["!disabled"])
        else:
            self.quest_jump_btn.state(["disabled"])

    def _jump_to_prereq(self):
        picked = self.quest_tree.selection()
        if len(picked) != 1 or picked[0] not in self._quest_rows:
            return
        item = self._quest_rows[picked[0]]
        targets = item.get("prereq_quest_keys") or []
        if not targets:
            return
        target_iid = targets[0]
        if not self.quest_tree.exists(target_iid):
            self.quest_kind_cb.set("全部")
            self.quest_status_cb.set("全部")
            self.quest_search_var.set("")
            self.refresh_quests()
        if self.quest_tree.exists(target_iid):
            self.quest_tree.selection_set(target_iid)
            self.quest_tree.focus(target_iid)
            self.quest_tree.see(target_iid)
            self._quest_selected()
