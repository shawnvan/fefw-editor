"""editor_advanced.py - Player / Classes / Flags pages for the save editor.

These pages are driven by fefw_core (the save's own object tree), so they
can add and remove records: classes, weapon masteries, named
flags.  Everything else in the app keeps working on the same payload; after a
resizing edit the records are re-scanned, so no offsets go stale.

The class owns no state of its own beyond widget caches: the App supplies
self.sv (SaveFile), self.core (fefw_core.SaveFile for the shown slot),
self._targets(), self._touch(), self.flash().
"""
from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import messagebox, ttk

import fefw_core as core
import supports
from ui_util import ScrollableFrame
from fefw_save import chinese_name, is_slot_name, item_ids, item_label, unit_ids
from weapon_stats import item_preview_text
from map_page import MapPageMixin
from tutorial_page import TutorialPageMixin
from blessing_page import BlessingPageMixin
from fast_travel_page import FastTravelPageMixin
import fast_travel
import deeds
from deed_page import DeedPageMixin
from boon_page import BoonPageMixin
from facility_page import FacilityPageMixin
from story_page import StoryPageMixin
from unit_entry_page import UnitEntryPageMixin
import audiences
import facilities
import tutorials
import flag_display
import flag_labels
from ui_util import enable_multi_select

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Fortune's Weave Save Editor"

# The 13 skill EXP fields in save order. Display terms follow System.msbt.
MASTERIES = [
    ("剑术", "剣術"), ("枪术", "槍術"), ("斧术", "斧術"), ("箭术", "弓術"),
    ("格斗术", "格闘術"), ("黑魔术", "黒魔術"), ("白魔术", "白魔術"),
    ("特殊", "特殊"), ("步兵术", "歩兵術"), ("马术", "馬術"),
    ("重装术", "重装術"), ("飞行术", "飛行術"), ("指挥术", "指揮術"),
]
RANKS = ["E", "E+", "D", "D+", "C", "C+", "B", "B+", "A", "A+", "S", "S+"]
from fefw_save import SKILL_THRESHOLDS as RANK_EXP  # noqa: E402  (one table for both tabs)
DIFFICULTIES = ["普通", "困难", "疯狂"]
MODES = ["轻松模式", "经典模式"]
PLAYER_RESOURCES = {"gold": "金币", "renown": "名声值", "karma": "因果碎片"}

_JOBS = None
_FLAG_NAMES = None


def scrolled_tree(master, **kw) -> ttk.Treeview:
    """Treeview with its own scrollbar, in a Card frame (same as editor.py)."""
    frame = ttk.Frame(master, style="Card.TFrame")
    tree = ttk.Treeview(frame, **kw)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.container = frame
    return tree


def jobs() -> dict:
    global _JOBS
    if _JOBS is None:
        raw = json.load(open(os.path.join(HERE, "job_ids.json"), encoding="utf8"))
        _JOBS = {int(k, 16): v for k, v in raw.items()}
    return _JOBS


def flag_names() -> dict:
    global _FLAG_NAMES
    if _FLAG_NAMES is None:
        try:
            with open(os.path.join(HERE, "flag_names.json"), encoding="utf8") as fh:
                raw = json.load(fh)
            _FLAG_NAMES = {int(k, 16): v for k, v in raw.items()}
        except Exception:                     # noqa: BLE001 — the page still works
            _FLAG_NAMES = {}
        flag_labels.apply_labels(_FLAG_NAMES, strict=False)
    return _FLAG_NAMES


def flag_english_mode(app) -> bool:
    setting = getattr(app, "flag_english", None)
    return setting is None or setting.get()


def flag_display_name(h: int, meta: dict | None, english: bool, zh: str | None = None) -> str:
    key = meta["name"] if meta else ""
    if english:
        label = meta.get("label") if meta else ""
        return label if label and label != key else f"unknown 0x{h:08x}"
    if zh is None:
        zh = flag_display.chinese_label(h, meta)
    return zh or flag_display.unknown_label(h, key)


def class_label(cid: int) -> str:
    from fefw_save import job_name
    return job_name(cid & 0xFFFFFFFF)


def class_jp(cid: int) -> str:
    return (jobs().get(cid & 0xFFFFFFFF) or {}).get("jp", "")


def unit_name(person: int) -> str:
    meta = unit_ids().get(person & 0xFFFFFFFF) or {}
    return meta.get("name") or meta.get("pid") or "unknown 0x%08X" % (person & 0xFFFFFFFF)


def rank_of(exp: int) -> int:
    r = 0
    while r + 1 < len(RANK_EXP) and exp >= RANK_EXP[r + 1]:
        r += 1
    return r


def matching_flag_map(source, target, map_i):
    """Resolve a mirrored flag list by its member hash, never by scan order."""
    selected = next((m for m in core.flag_maps(source) if m.index == map_i), None)
    if selected is None:
        raise core.SaveError('所选标记列表已不存在。')
    if source.payload == target.payload:
        return selected
    tree, group, _ = facilities.flag_group(source)
    node = tree.nodes[group]; table = source.ptr(node.start)
    keys = [source.u32(table+8*i) for i in range(source.u32(node.start) >> 24)
            if source.ptr(table+8*i+4) == selected.start]
    if len(keys) != 1:
        raise core.SaveError('无法识别所选标记列表在其他存档中的位置，请关闭同路线镜像后编辑。')
    return facilities.locate_member(target, keys[0])


