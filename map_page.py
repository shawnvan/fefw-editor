"""map_page.py - the World map page: show or hide the map's points.

A mixin for the App (see editor_advanced.AdvancedMixin).  The page lists the
game's map points and roads from `map_spots.json`, reads what this save says
about each (`GmapSpotMgr`, see worldmap.py) and writes the two bytes that
control whether a point is drawn and whether it is marked visited.  Every
edit goes through `_core_edit`, so it is undoable, mirrored across the route's
slots like every other page, and written with Ctrl+S.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import fefw_core as core
import worldmap as wm
from ui_util import enable_multi_select

VIEWS = (("places", "地点"), ("roads", "道路"), ("points", "岔路与道路点"))
NOTE = (
    "“已显示”让一个点出现在世界地图上，但不会打开它背后的任何内容——城门、战斗、奖励、"
    "任务和快速传送各有各的条件。“已到访”是白色与深色图标的区别。道路沿自己的点绘制，"
    "因此显示一条道路也会显示其两端的地点和岔路；隐藏道路只会隐藏属于该道路的点。"
    "“状态 2”是游戏写在部分道路点上的、尚未理解的值：编辑器按原样列出，只有你选中它才会"
    "改动。写入前请关闭游戏并保留备份。"
)


class MapPageMixin:
    # ------------------------------------------------------------------ build
    def _build_world_map(self):
        page = self.world_page
        self.map_view = "places"
        self._map_rows: dict[str, tuple] = {}
        top = ttk.Frame(page, style="Card.TFrame", padding=(14, 12))
        top.pack(fill="x")
        row = ttk.Frame(top, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="世界地图", style="CardH3.TLabel").pack(side="left")
        self.map_count = ttk.Label(row, text="", style="CardMuted.TLabel")
        self.map_count.pack(side="left", padx=(10, 0))
        for text, cmd in (("标记未到访", lambda: self.map_selected(None, 0)),
                          ("标记已到访", lambda: self.map_selected(None, 1)),
                          ("隐藏", lambda: self.map_selected(wm.HIDDEN, None)),
                          ("显示", lambda: self.map_selected(wm.SHOWN, None))):
            ttk.Button(row, text=text, style="Small.TButton", command=cmd).pack(
                side="right", padx=(6, 0))

        views = ttk.Frame(top, style="Card.TFrame")
        views.pack(fill="x", pady=(8, 0))
        self._map_view_btns = {}
        for key, text in VIEWS:
            b = ttk.Button(views, text=text, style="Small.TButton",
                           command=lambda k=key: self.set_map_view(k))
            b.pack(side="left", padx=(0, 6))
            self._map_view_btns[key] = b
        self.map_search = ttk.Entry(views, width=34)
        self.map_search.pack(side="right")
        self.map_search.bind("<KeyRelease>", lambda e: self.refresh_world_map())

        bulk = ttk.Frame(top, style="Card.TFrame")
        bulk.pack(fill="x", pady=(8, 0))
        ttk.Label(bulk, text="一键操作：", style="CardMuted.TLabel").pack(side="left")
        ttk.Button(bulk, text="显示全部地点", style="Small.TButton",
                   command=self.map_show_all_places).pack(side="left", padx=(8, 0))
        ttk.Button(bulk, text="显示全部道路", style="Small.TButton",
                   command=self.map_show_all_roads).pack(side="left", padx=(6, 0))
        self.map_mark_visited = tk.BooleanVar(value=False)
        ttk.Checkbutton(bulk, text="同时标记这些地点为已到访", style="Card.TCheckbutton",
                        variable=self.map_mark_visited).pack(side="left", padx=(12, 0))

        # the note goes in before the table so a short window can't push it off
        ttk.Label(page, style="CardFaint.TLabel", justify="left", wraplength=980,
                  text=NOTE).pack(side="bottom", anchor="w", pady=(8, 0))
        self.map_tree = _scrolled_tree(page, columns=("jp", "kind", "state", "visited"),
                                       show="tree headings", selectmode="extended")
        t = self.map_tree
        t.heading("#0", text="名称", anchor="w")
        t.heading("jp", text="游戏内名称", anchor="w")
        t.heading("kind", text="类型", anchor="w")
        t.heading("state", text="地图上", anchor="w")
        t.heading("visited", text="已到访", anchor="w")
        t.column("#0", width=300)
        t.column("jp", width=190)
        t.column("kind", width=140, stretch=False)
        t.column("state", width=110, stretch=False)
        t.column("visited", width=80, stretch=False)
        t.container.pack(fill="both", expand=True, pady=(8, 0))
        enable_multi_select(t)
        self._style_map_views()

    def _style_map_views(self):
        for key, b in self._map_view_btns.items():
            b.configure(style="Accent.TButton" if key == self.map_view else "Small.TButton")

    def set_map_view(self, view: str):
        self.map_view = view
        self._style_map_views()
        self.refresh_world_map()

    # ------------------------------------------------------------------- read
    def _map_records(self):
        """(records, error) for the shown slot, cached until its payload changes."""
        sv = self.sv
        if sv is None:
            return None, None
        f = self.core_of(sv)             # replaced whenever the payload is edited
        cached = getattr(sv, "_map_cache", None)
        if cached and cached[0] is f:
            return cached[1], cached[2]
        try:
            recs, err = wm.read(f), None
        except (wm.MapError, core.SaveError) as exc:
            recs, err = None, str(exc)
        sv._map_cache = (f, recs, err)
        return recs, err

    def refresh_world_map(self):
        if not hasattr(self, "map_tree"):
            return
        tree = self.map_tree
        keep = set(tree.selection())
        tree.delete(*tree.get_children())
        self._map_rows = {}
        cat = wm.catalog()
        recs, err = self._map_records()
        if cat is None:
            self.map_count.configure(text="缺少 map_spots.json——无法为地图点命名。")
            return
        if recs is None:
            self.map_count.configure(text=err or "打开存档以查看地图。")
            return
        tree.tag_configure("flag_unknown", foreground=self.p["muted"])
        q = self.map_search.get().strip().lower()
        listed = total = 0
        if self.map_view == "roads":
            total = len(cat.roads)
            for n, road in enumerate(cat.roads):
                pts = [recs.get(cat.spots[i].key) for i in road]
                name = cat.road_name(road)
                en = "%s %s" % (cat.spots[road[0]].en, cat.spots[road[-1]].en)
                jp = "%s \u2192 %s" % (cat.spots[road[0]].jp, cat.spots[road[-1]].jp)
                if any(r is None for r in pts) or (q and q not in (name + " " + en + " " + jp).lower()):
                    continue
                on = sum(1 for r in pts if r.state == wm.SHOWN)
                state = ("已显示" if on == len(pts) else "隐藏" if on == 0
                         else "%d / %d" % (on, len(pts)))
                iid = "r%d" % n
                tree.insert("", "end", iid=iid, text=name, tags=() if on else ("flag_unknown",),
                            values=(jp, "%d 个点" % len(road), state, ""))
                self._map_rows[iid] = ("road", n)
                listed += 1
        else:
            want_places = self.map_view == "places"
            for s in cat.spots:
                rec = recs.get(s.key)
                if rec is None or s.is_place != want_places:
                    continue
                total += 1
                if q and q not in (s.name + " " + s.en + " " + s.jp + " "
                                   + cat.kind_name(s.kind)).lower():
                    continue
                iid = "s%d" % s.index
                tree.insert("", "end", iid=iid, text=s.name,
                            tags=() if rec.state != wm.HIDDEN else ("flag_unknown",),
                            values=(s.jp if s.jp != s.name else "", cat.kind_name(s.kind),
                                    wm.STATE_NAMES.get(rec.state, "状态 %d" % rec.state),
                                    {0: "否", 1: "是"}.get(rec.visited, str(rec.visited))))
                self._map_rows[iid] = ("spot", s.index)
                listed += 1
        keep = [i for i in keep if tree.exists(i)]
        if keep:
            tree.selection_set(keep)
        pl = [recs[s.key] for s in wm.places(cat) if s.key in recs]
        on = sum(1 for r in pl if r.state != wm.HIDDEN)
        self.map_count.configure(text="地图上有 %d / %d 个地点 · 列出 %d / %d 条"
                                      % (on, len(pl), listed, total))

    # ------------------------------------------------------------------ write
    def map_selected(self, state, visited):
        if not self.sv:
            return
        cat = wm.catalog()
        picked: dict[int, tuple] = {}
        roads = bare = 0
        for iid in self.map_tree.selection():
            kind, n = self._map_rows.get(iid, (None, None))
            if kind == "spot":
                picked[cat.spots[n].key] = (state, visited)
            elif kind == "road" and state is not None:
                # showing a road shows the places and junctions it joins; hiding it
                # only hides what belongs to it, never a town or a shared junction
                roads += 1
                pts = wm.road_points(cat, cat.roads[n], whole=state == wm.SHOWN)
                bare += not pts
                for s in pts:
                    picked[s.key] = (state, None)
        if not picked:
            if not self.map_tree.selection():
                msg = "请先选择一行。"
            elif roads and bare == roads:
                msg = "这条道路没有自己独享的点可隐藏——它直接连接两个地点。"
            else:
                msg = "到访标记只适用于地点，不适用于道路。"
            self.flash(msg, "info")
            return
        parts = []
        if state is not None:
            parts.append("显示" if state == wm.SHOWN else "隐藏")
        if visited is not None:
            parts.append("标记为" + ("已到访" if visited else "未到访"))
        self._map_write(picked, "%s %d 个地图点" % ("并".join(parts), len(picked)))

    def _map_write(self, edits: dict, what: str):
        skipped = {"n": 0}

        def edit(g):
            _changed, skip = wm.apply(g, edits)
            skipped["n"] = max(skipped["n"], skip)
            return g.payload

        if self._core_edit(edit, what) and skipped["n"]:
            self.flash("%s——跳过 %d 个（存在编辑器未见过的布局或数值）——按 Ctrl+S 写入。"
                       % (what, skipped["n"]), "warn")

    def map_show_all_places(self):
        recs, _ = self._map_records()
        cat = wm.catalog()
        if recs is None or cat is None:
            self.flash("请先打开一个有世界地图的存档。", "info")
            return
        mark = self.map_mark_visited.get()
        edits = {s.key: (wm.SHOWN, 1 if mark else None) for s in wm.places(cat)
                 if s.key in recs and (recs[s.key].state == wm.HIDDEN
                                       or (mark and recs[s.key].visited == 0))}
        if not edits:
            self.flash("所有地点都已在地图上。", "info")
            return
        self._map_write(edits, "已显示 %d 个地点" % len(edits))

    def map_show_all_roads(self):
        recs, _ = self._map_records()
        cat = wm.catalog()
        if recs is None or cat is None:
            self.flash("请先打开一个有世界地图的存档。", "info")
            return
        edits = {s.key: (wm.SHOWN, None) for s in wm.network(cat)
                 if s.key in recs and recs[s.key].state == wm.HIDDEN}
        if not edits:
            self.flash("所有岔路和道路点都已显示。", "info")
            return
        if not messagebox.askyesno(
                "存档编辑器",
                "显示 %d 个隐藏的岔路和道路点？\n\n这会画出游戏拥有的全部道路，"
                "包括剧情尚未开通的。可按 Ctrl+Z 撤销。" % len(edits),
                parent=self):
            return
        self._map_write(edits, "已显示 %d 个岔路和道路点" % len(edits))


def _scrolled_tree(master, **kw) -> ttk.Treeview:
    """Treeview with its own scrollbar, in a Card frame (same as editor.py)."""
    frame = ttk.Frame(master, style="Card.TFrame")
    tree = ttk.Treeview(frame, **kw)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.container = frame
    return tree
