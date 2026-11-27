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
import queue
import threading
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
from quest_page import QuestPageMixin
from facility_page import FacilityPageMixin
from story_page import StoryPageMixin
from unit_entry_page import UnitEntryPageMixin
import audiences
import facilities
import learnsets
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


class AdvancedMixin(MapPageMixin, TutorialPageMixin, BlessingPageMixin, FastTravelPageMixin, DeedPageMixin, BoonPageMixin, QuestPageMixin, FacilityPageMixin, StoryPageMixin, UnitEntryPageMixin):
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

    def _core_edit(self, fn, what: str, *, current_only=False, slots=None) -> bool:
        """Preflight every target, then stage one undoable multi-slot edit."""
        if not self.sv:
            return False
        self._commit_form()
        changes = []
        if slots is None:
            slots = [self.sv] if current_only else self._targets()
        for sv in slots:
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
        self.refresh_quests()
        if self.pages.select() == str(self.mounts_page):
            self.refresh_mounts()

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
        self.refresh_quests()

    # -------------------------------------------------------- pick a value
    def _picker(self, title: str, rows, current: int | None = None, *,
                search_terms=None, secondary_title="内部编号", preview_text=None,
                browse_only=False, confirm_text="使用此项", preview_lines=0,
                can_confirm=None):
        """Searchable list of (key, primary, secondary) rows with an optional preview."""
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        width, height = (640, 540) if browse_only else (560, 460)
        self.place_dialog(win, width, height)
        win.configure(background=self.p["bg"])
        top = ttk.Frame(win, padding=10)
        top.pack(fill="x")
        query = tk.StringVar(master=win)
        ent = ttk.Entry(top, textvariable=query)
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
            q = query.get().strip().lower()
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
            if browse_only or can_confirm is not None:
                if iids and not tree.selection():
                    first = next(iter(iids))
                    if not browse_only and can_confirm is not None:
                        first = next((iid for iid, key in iids.items() if can_confirm(key)), first)
                    tree.selection_set(first)
                    tree.see(first)
                update_selection()

        def selected_key():
            sel = tree.selection()
            return iids.get(sel[0]) if sel else None

        def ok(*_a):
            key = selected_key()
            if key is not None and (can_confirm is None or can_confirm(key)):
                result["key"] = key
                win.destroy()

        query.trace_add("write", fill)
        bar = ttk.Frame(win, padding=(10, 6))
        bar.pack(side="bottom", fill="x")
        confirm = None
        if browse_only:
            ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")
            win.bind("<Escape>", lambda _event: win.destroy())
        else:
            tree.bind("<Double-1>", ok)
            tree.bind("<Return>", ok)
            confirm = ttk.Button(bar, text=confirm_text, style="Accent.TButton", command=ok)
            confirm.pack(side="right")
            ttk.Button(bar, text='取消', command=win.destroy).pack(side="right", padx=(0, 8))
        if preview_text is not None:
            if preview_lines:
                detail = ttk.Frame(win)
                detail.pack(side="bottom", fill="x", padx=10, pady=(8, 0))
                preview = tk.Text(detail, height=preview_lines, width=1, wrap="char",
                                  state="disabled", font=self.f_body, padx=8, pady=6,
                                  background=self.p["card"], foreground=self.p["fg"],
                                  selectbackground=self.p["sel"], relief="flat")
                scroll = ttk.Scrollbar(detail, orient="vertical", command=preview.yview)
                preview.configure(yscrollcommand=scroll.set)
                scroll.pack(side="right", fill="y")
                preview.pack(side="left", fill="both", expand=True)
            else:
                preview = ttk.Label(win, justify="left", wraplength=520)
                preview.pack(side="bottom", fill="x", padx=10)
                if browse_only:
                    preview.bind("<Configure>",
                                 lambda event: preview.configure(wraplength=max(100, event.width - 4)))

        def update_selection(_event=None):
            key = selected_key()
            if confirm is not None and can_confirm is not None:
                confirm.state(["!disabled"] if key is not None and can_confirm(key) else ["disabled"])
            if preview_text is not None:
                text = "没有匹配的条目。" if not iids else preview_text(key)
                if preview_lines:
                    preview.configure(state="normal")
                    preview.delete("1.0", "end")
                    preview.insert("1.0", text)
                    preview.configure(state="disabled")
                    preview.yview_moveto(0)
                else:
                    preview.configure(text=text)

        if preview_text is not None or can_confirm is not None:
            tree.bind("<<TreeviewSelect>>", update_selection)
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
        for text, cmd in (("补全战技/特技…", self.complete_class_learnsets),
                          ("设置技能LV…", self.set_mastery_rank), ("设置技能EXP…", self.set_mastery_exp),
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
            meta = unit_ids().get(u.person & 0xFFFFFFFF) or {}
            lv = f.payload[u.level_off]
            iid = self.cls_unit_tree.insert(
                "", "end", text=" " + label,
                image=self.unit_list_icon(
                    meta.get("name") or label, meta.get("pid", ""),
                    uid=u.person & 0xFFFFFFFF, name_en=meta.get("name_en", ""),
                ),
                values=(lv,),
            )
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

    def complete_class_learnsets(self):
        u = self._cls_unit()
        if u is None:
            self.flash("请先选择一个单位。", "info")
            return
        self.open_learnset_completion(person=u.person)

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
    top = ttk.Frame(tab, style="Card.TFrame")
    top.pack(fill="x", pady=(0, 8))
    ttk.Button(top, text="按熟练度/兵种补全…", style="Accent.TButton",
               command=lambda: self.open_learnset_completion()).pack(side="left")
    ttk.Label(top, style="CardMuted.TLabel",
              text="按当前等级、技能熟练度与已精通兵种补全「已习得（全部）」与缺失魔法").pack(
        side="left", padx=(10, 0))
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
                   "一切。自动补全只会补写「已习得（全部）」与魔法栏，不改写其余子列表，也不注入秘传书或剧情触发项。"
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


def open_learnset_completion(self, person: int | None = None):
    """Preview and apply earned skills, arts, magic, and class masteries across target slots."""
    if not self.sv:
        self.flash("请先打开存档。", "info")
        return
    self._commit_form()
    target_pid = self.uid if person is None else person
    if target_pid is None:
        self.flash("请先选择一个单位。", "info")
        return
    f = self.core_of()
    if f is None:
        return

    route = layer.route_info(self.sv.payload)
    default_part3 = bool(route and route[1] == 3)
    try:
        initial_plan = learnsets.plan(f, target_pid, include_part3=default_part3)
    except core.SaveError:
        initial_plan = None

    win = tk.Toplevel(self)
    win.title("按熟练度/兵种补全战技与特技")
    win.transient(self)
    self.place_dialog(win, 800, 580)
    win.configure(background=self.p["bg"])

    top = ttk.Frame(win, style="Card.TFrame", padding=(14, 12))
    top.pack(fill="x", padx=12, pady=(12, 8))
    title_name = initial_plan.unit_name if initial_plan else unit_name(target_pid)
    ttk.Label(top, text=f"按熟练度 / 等级 / 已精通兵种补全 — {title_name}",
              style="CardH3.TLabel").pack(anchor="w")

    scope_var = tk.StringVar(master=win, value="party" if initial_plan is None else "unit")
    scope_row = ttk.Frame(top, style="Card.TFrame")
    scope_row.pack(fill="x", pady=(8, 4))
    ttk.Label(scope_row, text="补全范围：", style="Card.TLabel").pack(side="left")
    ttk.Radiobutton(scope_row, text=f"仅当前单位（{title_name}）", value="unit",
                    variable=scope_var).pack(side="left", padx=(4, 12))
    ttk.Radiobutton(scope_row, text="全队可招募角色", value="party",
                    variable=scope_var).pack(side="left")

    magic_var = tk.BooleanVar(master=win, value=True)
    replace_var = tk.BooleanVar(master=win, value=True)
    part3_var = tk.BooleanVar(master=win, value=default_part3)
    init_var = tk.BooleanVar(master=win, value=False)

    opts = ttk.Frame(top, style="Card.TFrame")
    opts.pack(fill="x", pady=(4, 0))
    ttk.Checkbutton(opts, text="同步补全缺失的黑/白魔法到单位魔法栏",
                    variable=magic_var).pack(anchor="w")
    ttk.Checkbutton(opts, text="用高阶版本替换低阶（如 LV1~5 → 奥妙、＋ / ＋＋；保留仍在使用的旧阶）",
                    variable=replace_var).pack(anchor="w")
    ttk.Checkbutton(opts, text="包含第 3 部限定习得项",
                    variable=part3_var).pack(anchor="w")
    ttk.Checkbutton(opts, text="包含 LV 1 初始固有技能（部分早期/第 0 部剧情角色有意隐藏）",
                    variable=init_var).pack(anchor="w")

    summary_lbl = ttk.Label(top, text="", style="CardMuted.TLabel", wraplength=750, justify="left")
    summary_lbl.pack(anchor="w", pady=(8, 0))

    tree_frame = ttk.Frame(win, style="Card.TFrame")
    tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
    tree = ttk.Treeview(
        tree_frame, columns=("action", "name", "reason", "sid"),
        show="tree headings", selectmode="none",
    )
    for col, title, width in (("#0", "槽位 / 单位", 150), ("action", "操作", 145),
                              ("name", "名称", 150), ("reason", "依据 / 来源", 185),
                              ("sid", "内部编号", 145)):
        tree.heading(col, text=title, anchor="w")
        tree.column(col, width=width, minwidth=80, stretch=col in ("name", "reason", "sid"))
    sb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    bar = ttk.Frame(win, padding=(12, 0, 12, 12))
    bar.pack(fill="x")
    ttk.Label(bar, style="CardFaint.TLabel",
              text="只修改「已习得（全部）」与魔法栏；不改写「已习得战技」或仍被装备的低阶技能。"
              ).pack(side="left")
    apply_btn = ttk.Button(bar, text="应用补全", style="Accent.TButton")
    apply_btn.pack(side="right")
    cancel_btn = ttk.Button(bar, text="取消")
    cancel_btn.pack(side="right", padx=(0, 8))

    CAT_ADD = {"art": "+ 已习得(战技)", "magic": "+ 已习得(魔法)", "blaze": "+ 已习得(猛击)",
               "skill": "+ 已习得(特技)"}

    # Capture target slots when the dialog opens; compute fast preview synchronously,
    # run write-preflight in a worker thread, and re-verify digests before apply.
    preview_state = {
        "slots": list(self._targets()),
        "slot_plans": [],
        "scope": scope_var.get(),
        "opts": {},
        "gen": 0,
        "validating": False,
        "poll_id": None,
        "closed": False,
        "notice": "",
    }
    result_queue: queue.Queue = queue.Queue()

    def _cancel_poll():
        pid = preview_state["poll_id"]
        preview_state["poll_id"] = None
        if pid is not None:
            try:
                win.after_cancel(pid)
            except tk.TclError:
                pass

    def _schedule_poll():
        if preview_state["closed"] or preview_state["poll_id"] is not None:
            return
        try:
            preview_state["poll_id"] = win.after(40, _poll_validation)
        except tk.TclError:
            preview_state["poll_id"] = None

    def _close_dialog(*_args):
        preview_state["closed"] = True
        preview_state["validating"] = False
        _cancel_poll()
        try:
            if win.winfo_exists():
                win.destroy()
        except tk.TclError:
            pass

    def _slot_label(sv) -> str:
        base = os.path.basename(sv.path)
        if len(preview_state["slots"]) <= 1:
            return base
        return f"{base}（当前）" if sv is self.sv else f"{base}（镜像）"

    def _decoded_slots(slot_list):
        decoded = []
        decode_errors = []
        for sv in slot_list:
            label = _slot_label(sv)
            try:
                sf = self.core_of(sv)
                if sf is None:
                    raise core.SaveError("无法解析存档结构。")
                decoded.append((sv.path, label, sf))
            except Exception as exc:          # noqa: BLE001 — surface slot decode failure
                decode_errors.append((sv.path, label, learnsets.payload_digest(sv.payload), str(exc)))
        return decoded, decode_errors

    def _current_opts():
        return {
            "include_part3": part3_var.get(),
            "replace_lower": replace_var.get(),
            "include_magic": magic_var.get(),
            "include_initial": init_var.get(),
        }

    def _render_plans(ordered_plans, *, notice: str = "", validating: bool = False):
        tree.delete(*tree.get_children())
        errors = [sp for sp in ordered_plans if sp.error]
        total_add = total_rep = total_mag = total_skip = total_kept = 0
        changed_slots = 0

        for sp in ordered_plans:
            changed_units = sp.changed_plans
            if sp.error:
                slot_node = tree.insert("", "end", text=sp.slot_label, open=True, values=(
                    "⚠ 无法补全", "该槽位拒绝修改", sp.error, ""))
            else:
                n_add = sum(len(p.add_skills) for p in changed_units)
                n_rep = sum(len(p.remove_skills) for p in changed_units)
                n_mag = sum(len(p.add_magic) for p in changed_units)
                n_skip = sum(p.skipped_magic_full for p in changed_units)
                n_kept = sum(len(p.kept_in_use) for p in changed_units)

                total_add += n_add
                total_rep += n_rep
                total_mag += n_mag
                total_skip += n_skip
                total_kept += n_kept

                if not changed_units:
                    slot_summary = "无需修改（已拥有全部应得项）"
                    slot_node = tree.insert("", "end", text=sp.slot_label, open=True, values=(
                        "—", slot_summary, "", ""))
                else:
                    changed_slots += 1
                    kept_part = f" / {n_kept} 保留" if n_kept else ""
                    slot_summary = f"{len(changed_units)} 名单位：+{n_add} 新增 / -{n_rep} 替换 / +{n_mag} 魔法{kept_part}"
                    slot_node = tree.insert("", "end", text=sp.slot_label, open=True, values=(
                        f"{len(changed_units)} 名单位", slot_summary, "", ""))

            for p in changed_units:
                for item in p.add_skills:
                    tree.insert(slot_node, "end", text=p.unit_name, values=(
                        CAT_ADD.get(item.category, "+ 已习得"), item.name, item.reason, item.sid))
                for item in p.remove_skills:
                    tree.insert(slot_node, "end", text=p.unit_name, values=(
                        "- 替换旧阶", item.name, item.reason, item.sid))
                for item in p.kept_in_use:
                    tree.insert(slot_node, "end", text=p.unit_name, values=(
                        "= 保留旧阶", item.name, item.reason, item.sid))
                for item in p.add_magic:
                    tree.insert(slot_node, "end", text=p.unit_name, values=(
                        "+ 单位魔法栏", item.name, item.reason, item.sid))

        prefix = f"{notice} " if notice else ""
        if errors:
            err_text = "；".join(f"{sp.slot_label}（{sp.error}）" for sp in errors)
            summary_lbl.configure(text=f"{prefix}⚠ 以下槽位存在异常，已禁用应用：{err_text}")
            apply_btn.state(["disabled"])
        elif changed_slots == 0:
            summary_lbl.configure(
                text=f"{prefix}所选 {len(ordered_plans)} 个槽位内均已拥有全部应得技能、战技与魔法，无需补全。"
            )
            apply_btn.state(["disabled"])
        elif validating or any(sp.has_changes and sp.staged is None for sp in ordered_plans):
            extra = []
            if total_kept:
                extra.append(f"{total_kept} 项低阶仍在使用已保留")
            if total_skip:
                extra.append(f"{total_skip} 项魔法因魔法栏已满跳过")
            extra_note = f" · {'；'.join(extra)}" if extra else ""
            summary_lbl.configure(
                text=f"{prefix}共 {changed_slots}/{len(ordered_plans)} 个槽位待修改："
                     f"新增 {total_add} 项已习得、替换 {total_rep} 项低阶、补齐 {total_mag} 项魔法栏{extra_note}"
                     f" —— 正在后台校验写入兼容性…"
            )
            apply_btn.state(["disabled"])
        else:
            extra = []
            if total_kept:
                extra.append(f"{total_kept} 项低阶仍在使用已保留")
            if total_skip:
                extra.append(f"{total_skip} 项魔法因魔法栏已满跳过")
            extra_note = f" · {'；'.join(extra)}" if extra else ""
            summary_lbl.configure(
                text=f"{prefix}共 {changed_slots}/{len(ordered_plans)} 个槽位待修改："
                     f"新增 {total_add} 项已习得、替换 {total_rep} 项低阶、补齐 {total_mag} 项魔法栏{extra_note}"
                     f"（已通过写入校验）。"
            )
            apply_btn.state(["!disabled"])

    def _poll_validation():
        preview_state["poll_id"] = None
        try:
            if preview_state["closed"] or not win.winfo_exists():
                return
        except tk.TclError:
            return
        latest = None
        while True:
            try:
                res_gen, validated, err = result_queue.get_nowait()
            except queue.Empty:
                break
            if res_gen == preview_state["gen"]:
                latest = (validated, err)

        if latest is not None:
            validated, err = latest
            preview_state["validating"] = False
            if err:
                preview_state["slot_plans"] = []
                summary_lbl.configure(text=f"⚠ 写入校验失败：{err}")
                apply_btn.state(["disabled"])
                return
            live_slots = list(self._targets())
            if ([sv.path for sv in live_slots] != [sv.path for sv in preview_state["slots"]]
                    or any(learnsets.payload_digest(sv.payload) != sp.digest
                           for sv, sp in zip(live_slots, validated))):
                preview_state["slots"] = live_slots
                refresh_preview(notice="⚠ 目标槽位内容自预览开始后已发生变化，已自动重新生成预览。")
                return
            preview_state["slot_plans"] = validated
            _render_plans(validated, notice=preview_state["notice"], validating=False)
            return

        if preview_state["validating"]:
            _schedule_poll()

    def refresh_preview(*_args, notice: str = ""):
        if preview_state["closed"]:
            return
        # Bump generation immediately so any in-flight validation results are discarded.
        preview_state["gen"] += 1
        my_gen = preview_state["gen"]
        preview_state["slot_plans"] = []
        preview_state["validating"] = False
        preview_state["notice"] = notice
        apply_btn.state(["disabled"])

        opts_kw = _current_opts()
        scope = scope_var.get()
        slot_list = preview_state["slots"]
        decoded, decode_errors = _decoded_slots(slot_list)
        sp_by_key = {}
        if decoded:
            for sp in learnsets.plan_slots(
                decoded, target_pid, scope=scope, validate_write=False, **opts_kw
            ):
                sp_by_key[sp.slot_key] = sp
        for key, label, digest, err in decode_errors:
            sp_by_key[key] = learnsets.SlotPlan(
                slot_key=key, slot_label=label, digest=digest, plans=(), error=err,
            )

        ordered_plans = [sp_by_key[sv.path] for sv in slot_list if sv.path in sp_by_key]
        preview_state["slot_plans"] = ordered_plans
        preview_state["scope"] = scope
        preview_state["opts"] = opts_kw

        needs_write = (
            not any(sp.error for sp in ordered_plans)
            and any(sp.has_changes for sp in ordered_plans)
        )
        _render_plans(ordered_plans, notice=notice, validating=needs_write)
        if not needs_write:
            _cancel_poll()
            return

        preview_state["validating"] = True
        raw_slots = [(sv.path, _slot_label(sv), bytes(sv.payload))
                     for sv in slot_list if sv.path in sp_by_key]
        # Ensure read-only catalog caches are warm on the main thread before spawning worker.
        learnsets.catalog()

        def worker():
            try:
                validated = learnsets.validate_slot_plans(
                    raw_slots,
                    ordered_plans,
                    cancel_check=lambda: preview_state["closed"] or preview_state["gen"] != my_gen,
                )
                result_queue.put((my_gen, validated, ""))
            except learnsets.CancelledError:
                return
            except Exception as exc:          # noqa: BLE001 — forward worker failure to UI
                result_queue.put((my_gen, (), str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        _schedule_poll()

    def do_apply():
        if preview_state["validating"]:
            return
        self._commit_form()
        live_slots = list(self._targets())
        expected_plans = preview_state["slot_plans"]
        scope = preview_state["scope"]
        opts_kw = preview_state["opts"]

        stale = (
            [sv.path for sv in live_slots] != [sv.path for sv in preview_state["slots"]]
            or len(live_slots) != len(expected_plans)
            or opts_kw != _current_opts()
            or scope != scope_var.get()
            or any(learnsets.payload_digest(sv.payload) != sp.digest
                   for sv, sp in zip(live_slots, expected_plans))
            or any(sp.has_changes and sp.staged is None for sp in expected_plans)
        )
        if stale:
            preview_state["slots"] = live_slots
            refresh_preview(notice="⚠ 目标槽位或选项自上次预览后已发生变化，已重新生成预览，请确认后再次点击应用。")
            return

        if any(sp.error for sp in expected_plans):
            _render_plans(expected_plans)
            return

        plan_by_digest = {sp.digest: sp for sp in expected_plans}

        def edit(g):
            sp = plan_by_digest.get(learnsets.payload_digest(g.payload))
            if sp is None:
                raise learnsets.StalePreviewError("存档内容与预览不一致，请刷新预览后重试。")
            return learnsets.apply_slot_plan(g, sp, scope=scope, **opts_kw)

        label = f"{title_name}：补全已习得战技与特技" if scope == "unit" else "全队：补全已习得战技与特技"
        if self._core_edit(edit, label, slots=preview_state["slots"]):
            _close_dialog()

    def _on_destroy(event):
        if event.widget is win:
            preview_state["closed"] = True
            preview_state["validating"] = False
            _cancel_poll()

    apply_btn.configure(command=do_apply)
    cancel_btn.configure(command=_close_dialog)
    for var in (scope_var, magic_var, replace_var, part3_var, init_var):
        var.trace_add("write", lambda *_a: refresh_preview())
    win.protocol("WM_DELETE_WINDOW", _close_dialog)
    win.bind("<Destroy>", _on_destroy)
    win.bind("<Escape>", _close_dialog)
    refresh_preview()
    win.grab_set()
    win.focus_set()


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
              text="选择单位或资源池条目查看装备详情。").pack(
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
    header = ttk.Frame(right, style="Card.TFrame")
    header.pack(fill="x")
    ttk.Label(header, text='此存档的资源池', style="CardH3.TLabel").pack(side="left")
    catalogs = ttk.Frame(right, style="Card.TFrame")
    catalogs.pack(fill="x", pady=(4, 0))
    ttk.Button(catalogs, text="添加军团…", style="Small.TButton",
               command=self.show_legion_catalog).pack(side="left")
    ttk.Button(catalogs, text="添加坐骑…", style="Small.TButton",
               command=self.show_ride_catalog).pack(side="left", padx=(6, 0))
    with open(os.path.join(HERE, "legion_details.json"), encoding="utf-8") as source:
        self.legion_details = json.load(source)
    self.ride_details = layer.ride_catalogue()
    self.pool_tree = scrolled_tree(right, columns=("rank", "owner"), show="tree headings",
                                   selectmode="browse")
    for column, title, width in (("#0", "军团兵／坐骑", 180), ("rank", "指挥／种类", 94),
                                 ("owner", "配属单位", 110)):
        self.pool_tree.heading(column, text=title, anchor="w")
        self.pool_tree.column(column, width=width)
    self.mount_detail = tk.StringVar(master=right)
    detail = ttk.Label(right, textvariable=self.mount_detail, style="CardMuted.TLabel",
                       justify="left", wraplength=350)
    detail.pack(side="bottom", fill="x", pady=(8, 0))
    detail.bind("<Configure>", lambda e: detail.configure(wraplength=max(100, e.width - 4)))

    def show_detail(event):
        selected = event.widget.selection()
        if selected:
            self.mount_detail.set(self._mount_details.get((event.widget, selected[0]), ""))

    for tree in (self.pool_tree, self.mount_tree):
        tree.bind("<<TreeviewSelect>>", show_detail)
    self.pool_tree.container.pack(fill="both", expand=True, pady=(6, 6))
    row = ttk.Frame(right, style="Card.TFrame")
    row.pack(fill="x")
    ttk.Button(row, text='分配军团', style="Small.TButton",
               command=self.assign_battalion).pack(side="left")
    ttk.Button(row, text='卸下军团', style="Small.TButton",
               command=lambda: self.assign_battalion(clear=True)).pack(side="left", padx=(6, 0))
    ttk.Button(row, text='从资源池移除', style="Small.TButton",
               command=self.remove_pool_entry).pack(side="left", padx=(6, 0))
    ride_actions = ttk.Frame(right, style="Card.TFrame")
    ride_actions.pack(fill="x", pady=(4, 0))
    ttk.Button(ride_actions, text="分配坐骑", style="Small.TButton",
               command=self.assign_ride).pack(side="left")
    ttk.Button(ride_actions, text="卸下坐骑", style="Small.TButton",
               command=lambda: self.assign_ride(clear=True)).pack(side="left", padx=(6, 0))

    def refresh_on_open(_event):
        if self.pages.select() == str(self.mounts_page):
            self.refresh_mounts()

    self.pages.bind("<<NotebookTabChanged>>", refresh_on_open, add="+")


def ride_profile(details, *, all_levels=False):
    """Describe the species' bonuses and possible skills from the game tables."""
    def bonuses(values, suffix=""):
        return "、".join(f"{stat} {value:+d}{suffix}" for stat, value in values.items()) or "—"

    lines = [details["description"]]
    skills = details["skills"]
    if skills and all_levels:
        lines.extend(("", "骑乘特技候选"))
        for skill in skills:
            lines.append(f"「{skill['name']}」")
            lines.extend(f"  阶段 {stage}　{effect}"
                         for stage, effect in enumerate(skill["effects"], 1))
        lines.append("")
    if any(details["stats"]) or any(details["growth"]):
        levels = range(1, 6) if all_levels else (5,)
        for level in levels:
            stats = bonuses(details["stats"][level - 1])
            growth = bonuses(details["growth"][level - 1], "%")
            level_label = f"等级 {level}" if all_levels else f"等级 {level} 参考"
            lines.append(f"{level_label}　属性 {stats}　｜　成长率 {growth}")
    if skills and not all_levels:
        lines.append("骑乘特技候选　" + "、".join(skill["name"] for skill in skills))
    return "\n".join(lines)


def show_ride_catalog(self):
    slot = self.sv
    rows, previews = [], {}

    def can_add(key):
        return self.ride_details[key].get("addable", False)

    for key, details in self.ride_details.items():
        name, species = details["name"], details["type"]
        rows.append((key, name, species if can_add(key) else f"{species} · 仅供浏览"))
        previews[key] = "\n".join((
            f"{name}　·　{species}", ride_profile(details, all_levels=True),
            f"{details['key']}　·　0x{key}",
        ))
    selected = self._picker(
        "添加坐骑到资源池（实验）" if slot else "坐骑资料", rows,
        search_terms={key: preview.lower() for key, preview in previews.items()},
        browse_only=slot is None, secondary_title="种类", preview_lines=12, can_confirm=can_add,
        preview_text=lambda key: previews.get(key, ""), confirm_text="加入资源池")
    if slot is None or selected is None or not can_add(selected):
        return
    if self.sv is not slot:
        self.flash("当前存档已切换，请重新选择要添加的坐骑。", "warn")
        return
    name = self.ride_details[selected]["name"]
    self._core_edit(lambda f: layer.with_added_ride(f, int(selected, 16)),
                    f"已添加 {name}（未配属，实验）", current_only=True)


def show_legion_catalog(self):
    slot = self.sv
    rows, search_terms, previews = [], {}, {}
    ranks = {rank: index for index, rank in enumerate(("E", "D", "C", "B", "A", "—"))}
    for type_id, (name, rank, info, meta) in sorted(
            self.legion_details.items(), key=lambda entry: ranks[entry[1][1]]):
        rows.append((type_id, name, "内部记录" if rank == "—" else rank))
        identity = f"行号 {meta['row']}　·　类型 ID 0x{type_id}"
        labels = "　·　".join(label for label in (meta.get("jp"), meta["key"])
                              if label and label != name)
        previews[type_id] = "\n".join(part for part in (name, info, labels, identity) if part)
        search_terms[type_id] = previews[type_id].lower()
    def can_add(key):
        return self.legion_details[key][1] != "—"

    selected_id = self._picker("添加军团到资源池" if slot else "军团资料", rows,
                               search_terms=search_terms, browse_only=slot is None,
                               secondary_title="指挥等级／类型", can_confirm=can_add,
                               preview_text=lambda key: previews.get(key, ""),
                               confirm_text="加入资源池")
    if slot is None or selected_id is None or not can_add(selected_id):
        return
    if self.sv is not slot:
        self.flash("当前存档已切换，请重新选择要添加的军团。", "warn")
        return
    name, _, _, meta = self.legion_details[selected_id]
    legion = core.Legion(meta["row"], 0, int(selected_id, 16))

    def edit(f):
        if not layer.add_battalion(f.payload, legion):
            raise core.SaveError("无法定位此存档的军团资源池。")
        return f.payload

    self._core_edit(edit, f"已添加 {name}（未配属）", current_only=True)


def refresh_mounts(self):
    if not hasattr(self, "mount_tree"):
        return
    self.mount_tree.delete(*self.mount_tree.get_children())
    self.pool_tree.delete(*self.pool_tree.get_children())
    self._mount_rows, self._pool_rows = {}, {}
    self._mount_details = {}
    self.mount_detail.set("")
    f = self.core_of()
    if f is None:
        return

    def describe(legion):
        key = f"{legion.id:016x}"
        details = self.legion_details.get(key)
        return details[:3] if details else (f"未知军团兵 0x{key}", "—", "")

    def describe_ride(ride):
        key = f"{ride.id:016x}"
        details = self.ride_details.get(key)
        name = details["name"] if details else f"未知坐骑 #{ride.serial}"
        if ride.name and ride.name != name:
            name = f"{ride.name}（{name}）" if details else ride.name
        species = details["type"] if details else "—"
        identity = f"个体序号 {ride.serial}　·　类型 ID 0x{key}"
        info = f"{ride_profile(details)}\n{identity}" if details else identity
        return name, species, info

    store = core.party_store(f)
    pool = core.legions(f, store[2]) if store else []
    secondary = core.reliquary_store(f)
    known = pool + (core.legions(f, secondary[2]) if secondary else [])
    ride_pools, unreadable = {}, []
    for equipment, source in ((store, "主资源池"), (secondary, "备用装备库")):
        if equipment is None or equipment[1] in ride_pools:
            continue
        try:
            rides = core.rides(f, equipment[1])
        except core.SaveError:
            unreadable.append(source)
            continue
        ride_pools[equipment[1]] = (source, rides)
    known_rides = [ride for _, rides in ride_pools.values() for ride in rides]
    equipped, equipped_rides = {}, {}
    for u in layer.find_units(self.sv.payload):
        unit = layer.unit_in(f, u.uid)
        g = core.gear(f, unit) if unit else None
        bat = g.legion if g else None
        bat_txt = ride_txt = "—"
        sections = []
        if bat:
            bat_txt, _, info = describe(bat)
            if bat.owner == g.unit_number and bat in known:
                equipped[(bat.id, g.unit_number)] = u.name
            else:
                bat_txt += "（归属待核对）"
            sections.append(f"{bat_txt}　·　{u.name}\n{info}")
        if g and g.ride:
            ride_txt, species, info = describe_ride(g.ride)
            if g.ride.owner and g.ride.owner == g.unit_number and g.ride in known_rides:
                equipped_rides[g.ride.bytes()] = u.name
            else:
                ride_txt += "（归属待核对）"
            sections.append(f"{ride_txt}　·　{species}　·　{u.name}\n{info}")
        iid = self.mount_tree.insert("", "end", text=" " + u.name,
                                     image=self.unit_list_icon(u.name, u.pid, uid=u.uid),
                                     values=(bat_txt, ride_txt))
        self._mount_rows[iid] = u.uid
        self._mount_details[self.mount_tree, iid] = "\n\n".join(sections)
    for i, legion in enumerate(pool):
        name, rank, info = describe(legion)
        owner = equipped.get((legion.id, legion.owner),
                             f"归属待核对（{legion.owner}）" if legion.owner else "未配属")
        iid = self.pool_tree.insert("", "end", text=name, values=(rank, owner), iid=f"b{i}")
        self._mount_details[self.pool_tree, iid] = f"{name}　·　{owner}\n{info}"
        self._pool_rows[iid] = ("battalion", i)
    for source, rides in ride_pools.values():
        for ride in rides:
            name, species, info = describe_ride(ride)
            owner = (equipped_rides.get(ride.bytes(), f"归属待核对（{ride.owner}）")
                     if ride.owner else "未配属")
            iid = self.pool_tree.insert("", "end", text=name, values=(species, owner))
            self._mount_details[self.pool_tree, iid] = f"{name}　·　{source}　·　{owner}\n{info}"
            self._pool_rows[iid] = ("ride", (ride.id, ride.serial))
    for source in unreadable:
        self.pool_tree.insert("", "end", text=f"{source}的坐骑记录无法读取")


def assign_battalion(self, clear: bool = False):
    sel = self.mount_tree.selection() if hasattr(self, "mount_tree") else ()
    uid = getattr(self, "_mount_rows", {}).get(sel[0] if sel else None)
    if uid is None or not self.sv:
        self.flash("请先在坐骑页选择一个单位。", "info")
        return
    name = self.mount_tree.item(sel[0], "text")
    index = None
    required_rank = None
    message = f"已卸下 {name} 的军团"
    if not clear:
        psel = self.pool_tree.selection()
        if not psel:
            self.flash("请先在资源池中选择一个军团兵。", "info")
            return
        kind, index = self._pool_rows.get(psel[0], (None, None))
        if kind != "battalion":
            self.flash("这里只能分配军团兵。", "warn")
            return
        required_rank, _ = self.pool_tree.item(psel[0], "values")
        message = f"已将 {self.pool_tree.item(psel[0], 'text')} 配属给 {name}"

    self._core_edit(lambda f: layer.with_battalion_assignment(f, uid, index,
                                                             required_rank=required_rank),
                    message, current_only=True)


def assign_ride(self, clear: bool = False):
    selected = self.mount_tree.selection()
    uid = self._mount_rows.get(selected[0]) if selected else None
    if uid is None or self.sv is None:
        self.flash("请先在坐骑页选择一个单位。", "info")
        return
    unit_name = self.mount_tree.item(selected[0], "text")
    identity = None
    message = f"已卸下 {unit_name} 的坐骑"
    if not clear:
        pool_selection = self.pool_tree.selection()
        if not pool_selection:
            self.flash("请在右侧资源池选中要分配的坐骑。", "info")
            return
        kind, identity = self._pool_rows.get(pool_selection[0], (None, None))
        if kind != "ride":
            self.flash("当前选中的是军团或提示行，请选中坐骑。", "warn")
            return
        name = self.pool_tree.item(pool_selection[0], "text")
        message = f"已将 {name} 配属给 {unit_name}"
    self._core_edit(lambda f: layer.with_ride_assignment(f, uid, identity),
                    message, current_only=True)


def remove_pool_entry(self):
    psel = self.pool_tree.selection() if hasattr(self, "pool_tree") else ()
    if not self.sv:
        return
    kind, key = self._pool_rows.get(psel[0], (None, None)) if psel else (None, None)
    if kind == "battalion":
        self._core_edit(lambda f: layer_del_battalion(f, key), "已从资源池移除军团兵")
    elif kind == "ride":
        name = self.pool_tree.item(psel[0], "text")
        self._core_edit(lambda f: layer.with_removed_ride(f, key),
                        f"已从资源池移除 {name}", current_only=True)
    else:
        self.flash("请先在右侧资源池选中要移除的军团兵或坐骑。", "info")


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
              "add_skill", "open_learnset_completion", "remove_skill",
              "_build_mounts", "show_legion_catalog",
              "show_ride_catalog", "refresh_mounts",
              "assign_battalion", "remove_pool_entry", "assign_ride", "copy_unit_in",
              "remove_current_unit", "add_convoy_item_ui", "remove_convoy_item_ui",
              "fill_materials_ui"):
    setattr(AdvancedMixin, _name, globals()[_name])