class AdvancedMixin(MapPageMixin, TutorialPageMixin, BlessingPageMixin, FastTravelPageMixin, DeedPageMixin, BoonPageMixin, FacilityPageMixin, StoryPageMixin, UnitEntryPageMixin):
    # ------------------------------------------------------------- helpers
    def core_of(self, sv=None):
        """Decoded slot, reused only while its source bytes are unchanged."""
        sv = sv or self.sv
        if sv is None:
            return None
        key = (sv.path, id(sv.payload))
        cache = getattr(sv, "_core_cache", None)
        if cache and cache[0] == key and cache[1].payload == sv.payload:
            return cache[1]
        f = core.SaveFile(bytes(sv.payload))
        sv._core_cache = (key, f)
        return f

    def _core_edit(self, fn, what: str) -> bool:
        """Preflight every target, then stage one undoable multi-slot edit."""
        if not self.sv:
            return False
        self._commit_form()
        changes = []
        for sv in self._targets():
            try:
                f = core.SaveFile(bytes(sv.payload))
                new = fn(f)
            except Exception as exc:          # noqa: BLE001 — atomic preflight
                self.flash(f'未做更改：{os.path.basename(sv.path)} —— {exc}', 'error')
                return False
            if new is None or bytes(new) == bytes(sv.payload):
                continue
            changes.append((sv, bytearray(new)))
        if not changes:
            self.flash(f'已是该值：{what}。', 'info')
            return False
        touched = [sv for sv, _ in changes]
        self.push_undo(slots=touched)
        for sv, new in changes:
            sv.payload = new
            sv.scan()
            sv._core_cache = None
        self._touch(touched)
        self.refresh_after_core()
        where = f"（{len(touched)} 个槽位）" if len(touched) > 1 else ""
        self.flash(f"{what}{where} —— 按 Ctrl+S 写入。", "ok")
        return True

    def refresh_after_core(self):
        keep = self.uid
        self._shown = {}
        self.refresh_units(keep=keep)
        self.refresh_convoy()
        self.refresh_player()
        self.refresh_classes_page()
        self.refresh_flags()
        self.refresh_supports()
        self.refresh_world_map()
        self.refresh_tutorials()
        self.refresh_blessings()
        self.refresh_fast_travel()
        self.refresh_facilities()
        self.refresh_deeds()
        self.refresh_boons()

    def refresh_core_pages(self):
        self.refresh_player()
        self.refresh_classes_page()
        self.refresh_flags()
        self.refresh_world_map()
        self.refresh_tutorials()
        self.refresh_blessings()
        self.refresh_fast_travel()
        self.refresh_facilities()
        self.refresh_deeds()
        self.refresh_boons()

    # -------------------------------------------------------- pick a value
    def _picker(self, title: str, rows, current: int | None = None, *,
                search_terms=None, secondary_title="内部编号", preview_text=None):
        """Modal searchable single-choice list. rows: [(key, primary, secondary)]."""
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        self.place_dialog(win, 560, 460)
        win.configure(background=self.p["bg"])
        top = ttk.Frame(win, padding=10)
        top.pack(fill="x")
        ent = ttk.Entry(top)
        ent.pack(fill="x")
        tree = ttk.Treeview(win, columns=("sec",), show="tree headings", selectmode="browse")
        tree.heading("#0", text='名称', anchor="w")
        tree.heading("sec", text=secondary_title, anchor="w")
        tree.column("#0", width=300)
        tree.column("sec", width=210)
        sb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        result = {"key": None}
        iids: dict[str, object] = {}

        def fill(*_a):
            q = ent.get().strip().lower()
            tree.delete(*tree.get_children())
            iids.clear()
            n = 0
            for key, primary, secondary in rows:
                if (q and q not in str(primary).lower() and q not in str(secondary).lower()
                        and (search_terms is None or q not in search_terms[key])):
                    continue
                iid = tree.insert("", "end", text=primary, values=(secondary,))
                iids[iid] = key
                if current is not None and key == current:
                    tree.selection_set(iid)
                    tree.see(iid)
                n += 1
                if n > 4000:
                    break

        def ok(*_a):
            sel = tree.selection()
            if sel:
                result["key"] = iids[sel[0]]
                win.destroy()

        ent.bind("<KeyRelease>", fill)
        tree.bind("<Double-1>", ok)
        tree.bind("<Return>", ok)
        bar = ttk.Frame(win, padding=(10, 6))
        bar.pack(side="bottom", fill="x")
        ttk.Button(bar, text='使用此项', style="Accent.TButton", command=ok).pack(side="right")
        ttk.Button(bar, text='取消', command=win.destroy).pack(side="right", padx=(0, 8))
        if preview_text is not None:
            preview = ttk.Label(win, justify="left", wraplength=520)
            preview.pack(side="bottom", fill="x", padx=10)

            def update_preview(_event=None):
                sel = tree.selection()
                key = iids.get(sel[0]) if sel else None
                preview.configure(text=preview_text(key))

            tree.bind("<<TreeviewSelect>>", update_preview)
        tree.pack(side="left", fill="both", expand=True, padx=(10, 0))
        sb.pack(side="left", fill="y", padx=(0, 10))
        fill()
        ent.focus_set()
        self.wait_window(win)
        return result["key"]

    # ============================================================ PLAYER
    def _build_player(self):
        self.player_scroll = ScrollableFrame(self.player_page)
        self.player_scroll.pack(fill="both", expand=True)
        page = self.player_scroll.body
        card = ttk.Frame(page, style="Card.TFrame", padding=(16, 10))
        card.pack(fill="x")
        ttk.Label(card, text='玩家', style="CardH3.TLabel").pack(anchor="w")
        ttk.Label(card, text="全军共用的数值，保存在存档的玩家数据中。",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(2, 10))
        self.player_vars: dict[str, tk.StringVar] = {}
        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="x")
        rows = [("gold", 9_999_999), ("renown", 999_999), ("karma", 999_999)]
        for i, (key, limit) in enumerate(rows):
            ttk.Label(grid, text=PLAYER_RESOURCES[key], style="Card.TLabel", width=16).grid(
                row=i, column=0, sticky="w", pady=4)
            var = tk.StringVar()
            self.player_vars[key] = var
            box = ttk.Entry(grid, textvariable=var, width=14, justify="right")
            box.grid(row=i, column=1, sticky="w", pady=4)
            box.bind("<Return>", lambda e, k=key, l=limit: self.apply_player(k, l))
            box.bind("<FocusOut>", lambda e, k=key, l=limit: self.apply_player(k, l))
            ttk.Label(grid, text=f"0 – {limit:,}", style="CardFaint.TLabel").grid(
                row=i, column=2, sticky="w", padx=(10, 0))
        self.player_note = ttk.Label(grid, text="", style="CardFaint.TLabel", wraplength=520,
                                     justify="left")
        self.player_note.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))

        card2 = ttk.Frame(page, style="Card.TFrame", padding=(16, 10))
        card2.pack(fill="x", pady=(10, 0))
        ttk.Label(card2, text='本周目设置', style="CardH3.TLabel").pack(anchor="w")
        ttk.Label(card2, text="写入存档保留的每一份副本（头部、摘要和各个重启点），"
                             "也写入此文件夹的每个槽位（本周目的难度也包含在内）。",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(2, 8))
        row = ttk.Frame(card2, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text='难度', style="Card.TLabel", width=12).pack(side="left")
        self.diff_var = tk.StringVar()
        cb = ttk.Combobox(row, textvariable=self.diff_var, values=DIFFICULTIES,
                          state="readonly", width=12)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.apply_settings())
        ttk.Label(row, text='模式', style="Card.TLabel", width=9).pack(side="left", padx=(20, 0))
        self.mode_var = tk.StringVar()
        cb2 = ttk.Combobox(row, textvariable=self.mode_var, values=MODES, state="readonly",
                           width=10)
        cb2.pack(side="left")
        cb2.bind("<<ComboboxSelected>>", lambda e: self.apply_settings())
        self.play_lbl = ttk.Label(card2, text="", style="CardFaint.TLabel")
        self.play_lbl.pack(anchor="w", pady=(10, 0))
        self._build_story(page)

    def refresh_player(self):
        self.refresh_story()
        if not hasattr(self, "player_vars"):
            return
        f = self.core_of()
        if f is None:
            for v in self.player_vars.values():
                v.set("")
            return
        ud = core.user_data(f)
        if ud < 0:
            for v in self.player_vars.values():
                v.set("—")
            self.player_note.configure(text="此存档的玩家数据结构未能识别。")
            return
        self.player_vars["gold"].set(str(f.u32(ud + core.GOLD_AT)))
        self.player_vars["renown"].set(str(f.u32(ud + core.RENOWN_AT)))
        self.player_vars["karma"].set(str(f.u32(ud + core.KARMA_AT)))
        st = core.read_settings(f)
        if st:
            self.diff_var.set(DIFFICULTIES[st[1]] if st[1] < 3 else str(st[1]))
            self.mode_var.set(MODES[st[2]] if st[2] < 2 else str(st[2]))
            line = f"第 {st[0]} 周目"
            counts: dict[tuple[int, int], int] = {}
            for sv in getattr(self, "files", {}).values():
                q = sv.payload
                if len(q) > 15 and q[12] == 15:
                    counts[(q[15], q[14])] = counts.get((q[15], q[14]), 0) + 1   # (difficulty, mode)
            if len(counts) <= 1:
                line += (f" · 各槽位一致：{DIFFICULTIES[st[1]] if st[1] < 3 else st[1]}"
                         f" / {MODES[st[2]] if st[2] < 2 else st[2]}")
            else:
                bits = [f"{DIFFICULTIES[a] if a < 3 else a}/{MODES[b] if b < 2 else b} ×{n}"
                        for (a, b), n in sorted(counts.items())]
                line += " · 槽位不一致 —— " + " · ".join(bits)
            self.play_lbl.configure(text=line)
        self.player_note.configure(
            text="名声值是全军的累计值；等级本身由游戏自己的 Reward.bin 表决定，"
                 "把数值设在两级之间即可停在想要的等级上。")

    def apply_player(self, key: str, limit: int):
        var = self.player_vars.get(key) if hasattr(self, "player_vars") else None
        if var is None or not self.sv:
            return
        text = var.get().strip().replace(",", "")
        if not text.isdigit():
            self.refresh_player()
            return
        value = min(int(text), limit)
        f = self.core_of()
        ud = core.user_data(f) if f else -1
        if ud < 0:
            return
        off = {"gold": core.GOLD_AT, "renown": core.RENOWN_AT, "karma": core.KARMA_AT}[key]
        if f.u32(ud + off) == value:
            return

        def edit(g, off=off, value=value):
            base = core.user_data(g)
            if base < 0:
                return None
            core.set_u32(g, base + off, value)
            return g.payload

        self._core_edit(edit, f"{PLAYER_RESOURCES[key]} → {value:,}")

    def apply_settings(self):
        if not self.sv or not hasattr(self, "diff_var"):
            return
        try:
            d = DIFFICULTIES.index(self.diff_var.get())
            m = MODES.index(self.mode_var.get())
        except ValueError:
            return
        f0 = self.core_of()
        cur = core.read_settings(f0) if f0 is not None else None
        if cur and cur[1] == d and cur[2] == m:
            return

        # Difficulty and mode are properties of the run, not of one slot: the
        # game's save screen shows them for every slot, so write them into the
        # whole folder (Auto, Chapter_* and Route_*), not only the open one.
        touched, err = [], None
        for sv in list(getattr(self, "files", {}).values()):
            try:
                g = core.SaveFile(bytes(sv.payload))
                offs = settings_offsets(g)
                if not offs:
                    continue
                for at in offs:
                    g.payload[at] = m & 0xFF          # mode comes first
                    g.payload[at + 1] = d & 0xFF      # then difficulty
                core.fix_embedded_hashes(g.payload)
                if bytes(g.payload) == bytes(sv.payload):
                    continue
                sv.payload = bytearray(g.payload)
                sv.scan()
                sv._core_cache = None
                touched.append(sv)
            except Exception as exc:          # noqa: BLE001 — report, never crash
                err = exc
        if not touched:
            self.flash("无法应用难度/模式" + (f"：{err}" if err else "。"), "error")
            return
        self._touch(touched)
        self.refresh_after_core()
        where = f"（共 {len(touched)} 个槽位）" if len(touched) > 1 else "（本槽位）"
        self.flash(f"{DIFFICULTIES[d]} / {MODES[m]} {where}——按 Ctrl+S 写入。", "ok")

    # ============================================================ CLASSES
    def _build_classes(self):
        pane = ttk.PanedWindow(self.classes_page, orient="horizontal")
        pane.pack(fill="both", expand=True)

        side = ttk.Frame(pane, style="Card.TFrame", padding=12)
        pane.add(side, weight=0)
        head = ttk.Frame(side, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text='单位', style="CardH3.TLabel").pack(side="left")
        self.cls_count = ttk.Label(head, text="", style="CardMuted.TLabel")
        self.cls_count.pack(side="right")
        self.cls_search = ttk.Entry(side)
        self.cls_search.pack(fill="x", pady=(6, 6))
        self.cls_search.bind("<KeyRelease>", lambda e: self.refresh_class_units())
        self.cls_unit_tree = ttk.Treeview(side, columns=("lv",), show="tree headings",
                                          selectmode="browse", height=20)
        self.cls_unit_tree.heading("#0", text='名称', anchor="w")
        self.cls_unit_tree.heading("lv", text='LV', anchor="e")
        self.cls_unit_tree.column("#0", width=170)
        self.cls_unit_tree.column("lv", width=40, anchor="e", stretch=False)
        sb = ttk.Scrollbar(side, orient="vertical", command=self.cls_unit_tree.yview)
        self.cls_unit_tree.configure(yscrollcommand=sb.set)
        self.cls_unit_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.cls_unit_tree.bind("<<TreeviewSelect>>", lambda e: self.on_class_unit())

        main = ttk.Frame(pane)
        pane.add(main, weight=1)

        head = ttk.Frame(main, style="Card.TFrame", padding=(16, 12))
        head.pack(fill="x", padx=(10, 0))
        self.cls_name = ttk.Label(head, text='未选择单位', style="CardH1.TLabel")
        self.cls_name.pack(anchor="w")
        self.cls_sub = ttk.Label(head, text="", style="CardMuted.TLabel")
        self.cls_sub.pack(anchor="w", pady=(2, 0))

        cur = ttk.Frame(main, style="Card.TFrame", padding=(16, 10))
        cur.pack(fill="x", padx=(10, 0), pady=(8, 0))
        ttk.Label(cur, text='当前兵种', style="Card.TLabel", width=13).pack(side="left")
        self.cur_class_lbl = ttk.Label(cur, text="—", style="CardH3.TLabel")
        self.cur_class_lbl.pack(side="left")
        self.cur_class_jp = ttk.Label(cur, text="", style="CardFaint.TLabel")
        self.cur_class_jp.pack(side="left", padx=(10, 0))
        ttk.Button(cur, text='更改…', style="Small.TButton",
                   command=self.change_current_class).pack(side="right")

        body = ttk.PanedWindow(main, orient="vertical")
        body.pack(fill="both", expand=True, padx=(10, 0), pady=(8, 0))

        cbox = ttk.Frame(body, style="Card.TFrame", padding=(12, 10))
        body.add(cbox, weight=3)
        bar = ttk.Frame(cbox, style="Card.TFrame")
        bar.pack(fill="x")
        ttk.Label(bar, text='单位拥有的兵种', style="CardH3.TLabel").pack(side="left")
        for text, cmd in (("设置兵种EXP…", self.set_class_exp), ("添加…", self.add_class),
                          ("替换…", self.swap_class), ("移除", self.remove_class),
                          ("设为当前", self.set_current_class)):
            ttk.Button(bar, text=text, style="Small.TButton", command=cmd).pack(side="right",
                                                                               padx=(6, 0))
        self.cls_tree = ttk.Treeview(cbox, columns=("value", "flags", "now"),
                                     show="tree headings", selectmode="extended")
        self.cls_tree.heading("#0", text='兵种', anchor="w")
        self.cls_tree.heading("value", text='兵种EXP', anchor="e")
        self.cls_tree.heading("flags", text='标记', anchor="e")
        self.cls_tree.heading("now", text="", anchor="center")
        self.cls_tree.column("#0", width=300)
        self.cls_tree.column("value", width=80, anchor="e", stretch=False)
        self.cls_tree.column("flags", width=70, anchor="e", stretch=False)
        self.cls_tree.column("now", width=40, anchor="center", stretch=False)
        cls_sb = ttk.Scrollbar(cbox, orient="vertical", command=self.cls_tree.yview)
        self.cls_tree.configure(yscrollcommand=cls_sb.set)
        self.cls_tree.pack(side="left", fill="both", expand=True, pady=(6, 0))
        cls_sb.pack(side="left", fill="y", pady=(6, 0))
        self.cls_tree.bind("<Double-1>", lambda e: self.set_current_class())
        enable_multi_select(self.cls_tree)

        mbox = ttk.Frame(body, style="Card.TFrame", padding=(12, 10))
        body.add(mbox, weight=3)
        bar2 = ttk.Frame(mbox, style="Card.TFrame")
        bar2.pack(fill="x")
        ttk.Label(bar2, text='技能', style="CardH3.TLabel").pack(side="left")
        self.mas_sel_lbl = ttk.Label(bar2, text="", style="CardMuted.TLabel")
        self.mas_sel_lbl.pack(side="left", padx=(10, 0))
        for text, cmd in (("设置技能LV…", self.set_mastery_rank), ("设置技能EXP…", self.set_mastery_exp),
                          ("添加…", self.add_mastery), ("移除", self.remove_mastery)):
            ttk.Button(bar2, text=text, style="Small.TButton", command=cmd).pack(side="right",
                                                                               padx=(6, 0))
        self.mas_tree = ttk.Treeview(mbox, columns=("rank", "exp", "next"),
                                     show="tree headings", selectmode="extended")
        self.mas_tree.heading("#0", text='技能', anchor="w")
        self.mas_tree.heading("rank", text='技能LV', anchor="center")
        self.mas_tree.heading("exp", text='技能EXP', anchor="e")
        self.mas_tree.heading("next", text='下级EXP阈值', anchor="e")
        self.mas_tree.column("#0", width=220)
        self.mas_tree.column("rank", width=60, anchor="center", stretch=False)
        self.mas_tree.column("exp", width=70, anchor="e", stretch=False)
        self.mas_tree.column("next", width=110, anchor="e", stretch=False)
        mas_sb = ttk.Scrollbar(mbox, orient="vertical", command=self.mas_tree.yview)
        self.mas_tree.configure(yscrollcommand=mas_sb.set)
        self.mas_tree.pack(side="left", fill="both", expand=True, pady=(6, 0))
        mas_sb.pack(side="left", fill="y", pady=(6, 0))
        self.mas_tree.bind("<Double-1>", lambda e: self.set_mastery_rank())
        self.mas_tree.bind("<<TreeviewSelect>>", lambda e: self._on_mastery_select())
        enable_multi_select(self.mas_tree)

        note = ttk.Frame(main, style="Card.TFrame")
        note.pack(fill="x", padx=(10, 0), pady=(6, 0))
        ttk.Label(note, style="CardFaint.TLabel", justify="left", wraplength=900,
                  text="兵种和技能是单位记录内的两个独立列表；添加条目会增大记录，其后"
                       "所有指针都会被重写（这是旧版编辑器做不到的）。等级阈值来自游戏："
                       "E 0 · E+ 20 · D 35 · D+ 80 · C 220 · C+ 440 · B 690 · B+ 910 "
                       "· A 1215 · A+ 1510 · S 1960 · S+ 2560。"
                  ).pack(anchor="w")

    def _core_units(self):
        f = self.core_of()
        return f.units() if f else []

    def refresh_classes_page(self):
        if not hasattr(self, "cls_unit_tree"):
            return
        self.refresh_class_units()
        self.on_class_unit()

    def refresh_class_units(self):
        f = self.core_of()
        keep = getattr(self, "_cls_uid", None)
        self.cls_unit_tree.delete(*self.cls_unit_tree.get_children())
        self._cls_rows = {}
        if not f:
            return
        q = ""
        try:
            q = self.cls_search.get().strip().lower()
        except Exception:                     # noqa: BLE001
            pass
        first = None
        rows = []
        by_start = {u.start: u for u in f.units()}
        for u in f.units():
            meta = unit_ids().get(u.person & 0xFFFFFFFF) or {}
            name = meta.get("name") or meta.get("pid") or "unknown 0x%08X" % (u.person & 0xFFFFFFFF)
            kind = (meta.get("kind") or "char")
            label = name + ("" if kind == "char" else f"  · {kind}")
            rows.append((0 if kind == "char" else 1, label.lower(), u.start, label))
        rows.sort()
        for _k, _s, start, label in rows:
            if q and q not in label.lower():
                continue
            u = by_start[start]
            lv = f.payload[u.level_off]
            iid = self.cls_unit_tree.insert("", "end", text=label, values=(lv,))
            self._cls_rows[iid] = u.start
            if first is None:
                first = iid
            if keep is not None and u.start == keep:
                first = iid
        self.cls_count.configure(text=f"{len(self._cls_rows)} 个单位")
        if first:
            self.cls_unit_tree.selection_set(first)
            self.cls_unit_tree.see(first)

    def _cls_unit(self):
        sel = self.cls_unit_tree.selection() if hasattr(self, "cls_unit_tree") else ()
        start = getattr(self, "_cls_rows", {}).get(sel[0] if sel else None)
        if start is None:
            return None
        for u in self._core_units():
            if u.start == start:
                return u
        return None

    def on_class_unit(self):
        u = self._cls_unit()
        f = self.core_of()
        self.cls_tree.delete(*self.cls_tree.get_children())
        self.mas_tree.delete(*self.mas_tree.get_children())
        if u is None or f is None:
            self.cls_name.configure(text='未选择单位')
            self.cur_class_lbl.configure(text="—")
            return
        self._cls_uid = u.start
        self.cls_name.configure(text=unit_name(u.person))
        self.cls_sub.configure(text=f"LV {f.payload[u.level_off]} · {len(u.classes)} 个兵种"
                                    f" · {len(u.masteries)} 项技能")
        cid = u.klass
        cur = class_label(cid)
        jp = class_jp(cid)
        self.cur_class_lbl.configure(text=cur)
        self.cur_class_jp.configure(text=jp if jp and jp != cur else "")
        for i, (klass, value, flags) in enumerate(u.classes):
            self.cls_tree.insert("", "end", text=class_label(klass),
                                 values=(value, flags, "●" if klass == cid else ""),
                                 iid=str(i))
        keep_uid, keep = getattr(self, "_mas_keep", (None, ()))
        kept = []
        for i, (idx, exp) in enumerate(u.masteries):
            r = rank_of(exp)
            nxt = RANK_EXP[r + 1] if r + 1 < len(RANK_EXP) else None
            nm = MASTERIES[idx][0] if 0 <= idx < len(MASTERIES) else f"#{idx}"
            self.mas_tree.insert("", "end", text=nm,
                                 values=(RANKS[r], exp, nxt if nxt is not None else "已满级"),
                                 iid=str(i))
            if keep_uid == u.start and idx in keep:
                kept.append(str(i))
        # an edit rebuilds this list; keep the same weapons selected (by weapon,
        # not row, because Add / Remove shift the rows)
        if kept:
            self.mas_tree.selection_set(kept)
            self.mas_tree.focus(kept[0])
            self.mas_tree.see(kept[0])
        else:
            self._mas_keep = (None, ())
        self._on_mastery_select()

    # -- class actions
    def _pick_class(self, title, *, current=None, exclude=()):
        rows = []
        search_terms = {}
        for cid, meta in jobs().items():
            if cid in exclude:
                continue
            rows.append((cid, class_label(cid), meta.get("jp", "")))
            search_terms[cid] = (meta.get("en") or "").lower()
        rows.sort(key=lambda r: r[1].lower())
        return self._picker(title, rows, current, search_terms=search_terms,
                            secondary_title="原始兵种键名")

    def change_current_class(self):
        u = self._cls_unit()
        if u is None:
            return
        cid = self._pick_class("选择单位当前兵种", current=u.klass)
        if cid is None:
            return
        self._set_current(u, cid)

    def set_current_class(self):
        u = self._cls_unit()
        sel = self.cls_tree.selection()
        if u is None or not sel:
            return
        klass = u.classes[int(sel[0])][0]
        self._set_current(u, klass)

    def _set_current(self, u, cid: int):
        if cid == u.klass:
            return

        def edit(g):
            v = next((x for x in g.units() if x.start == u.start), None)
            if v is None:
                return None
            if not any(c[0] == cid for c in v.classes):
                new = core.with_lists(g, v, v.masteries, list(v.classes) + [(cid, 0, 4)])
                g.payload = new
                v = next((x for x in g.units() if x.start == u.start), None)
            core.set_class(g, v, cid)
            return g.payload

        self._core_edit(edit, f"当前兵种 → {class_label(cid)}")

    def _class_entries(self) -> list[int]:
        """Selected class rows (entry index), in list order."""
        sel = self.cls_tree.selection() if hasattr(self, "cls_tree") else ()
        out = sorted({int(i) for i in sel if str(i).isdigit()})
        return out

    def set_class_exp(self):
        """Set the class exp ("progress") saved with each selected class."""
        u = self._cls_unit()
        entries = self._class_entries()
        if u is None or not entries:
            self.flash("请先选择兵种行。", "info")
            return
        cur = u.classes[entries[0]][1] if len(entries) == 1 else 0
        many = len(entries) > 1
        prompt = ("所选兵种的EXP（0 – 255）：" if many
                  else "该兵种保存的兵种EXP（0 – 255）：")
        val = _ask_number(self, "兵种EXP", prompt, cur, 255)
        if val is None:
            return

        def edit(g):
            v = next((x for x in g.units() if x.start == u.start), None)
            if v is None:
                return None
            classes = list(v.classes)
            changed = False
            for i in entries:
                if classes[i][1] != val:
                    classes[i] = (classes[i][0], val, classes[i][2])
                    changed = True
            if not changed:
                return None
            return core.with_lists(g, v, v.masteries, classes)

        what = (f"{class_label(u.classes[entries[0]][0])}兵种EXP → {val}" if not many
                else f"{len(entries)} 个兵种的EXP → {val}")
        self._core_edit(edit, what)

    def add_class(self):
        u = self._cls_unit()
        if u is None:
            return
        have = {c[0] for c in u.classes}
        cid = self._pick_class("添加兵种", exclude=have)
        if cid is None:
            return

        def edit(g):
            v = next((x for x in g.units() if x.start == u.start), None)
            if v is None or any(c[0] == cid for c in v.classes):
                return None
            return core.with_lists(g, v, v.masteries, list(v.classes) + [(cid, 0, 4)])

        self._core_edit(edit, f"已添加兵种 {class_label(cid)}")

    def swap_class(self):
        u = self._cls_unit()
        sel = self.cls_tree.selection()
        if u is None or not sel:
            return
        entry = int(sel[0])
        old = u.classes[entry]
        have = {c[0] for c in u.classes}
        cid = self._pick_class(f"将 {class_label(old[0])} 替换为…", exclude=have)
        if cid is None:
            return

        def edit(g):
            v = next((x for x in g.units() if x.start == u.start), None)
            if v is None:
                return None
            classes = list(v.classes)
            was_current = classes[entry][0] == v.klass
            classes[entry] = (cid, classes[entry][1], classes[entry][2])
            new = core.with_lists(g, v, v.masteries, classes)
            g.payload = new
            if was_current:
                w = next((x for x in g.units() if x.start == u.start), None)
                core.set_class(g, w, cid)
            return g.payload

        self._core_edit(edit, f"兵种 {class_label(old[0])} → {class_label(cid)}")

    def remove_class(self):
        u = self._cls_unit()
        entries = self._class_entries()
        if u is None or not entries:
            return
        if any(u.classes[i][0] == u.klass for i in entries):
            messagebox.showinfo("存档编辑器", "其中包含该单位的当前兵种。请先更改当前兵种，"
                                               "或使用“替换”。", parent=self)
            entries = [i for i in entries if u.classes[i][0] != u.klass]
            if not entries:
                return
        names = [class_label(u.classes[i][0]) for i in entries]

        def edit(g):
            v = next((x for x in g.units() if x.start == u.start), None)
            if v is None:
                return None
            classes = [c for i, c in enumerate(v.classes) if i not in set(entries)]
            return core.with_lists(g, v, v.masteries, classes)

        what = (f"已移除兵种 {names[0]}" if len(names) == 1
                else f"已移除 {len(names)} 个兵种")
        self._core_edit(edit, what)

    # -- mastery actions
    def _mastery_entries(self) -> list[int]:
        """Selected mastery rows (entry index), in list order."""
        sel = self.mas_tree.selection() if hasattr(self, "mas_tree") else ()
        return sorted({int(i) for i in sel if str(i).isdigit()})

    def _mastery_names(self, u, entries) -> list[str]:
        out = []
        for i in entries:
            idx = u.masteries[i][0]
            out.append(MASTERIES[idx][0] if 0 <= idx < len(MASTERIES) else f"#{idx}")
        return out

    def _remember_masteries(self, u, entries, extra=()):
        """Keep these weapons selected when the edit rebuilds the list."""
        self._mas_keep = (u.start, {u.masteries[i][0] for i in entries} | set(extra))

    def _on_mastery_select(self):
        if not hasattr(self, "mas_sel_lbl"):
            return
        n = len(self._mastery_entries())
        self.mas_sel_lbl.configure(
            text=f"已选择 {n} 项。设置技能LV、技能EXP或移除会应用到全部所选项。" if n > 1 else "")

    @staticmethod
    def _short_list(names, limit: int = 4) -> str:
        return ", ".join(names[:limit]) + (f" 等{len(names) - limit}项"
                                          if len(names) > limit else "")

    def set_mastery_rank(self):
        u = self._cls_unit()
        entries = self._mastery_entries()
        if u is None or not entries:
            self.flash("请先选择一项技能。", "info")
            return
        names = self._mastery_names(u, entries)
        rows = [(i, RANKS[i], f"{RANK_EXP[i]} EXP") for i in range(len(RANKS))]
        if len(entries) == 1:
            title = f"{names[0]}的技能LV"
            current = rank_of(u.masteries[entries[0]][1])
        else:
            title = f"{len(entries)} 项技能的LV（{self._short_list(names)}）"
            current = None
        r = self._picker(title, rows, current)
        if r is None:
            return
        exp = RANK_EXP[r]
        self._remember_masteries(u, entries)

        def edit(g):
            v = next((x for x in g.units() if x.start == u.start), None)
            if v is None:
                return None
            mas = list(v.masteries)
            changed = False
            for i in entries:
                if i < len(mas) and mas[i][1] != exp:
                    mas[i] = (mas[i][0], exp)
                    changed = True
            if not changed:
                return None
            return core.with_lists(g, v, mas, list(v.classes))

        what = (f"{names[0]} → {RANKS[r]}" if len(entries) == 1
                else f"{len(entries)} 项技能 → {RANKS[r]}")
        self._core_edit(edit, what)

    def set_mastery_exp(self):
        u = self._cls_unit()
        entries = self._mastery_entries()
        if u is None or not entries:
            self.flash("请先选择一项技能。", "info")
            return
        names = self._mastery_names(u, entries)
        cur = u.masteries[entries[0]][1] if len(entries) == 1 else 0
        if len(entries) == 1:
            prompt = f"{names[0]}的技能EXP（0 – 65535）："
        else:
            prompt = f"{len(entries)} 项技能（{self._short_list(names)}）的EXP（0 – 65535）："
        val = _ask_number(self, "技能EXP", prompt, cur, 65535)
        if val is None:
            return
        self._remember_masteries(u, entries)

        def edit(g):
            v = next((x for x in g.units() if x.start == u.start), None)
            if v is None:
                return None
            mas = list(v.masteries)
            changed = False
            for i in entries:
                if i < len(mas) and mas[i][1] != val:
                    mas[i] = (mas[i][0], val)
                    changed = True
            if not changed:
                return None
            return core.with_lists(g, v, mas, list(v.classes))

        if len(entries) == 1:
            what = f"{names[0]} EXP → {val}"
        else:
            what = f"{len(entries)} 项技能的EXP → {val}"
        self._core_edit(edit, what)

    def add_mastery(self):
        u = self._cls_unit()
        if u is None:
            return
        have = {m[0] for m in u.masteries}
        rows = [(i, MASTERIES[i][0], MASTERIES[i][1]) for i in range(len(MASTERIES))
                if i not in have]
        if not rows:
            self.flash("该单位已拥有全部技能。", "info")
            return
        idx = self._picker("添加技能", rows)
        if idx is None:
            return
        self._mas_keep = (u.start, {idx})

        def edit(g):
            v = next((x for x in g.units() if x.start == u.start), None)
            if v is None:
                return None
            mas = sorted(list(v.masteries) + [(idx, 0)])
            return core.with_lists(g, v, mas, list(v.classes))

        self._core_edit(edit, f"已添加技能 {MASTERIES[idx][0]}")

    def remove_mastery(self):
        u = self._cls_unit()
        entries = self._mastery_entries()
        if u is None or not entries:
            self.flash("请先选择一项技能。", "info")
            return
        names = self._mastery_names(u, entries)
        what_q = (f"{names[0]}" if len(entries) == 1
                  else f"{len(entries)} 项技能（{self._short_list(names, 6)}）")
        if not messagebox.askyesno("存档编辑器",
                                   f"移除 {unit_name(u.person)} 的{what_q}？",
                                   parent=self):
            return
        drop = set(entries)
        self._mas_keep = (None, ())

        def edit(g):
            v = next((x for x in g.units() if x.start == u.start), None)
            if v is None:
                return None
            mas = [m for i, m in enumerate(v.masteries) if i not in drop]
            if len(mas) == len(v.masteries):
                return None
            return core.with_lists(g, v, mas, list(v.classes))

        what = (f"已移除技能 {names[0]}" if len(entries) == 1
                else f"已移除 {len(entries)} 项技能")
        self._core_edit(edit, what)

    # ============================================================== FLAGS
    def _build_flags(self):
        page = self.flags_page
        top = ttk.Frame(page, style="Card.TFrame", padding=(14, 12))
        top.pack(fill="x")
        row = ttk.Frame(top, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text='存档标记', style="CardH3.TLabel").pack(side="left")
        self.flag_count = ttk.Label(row, text="", style="CardMuted.TLabel")
        self.flag_count.pack(side="left", padx=(10, 0))
        ttk.Button(row, text='添加标记…', style="Small.TButton",
                   command=self.add_flag).pack(side="right")
        ttk.Button(row, text='设置数值…', style="Small.TButton",
                   command=self.set_flag_value).pack(side="right", padx=(6, 0))
        ttk.Button(row, text='移除', style="Small.TButton",
                   command=self.remove_flag).pack(side="right", padx=(6, 0))
        self.flag_search = ttk.Entry(top)
        self.flag_search.pack(fill="x", pady=(8, 6))
        self.flag_search.bind("<KeyRelease>", lambda e: self.refresh_flags())
        quick = ttk.Frame(top, style="Card.TFrame")
        quick.pack(fill="x")
        for text, prefixes in (("全部", ()), ("支援", ("支援会話_",)), ("已加入", ("加入_",)),
                               ("已会面", ("面識_",)), ("伟业录", ("実績_", "実績状態_")),
                               ("教程", ("TUTO_",)), ("兵种解锁", ("兵種解放_", "共有_兵種解放_"))):
            ttk.Button(quick, text=text, style="Small.TButton",
                       command=lambda p=prefixes, t=text: self.set_flag_filter(p, t)).pack(side="left", padx=(0, 6))
        self.flag_filter = ()
        self.flag_filter_label = ""
        self.flag_english = tk.BooleanVar(master=self, value=False)
        ttk.Checkbutton(quick, text="英文名与原始键名", variable=self.flag_english,
                        command=self._update_flag_columns).pack(side="right")
        self.flag_tree = scrolled_tree(page, columns=("label", "value", "map"),
                                       show="tree headings", selectmode="browse")
        self.flag_tree.heading("label", text='原始键名', anchor="w")
        self.flag_tree.heading("value", text='数值', anchor="e")
        self.flag_tree.heading("map", text='列表', anchor="e")
        self.flag_tree.column("#0", width=260)
        self.flag_tree.column("label", width=360)
        self.flag_tree.column("value", width=80, anchor="e", stretch=False)
        self.flag_tree.column("map", width=60, anchor="e", stretch=False)
        self.flag_tree.container.pack(fill="both", expand=True, pady=(8, 0))
        ttk.Label(page, style="CardFaint.TLabel", justify="left", wraplength=980,
                  text="可按中文名、英文名、原始键名或哈希搜索。勾选「英文名与原始键名」可查看存档键。"
                       "中文名尚待核对或原始键尚未识别的标记，可用哈希定位。"
                  ).pack(anchor="w", pady=(8, 0))
        self._update_flag_columns(refresh=False)

    def _update_flag_columns(self, refresh=True):
        english = flag_english_mode(self)
        self.flag_tree.heading("#0", text="英文名称" if english else "中文名称", anchor="w")
        self.flag_tree.configure(displaycolumns=("label", "value", "map") if english else ("value", "map"))
        if refresh:
            self.refresh_flags()

    def set_flag_filter(self, prefixes: tuple[str, ...], label: str):
        self.flag_filter = prefixes
        self.flag_filter_label = label if prefixes else ""
        self.refresh_flags()

    def refresh_flags(self):
        if not hasattr(self, "flag_tree"):
            return
        f = self.core_of()
        self.flag_tree.delete(*self.flag_tree.get_children())
        self._flag_rows = {}
        if not f:
            self.flag_count.configure(text="打开存档后可查看标记。")
            return
        names = flag_names()
        q = ""
        try:
            q = self.flag_search.get().strip().lower()
        except Exception:                     # noqa: BLE001
            pass
        shown = 0
        total = 0
        english = flag_english_mode(self)
        for m in core.flag_maps(f):
            for fl in m.flags:
                total += 1
                meta = names.get(fl.hash)
                name = meta["name"] if meta else ""
                label = meta.get("label") if meta else ""
                if self.flag_filter:
                    if not name.startswith(self.flag_filter):
                        continue
                zh = flag_display.chinese_label(fl.hash, meta) if q or not english else ""
                text = flag_display_name(fl.hash, meta, english, zh)
                if q:
                    hay = f"{name} {label or ''} {zh} {text} 0x{fl.hash:08x}".lower()
                    if q not in hay:
                        continue
                iid = self.flag_tree.insert("", "end", text=text,
                                            values=(name, fl.value, m.index),
                                            tags=("flag_unknown",) if not meta or (not english and not zh) else ())
                self._flag_rows[iid] = (m.index, fl.hash)
                shown += 1
        filter_label = getattr(self, "flag_filter_label", "")
        self.flag_count.configure(text=f"共 {total:,} 项，显示 {shown:,} 项"
                                       + (f" · {filter_label}" if filter_label else ""))
        p = self.p
        self.flag_tree.tag_configure("flag_unknown", foreground=p["muted"])

    def _flag_sel(self):
        sel = self.flag_tree.selection() if hasattr(self, "flag_tree") else ()
        return getattr(self, "_flag_rows", {}).get(sel[0] if sel else None)

    def set_flag_value(self):
        key = self._flag_sel()
        if not key:
            return
        f = self.core_of()
        map_i, hash_ = key
        cur = next((fl.value for m in core.flag_maps(f) for fl in m.flags
                    if m.index == map_i and fl.hash == hash_), 0)
        val = _ask_number(self, "标记数值", "新数值（0 – 2147483647）：", cur, 2_147_483_647)
        if val is None or (val == cur and not self.mirror.get()):
            return
        self._write_flag(map_i, hash_, val)

    def _write_flag(self, map_i: int, hash_: int, value: int):
        if not self.sv:
            return
        source = core.SaveFile(self.sv.payload)
        selected_map = next((m for m in core.flag_maps(source) if m.index == map_i), None)
        if selected_map is None or not any(fl.hash == hash_ for fl in selected_map.flags):
            self.flash('所选标记已不存在。请刷新列表。', 'error')
            return
        def edit(f):
            fm = matching_flag_map(source, f, map_i)
            if any(fl.hash == hash_ for fl in fm.flags):
                return core.flag_set(f, fm, hash_, value)
            return core.flag_add(f, fm, hash_, value)
        name = flag_display_name(hash_, flag_names().get(hash_), flag_english_mode(self))
        self._core_edit(edit, f'{name} = {value}')

    def remove_flag(self):
        key = self._flag_sel()
        if not key:
            return
        map_i, hash_ = key
        name = flag_display_name(hash_, flag_names().get(hash_), flag_english_mode(self))
        if not messagebox.askyesno("存档编辑器", f"移除标记 {name}？", parent=self):
            return

        source = core.SaveFile(self.sv.payload)
        def edit(g):
            m = matching_flag_map(source, g, map_i)
            fl = next((x for x in m.flags if x.hash == hash_), None)
            return core.flag_remove(g, m, fl) if fl else g.payload

        self._core_edit(edit, f"已移除 {name}")

    def add_flag(self):
        names = flag_names()
        english = flag_english_mode(self)
        rows = []
        titled = set()
        search_terms = {}
        for h, meta in names.items():
            zh = flag_display.chinese_label(h, meta)
            name = flag_display_name(h, meta, english, zh)
            key = meta["name"]
            if (english and meta.get("label") not in (None, "", key)) or (not english and zh):
                titled.add(h)
            rows.append((h, name, key if english else f"0x{h:08x}"))
            search_terms[h] = f"{zh} {meta.get('label', '')} {key} 0x{h:08x}".lower()
        rows.sort(key=lambda row: (row[0] not in titled, row[1].lower()))
        hash_ = self._picker("添加标记", rows, search_terms=search_terms,
                             secondary_title="原始键名" if english else "哈希")
        if hash_ is None:
            return
        val = _ask_number(self, "标记数值", "要保存的数值（0 – 254，更大的值也可以）：",
                          1, 2_147_483_647)
        if val is None:
            return
        f = self.core_of()
        maps = core.flag_maps(f)
        if not maps:
            self.flash("此存档没有可添加标记的列表。", "error")
            return
        target = maps[0].index

        source = core.SaveFile(self.sv.payload)
        def edit(g):
            m = matching_flag_map(source, g, target)
            if any(fl.hash == hash_ for fl in m.flags):
                return core.flag_set(g, m, hash_, val)
            return core.flag_add(g, m, hash_, val)

        name = flag_display_name(hash_, names.get(hash_), english)
        self._core_edit(edit, f"已添加 {name} = {val}")

    # ====================================================== support unlock
    def _map_for_prefix(self, f, prefix: str) -> int:
        """The flag list that already holds flags named like `prefix`."""
        names = flag_names()
        best, best_n = None, 0
        for m in core.flag_maps(f):
            n = sum(1 for x in m.flags
                    if (names.get(x.hash) or {}).get("name", "").startswith(prefix))
            if n > best_n:
                best, best_n = m, n
        if best is not None:
            return best.index
        maps = core.flag_maps(f)
        return maps[0].index if maps else -1

    def sup_set_rank(self, rank: str):
        """Mark the <rank> conversation as seen for every selected pair."""
        sel = self.sup_tree.selection() if hasattr(self, "sup_tree") else ()
        rows = getattr(self, "_sup_pairs", {})
        picked = [rows[i] for i in sel if i in rows]
        if not picked:
            self.flash("请先在支援页选择一个支援对象。", "info")
            return
        if not any((fl or {}).get(rank) for _p, fl in picked):
            self.flash(("这对单位在游戏数据中没有" if len(picked) == 1 else "所选配对均没有")
                       + f" {rank} 对话。", "error")
            return
        f = self.core_of()
        have = {x.hash for m in core.flag_maps(f) for x in m.flags}
        todo = [(pair, fl[rank]) for pair, fl in picked
                if (fl or {}).get(rank) and fl[rank] not in have]
        if not todo:
            self.flash(f"已记录（{rank}）。")
            return
        target = self._map_for_prefix(f, "支援会話_")
        hashes = [h for _p, h in todo]

        def edit(g):
            for h in hashes:
                ms = core.flag_maps(g)
                m = next((x for x in ms if x.index == target), ms[0] if ms else None)
                if m is None:
                    return None
                out = core.flag_add(g, m, h, 1)
                if out is None:
                    return None
                g = core.SaveFile(out)
            return g.payload

        pair = todo[0][0]
        who = pair["b_en"] if len(todo) == 1 else f"{len(todo)} 名对象"
        self._core_edit(edit, f"已记录支援对话：{pair['a_en']} – {who}（{rank}）")
        self.refresh_support_chars()

    def sup_forget_rank(self):
        sel = self.sup_tree.selection() if hasattr(self, "sup_tree") else ()
        rows = getattr(self, "_sup_pairs", {})
        if not sel or sel[0] not in rows:
            self.flash("请先在支援页选择一个支援对象。", "info")
            return
        pair, fl = rows[sel[0]]
        ranks = [(r, r, "") for r in ("C", "B", "A") if fl.get(r)]
        if not ranks:
            self.flash("游戏数据中这对单位没有任何对话。", "error")
            return
        rank = self._picker("要清除哪段对话？", ranks)
        if rank:
            self.sup_clear_rank(rank)

    def sup_clear_rank(self, rank: str):
        sel = self.sup_tree.selection() if hasattr(self, "sup_tree") else ()
        rows = getattr(self, "_sup_pairs", {})
        if not sel or sel[0] not in rows:
            return
        pair, fl = rows[sel[0]]
        hash_ = (fl or {}).get(rank)
        if not hash_:
            return
        if not messagebox.askyesno("存档编辑器",
                                   f"清除 {pair['a_en']} 与 {pair['b_en']} 的 "
                                   f"{rank} 对话记录？", parent=self):
            return

        def edit(g):
            for m in core.flag_maps(g):
                for x in m.flags:
                    if x.hash == hash_:
                        return core.flag_remove(g, m, x)
            return None

        self._core_edit(edit, f"已清除支援对话：{pair['a_en']} – {pair['b_en']}（{rank}）")
        self.refresh_support_chars()


    # ====================================================== support points
    def sup_apply_points(self):
        """Write the number in the points box onto the selected pair."""
        raw = (self.sup_pt_var.get() or "").strip().replace(",", "").replace(" ", "")
        if not raw.isdigit():
            self.flash(f"请输入 0 到 {supports.max_points():,} 之间的支援值。", "error")
            return
        self.sup_set_points(int(raw))

    def sup_set_points(self, value: int):
        """Set support points (the save's own value) on every selected pair."""
        sel = self.sup_tree.selection() if hasattr(self, "sup_tree") else ()
        pt = getattr(self, "_sup_pt", {})
        rows = [i for i in sel if i in pt]
        if not rows:
            self.flash("请先在支援页选择一个支援对象。", "info")
            return
        value = max(0, min(int(value), supports.max_points()))
        a_en = self.sup_name.cget("text")
        todo = [(pt[i][0], self.sup_tree.item(i, "text")) for i in rows if pt[i][1] != value]
        if not todo:
            who = (self.sup_tree.item(rows[0], "text") if len(rows) == 1
                   else f"全部 {len(rows)} 组")
            self.flash(f"{a_en} – {who} 已经是 {value:,} 点。", "info")
            return
        hashes = [h for h, _n in todo]

        def edit(g):
            blk = g.block("GameUserData")
            if not blk:
                return None
            table = supports.find_table(g.payload, blk.start, blk.end) or {}
            changed = False
            for h in hashes:
                entry = table.get(h)
                if entry is None or entry[0] == value:
                    continue
                supports.set_points(g.payload, entry[2], value)
                changed = True
            return g.payload if changed else None

        who = todo[0][1] if len(todo) == 1 else f"{len(todo)} 组"
        # _core_edit re-reads the save and refreshes every page, which rebuilds
        # this one with the same character and partners still selected.
        self._core_edit(edit, f"支援值 {a_en} – {who} → {value:,}")

def settings_offsets(f: core.SaveFile) -> list[int]:
    """Offsets of the (mode, difficulty) byte pairs, everywhere the save keeps them."""
    out = []

    def collect(start: int, length: int, depth: int):
        if depth > 6:
            return
        sub = core.SaveFile(bytes(f.payload[start:start + length]))
        hdr = sub.block("Header")
        if hdr is not None and hdr.start == 4 and sub.payload[12] == 15:
            out.append(start + 14)
        ud = sub.block("GameUserData")
        if ud is not None and ud.start + 7 <= len(sub.payload) and sub.payload[ud.start + 4] == 23:
            out.append(start + ud.start + 5)
        for node in sub.tree().nodes:
            if not node.leaf:
                continue
            at = node.start + 4
            body = None
            for probe in (node.start + 4, node.start + 8):
                if probe + 32 <= node.end and \
                        sub.u32(probe) == core.MAGIC:
                    size = sub.u32(probe + 4)
                    if sub.u32(probe + 12) == 1 and 0 < size and probe + 32 + size <= node.end:
                        body = (probe + 32, size)
                        break
            if body and sub.payload[body[0] + 4] == 15:
                out.append(start + body[0] + 6)
            elif body:
                try:
                    collect(start + body[0], body[1], depth + 1)
                except Exception:             # noqa: BLE001
                    pass

    collect(0, len(f.payload), 0)
    return sorted(set(out))


def _ask_number(parent, title: str, prompt: str, current: int, limit: int):
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.resizable(False, False)
    win.configure(background=parent.p["bg"])
    ttk.Label(win, text=prompt, padding=(16, 14, 16, 6)).pack(anchor="w")
    var = tk.StringVar(value=str(current))
    ent = ttk.Entry(win, textvariable=var, width=18, justify="right")
    ent.pack(padx=16)
    out = {"v": None}

    def ok(*_a):
        t = var.get().strip().replace(",", "")
        if t.isdigit():
            out["v"] = min(int(t), limit)
        win.destroy()

    bar = ttk.Frame(win, padding=12)
    bar.pack(fill="x")
    ttk.Button(bar, text='确定', style="Accent.TButton", command=ok).pack(side="right")
    ttk.Button(bar, text='取消', command=win.destroy).pack(side="right", padx=(0, 8))
    ent.bind("<Return>", ok)
    if hasattr(parent, "place_dialog"):
        parent.place_dialog(win)
    ent.focus_set()
    ent.select_range(0, "end")
    parent.wait_window(win)
    return out["v"]



# ==========================================================================
# part 2 — the pages that replace the guessing code
# (appended to editor_advanced.py; every method here uses fefw_layer, which
#  answers unit / item / skill / convoy / gold questions from fefw_core)
# ==========================================================================
import fefw_layer as layer          # noqa: E402


def _slot_payload(app):
    return app.sv.payload if app.sv else None


# ---------------------------------------------------------------- undo/redo
def _history_snapshot(slots):
    return [(sv, bytes(sv.payload), getattr(sv, '_pending_story_chapter', None)) for sv in slots]


def push_undo(self, slots=None):
    """Snapshot every affected slot; story actions pass only the selected slot."""
    if not self.sv:
        return
    if getattr(self, '_history_slot', None) is not self.sv:
        self._undo_stack, self._redo_stack = [], []
        self._history_slot = self.sv
    slots = list(self._targets() if slots is None else slots)
    snapshot = _history_snapshot(slots)
    self._undo_stack.append(snapshot)
    del self._undo_stack[:-60]
    self._redo_stack.clear()


def _restore_history(self, source, destination, message):
    stack = getattr(self, source, None)
    if not stack or not self.sv or getattr(self, '_history_slot', None) is not self.sv:
        self.flash('没有可撤销的操作。' if source == '_undo_stack' else '没有可重做的操作。', 'info')
        return
    snapshot = stack[-1]
    files = getattr(self, 'files', None)
    if files is not None and any(files.get(sv.path) is not sv for sv, _, _ in snapshot):
        self.flash('此编辑涉及的某个存档已被重新加载。历史已保留；请先重新打开原存档再撤销。', 'warn')
        return
    slots = [sv for sv, _, _ in snapshot]
    getattr(self, destination).append(_history_snapshot(slots))
    stack.pop()
    for sv, payload, pending in snapshot:
        sv.payload = bytearray(payload)
        sv._pending_story_chapter = pending
        sv.scan()
        sv._core_cache = None
    self._touch(slots)
    self.refresh_after_core()
    self.flash(message, 'ok')


def undo(self):
    _restore_history(self, '_undo_stack', '_redo_stack', '已撤销——按 Ctrl+Y 恢复。')


def redo(self):
    _restore_history(self, '_redo_stack', '_undo_stack', '已重做。')


# --------------------------------------------------------------- snapshots
def snapshot_root(self) -> str:
    import os
    return os.path.join(os.path.dirname(os.path.abspath(self.sv.path)), "_snapshots")


def take_snapshot(self, name: str | None = None):
    import os
    import shutil
    import time
    if not self.folder:
        return
    stamp = time.strftime("%Y%m%d_%H%M%S")
    label = (name or "").strip()
    safe = "".join(c if c not in '\\/:*?"<>|' else "_" for c in label)
    dest = os.path.join(self.snapshot_root(), stamp + ((" " + safe) if safe else ""))
    os.makedirs(dest, exist_ok=True)
    saved = 0
    for p in self.slot_paths:
        if os.path.exists(p):
            shutil.copy2(p, dest)
            saved += 1
    self.flash(f"已将 {saved} 个槽位的快照保存到 {os.path.basename(dest)}", "ok")


def restore_snapshot(self):
    import os
    import shutil
    from tkinter import filedialog, messagebox
    root = self.snapshot_root()
    if not os.path.isdir(root):
        self.flash("还没有快照——请先点击“创建快照”。", "warn")
        return
    pick = filedialog.askdirectory(parent=self, initialdir=root,
                                   title="选择要恢复的快照")
    if not pick:
        return
    n = len([f for f in os.listdir(pick) if os.path.isfile(os.path.join(pick, f))])
    if not messagebox.askyesno(
            APP_NAME, f"把 {n} 个槽位文件从\n{pick}\n复制并覆盖存档文件夹？\n\n"
                      "编辑器中的所有内容都会从这些文件重新加载。", parent=self):
        return
    for name in os.listdir(pick):
        src = os.path.join(pick, name)
        if os.path.isfile(src) and is_slot_name(name):
            shutil.copy2(src, os.path.join(self.folder, name))
    self.dirty.clear()
    self.reload()
    self.flash(f"已从 {os.path.basename(pick)} 恢复 {n} 个槽位。", "ok")


# ------------------------------------------------------------- arts/skills
def _build_skill_tab(self):
    """The record's skill lists (all six), each with add / remove."""
    tab = ttk.Frame(self.unit_tabs, style="Card.TFrame", padding=16)
    self.unit_tabs.add(tab, text='战技与特技')
    grid = ttk.Frame(tab, style="Card.TFrame")
    grid.pack(fill="both", expand=True)
    self._skill_trees = {}
    for idx, name in enumerate(layer.SKILL_LIST_NAMES):
        box = ttk.Frame(grid, style="Card.TFrame", padding=(0, 0, 10, 10))
        box.grid(row=idx // 3, column=idx % 3, sticky="nsew")
        grid.columnconfigure(idx % 3, weight=1, uniform="skills")
        grid.rowconfigure(idx // 3, weight=1)
        head = ttk.Frame(box, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=name, style="CardH3.TLabel").pack(side="left")
        for text, fn in (("移除", lambda i=idx: self.remove_skill(i)),
                         ("添加…", lambda i=idx: self.add_skill(i))):
            ttk.Button(head, text=text, style="Small.TButton", width=7,
                       command=fn).pack(side="right", padx=(4, 0))
        tree = scrolled_tree(box, show="tree", selectmode="browse", height=5)
        tree.container.pack(fill="both", expand=True, pady=(4, 0))
        self._skill_trees[idx] = tree
    ttk.Label(tab, style="CardFaint.TLabel", justify="left", wraplength=820,
              text="「装备战技／装备特技」是单位带上战场的内容；「已习得（全部）」是学过的"
                   "一切。添加或删除条目都会安全地重写列表。"
              ).pack(anchor="w", pady=(8, 0))


def refresh_skill_lists(self):
    if not hasattr(self, "_skill_trees") or not self.sv or self.uid is None:
        return
    lists = layer.skill_lists(self.sv.payload, self.uid)
    for idx, tree in self._skill_trees.items():
        tree.delete(*tree.get_children())
        ids = lists[idx] if idx < len(lists) else []
        for k, sid in enumerate(ids):
            tree.insert("", "end", iid=str(k), text="  " + layer.skill_label(sid))
        if not ids:
            tree.insert("", "end", iid="none", text='  （空）', tags=("empty",))


def add_skill(self, index: int):
    if not self.sv or self.uid is None:
        return
    # every entry in these lists is a skill / combat art id (SID_…), never an item;
    # [e] / [効果] / SC_ / エフェクト_ entries are internal effect definitions,
    # not skills a unit can learn, so they stay out of the picker
    # pickable = has an English name (the real learnable skills) or an actual
    # table translation; a Japanese-only label is kanji too, so "contains a
    # Han character" is not proof of a translation — the table must hit
    rows = []
    skills = layer._skills()
    for h, m in skills.items():
        jp = m.get("jp") or ""
        if (m.get("sid") or "").startswith("SID_>") or "[" in jp \
                or jp.startswith(("[e]", "SC", "エフェクト", "イベント", "SID_", ">")):
            continue
        if not ((m.get("en") or "").strip() or chinese_name("skills", h) or layer._skill_zh(jp)):
            continue
        rows.append((h, layer.skill_label(h), jp))
    # skills with an English name first; internal / enemy-only entries after
    eng = {h for h, m in skills.items() if m.get("en")}
    rows.sort(key=lambda r: (r[0] not in eng, str(r[1]).lower()))
    if not rows:
        self.flash("没有可用的战技或特技名称。", "warn")
        return
    search = {h: f"{skills[h].get('en', '')} {skills[h].get('sid', '')} {h:08x}".lower()
              for h, _, _ in rows}
    sid = self._picker(f"添加到「{layer.SKILL_LIST_NAMES[index]}」", rows,
                       search_terms=search, secondary_title="日文原名")
    if sid is None:
        return

    def edit(g):
        u = layer.unit_in(g, self.uid)
        if u is None:
            return None
        try:
            ids = core.skill_list(g, u, index + 1)
        except Exception:                     # noqa: BLE001
            return None
        if sid in ids:
            return None
        return core.with_skill_list(g, u, index + 1, ids + [sid])

    self._core_edit(edit, f"{layer.SKILL_LIST_NAMES[index]}：已添加 {layer.skill_label(sid)}")


def remove_skill(self, index: int):
    tree = getattr(self, "_skill_trees", {}).get(index)
    sel = tree.selection() if tree else ()
    if not self.sv or self.uid is None or not sel or sel[0] == "none":
        return
    k = int(sel[0])

    def edit(g):
        u = layer.unit_in(g, self.uid)
        if u is None:
            return None
        try:
            ids = core.skill_list(g, u, index + 1)
        except Exception:                     # noqa: BLE001
            return None
        if k >= len(ids):
            return None
        return core.with_skill_list(g, u, index + 1, [x for i, x in enumerate(ids) if i != k])

    self._core_edit(edit, f"{layer.SKILL_LIST_NAMES[index]}：已移除第 {k + 1} 项")


# ------------------------------------------------------------------ mounts
def _build_mounts(self):
    pane = ttk.PanedWindow(self.mounts_page, orient="horizontal")
    pane.pack(fill="both", expand=True)
    left = ttk.Frame(pane, style="Card.TFrame", padding=12)
    pane.add(left, weight=3)
    ttk.Label(left, text='军团兵与坐骑', style="CardH3.TLabel").pack(anchor="w")
    ttk.Label(left, style="CardMuted.TLabel", wraplength=520, justify="left",
              text="每个单位在记录末尾携带一个军团兵（骑乘兵种还有一匹坐骑）。"
                   "从本存档持有的资源池中选择。").pack(
        anchor="w", pady=(2, 8))
    self.mount_tree = scrolled_tree(left, columns=("bat", "mount"), show="tree headings",
                                    selectmode="browse")
    self.mount_tree.heading("#0", text='单位', anchor="w")
    self.mount_tree.heading("bat", text='军团兵', anchor="w")
    self.mount_tree.heading("mount", text='坐骑', anchor="w")
    self.mount_tree.column("#0", width=190)
    self.mount_tree.column("bat", width=230)
    self.mount_tree.column("mount", width=140)
    self.mount_tree.container.pack(fill="both", expand=True)

    right = ttk.Frame(pane, style="Card.TFrame", padding=12)
    pane.add(right, weight=2)
    ttk.Label(right, text='此存档的资源池', style="CardH3.TLabel").pack(anchor="w")
    self.pool_tree = scrolled_tree(right, columns=("kind", "id"), show="tree headings",
                                   selectmode="browse")
    self.pool_tree.heading("#0", text='条目', anchor="w")
    self.pool_tree.heading("kind", text='种类', anchor="w")
    self.pool_tree.heading("id", text='记录编号', anchor="w")
    self.pool_tree.column("#0", width=90)
    self.pool_tree.column("kind", width=80)
    self.pool_tree.column("id", width=190)
    self.pool_tree.container.pack(fill="both", expand=True, pady=(6, 6))
    row = ttk.Frame(right, style="Card.TFrame")
    row.pack(fill="x")
    ttk.Button(row, text='分配给单位', style="Small.TButton",
               command=self.assign_battalion).pack(side="left")
    ttk.Button(row, text='清除单位装备', style="Small.TButton",
               command=lambda: self.assign_battalion(clear=True)).pack(side="left", padx=(6, 0))
    ttk.Button(row, text='从资源池移除', style="Small.TButton",
               command=self.remove_battalion).pack(side="left", padx=(6, 0))


def refresh_mounts(self):
    if not hasattr(self, "mount_tree"):
        return
    self.mount_tree.delete(*self.mount_tree.get_children())
    self.pool_tree.delete(*self.pool_tree.get_children())
    self._mount_rows, self._pool_rows = {}, {}
    if not self.sv:
        return
    bat_by_unit = {}
    for l in layer.battalions(self.sv.payload):
        bat_by_unit.setdefault(l.owner, l)
    for u in layer.find_units(self.sv.payload):
        g = layer.unit_gear(self.sv.payload, u.uid)
        bat = bat_by_unit.get((g.unit_number if g else -1))
        bat_txt = (f"军团兵 第 {bat.row} 行") if bat else "—"
        mnt = (f"坐骑 #{g.ride.serial}") if (g and g.ride) else "—"
        iid = self.mount_tree.insert("", "end", text=u.name,
                                     values=(bat_txt, mnt))
        self._mount_rows[iid] = u.uid
    for i, l in enumerate(layer.battalions(self.sv.payload)):
        iid = self.pool_tree.insert("", "end", text=f"#{i + 1}", values=("军团兵", hex(l.id)),
                                    iid="b%d" % i)
        self._pool_rows[iid] = ("battalion", i)
    for i, r in enumerate(layer.mounts(self.sv.payload)):
        iid = self.pool_tree.insert("", "end", text=f"#{i + 1}", values=("坐骑", hex(r.id)),
                                    iid="m%d" % i)
        self._pool_rows[iid] = ("mount", i)


def assign_battalion(self, clear: bool = False):
    sel = self.mount_tree.selection() if hasattr(self, "mount_tree") else ()
    uid = getattr(self, "_mount_rows", {}).get(sel[0] if sel else None)
    if uid is None or not self.sv:
        self.flash("请先在坐骑页选择一个单位。", "info")
        return
    legion = None
    ride = None
    g = layer.unit_gear(self.sv.payload, uid)
    if g is None:
        self.flash("该单位的记录没有军团兵栏位（单位实际参战时游戏才会写入）"
                   "——没有可写的内容。", "warn")
        return
    if g.ride:
        ride = g.ride
    if not clear:
        psel = self.pool_tree.selection()
        if not psel:
            self.flash("请先在资源池中选择一个军团兵。", "info")
            return
        kind, idx = self._pool_rows.get(psel[0], (None, None))
        if kind != "battalion":
            self.flash("这里只能分配军团兵。", "warn")
            return
        legion = layer.battalions(self.sv.payload)[idx]
        legion = core.Legion(legion.row, g.unit_number if g else 0, legion.id)

    def edit(f):
        u = layer.unit_in(f, uid)
        gg = core.gear(f, u)
        if gg is None:
            return None
        return core.with_gear(f, u, gg, ride, legion)

    self._core_edit(edit, "已清除军团兵" if clear
                    else (f"军团兵 → 第 {legion.row} 行"))


def remove_battalion(self):
    psel = self.pool_tree.selection() if hasattr(self, "pool_tree") else ()
    if not psel or not self.sv:
        return
    kind, idx = self._pool_rows.get(psel[0], (None, None))
    if kind != "battalion":
        self.flash("请先在资源池中选择一个军团兵。", "info")
        return
    self._core_edit(lambda f: layer_del_battalion(f, idx), "已从资源池移除军团兵")


def layer_del_battalion(f, idx: int):
    st = core.party_store(f)
    if not st:
        return None
    pool = core.legions(f, st[2])
    if not (0 <= idx < len(pool)):
        return None
    return core.with_legions(f, st[2], [l for i, l in enumerate(pool) if i != idx])


# ------------------------------------------------------- units: copy / remove
def copy_unit_in(self):
    """Bring a unit over from another slot of the same folder."""
    import os
    from tkinter import filedialog
    if not self._unit_entry_ready():
        return
    expected=(self.sv,bytes(self.sv.payload))
    start = self.folder or os.path.dirname(self.sv.path)
    # no filetypes: slot files have no extension and the macOS panel greys
    # out everything the glob patterns don't match
    pick = filedialog.askopenfilename(
        parent=self, initialdir=start, title="从哪个存档复制单位？")
    if not pick:
        return
    try:
        src = layer.SaveFile(pick)
    except Exception as exc:                  # noqa: BLE001
        self.flash(f"无法读取 {os.path.basename(pick)}：{exc}", "error")
        return
    rows = [(u.uid, u.name, f"LV {u.level} · {u.job}") for u in src.units]
    rows.sort(key=lambda r: r[1].lower())
    uid = self._picker(f"从 {os.path.basename(pick)} 复制单位", rows)
    if uid is None:
        return
    if self.sv is not expected[0] or bytes(self.sv.payload)!=expected[1]:
        self.flash('目标存档已发生变化。请重新打开导入对话框。', 'warn');return
    try:
        import unit_entries
        source=self.core_of(src)
        unit=layer.unit_in(source,uid)
        if unit is None:raise core.SaveError('No unique source unit.')
    except core.SaveError as exc:self.flash(str(exc),'error');return
    name = next(r[1] for r in rows if r[0] == uid)
    if self._core_edit(lambda f: unit_entries.copy_in(source, unit, f),
                       f"已导入 {name}（自动分配空闲编号）"):
        self.uid = uid
        self.show_unit()


def remove_current_unit(self):
    if not self.sv or self.uid is None:
        return
    from tkinter import messagebox
    u = self._current()
    if u is None:
        return
    if not messagebox.askyesno(
            APP_NAME, f"从存档中移除 {u.name}？\n\n"
                      "游戏可能仍会引用该单位（加入标记、军团兵、支援），这可能让游戏产生"
                      "混乱。可以用撤销（Ctrl+Z）恢复；保存之前不会写入磁盘。", parent=self):
        return
    self._core_edit(lambda f: (layer.remove_unit(f.payload, self.uid) and f.payload) or None,
                    f"已移除 {u.name}")
    self.uid = None


# ------------------------------------------------------------ convoy extras
def add_convoy_item_ui(self):
    if not self.sv:
        return
    rows, internal_rows = [], []
    search_terms = {}
    show_internal = self.show_internal.get()
    for iid, _, label, jp, _, _, internal, blob in self.item_rows:
        if internal and not show_internal:
            continue
        (internal_rows if internal else rows).append((iid, label, jp))
        search_terms[iid] = blob
    iid = self._picker("添加到道具袋", rows + internal_rows, search_terms=search_terms,
                       secondary_title="日文／内部键名", preview_text=item_preview_text)
    if iid is None:
        return
    qty = _ask_number(self, "数量", "要添加多少个？", 1, 999)
    if not qty:
        return
    conv = layer.live_convoy(bytes(self.sv.payload))
    if conv is not None and not layer.stacks(iid):
        # gear takes one slot per copy: say so up front if they won't fit
        room = layer.convoy_free_gear_slots(bytes(self.sv.payload))
        if room < qty:
            self.flash(f"{layer.item_label(iid)} 不可堆叠——{qty} 件需要 {qty} 个槽位，"
                       f"道具袋只剩 {room} 个。", "warn")
            return
        if self._core_edit(
                lambda f: (layer.add_convoy_copies(f.payload, iid, qty) and f.payload) or None,
                f"道具袋：已添加 {layer.item_label(iid)} ×{qty}（每件一槽）"):
            self.refresh_convoy()
            return
    home = layer.convoy_home(iid, conv.slots) if conv else None
    if home is None:
        self.flash(f"{layer.item_label(iid)} 没有自己独立的道具袋槽位。", "warn")
        return
    other = next((r for r in conv.rows if r.slot == home and r.qty and r.iid != iid), None)
    if other:
        self.flash(f"槽位 {home + 1} 是 {layer.item_label(iid)} 的专属槽位，现在放着 "
                   f"{layer.item_label(other.iid)}——请先移除它。", "warn")
        return
    self._core_edit(lambda f: (layer.add_convoy_slot(f.payload, iid, qty) and f.payload) or None,
                    f"道具袋：已添加 {layer.item_label(iid)} ×{qty}")
    self.refresh_convoy()


def fill_materials_ui(self):
    """Put every forging/cooking material in the convoy at one quantity."""
    if not self.sv:
        return
    mats = layer.material_ids()
    if not mats:
        self.flash("道具列表中未找到材料。", "error")
        return
    try:
        start = int(self.qty_var.get())
    except (AttributeError, ValueError):
        start = 99
    qty = _ask_number(self, "所有材料",
                      f"每种材料的数量（共 {len(mats)} 种：矿石、肉、鱼、\n"
                      "植物、叶、粪）。每种材料放入自己的道具袋槽位；\n"
                      "已有的堆叠会被设为该数量。", max(1, start), 999)
    if not qty:
        return
    got = {}

    def run(f):
        res = layer.fill_materials(f.payload, qty)
        got.setdefault("r", res)
        return f.payload if any(res[:3]) else None

    if self._core_edit(run, f"Convoy: all materials ×{qty}"):
        kept, added, moved, blocked = got.get("r", (0, 0, 0, 0))
        msg = f"材料 ×{qty}：新增 {added} 种，设定 {kept} 种"
        if moved:
            msg += f"，{moved} 种已移入自己的槽位"
        if blocked:
            msg += f"——{blocked} 种跳过（其槽位放着其他道具）"
        self.flash(msg + "——按 Ctrl+S 写入。", "warn" if blocked else "ok")
    self.refresh_convoy()


def remove_convoy_item_ui(self):
    slots = self._conv_selected() if hasattr(self, "_conv_selected") else []
    if not slots:
        self.flash("请先选择一个道具袋行。", "info")
        return
    for slot in sorted(slots, reverse=True):
        self._core_edit(
            lambda f, s=slot: (layer.remove_convoy_slot(f.payload, s) and f.payload) or None,
            f"道具袋：已清空槽位 {slot + 1}")
    self.refresh_convoy()


# everything above is a plain function taking the app; attach it to the mixin
for _name in ("push_undo", "undo", "redo", "snapshot_root", "take_snapshot",
              "restore_snapshot", "_build_skill_tab", "refresh_skill_lists",
              "add_skill", "remove_skill", "_build_mounts", "refresh_mounts",
              "assign_battalion", "remove_battalion", "copy_unit_in",
              "remove_current_unit", "add_convoy_item_ui", "remove_convoy_item_ui",
              "fill_materials_ui"):
    setattr(AdvancedMixin, _name, globals()[_name])
