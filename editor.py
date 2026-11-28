"""
Fortune's Weave Save Editor — Fire Emblem: Fortune's Weave (Eden / eglantine slots).

A Tkinter GUI over fefw_save.py.  Every slot in the save folder is loaded; edits
stay in memory until you press Save (Ctrl+S), which writes every slot that was
changed.  With "Apply to every slot" on, an edit goes into the same unit / list
in every slot of the folder, because mid-chapter the game resumes from a
Chapter_* slot rather than Auto.
"""

from __future__ import annotations

from fractions import Fraction
import json
import os
import re
import struct
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# fefw_layer re-exports all of fefw_save and answers the structural questions
# (units, item slots, skill lists, convoy, gold) from fefw_core, which reads the
# save's own block table / unit pool / object tree instead of pattern-matching.
from fefw_layer import USER_DIR, current_gold  # noqa: E402
from fefw_layer import set_gold as write_gold  # noqa: E402
from fefw_layer import (GRADES, ITEM_KINDS, SKILL_NAMES, SKILL_THRESHOLDS,  # noqa: E402
                        STAT_NAMES, SaveError, SaveFile, live_convoy, set_convoy_item,
                        set_convoy_qty, default_save_dir, grade_label, grade_of,
                        is_enemy, is_shadow, is_slot_name, item_category, item_ids,
                        item_kind, item_label, job_ids, job_name, next_threshold, pick_live)
import fefw_layer as layer  # noqa: E402
import supports  # noqa: E402  (support pair chart + conversation flags)
from weapon_stats import item_preview_text  # noqa: E402
from zh_names import CHARACTERS  # noqa: E402
import fefw_core as core  # noqa: E402  (save object tree: resizing edits)
from editor_advanced import AdvancedMixin  # noqa: E402  (Player / Classes / Flags pages)
from ui_util import ScrollableFrame, enable_multi_select, last_tree  # noqa: E402  (Ctrl+A / Shift+drag in tables)

APP_NAME = "Fortune's Weave Save Editor"
__version__ = "1.4.5"
SETTINGS_PATH = os.path.join(USER_DIR, "editor_settings.json")
ICON_DIR = os.path.join(USER_DIR, "icons")

STAT_MAX = 255          # the record parser rejects stat blocks with values >= 300
SKILL_MAX = 9999        # find_skills() stops recognising a table above this
QTY_MAX = 999           # quantities seen in real saves stay well under this

STAT_LONG = {"HP": "HP", "Str": "力量", "Mag": "魔力", "Dex": "技巧",
             "Spd": "速度", "Lck": "幸运", "Def": "防守", "Res": "魔防",
             "Cha": "魅力"}

# --------------------------------------------------------------------------
# look
# --------------------------------------------------------------------------
PALETTES = {
    "dark": dict(
        bg="#14161b", side="#191c22", card="#20242c", raised="#272c36", border="#2f3541",
        fg="#e7e9ef", muted="#9ba2b2", faint="#666e7e", accent="#7c9cff",
        accent_fg="#0d1120", accent_hi="#9db4ff", sel="#2c3758", entry="#171a20",
        track="#2a303b", ok="#4fd1a5", warn="#f5b453", danger="#ff7676"),
    "light": dict(
        bg="#f3f4f7", side="#eceef3", card="#ffffff", raised="#f6f7fa", border="#dadee6",
        fg="#1b1f2c", muted="#596173", faint="#8d94a3", accent="#3d5afe",
        accent_fg="#ffffff", accent_hi="#5a73ff", sel="#dde4ff", entry="#ffffff",
        track="#e6e9f0", ok="#16936b", warn="#b26b00", danger="#d23f3f"),
}

KIND_COLORS = {
    "Sword": "#ef6f7c", "Lance": "#5fa8f5", "Axe": "#e39a52", "Bow": "#8fcf6a",
    "Tome": "#b77df0", "Staff": "#f2cc5c", "Fist": "#ff8f66", "Shield": "#4cc3c9",
    "Weapon": "#d58aa8", "Accessory": "#f29ad8", "Consumable": "#5fd3a2", "Item": "#8e97a8",
}

GRADE_COLORS = ["#7b8494", "#8b93a3", "#5aa9e6", "#4c9be0", "#4fc29a", "#3fb489",
                "#e3c04d", "#e0ab3a", "#f0884f", "#ec7040", "#e8557a", "#d9417e"]

CAT_LABEL = {"weapon": "武器", "accessory": "装备", "consumable": "消耗品"}

# display-only: the kind keys themselves stay English (they are logic keys for
# filters, colours and slot locks); only what the user reads is translated
KIND_ZH = {"Sword": "剑", "Lance": "枪", "Axe": "斧", "Bow": "弓", "Tome": "魔道书",
           "Staff": "杖", "Fist": "手甲", "Shield": "盾", "Weapon": "武器",
           "Accessory": "装备", "Consumable": "消耗品", "Item": "道具", "Spell": "魔法"}
KIND_OF = {v: k for k, v in KIND_ZH.items()}


def _pick_family(candidates, fallback):
    try:
        have = {f.lower() for f in tkfont.families()}
    except tk.TclError:
        return fallback
    for c in candidates:
        if c.lower() in have:
            return c
    return fallback


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s_\-]+", name) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].title()
    return (parts[0][0] + parts[1][0]).upper()


def _name_color(name: str) -> str:
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return list(KIND_COLORS.values())[h % 10]


def _fmt_time(ts: float) -> str:
    now = time.time()
    if now - ts < 86400 and time.localtime(now).tm_mday == time.localtime(ts).tm_mday:
        return "today " + time.strftime("%H:%M", time.localtime(ts))
    return time.strftime("%b %d %H:%M", time.localtime(ts))


def _slug(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in norm if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", ascii_only.lower()).strip("_")


# --------------------------------------------------------------------------
# small widgets
# --------------------------------------------------------------------------
class SearchEntry(ttk.Entry):
    """Entry with placeholder text and a debounced change callback."""

    def __init__(self, master, placeholder: str, on_change, delay: int = 140, **kw):
        self._var = tk.StringVar()
        super().__init__(master, textvariable=self._var, **kw)
        self._ph = placeholder
        self._on_change = on_change
        self._delay = delay
        self._job = None
        self._showing_ph = False
        # Showing or hiding the placeholder writes the StringVar but never
        # changes the query, so it must not fire on_change (#18: on the
        # Supports page that refresh reselected the first character).
        self._muted = False
        self._show_ph()
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", lambda e: self._show_ph())
        self.bind("<Escape>", lambda e: self.clear())
        self._var.trace_add("write", self._changed)

    def _show_ph(self):
        if not self._var.get():
            self._showing_ph = True
            self._set_quietly(self._ph)
            self.configure(style="Placeholder.TEntry")

    def _focus_in(self, _e=None):
        if self._showing_ph:
            self._showing_ph = False
            self._set_quietly("")
            self.configure(style="TEntry")

    def _set_quietly(self, text: str):
        self._muted = True
        try:
            self._var.set(text)
        finally:
            self._muted = False

    def _changed(self, *_a):
        if self._muted:
            return
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(self._delay, self._fire)

    def _fire(self):
        self._job = None
        self._on_change()

    def get_query(self) -> str:
        return "" if self._showing_ph else self._var.get().strip()

    def clear(self):
        if self._showing_ph:
            return
        self._var.set("")
        if self.focus_get() is not self:
            self._show_ph()


class Bar(tk.Canvas):
    """Flat horizontal progress bar."""

    def __init__(self, master, app, width=120, height=8, **kw):
        super().__init__(master, width=width, height=height, highlightthickness=0, bd=0, **kw)
        self.app = app
        self._bw, self._bh = width, height
        self._frac, self._color = 0.0, None
        app.register_canvas(self)
        self.bind("<Configure>", self._resize)

    def _resize(self, e):
        self._bw = e.width
        self.redraw()

    def set(self, frac: float, color: str | None = None):
        self._frac = max(0.0, min(1.0, frac))
        self._color = color
        self.redraw()

    def redraw(self):
        p = self.app.p
        bg = p["card"] if getattr(self, "on_card", True) else p["bg"]
        self.configure(background=bg)
        self.delete("all")
        w, h = max(self._bw, 4), self._bh
        self.create_rectangle(0, 0, w, h, fill=p["track"], outline="")
        if self._frac > 0:
            self.create_rectangle(0, 0, max(2, int(w * self._frac)), h,
                                  fill=self._color or p["accent"], outline="")


class Avatar(tk.Canvas):
    """Unit portrait: icons/units/<name>.png if present, otherwise initials."""

    def __init__(self, master, app, size=60):
        super().__init__(master, width=size, height=size, highlightthickness=0, bd=0)
        self.app, self.size = app, size
        self._name, self._img = "", None
        app.register_canvas(self)

    def show(self, name: str, pid: str = "", uid: int | None = None, name_en: str = ""):
        self._name = name
        self._img = (self.app.unit_image(name, pid, self.size, uid=uid, name_en=name_en)
                     if name else None)
        self.redraw()

    def redraw(self):
        p = self.app.p
        self.configure(background=p["card"])
        self.delete("all")
        s = self.size
        if self._img is not None:
            self.create_image(s // 2, s // 2, image=self._img)
            return
        if not self._name:
            return
        self.create_oval(2, 2, s - 2, s - 2, fill=_name_color(self._name), outline="")
        self.create_text(s // 2, s // 2, text=_initials(self._name), fill="#10131a",
                         font=(self.app.family, -max(10, int(s * 0.36)), "bold"))


def scrolled_tree(master, **kw) -> ttk.Treeview:
    frame = ttk.Frame(master, style="Card.TFrame")
    tree = ttk.Treeview(frame, **kw)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.container = frame
    enable_multi_select(tree)
    return tree


# --------------------------------------------------------------------------
# item picker (shared by Inventory and Convoy)
# --------------------------------------------------------------------------
class ItemPicker(ttk.Frame):
    MAX_ROWS = 600

    def __init__(self, master, app, on_activate, action_text: str):
        super().__init__(master, style="Card.TFrame", padding=12)
        self.app = app
        self.on_activate = on_activate
        self.lock_category: str | None = None
        self._result_limit = self.MAX_ROWS
        self._last_filter = None

        top = ttk.Frame(self, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text='道具目录', style="CardH3.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(top, text="", style="CardMuted.TLabel")
        self.count_lbl.pack(side="right")

        self.search = SearchEntry(self, "按中文、英文、日文名称或编号搜索…", self.refresh)
        self.search.pack(fill="x", pady=(8, 6))
        self.search.bind("<Return>", lambda e: self._activate())
        self.search.bind("<Down>", self._to_list)

        row = ttk.Frame(self, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text='类型', style="CardMuted.TLabel").pack(side="left")
        self.kind_var = tk.StringVar(value="全部类型")
        kb = ttk.Combobox(row, textvariable=self.kind_var, state="readonly", width=14,
                          values=["全部类型"] + [KIND_ZH.get(k, k) for k in ITEM_KINDS])
        kb.pack(side="left", padx=(6, 12))
        kb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        app.register_combo(kb)
        ttk.Checkbutton(row, text='内部重复项', style="Card.TCheckbutton",
                        variable=app.show_internal, command=self.refresh).pack(side="left")

        self.lock_lbl = ttk.Label(self, text="", style="CardWarn.TLabel", wraplength=360)
        self._lock_anchor = row

        bottom = ttk.Frame(self, style="Card.TFrame")
        bottom.pack(side="bottom", fill="x")
        self.more_btn = ttk.Button(bottom, text="显示更多", command=self.show_more)
        self.action_btn = ttk.Button(bottom, text=action_text, style="Accent.TButton",
                                     command=self._activate)
        self.action_btn.pack(side="right")
        ttk.Label(bottom, text='也可双击或按回车',
                  style="CardFaint.TLabel").pack(side="left")

        self.item_preview = ttk.Label(self, style="CardMuted.TLabel", justify="left",
                                        wraplength=340)
        self.item_preview.pack(side="bottom", fill="x")
        self.tree = scrolled_tree(self, columns=("kind", "jp", "id"), show="tree headings",
                                  selectmode="browse", height=6)
        self.tree.heading("#0", text='道具', anchor="w")
        self.tree.heading("kind", text='类型', anchor="w")
        self.tree.heading("jp", text='日文', anchor="w")
        self.tree.heading("id", text='编号', anchor="w")
        self.tree.column("#0", width=210, minwidth=140, stretch=True)
        self.tree.column("kind", width=90, minwidth=70, stretch=False)
        self.tree.column("jp", width=130, minwidth=60, stretch=True)
        self.tree.column("id", width=78, minwidth=70, stretch=False)
        self.tree.container.pack(fill="both", expand=True, pady=(8, 8))
        self.tree.bind("<Double-1>", lambda e: self._activate())
        self.tree.bind("<Return>", lambda e: self._activate())
        self.tree.bind("<<TreeviewSelect>>", self._update_item_preview)
        self.refresh()

    def _update_item_preview(self, _event=None):
        self.item_preview.configure(text=item_preview_text(self.selected()))

    def _to_list(self, _e=None):
        kids = self.tree.get_children()
        if kids:
            self.tree.focus_set()
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])
        return "break"

    def _activate(self):
        iid = self.selected()
        if iid is None:
            kids = self.tree.get_children()
            if len(kids) == 1:
                self.tree.selection_set(kids[0])
                iid = int(kids[0], 16)
        if iid is None:
            self.app.flash("请先在目录中选择一个道具。", "warn")
            return
        self.on_activate(iid)

    def selected(self) -> int | None:
        sel = self.tree.selection()
        return int(sel[0], 16) if sel else None

    def set_lock(self, category: str | None, allow_any: bool):
        self.lock_category = None if allow_any or category in (None, "misc") else category
        if self.lock_category:
            self.lock_lbl.configure(
                text=f"仅显示{CAT_LABEL.get(self.lock_category, self.lock_category)}，"
                     "与所选槽位的类型一致。")
            self.lock_lbl.pack(fill="x", pady=(6, 0), after=self._lock_anchor)
        else:
            self.lock_lbl.pack_forget()
        self.refresh()

    def refresh(self):
        q = self.search.get_query().lower()
        tokens = q.split()
        picked = self.kind_var.get()
        kind = None if picked == "全部类型" else KIND_OF.get(picked)
        show_internal = self.app.show_internal.get()
        current_filter = (q, kind, show_internal, self.lock_category)
        if current_filter != self._last_filter:
            self._result_limit = self.MAX_ROWS
            self._last_filter = current_filter
        hits = []
        for row in self.app.item_rows:
            iid, meta, en, jp, k, cat, shadow, blob = row
            if shadow and not show_internal:
                continue
            if kind and k != kind:
                continue
            if self.lock_category and cat != self.lock_category:
                continue
            if tokens and not all(t in blob for t in tokens):
                continue
            el = en.lower()
            if not q:
                score = 3
            elif el == q or f"{iid:08x}" == q:
                score = 0
            elif el.startswith(q):
                score = 1
            elif re.search(r"\b" + re.escape(q), el):
                score = 2
            else:
                score = 3
            hits.append((score, el, row))
        hits.sort(key=lambda h: (h[0], h[1]))
        prev = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for _s, _el, (iid, meta, en, jp, k, cat, shadow, blob) in hits[:self._result_limit]:
            self.tree.insert("", "end", iid=f"{iid:08x}", text=" " + en,
                             image=self.app.item_icon(iid, meta, k),
                             values=(KIND_ZH.get(k, k), jp, f"{iid:08x}"))
        if prev and self.tree.exists(prev[0]):
            self.tree.selection_set(prev[0])
            self.tree.see(prev[0])
        self._update_item_preview()
        more = len(hits) - self._result_limit
        self.count_lbl.configure(text=f"共 {len(hits):,} 项"
                                 + (f" · 显示前 {self._result_limit}" if more > 0 else ""))
        if more > 0:
            self.more_btn.pack(side="left", padx=(8, 0))
        else:
            self.more_btn.pack_forget()

    def show_more(self):
        self._result_limit += self.MAX_ROWS
        self.refresh()


# --------------------------------------------------------------------------
# the app
# --------------------------------------------------------------------------
class _PageStack(ttk.Frame):
    """Stacked page container that switches pages via lift() without Notebook remap flicker."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._pages: list[ttk.Frame] = []
        self._titles: dict[str, str] = {}
        self._current: str = ""

    def add(self, child: ttk.Frame, *, text: str = ""):
        key = str(child)
        self._pages.append(child)
        self._titles[key] = text
        if not self._current:
            self._current = key
            child.grid(row=0, column=0, sticky="nsew")
            child.lift()

    def tabs(self) -> tuple[str, ...]:
        return tuple(str(p) for p in self._pages)

    def tab(self, page_id, option: str | None = None):
        key = str(page_id)
        if option == "text":
            return self._titles.get(key, "")
        return {"text": self._titles.get(key, "")}

    def select(self, page_id=None):
        if page_id is None:
            return self._current
        key = str(page_id)
        if key not in self._titles or key == self._current:
            return self._current
        prev = next((p for p in self._pages if str(p) == self._current), None)
        nxt = next((p for p in self._pages if str(p) == key), None)
        self._current = key
        if nxt is not None:
            nxt.grid(row=0, column=0, sticky="nsew")
            nxt.lift()
        if prev is not None:
            prev.grid_remove()
        self.event_generate("<<NotebookTabChanged>>")
        return self._current

    def _step(self, delta: int):
        if not self._pages:
            return None
        cur = next((i for i, p in enumerate(self._pages) if str(p) == self._current), 0)
        self.select(self._pages[(cur + delta) % len(self._pages)])
        return "break"

    def enable_traversal(self):
        top = self.winfo_toplevel()
        top.bind("<Control-Tab>", lambda _e: self._step(1))
        top.bind("<Control-Shift-Tab>", lambda _e: self._step(-1))
        top.bind("<Control-ISO_Left_Tab>", lambda _e: self._step(-1))


class App(AdvancedMixin, tk.Tk):
    def __init__(self, folder: str | None = None):
        super().__init__()
        self.title(APP_NAME)
        self.ui_scale = _install_scaling(self)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(self.px(1360), sw - 40), min(self.px(900), sh - 80)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        self.minsize(min(self.px(1000), w), min(self.px(640), h))

        self.settings = self._load_settings()
        self.p = PALETTES.get(self.settings.get("theme"), PALETTES["dark"])
        self.style = ttk.Style(self)
        self._canvases: list[tk.Canvas] = []
        self._combos: list[ttk.Combobox] = []
        self._img_cache: dict = {}
        self._fonts()

        # state
        self.folder: str | None = None
        self.slot_paths: list[str] = []
        self.files: dict[str, SaveFile] = {}
        self.mtimes: dict[str, float] = {}
        self.dirty: set[str] = set()
        self.sv: SaveFile | None = None
        self.uid: int | None = None          # unit shown in the form
        self.money_off: int | None = None
        self.gold_auto = False              # gold found by its record, not by search
        self._gold_cands: list[int] = []
        self._inv_items: list[tuple[int, int, int]] = []
        self._inv_spells: list[tuple[int, int, int]] = []
        self._conv_rows: list = []          # ConvoyRow, in slot order
        self._skill_rows: list = []
        self._shown: dict = {}
        self._skill_shown: dict[int, int] = {}
        self._flash_job = None

        # Off by default: Auto is the live save, the other slots are snapshots.
        # (New key, so a "mirror": true saved by an older version doesn't stick.)
        self.mirror = tk.BooleanVar(value=self.settings.get("mirror_route", False))
        self.show_enemies = tk.BooleanVar(value=self.settings.get("show_enemies", False))
        self.show_internal = tk.BooleanVar(value=False)
        self.show_bad_classes = tk.BooleanVar(value=False)
        self.allow_any_item = tk.BooleanVar(value=False)

        self.item_rows = self._build_item_rows()
        self.apply_theme()
        self._build()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        start = folder or self.settings.get("last_folder")
        if not (start and os.path.isdir(start)):
            start = default_save_dir()
        if start and os.path.isdir(start):
            self.after(50, lambda: self.open_folder(start))
        else:
            self.after(50, self._show_welcome)

    # ------------------------------------------------------------ settings
    def _load_settings(self) -> dict:
        try:
            with open(SETTINGS_PATH, encoding="utf8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_settings(self):
        self.settings.update(theme=self.theme_name(), mirror_route=bool(self.mirror.get()),
                             show_enemies=bool(self.show_enemies.get()),
                             last_folder=self.folder)
        try:
            with open(SETTINGS_PATH, "w", encoding="utf8") as fh:
                json.dump(self.settings, fh, indent=1)
        except OSError:
            pass

    def theme_name(self) -> str:
        return "light" if self.p is PALETTES["light"] else "dark"

    # --------------------------------------------------------------- theme
    # ------------------------------------------------------------ scaling
    def px(self, n: float) -> int:
        """A size given in 96-dpi pixels, in this display's pixels."""
        return int(round(n * self.ui_scale))

    def pad(self, *v):
        return tuple(self.px(x) for x in v)

    def _row_height(self) -> int:
        """Treeview rows sized from the font, not a fixed 28 px, so text and the
        22 px icons aren't clipped when fonts grow with the display's DPI."""
        line = tkfont.Font(self, font=self.f_body).metrics("linespace")
        return max(line + self.px(10), self.px(28), 28)

    def place_dialog(self, win, w: int | None = None, h: int | None = None):
        """Size a dialog for the display's DPI and centre it over the main window
        (left alone, Windows opens it small in the screen's top-left corner)."""
        win.update_idletasks()
        w = self.px(w) if w else win.winfo_reqwidth()
        h = self.px(h) if h else win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(w, sw - 40), min(h, sh - 80)
        x = self.winfo_rootx() + (self.winfo_width() - w) // 2
        y = self.winfo_rooty() + (self.winfo_height() - h) // 3
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h))
        win.geometry(f"{w}x{h}+{x}+{y}")

    def show_dialog(self, win, w: int | None = None, h: int | None = None,
                    *, grab: bool = True, focus=None):
        """Place, reveal and optionally grab a withdrawn dialog cleanly."""
        self.place_dialog(win, w, h)
        win.deiconify()
        if grab:
            try:
                win.grab_set()
            except tk.TclError:
                try:
                    if self.winfo_viewable():
                        win.wait_visibility()
                    win.grab_set()
                except tk.TclError:
                    pass
        target = focus if focus is not None else win
        try:
            target.focus_set()
        except tk.TclError:
            pass

    def _fonts(self):
        fam = _pick_family(["Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text",
                            "Helvetica Neue", "Noto Sans", "Cantarell", "DejaVu Sans"],
                           "TkDefaultFont")
        mono = _pick_family(["Cascadia Mono", "Consolas", "JetBrains Mono", "Menlo",
                             "DejaVu Sans Mono"], "TkFixedFont")
        self.family, self.mono = fam, mono
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=fam, size=11)
            except tk.TclError:
                pass
        self.f_body = (fam, 11)
        self.f_small = (fam, 10)
        self.f_bold = (fam, 11, "bold")
        self.f_nav = (fam, 11, "bold")
        self.f_h3 = (fam, 12, "bold")
        self.f_h2 = (fam, 15, "bold")
        self.f_h1 = (fam, 19, "bold")
        self.f_huge = (fam, 30, "bold")
        self.f_mono = (mono, 10)

    def apply_theme(self):
        p, s = self.p, self.style
        s.theme_use("clam")
        self.configure(background=p["bg"])
        s.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["entry"],
                    bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                    troughcolor=p["track"], focuscolor=p["accent"], selectbackground=p["sel"],
                    selectforeground=p["fg"], insertcolor=p["fg"], font=self.f_body,
                    arrowcolor=p["muted"], relief="flat")
        for name, bg in (("TFrame", p["bg"]), ("Side.TFrame", p["side"]),
                         ("Card.TFrame", p["card"]), ("Header.TFrame", p["side"])):
            s.configure(name, background=bg)
        s.configure("Card.TFrame", bordercolor=p["border"])
        s.configure("Outlined.TFrame", background=p["card"], bordercolor=p["border"],
                    relief="solid", borderwidth=1)

        def lab(name, bg, fg, font=None):
            s.configure(name, background=bg, foreground=fg, **({"font": font} if font else {}))

        lab("TLabel", p["bg"], p["fg"])
        lab("Muted.TLabel", p["bg"], p["muted"])
        lab("Faint.TLabel", p["bg"], p["faint"], self.f_small)
        lab("Side.TLabel", p["side"], p["fg"])
        lab("SideMuted.TLabel", p["side"], p["muted"], self.f_small)
        lab("SideH3.TLabel", p["side"], p["fg"], self.f_h3)
        lab("Title.TLabel", p["side"], p["fg"], self.f_h2)
        lab("Subtitle.TLabel", p["side"], p["muted"], self.f_small)
        lab("Card.TLabel", p["card"], p["fg"])
        lab("CardMuted.TLabel", p["card"], p["muted"])
        lab("CardFaint.TLabel", p["card"], p["faint"], self.f_small)
        lab("CardBold.TLabel", p["card"], p["fg"], self.f_bold)
        lab("CardH3.TLabel", p["card"], p["fg"], self.f_h3)
        lab("CardH1.TLabel", p["card"], p["fg"], self.f_h1)
        lab("CardHuge.TLabel", p["card"], p["accent"], self.f_huge)
        lab("CardWarn.TLabel", p["card"], p["warn"], self.f_small)
        lab("CardOk.TLabel", p["card"], p["ok"], self.f_small)
        lab("CardMono.TLabel", p["card"], p["muted"], self.f_mono)
        lab("StatName.TLabel", p["card"], p["muted"], self.f_bold)
        lab("Status.TLabel", p["side"], p["muted"], self.f_small)
        lab("StatusWarn.TLabel", p["side"], p["warn"], self.f_small)
        lab("StatusOk.TLabel", p["side"], p["ok"], self.f_small)
        lab("StatusErr.TLabel", p["side"], p["danger"], self.f_small)
        lab("Chip.TLabel", p["raised"], p["muted"], self.f_small)
        s.configure("Chip.TLabel", padding=(8, 2))
        lab("ChipWarn.TLabel", p["raised"], p["warn"], self.f_small)
        s.configure("ChipWarn.TLabel", padding=(8, 2))
        lab("ChipOk.TLabel", p["raised"], p["ok"], self.f_small)
        s.configure("ChipOk.TLabel", padding=(8, 2))

        # buttons
        s.configure("TButton", background=p["raised"], foreground=p["fg"], padding=(12, 6),
                    bordercolor=p["border"], lightcolor=p["raised"], darkcolor=p["raised"],
                    focusthickness=0, anchor="center")
        s.map("TButton",
              background=[("disabled", p["card"]), ("pressed", p["border"]),
                          ("active", p["border"])],
              foreground=[("disabled", p["faint"])],
              lightcolor=[("active", p["border"])], darkcolor=[("active", p["border"])])
        s.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"],
                    bordercolor=p["accent"], lightcolor=p["accent"], darkcolor=p["accent"],
                    font=self.f_bold)
        s.map("Accent.TButton",
              background=[("disabled", p["raised"]), ("pressed", p["accent"]),
                          ("active", p["accent_hi"])],
              foreground=[("disabled", p["faint"])],
              bordercolor=[("disabled", p["border"]), ("active", p["accent_hi"])],
              lightcolor=[("active", p["accent_hi"])], darkcolor=[("active", p["accent_hi"])])
        s.configure("Danger.TButton", foreground=p["danger"])
        s.configure("Small.TButton", padding=(8, 3), font=self.f_small)
        s.configure("Icon.TButton", padding=(8, 4))

        # inputs
        for w in ("TEntry", "TSpinbox", "TCombobox"):
            s.configure(w, fieldbackground=p["entry"], foreground=p["fg"], padding=(8, 5),
                        bordercolor=p["border"], lightcolor=p["entry"], darkcolor=p["entry"],
                        background=p["raised"], arrowcolor=p["muted"], insertcolor=p["fg"])
            s.map(w, bordercolor=[("focus", p["accent"])],
                  lightcolor=[("focus", p["accent"])],
                  fieldbackground=[("readonly", p["entry"]), ("disabled", p["card"])],
                  foreground=[("disabled", p["faint"])],
                  arrowcolor=[("active", p["fg"])], background=[("active", p["border"])])
        s.configure("Placeholder.TEntry", foreground=p["faint"])
        s.configure("Stat.TSpinbox", font=(self.family, 13, "bold"), padding=(6, 3))
        s.map("TCombobox", selectbackground=[("readonly", p["entry"])],
              selectforeground=[("readonly", p["fg"])])
        self.option_add("*TCombobox*Listbox.background", p["card"])
        self.option_add("*TCombobox*Listbox.foreground", p["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", p["sel"])
        self.option_add("*TCombobox*Listbox.selectForeground", p["fg"])
        self.option_add("*TCombobox*Listbox.font", self.f_body)
        self.option_add("*TCombobox*Listbox.borderWidth", 0)

        self._paint_checks()
        for name, bg in (("TCheckbutton", p["bg"]), ("Card.TCheckbutton", p["card"]),
                         ("Side.TCheckbutton", p["side"])):
            s.layout(name, [("Checkbutton.padding", {"sticky": "nswe", "children": [
                ("Fancy.indicator", {"side": "left", "sticky": ""}),
                ("Checkbutton.label", {"side": "left", "sticky": "nswe"})]})])
            s.configure(name, background=bg, foreground=p["fg"], indicatorbackground=p["entry"],
                        indicatorforeground=p["accent_fg"], indicatormargin=(0, 0, 6, 0),
                        upperbordercolor=p["border"], lowerbordercolor=p["border"],
                        focusthickness=0)
            s.map(name, background=[("active", bg)],
                  indicatorbackground=[("selected", p["accent"]), ("active", p["raised"])],
                  foreground=[("disabled", p["faint"])])

        # lists
        s.configure("Treeview", background=p["card"], fieldbackground=p["card"],
                    foreground=p["fg"], rowheight=self._row_height(), borderwidth=0, relief="flat",
                    font=self.f_body)
        s.map("Treeview", background=[("selected", p["sel"])],
              foreground=[("selected", p["fg"])])
        s.configure("Treeview.Heading", background=p["card"], foreground=p["faint"],
                    relief="flat", font=self.f_small, padding=self.pad(6, 6), borderwidth=0,
                    bordercolor=p["card"], lightcolor=p["card"], darkcolor=p["card"])
        s.map("Treeview.Heading", background=[("active", p["raised"])])
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.configure("Side.Treeview", background=p["side"], fieldbackground=p["side"])
        s.layout("Nav.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.configure("Nav.Treeview", background=p["side"], fieldbackground=p["side"],
                    foreground=p["muted"], font=self.f_nav,
                    rowheight=max(self._row_height(), self.px(32), 32),
                    borderwidth=0, relief="flat")
        s.map("Nav.Treeview", background=[("selected", p["sel"])],
              foreground=[("selected", p["fg"])])

        for orientation in ("Vertical", "Horizontal"):
            style = f"{orientation}.TScrollbar"
            s.configure(style, background=p["raised"], troughcolor=p["card"],
                        bordercolor=p["card"], lightcolor=p["raised"], darkcolor=p["raised"],
                        arrowcolor=p["faint"], gripcount=0, arrowsize=12)
            s.map(style, background=[("active", p["border"])])

        # tabs
        s.configure("TNotebook", background=p["bg"], borderwidth=0, tabmargins=(0, 4, 0, 0))
        s.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"],
                    padding=self.pad(16, 8), borderwidth=0, font=self.f_bold,
                    lightcolor=p["bg"], bordercolor=p["bg"])
        s.map("TNotebook.Tab", background=[("selected", p["card"]), ("active", p["raised"])],
              foreground=[("selected", p["fg"]), ("active", p["fg"])],
              lightcolor=[("selected", p["accent"])],
              expand=[("selected", (0, 0, 0, 0))])
        s.configure("Main.TNotebook", background=p["bg"], borderwidth=0, tabmargins=0)
        s.layout("Main.TNotebook.Tab", [])
        if hasattr(self, "nav_tree"):
            self.nav_tree.tag_configure("group", foreground=p["faint"], font=self.f_small)
        s.configure("TPanedwindow", background=p["bg"])
        s.configure("Sash", sashthickness=6, gripcount=0, background=p["bg"],
                    lightcolor=p["bg"], bordercolor=p["bg"])
        s.configure("TSeparator", background=p["border"])
        s.configure("Horizontal.TProgressbar", background=p["accent"], troughcolor=p["track"],
                    bordercolor=p["track"], lightcolor=p["accent"], darkcolor=p["accent"])

        for c in self._canvases:
            try:
                c.redraw()
            except tk.TclError:
                pass
        for cb in self._combos:
            try:
                pd = self.tk.call("ttk::combobox::PopdownWindow", cb)
                self.tk.call(f"{pd}.f.l", "configure", "-background", p["card"],
                             "-foreground", p["fg"], "-selectbackground", p["sel"],
                             "-selectforeground", p["fg"])
            except tk.TclError:
                pass
        if hasattr(self, "unit_tree"):
            self._style_trees()
            self.refresh_skills()

    def _paint_checks(self):
        """Rounded checkbox images, repainted in place when the theme changes."""
        p = self.p
        if not hasattr(self, "_chk"):
            self._chk = {k: tk.PhotoImage(width=16, height=16) for k in ("off", "hover", "on")}
            self.style.element_create("Fancy.indicator", "image", self._chk["off"],
                                      ("selected", self._chk["on"]),
                                      ("active", self._chk["hover"]),
                                      border=0, width=24, sticky="w")
        tick = set()
        for (x0, y0), (x1, y1) in (((4, 8), (7, 11)), ((7, 11), (12, 5))):
            for t in range(41):
                x = x0 + (x1 - x0) * t / 40
                y = y0 + (y1 - y0) * t / 40
                tick.add((int(round(x)), int(round(y))))
                tick.add((int(round(x)), int(round(y)) - 1))
        for key, (fill, edge) in {"off": (p["entry"], p["faint"]),
                                  "hover": (p["raised"], p["accent"]),
                                  "on": (p["accent"], p["accent"])}.items():
            img = self._chk[key]
            img.blank()
            for y in range(1, 15):
                for x in range(1, 15):
                    # rounded corners: skip the outermost corner pixels
                    cx = min(x - 1, 14 - x)
                    cy = min(y - 1, 14 - y)
                    if cx + cy < 2 and cx < 2 and cy < 2 and (cx, cy) != (1, 1):
                        continue
                    border = x in (1, 14) or y in (1, 14) or (cx, cy) == (1, 1)
                    col = edge if border else fill
                    if key == "on" and (x, y) in tick:
                        col = p["accent_fg"]
                    img.put(col, (x, y))

    def _style_trees(self):
        p = self.p
        for tree in (self.unit_tree,):
            tree.tag_configure("enemy", foreground=p["faint"])
        for tree in (self.conv_tree, self.inv_tree):
            tree.tag_configure("empty", foreground=p["faint"])
        self.inv_tree.tag_configure("header", foreground=p["muted"], font=self.f_small)
        for tree in (getattr(self, "cls_tree", None), getattr(self, "mas_tree", None),
                     getattr(self, "flag_tree", None), getattr(self, "mount_tree", None),
                     getattr(self, "pool_tree", None),
                     getattr(self, "map_tree", None)):
            if tree is not None:
                tree.tag_configure("flag_unknown", foreground=p["muted"])
                tree.tag_configure("empty", foreground=p["faint"])

    def toggle_theme(self):
        self.p = PALETTES["light" if self.theme_name() == "dark" else "dark"]
        self._img_cache = {k: v for k, v in self._img_cache.items() if k[0] != "dot"}
        self.apply_theme()
        self.theme_btn.configure(text="☀" if self.theme_name() == "dark" else "☾")
        self._save_settings()

    def register_canvas(self, c):
        self._canvases.append(c)

    def register_combo(self, cb):
        self._combos.append(cb)

    # ------------------------------------------------------------- images
    def _dot(self, color: str, size: int = 12) -> tk.PhotoImage:
        key = ("dot", color, size)
        img = self._img_cache.get(key)
        if img is None:
            pad = 4
            img = tk.PhotoImage(width=size + pad, height=size)
            r = size / 2 - 0.5
            c = size / 2 - 0.5
            for y in range(size):
                dy = y - c
                span = (r * r - dy * dy)
                if span < 0:
                    continue
                half = span ** 0.5
                x0, x1 = int(round(c - half)), int(round(c + half)) + 1
                if x1 > x0:
                    img.put(color, to=(x0, y, x1, y + 1))
            self._img_cache[key] = img
        return img

    def _load_png(self, path: str, size: int):
        key = ("png", path, size)
        if key in self._img_cache:
            return self._img_cache[key]
        try:
            img = tk.PhotoImage(file=path)
            side = max(img.width(), img.height())
            if side > size > 0:
                if side % size == 0:
                    img = img.subsample(side // size, side // size)
                else:
                    frac = Fraction(size, side).limit_denominator(max(6, (side + size - 1) // size))
                    if frac.numerator == 0:
                        factor = max(1, round(side / size))
                        img = img.subsample(factor, factor)
                    else:
                        if frac.numerator > 1:
                            img = img.zoom(frac.numerator, frac.numerator)
                        if frac.denominator > 1:
                            img = img.subsample(frac.denominator, frac.denominator)
        except tk.TclError:
            img = None
        self._img_cache[key] = img
        return img

    def item_icon(self, iid: int, meta: dict, kind: str):
        folder = os.path.join(ICON_DIR, "items")
        if os.path.isdir(folder):
            for name in (f"{iid:08x}.png", _slug(meta.get("en") or "") + ".png"):
                path = os.path.join(folder, name)
                if os.path.isfile(path):
                    img = self._load_png(path, 22)
                    if img is not None:
                        return img
        return self._dot(KIND_COLORS.get(kind, KIND_COLORS["Item"]))

    @staticmethod
    def _unit_base_pid(pid: str) -> str:
        if not pid:
            return ""
        return re.sub(r"^PID_(?:(?:SC|BD)[0-9X]+_[0-9X]+_|(?:WD|DN)_)", "PID_", pid)

    def unit_image(self, name: str, pid: str, size: int,
                   uid: int | None = None, name_en: str = ""):
        folder = os.path.join(ICON_DIR, "units")
        if not os.path.isdir(folder):
            return None
        ids = layer.unit_ids()
        base_pid = self._unit_base_pid(pid)
        if not name_en:
            for key in (uid, core.id_hash(pid) if pid else None,
                        core.id_hash(base_pid) if base_pid and base_pid != pid else None):
                meta = ids.get(key) if key is not None else None
                if meta:
                    name_en = meta.get("name_en") or meta.get("name") or ""
                    if name_en:
                        break
        candidates: list[str] = []
        if uid is not None:
            candidates.append(f"{uid:08x}")
        for raw in (name, name_en, _slug(name_en), _slug(name),
                    pid, pid[4:] if pid.startswith("PID_") else "",
                    base_pid, base_pid[4:] if base_pid.startswith("PID_") else ""):
            if raw and raw not in candidates:
                candidates.append(raw)
        for stem in candidates:
            path = os.path.join(folder, stem + ".png")
            if os.path.isfile(path):
                return self._load_png(path, size)
        return None

    def unit_list_icon(self, name: str, pid: str = "",
                       uid: int | None = None, name_en: str = ""):
        img = self.unit_image(name, pid, self.px(20), uid=uid, name_en=name_en)
        return img if img is not None else self._dot(_name_color(name), 10)

    def _build_item_rows(self):
        rows = []
        for iid, meta in item_ids().items():
            en = item_label(iid, meta)
            jp = meta.get("jp") or ""
            blob = f"{en} {meta.get('en', '')} {jp} {iid:08x}".lower()
            rows.append((iid, meta, en, jp, item_kind(meta), item_category(meta),
                         is_shadow(meta), blob))
        rows.sort(key=lambda r: (r[2].lower(), r[0]))
        return rows

    # ----------------------------------------------------------------- ui
    def _build(self):
        # ---- header: two rows, so the slot picker never has to share a row
        # with the action buttons (on small windows pack squeezed the
        # "Open folder…" button out of the window entirely)
        head = ttk.Frame(self, style="Header.TFrame")
        head.pack(fill="x")
        actions = ttk.Frame(head, style="Header.TFrame")
        actions.pack(fill="x")
        brand = ttk.Frame(actions, style="Header.TFrame")
        brand.pack(side="left")
        ttk.Label(brand, text="Fortune's Weave", style="Title.TLabel").pack(anchor="w")
        ttk.Label(brand, text=f"SAVE EDITOR  ·  v{__version__}",
                  style="Subtitle.TLabel").pack(anchor="w")

        right = ttk.Frame(actions, style="Header.TFrame")
        right.pack(side="right")
        self.theme_btn = ttk.Button(right, text="☀" if self.theme_name() == "dark" else "☾",
                                    width=3, style="Icon.TButton", command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=(8, 0))
        self.save_btn = ttk.Button(right, text='保存', style="Accent.TButton",
                                   command=self.save_all)
        self.save_btn.pack(side="right", padx=(8, 0))
        self.revert_btn = ttk.Button(right, text='⟳ 重新加载', command=self.reload)
        self.revert_btn.pack(side="right")
        ttk.Button(right, text='创建快照', command=self.take_snapshot).pack(side="right", padx=(0, 8))
        ttk.Button(right, text='恢复快照…', command=self.restore_snapshot).pack(side="right")
        self.open_btn = ttk.Button(right, text='打开…', command=self.open_dialog)
        self.open_btn.pack(side="right", padx=(0, 8))

        mid = ttk.Frame(head, style="Header.TFrame")
        mid.pack(fill="x", pady=(10, 0))
        r1 = ttk.Frame(mid, style="Header.TFrame")
        r1.pack(fill="x")
        ttk.Label(r1, text='存档槽位', style="Side.TLabel").pack(side="left")
        self.slot_var = tk.StringVar()
        # width is a character count and its pixel size varies by Tk version and
        # platform; a fixed 52 columns overflows the header on Tk 9 / macOS, so
        # keep the base short and let the box stretch to whatever space the row
        # has left
        self.slot_box = ttk.Combobox(r1, textvariable=self.slot_var, state="readonly", width=28)
        self.slot_box.pack(side="left", padx=(8, 0), fill="x", expand=True)
        self.slot_box.bind("<<ComboboxSelected>>", self.on_slot_pick)
        self.register_combo(self.slot_box)
        r2 = ttk.Frame(mid, style="Header.TFrame")
        r2.pack(fill="x", pady=(6, 0))
        self.mirror_chk = ttk.Checkbutton(r2, text='同时编辑此路线的其他存档',
                                          style="Side.TCheckbutton", variable=self.mirror,
                                          command=self._mirror_changed)
        self.mirror_chk.pack(side="left")
        self.file_lbl = ttk.Label(r2, text="", style="SideMuted.TLabel")
        self.file_lbl.pack(side="left", padx=(16, 0))

        # ---- status bar (packed before the body so it keeps its space)
        bar = ttk.Frame(self, style="Header.TFrame", padding=(18, 6))
        bar.pack(side="bottom", fill="x")
        self.status_lbl = ttk.Label(bar, text='就绪', style="Status.TLabel")
        self.dirty_lbl = ttk.Label(bar, text="", style="Status.TLabel")
        self.dirty_lbl.pack(side="right")

        # ---- pages (left grouped sidebar + hidden-tab notebook)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        nav_box = ttk.Frame(body, style="Side.TFrame", width=self.px(164), padding=(8, 10, 8, 10))
        nav_box.pack(side="left", fill="y")
        nav_box.pack_propagate(False)
        self.nav_tree = ttk.Treeview(nav_box, style="Nav.Treeview", show="tree", selectmode="browse")
        self.nav_tree.pack(fill="both", expand=True)
        self.nav_tree.tag_configure("group", foreground=self.p["faint"], font=self.f_small)

        self.pages = _PageStack(body, style="TFrame")
        self.pages.pack(side="left", fill="both", expand=True)
        self.units_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.convoy_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.gold_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.supports_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.player_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.classes_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.flags_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.mounts_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.world_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.tutorials_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.blessings_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.fast_travel_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.facilities_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.deeds_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.boons_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.quests_page = ttk.Frame(self.pages, padding=(14, 14, 14, 8))
        self.pages.add(self.units_page, text='单位')
        self.pages.add(self.convoy_page, text='道具袋')
        self.pages.add(self.gold_page, text='金币')
        self.pages.add(self.supports_page, text='支援情报')
        self.pages.add(self.player_page, text='玩家')
        self.pages.add(self.classes_page, text='兵种')
        self.pages.add(self.mounts_page, text='坐骑')
        self.pages.add(self.world_page, text="世界地图")
        self.pages.add(self.tutorials_page, text="教程")
        self.pages.add(self.blessings_page, text="加护")
        self.pages.add(self.fast_travel_page, text="快速传送")
        self.pages.add(self.facilities_page, text="功能")
        self.pages.add(self.deeds_page, text="伟业录")
        self.pages.add(self.boons_page, text="救世的祝福")
        self.pages.add(self.quests_page, text="任务")
        self.pages.add(self.flags_page, text='标记')

        nav_groups = [
            ("队伍与背包", [
                (self.units_page, "单位"),
                (self.convoy_page, "道具袋"),
                (self.gold_page, "金币"),
                (self.supports_page, "支援情报"),
                (self.classes_page, "兵种"),
                (self.mounts_page, "坐骑"),
            ]),
            ("世界与进度", [
                (self.quests_page, "任务"),
                (self.world_page, "世界地图"),
                (self.fast_travel_page, "快速传送"),
                (self.facilities_page, "功能"),
                (self.player_page, "玩家"),
            ]),
            ("收集与系统", [
                (self.tutorials_page, "教程"),
                (self.blessings_page, "加护"),
                (self.deeds_page, "伟业录"),
                (self.boons_page, "救世的祝福"),
                (self.flags_page, "标记"),
            ]),
        ]
        self._nav_page_by_iid = {}
        self._nav_iid_by_page = {}
        for g_idx, (g_title, items) in enumerate(nav_groups):
            g_iid = f"__group_{g_idx}"
            self.nav_tree.insert("", "end", iid=g_iid, text=f" {g_title}", tags=("group",), open=True)
            for page, label in items:
                p_str = str(page)
                self._nav_page_by_iid[p_str] = page
                self._nav_iid_by_page[p_str] = p_str
                self.nav_tree.insert(g_iid, "end", iid=p_str, text=f"  {label}")
        self.nav_tree.column("#0", width=148, minwidth=120)

        def _nav_selected(_event=None):
            sel = self.nav_tree.selection()
            if not sel:
                return
            page = self._nav_page_by_iid.get(sel[0])
            if page is not None and self.pages.select() != str(page):
                self.pages.select(page)
            elif page is None:
                cur = self._nav_iid_by_page.get(self.pages.select())
                if cur and self.nav_tree.exists(cur):
                    self.nav_tree.selection_set(cur)

        def _sync_nav(_event=None):
            cur = self._nav_iid_by_page.get(self.pages.select())
            if cur and self.nav_tree.exists(cur) and self.nav_tree.selection() != (cur,):
                self.nav_tree.selection_set(cur)
                self.nav_tree.see(cur)

        self.nav_tree.bind("<<TreeviewSelect>>", _nav_selected)
        self.pages.bind("<<NotebookTabChanged>>", _sync_nav, add="+")
        _sync_nav()

        self.status_lbl.pack(side='left', fill='x', expand=True)
        self.pages.enable_traversal()

        self._build_units()
        self._build_convoy()
        self._build_gold()
        self._build_supports()
        self._build_player()
        self._build_classes()
        self._build_flags()
        self._build_mounts()
        self._build_world_map()
        self._build_tutorials()
        self._build_blessings()
        self._build_fast_travel()
        self._build_facilities()
        self._build_deeds()
        self._build_boons()
        self._build_quests()
        self._style_trees()
        self._enable_multi_select_all()
        self._update_dirty()

    def _enable_multi_select_all(self):
        """Ctrl+A / Shift+drag on every list except the three row-pickers.

        Unit and blessing lists drive edit forms, so stay single-choice; every
        other table (convoy, classes, masteries, flags, mounts) takes a range.
        """
        skip = {id(getattr(self, "nav_tree", None)),
                id(getattr(self, "unit_tree", None)),
                id(getattr(self, "cls_unit_tree", None)),
                id(getattr(self, "blessing_tree", None)),
                id(getattr(self, "deed_tree", None)),
                id(getattr(self, "boon_tree", None)),
                id(getattr(self, "facility_access_tree", None)),
                id(getattr(self, "quest_tree", None))}

        def walk(w):
            for child in w.winfo_children():
                if isinstance(child, ttk.Treeview) and id(child) not in skip:
                    enable_multi_select(child)
                walk(child)

        walk(self)

    # -------------------------------------------------------- units page
    def _build_units(self):
        pane = ttk.PanedWindow(self.units_page, orient="horizontal")
        pane.pack(fill="both", expand=True)

        side = ttk.Frame(pane, style="Card.TFrame", padding=12)
        pane.add(side, weight=0)
        top = ttk.Frame(side, style="Card.TFrame")
        side.columnconfigure(0, weight=1)
        side.rowconfigure(3, weight=1)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text='单位', style="CardH3.TLabel").pack(side="left")
        self.unit_count = ttk.Label(top, text="", style="CardMuted.TLabel")
        self.unit_count.pack(side="right")
        self.unit_search = SearchEntry(side, "搜索单位或兵种…  (Ctrl+F)",
                                       self.refresh_units, width=30)
        self.unit_search.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        self.unit_search.bind("<Down>", lambda e: (self.unit_tree.focus_set(), "break")[1])
        ttk.Checkbutton(side, text='显示敌人和 NPC', style="Card.TCheckbutton",
                        variable=self.show_enemies,
                        command=lambda: (self.refresh_units(), self._save_settings())
                        ).grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.unit_tree = scrolled_tree(side, columns=("number", "costume", "lv"),
                                       show="tree headings", selectmode="browse")
        self.unit_tree.heading("#0", text='名称', anchor="w")
        self.unit_tree.heading("costume", text='着装', anchor="center")
        self.unit_tree.heading("lv", text='LV', anchor="e")
        self.unit_tree.heading("number", text='编号', anchor="e")
        self.unit_tree.column("number", width=52, minwidth=46, anchor="e", stretch=False)
        self.unit_tree.column("costume", width=56, minwidth=48, anchor="center", stretch=False)
        self.unit_tree.column("#0", width=165, minwidth=120)
        self.unit_tree.column("lv", width=40, minwidth=36, anchor="e", stretch=False)
        self.unit_tree.container.grid(row=3, column=0, sticky="nsew")
        self.unit_tree.bind("<<TreeviewSelect>>", self.on_unit_select)
        acts = ttk.Frame(side, style="Card.TFrame")
        acts.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(acts, text='添加单位…', style="Small.TButton",
                   command=self.open_add_unit).pack(fill="x", pady=(0, 6))
        ttk.Button(acts, text='修改单位编号…', style="Small.TButton",
                   command=self.open_unit_number).pack(fill="x", pady=(0, 6))
        ttk.Button(acts, text='导入单位…', style="Small.TButton",
                   command=self.copy_unit_in).pack(fill="x")
        ttk.Button(acts, text='移除单位', style="Small.TButton",
                   command=self.remove_current_unit).pack(fill="x", pady=(6, 0))

        main = ttk.Frame(pane)
        pane.add(main, weight=1)

        # unit header card
        card = ttk.Frame(main, style="Card.TFrame", padding=(16, 14))
        card.pack(fill="x", padx=(10, 0))
        self.avatar = Avatar(card, self, size=self.px(60))
        self.avatar.pack(side="left")
        info = ttk.Frame(card, style="Card.TFrame")
        info.pack(side="left", padx=(14, 0), fill="x", expand=True)
        name_row = ttk.Frame(info, style="Card.TFrame")
        name_row.pack(anchor="w", fill="x")
        self.unit_name = ttk.Label(name_row, text='未选择单位', style="CardH1.TLabel")
        self.unit_name.pack(side="left")
        self.costume_chip = ttk.Label(name_row, text="", style="Chip.TLabel")
        self.costume_chip.bind("<Double-1>", lambda _e: self.open_costume_dialog())
        self.costume_btn = ttk.Button(name_row, text='更改着装…', style="Small.TButton",
                                      command=self.open_costume_dialog)
        self.unit_sub = ttk.Label(info, text='请先打开存档文件夹。',
                                  style="CardMuted.TLabel")
        self.unit_sub.pack(anchor="w", pady=(2, 0))
        chips = ttk.Frame(card, style="Card.TFrame")
        chips.pack(side="right", anchor="n")
        self.copies_chip = ttk.Label(chips, text="", style="Chip.TLabel")
        self.copies_chip.pack(anchor="e")
        self.sync_btn = ttk.Button(chips, text='同步副本', style="Small.TButton",
                                   command=self.sync_copies)

        self.unit_tabs = ttk.Notebook(main)
        self.unit_tabs.pack(fill="both", expand=True, padx=(10, 0), pady=(10, 0))
        self._build_stats_tab()
        self._build_skill_tab()
        self._build_inventory_tab()
        self._build_class_tab()

    def _num_vcmd(self):
        return (self.register(lambda P: P == "" or (P.isdigit() and len(P) <= 7)), "%P")

    def _build_stats_tab(self):
        self.stats_scroll = ScrollableFrame(self.unit_tabs, style="Card.TFrame", padding=18)
        self.unit_tabs.add(self.stats_scroll, text='状态')
        tab = self.stats_scroll.body
        self._stat_spins: list[ttk.Spinbox] = []
        vcmd = self._num_vcmd()

        row = ttk.Frame(tab, style="Card.TFrame")
        row.pack(fill="x")
        self.lvl_var, self.exp_var = tk.StringVar(), tk.StringVar()
        for label, var, fn, lo, hi in (("LV", self.lvl_var, self.commit_level, 1, 99),
                                       ("EXP", self.exp_var, self.commit_exp, 0, 99)):
            box = ttk.Frame(row, style="Card.TFrame")
            box.pack(side="left", padx=(0, 28))
            ttk.Label(box, text=label, style="StatName.TLabel").pack(anchor="w")
            sp = ttk.Spinbox(box, from_=lo, to=hi, width=6, textvariable=var, command=fn,
                             validate="key", validatecommand=vcmd, style="Stat.TSpinbox")
            sp.pack(anchor="w", pady=(4, 0))
            self._stat_spins.append(sp)
            sp.bind("<Return>", lambda e, f=fn: f())
            sp.bind("<FocusOut>", lambda e, f=fn: f())
        self.exp_bar = Bar(row, self, width=160, height=6)
        self.exp_bar.pack(side="left", anchor="s", pady=(0, 12))
        hpbox = ttk.Frame(row, style="Card.TFrame")
        hpbox.pack(side="left", padx=(40, 0))
        ttk.Label(hpbox, text="HP", style="StatName.TLabel").pack(anchor="w")
        self.maxhp_lbl = ttk.Label(hpbox, text="—", style="CardH1.TLabel")
        self.maxhp_lbl.pack(anchor="w")

        ttk.Separator(tab).pack(fill="x", pady=16)
        gh = ttk.Frame(tab, style="Card.TFrame")
        gh.pack(fill="x", pady=(0, 10))
        ttk.Label(gh, text='升级获得的属性点', style="CardH3.TLabel").pack(anchor="w")
        ttk.Label(gh, style="CardFaint.TLabel", wraplength=700, justify="left",
                  text='这些数值是升级获得的属性点，不是游戏中的最终属性；最终值还包括兵种、个人基础值和道具加成。').pack(anchor="w")

        grid = ttk.Frame(tab, style="Card.TFrame")
        grid.pack(fill="x")
        self.stat_vars, self.stat_bars = [], []
        for i, name in enumerate(STAT_NAMES):
            cell = ttk.Frame(grid, style="Card.TFrame", padding=(0, 0, 24, 18))
            cell.grid(row=i // 3, column=i % 3, sticky="ew")
            grid.columnconfigure(i % 3, weight=1, uniform="stat")
            head = ttk.Frame(cell, style="Card.TFrame")
            head.pack(fill="x")
            ttk.Label(head, text=STAT_LONG.get(name, name), style="StatName.TLabel"
                      ).pack(side="left")
            body = ttk.Frame(cell, style="Card.TFrame")
            body.pack(fill="x", pady=(4, 0))
            v = tk.StringVar()
            sp = ttk.Spinbox(body, from_=0, to=STAT_MAX, width=5, textvariable=v,
                             command=lambda i=i: self.commit_stat(i), validate="key",
                             validatecommand=vcmd, style="Stat.TSpinbox")
            sp.pack(side="left")
            self._stat_spins.append(sp)
            sp.bind("<Return>", lambda e, i=i: self.commit_stat(i))
            sp.bind("<FocusOut>", lambda e, i=i: self.commit_stat(i))
            bar = Bar(body, self, width=100, height=6)
            bar.pack(side="left", fill="x", expand=True, padx=(12, 0))
            self.stat_vars.append(v)
            self.stat_bars.append(bar)

        ttk.Label(tab, style="CardFaint.TLabel", wraplength=700, justify="left",
                  text='修改HP成长会同时改变最大HP。按回车或 Tab 应用，点击保存后写入。'
                  ).pack(anchor="w", side="bottom")


    def _build_inventory_tab(self):
        tab = ttk.Frame(self.unit_tabs, style="Card.TFrame", padding=(18, 18, 6, 18))
        self.unit_tabs.add(tab, text='持有物品')
        pane = ttk.PanedWindow(tab, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = ttk.Frame(pane, style="Card.TFrame", padding=(0, 0, 12, 0))
        pane.add(left, weight=1)
        ttk.Label(left, text='持有物品', style="CardH3.TLabel").pack(anchor="w")
        ttk.Label(left, text='选择槽位，再选择替换道具。',
                  style="CardMuted.TLabel").pack(anchor="w", pady=(2, 8))
        ttk.Checkbutton(left, text='允许任意道具类型（可能导致游戏异常）',
                        style="Card.TCheckbutton", variable=self.allow_any_item,
                        command=self._inv_lock).pack(side="bottom", anchor="w", pady=(8, 0))
        ib = ttk.Frame(left, style="Card.TFrame")
        ib.pack(side="bottom", anchor="w", pady=(8, 0))
        ttk.Button(ib, text='添加道具…', style="Small.TButton",
                   command=self.add_item).pack(side="left")
        ttk.Button(ib, text='移除道具', style="Small.TButton",
                   command=self.remove_item).pack(side="left", padx=(6, 0))
        self.inv_item_preview = ttk.Label(left, style="CardMuted.TLabel", justify="left",
                                           wraplength=340)
        self.inv_item_preview.pack(side="bottom", fill="x")
        self.inv_tree = scrolled_tree(left, columns=("kind",), show="tree headings",
                                      selectmode="browse", height=6)
        self.inv_tree.heading("#0", text='道具', anchor="w")
        self.inv_tree.heading("kind", text='类型', anchor="w")
        self.inv_tree.column("#0", width=220)
        self.inv_tree.column("kind", width=90, stretch=False)
        self.inv_tree.container.pack(fill="both", expand=True)
        self.inv_tree.bind("<<TreeviewSelect>>", lambda e: self._inv_lock())
        self.inv_picker = ItemPicker(pane, self, self.replace_item, "放入所选槽位")
        self.inv_picker.configure(padding=(12, 0, 0, 0))
        pane.add(self.inv_picker, weight=2)

    def _build_class_tab(self):
        tab = ttk.Frame(self.unit_tabs, style="Card.TFrame", padding=18)
        self.unit_tabs.add(tab, text='兵种')
        cur = ttk.Frame(tab, style="Card.TFrame")
        cur.pack(fill="x")
        ttk.Label(cur, text='当前兵种', style="CardMuted.TLabel").pack(side="left")
        self.class_now = ttk.Label(cur, text="—", style="CardH3.TLabel")
        self.class_now.pack(side="left", padx=(10, 0))

        row = ttk.Frame(tab, style="Card.TFrame")
        row.pack(fill="x", pady=(12, 6))
        self.class_search = SearchEntry(row, "搜索兵种…", self.refresh_classes)
        self.class_search.pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(row, text='包含怪物及内部兵种',
                        style="Card.TCheckbutton", variable=self.show_bad_classes,
                        command=self.refresh_classes).pack(side="left", padx=(12, 0))
        bottom = ttk.Frame(tab, style="Card.TFrame")
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(bottom, text='更改兵种', style="Accent.TButton",
                   command=self.apply_class).pack(side="right", anchor="n")
        ttk.Label(bottom, style="CardWarn.TLabel", wraplength=560, justify="left",
                  text='仅修改兵种编号，不会重新计算移动力、可用武器、兵种技能和最大HP。请在游戏中检查。'
                  ).pack(side="left", fill="x", expand=True)
        self.class_tree = scrolled_tree(tab, columns=("jp",), show="tree headings",
                                        selectmode="browse", height=6)
        self.class_tree.heading("#0", text='兵种', anchor="w")
        self.class_tree.heading("jp", text='内部名称', anchor="w")
        self.class_tree.column("#0", width=260)
        self.class_tree.column("jp", width=260)
        self.class_tree.container.pack(fill="both", expand=True)
        self.class_tree.bind("<Double-1>", lambda e: self.apply_class())
        self.refresh_classes()

    # ------------------------------------------------------- convoy page
    def _build_convoy(self):
        pane = ttk.PanedWindow(self.convoy_page, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = ttk.Frame(pane, style="Card.TFrame", padding=12)
        pane.add(left, weight=1)
        top = ttk.Frame(left, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text='道具袋', style="CardH3.TLabel").pack(side="left")
        self.conv_count = ttk.Label(top, text="", style="CardMuted.TLabel")
        self.conv_count.pack(side="right")
        ttk.Label(left, text='按槽位显示道具袋中的物品，包括材料。',
                  style="CardMuted.TLabel").pack(anchor="w", pady=(2, 6))
        self.conv_problem = ttk.Frame(left, style="Card.TFrame")
        self.conv_problem_lbl = ttk.Label(self.conv_problem, text="", style="CardWarn.TLabel",
                                          wraplength=420, justify="left")
        self.conv_problem_lbl.pack(fill="x")
        self.conv_repair_btn = ttk.Button(self.conv_problem, text='修复道具袋',
                                          style="Accent.TButton", command=self.repair_convoy_ui)
        self.conv_filter = SearchEntry(left, "搜索道具袋…", self.refresh_convoy)
        self.conv_filter.pack(fill="x", pady=(0, 6))
        ctl = ttk.Frame(left, style="Card.TFrame")
        ctl.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Label(left, style="CardFaint.TLabel", wraplength=420, justify="left",
                  text="每种可堆叠道具都有自己独立的道具袋槽位。“添加道具…”放入一整叠；不可堆叠"
                       "的装备（武器、魔法书、盾牌、多次使用的药剂）每件占一个槽位，放不下时"
                       "“添加”会拒绝。“移除”清空一个槽位；列表保持槽位总数不变，其后"
                       "的内容都会安全移动。“所有材料…”会以数量 1 备齐每种矿石、肉、鱼和植物。"
                  ).pack(side="bottom", anchor="w", pady=(8, 0))
        self.conv_item_preview = ttk.Label(left, style="CardMuted.TLabel", justify="left",
                                             wraplength=340)
        self.conv_item_preview.pack(side="bottom", fill="x")
        self.conv_tree = scrolled_tree(left, columns=("kind", "qty"), show="tree headings",
                                       selectmode="extended", height=6)
        self.conv_tree.heading("#0", text='槽位 · 道具', anchor="w")
        self.conv_tree.heading("kind", text='类型', anchor="w")
        self.conv_tree.heading("qty", text='数量', anchor="e")
        self.conv_tree.column("#0", width=250)
        self.conv_tree.column("kind", width=90, stretch=False)
        self.conv_tree.column("qty", width=56, anchor="e", stretch=False)
        self.conv_tree.container.pack(fill="both", expand=True)
        self.conv_tree.bind("<<TreeviewSelect>>", self._conv_row_picked)

        ttk.Label(ctl, text='数量', style="Card.TLabel").pack(side="left")
        self.qty_var = tk.StringVar(value="1")
        qs = ttk.Spinbox(ctl, from_=1, to=QTY_MAX, width=5, textvariable=self.qty_var,
                         validate="key", validatecommand=self._num_vcmd())
        qs.pack(side="left", padx=(8, 12))
        qs.bind("<Return>", lambda e: self.set_convoy_qty())
        ttk.Button(ctl, text='设置数量', command=self.set_convoy_qty).pack(side="left")
        ttk.Button(ctl, text='添加道具…', style="Small.TButton",
                   command=self.add_convoy_item_ui).pack(side="left", padx=(10, 0))
        ttk.Button(ctl, text='移除', style="Small.TButton",
                   command=self.remove_convoy_item_ui).pack(side="left", padx=(6, 0))
        ttk.Button(ctl, text='所有材料…', style="Small.TButton",
                   command=self.fill_materials_ui).pack(side="left", padx=(6, 0))

        right = ttk.Frame(pane, padding=(10, 0, 0, 0))
        pane.add(right, weight=1)
        self.conv_picker = ItemPicker(right, self, self.replace_convoy, "放入所选行")
        self.conv_picker.pack(fill="both", expand=True)

    # --------------------------------------------------------- gold page
    def _build_gold(self):
        wrap = ttk.Frame(self.gold_page)
        wrap.pack(fill="both", expand=True)
        card = ttk.Frame(wrap, style="Card.TFrame", padding=24)
        card.pack(fill="x")
        ttk.Label(card, text='金币', style="StatName.TLabel").pack(anchor="w")
        self.gold_now = ttk.Label(card, text="—", style="CardHuge.TLabel")
        self.gold_now.pack(anchor="w")
        self.gold_hint = ttk.Label(card, text="", style="CardMuted.TLabel")
        self.gold_hint.pack(anchor="w", pady=(0, 14))
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(anchor="w")
        ttk.Label(row, text='新金额', style="Card.TLabel").pack(side="left")
        self.gold_new = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.gold_new, width=14)
        e.pack(side="left", padx=8)
        e.bind("<Return>", lambda ev: self.set_gold())
        ttk.Button(row, text='设置金币', style="Accent.TButton",
                   command=self.set_gold).pack(side="left")
        for amt in (10_000, 100_000, 999_999):
            ttk.Button(row, text=f"{amt:,}", style="Small.TButton",
                       command=lambda a=amt: (self.gold_new.set(f"{a:,}"), self.set_gold())
                       ).pack(side="left", padx=(8 if amt == 10_000 else 4, 0))

        ttk.Label(wrap, style="CardFaint.TLabel", wraplength=760, justify="left",
                  text="金币在此游戏的每个槽位里位置固定（GameUserData + 6），无需定位："
                       "上方显示的数值就是游戏会读到的值。章节与战斗快照保留各自的金额。"
                  ).pack(anchor="w", pady=(12, 0))

    # --------------------------------------------------------- keys / misc

    def _visible_units(self) -> int:
        """Units the save's own unit list holds (the party and its reserves)."""
        try:
            return len(self.core_of().units())
        except Exception:  # noqa: BLE001 — fall back to the scanned copies
            return len(self._unit_map())

    def _build_supports(self):
        self.sup_party_only = tk.BooleanVar(value=True)
        pane = ttk.PanedWindow(self.supports_page, orient="horizontal")
        pane.pack(fill="both", expand=True)

        side = ttk.Frame(pane, style="Card.TFrame", padding=12)
        pane.add(side, weight=0)
        top = ttk.Frame(side, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text='支援对话', style="CardH3.TLabel").pack(side="left")
        self.sup_count = ttk.Label(top, text="", style="CardMuted.TLabel")
        self.sup_count.pack(side="right")
        ttk.Label(side, text='查看各单位的支援关系，以及每组伙伴在此存档中的支援值。',
                  style="CardMuted.TLabel", wraplength=210, justify="left"
                  ).pack(anchor="w", pady=(2, 8))
        self.sup_search = SearchEntry(side, "搜索单位…", self.refresh_support_chars,
                                      width=26)
        self.sup_search.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(side, text='仅显示此存档中的单位',
                        style="Card.TCheckbutton", variable=self.sup_party_only,
                        command=self.refresh_support_chars).pack(anchor="w", pady=(0, 6))
        self.sup_char_tree = scrolled_tree(side, columns=("n", "top"), show="tree headings",
                                           selectmode="browse")
        self.sup_char_tree.heading("#0", text='单位', anchor="w")
        self.sup_char_tree.heading("n", text='支援组合', anchor="e")
        self.sup_char_tree.heading("top", text='最高', anchor="e")
        self.sup_char_tree.column("#0", width=140, minwidth=100)
        self.sup_char_tree.column("n", width=48, anchor="e", stretch=False)
        self.sup_char_tree.column("top", width=52, anchor="e", stretch=False)
        self.sup_char_tree.container.pack(fill="both", expand=True)
        self.sup_char_tree.bind("<<TreeviewSelect>>", self.on_support_char)

        main = ttk.Frame(pane)
        pane.add(main, weight=1)

        card = ttk.Frame(main, style="Card.TFrame", padding=(16, 14))
        card.pack(fill="x", padx=(10, 0))
        self.sup_avatar = Avatar(card, self, size=self.px(60))
        self.sup_avatar.pack(side="left")
        info = ttk.Frame(card, style="Card.TFrame")
        info.pack(side="left", padx=(14, 0), fill="x", expand=True)
        self.sup_name = ttk.Label(info, text='未选择单位', style="CardH1.TLabel")
        self.sup_name.pack(anchor="w")
        self.sup_sub = ttk.Label(info, text='打开存档以查看记录。',
                                 style="CardMuted.TLabel")
        self.sup_sub.pack(anchor="w", pady=(2, 0))

        # the note is packed before the table so a short window shrinks the
        # table instead of pushing the buttons and the note out of sight
        ttk.Label(main, style="CardFaint.TLabel", justify="left", wraplength=700,
                  text="支援LV 1 至 5 的起始支援值分别为 0、400、1,400、2,400、3,200。"
                       "可直接勾选 C / B / A / S 记录或取消对应支援对话；"
                       "解锁状态与条件来自 GameData/Reliance.bin，按当前存档路线、部/章与已触发标记实时判定。"
                  ).pack(side="bottom", fill="x", anchor="w", padx=(12, 0), pady=(10, 0))

        body = ttk.Frame(main, style="Card.TFrame", padding=(12, 10))
        body.pack(fill="both", expand=True, padx=(10, 0), pady=(10, 0))

        acts = ttk.Frame(body, style="Card.TFrame")
        acts.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(acts, text='已确认对话（直接勾选）：', style="Card.TLabel").pack(side="left")
        self.sup_rank_vars: dict[str, tk.BooleanVar] = {}
        self.sup_rank_checks: dict[str, ttk.Checkbutton] = {}
        for r in supports.RANKS:
            var = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(acts, text=r, style="Card.TCheckbutton", variable=var,
                                 command=lambda rr=r: self.sup_toggle_rank(rr))
            cb.pack(side="left", padx=(6, 0))
            self.sup_rank_vars[r] = var
            self.sup_rank_checks[r] = cb
        ttk.Button(acts, text='清除…', style="Small.TButton",
                   command=self.sup_forget_rank).pack(side="left", padx=(12, 0))
        self.sup_write_lbl = ttk.Label(acts, text="", style="CardFaint.TLabel")
        self.sup_write_lbl.pack(side="left", padx=(12, 0))

        pts = ttk.Frame(body, style="Card.TFrame")
        pts.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(pts, text='支援值：', style="Card.TLabel").pack(side="left")
        self.sup_pt_var = tk.StringVar(value="")
        self.sup_pt_spin = ttk.Spinbox(pts, from_=0, to=supports.max_points(), increment=50,
                                       width=8, textvariable=self.sup_pt_var,
                                       command=self.sup_apply_points)
        self.sup_pt_spin.pack(side="left", padx=(8, 0))
        self.sup_pt_spin.bind("<Return>", lambda _e: (self.sup_apply_points(), "break")[1])
        ttk.Button(pts, text='应用', style="Small.TButton",
                   command=self.sup_apply_points).pack(side="left", padx=(6, 0))
        ttk.Button(pts, text='最大', style="Small.TButton",
                   command=lambda: self.sup_set_points(supports.max_points())
                   ).pack(side="left", padx=(6, 0))
        ttk.Button(pts, text="0", width=3, style="Small.TButton",
                   command=lambda: self.sup_set_points(0)).pack(side="left", padx=(6, 0))
        self.sup_pt_lbl = ttk.Label(pts, text="", style="CardFaint.TLabel")
        self.sup_pt_lbl.pack(side="left", padx=(12, 0))

        cond_box = ttk.Frame(body, style="Card.TFrame")
        cond_box.pack(side="bottom", fill="x", pady=(8, 0))
        self.sup_cond_lbl = ttk.Label(
            cond_box,
            text="选择一组支援对象，查看各等级（C / B / A / S）的路线、章节窗口与前置/顺序锁条件。",
            style="CardMuted.TLabel", justify="left", wraplength=720,
        )
        self.sup_cond_lbl.pack(anchor="w", fill="x")

        self.sup_tree = scrolled_tree(body, columns=("jp", "points", "lv", "next", "max", "seen", "cond", "party"),
                                      show="tree headings", selectmode="browse")
        self.sup_tree.heading("#0", text='支援对象', anchor="w")
        self.sup_tree.heading("jp", text='日文', anchor="w")
        self.sup_tree.heading("points", text='支援值', anchor="e")
        self.sup_tree.heading("lv", text='支援LV', anchor="center")
        self.sup_tree.heading("next", text='下级支援值阈值', anchor="e")
        self.sup_tree.heading("max", text='最高等级', anchor="center")
        self.sup_tree.heading("seen", text='已确认对话', anchor="w")
        self.sup_tree.heading("cond", text='解锁状态', anchor="w")
        self.sup_tree.heading("party", text='在队伍中', anchor="center")
        self.sup_tree.column("#0", width=150, minwidth=110)
        self.sup_tree.column("jp", width=110)
        self.sup_tree.column("points", width=62, anchor="e", stretch=False)
        self.sup_tree.column("lv", width=44, anchor="center", stretch=False)
        self.sup_tree.column("next", width=88, anchor="e", stretch=False)
        self.sup_tree.column("max", width=62, anchor="center", stretch=False)
        self.sup_tree.column("seen", width=92, stretch=False)
        self.sup_tree.column("cond", width=118, stretch=False)
        self.sup_tree.column("party", width=62, anchor="center", stretch=False)
        self.sup_tree.container.pack(fill="both", expand=True)
        self.sup_tree.bind("<<TreeviewSelect>>", self.on_support_pair)

    def _party_pids(self) -> set[str]:
        try:
            return {u.pid for u in self._unit_map().values() if getattr(u, "pid", "")}
        except Exception:  # noqa: BLE001 — never break the page over a bad record
            return set()

    def _support_flags(self) -> set[int]:
        if not self.sv:
            return set()
        key = (self.sv.path, id(self.sv.payload))
        cache = getattr(self, "_sup_flag_cache", None)
        if cache and cache[0] == key:
            return cache[1]
        try:
            f = self.core_of()
            flags = {x.hash for m in core.flag_maps(f) for x in m.flags}
        except Exception:  # noqa: BLE001 — the byte scan is only a fallback
            flags = supports.present_flags(bytes(self.sv.payload))
        self._sup_flag_cache = (key, flags)
        return flags

    def _support_points(self):
        """{pair hash: (points, extra, offset)} from the save's GameUserData."""
        if not self.sv:
            return None
        key = (self.sv.path, id(self.sv.payload))
        cache = getattr(self, "_sup_pt_cache", None)
        if cache and cache[0] == key:
            return cache[1]
        try:
            f = self.core_of()
            blk = f.block("GameUserData")
            table = supports.find_table(f.payload, blk.start, blk.end) if blk else None
        except Exception:  # noqa: BLE001 — never break the page over a missing table
            table = None
        self._sup_pt_cache = (key, table)
        return table

    def _support_context(self) -> tuple[int | None, int | None, int | None]:
        """Current save (route, part, chapter) from Header, or (None, None, None)."""
        if not self.sv:
            return None, None, None
        try:
            f = self.core_of()
            hdr = f.block("Header")
            if hdr and hdr.end - hdr.start >= 15:
                route, part, chapter = f.payload[hdr.start + 12:hdr.start + 15]
                return int(route), int(part), int(chapter)
        except Exception:  # noqa: BLE001
            pass
        return None, None, None

    def _sup_chars(self) -> list[tuple[str, dict]]:
        """Every character the point table knows, with the conversation chart merged in."""
        chart = supports.by_char()
        out: list[tuple[str, dict]] = []
        seen: set[str] = set()
        for jp, en in supports.point_chars():
            rec = chart.get(jp) or {}
            out.append((jp, {"jp": jp, "en": rec.get("en") or en or jp,
                             "partners": rec.get("partners") or []}))
            seen.add(jp)
        for jp, rec in chart.items():
            if jp not in seen:
                out.append((jp, rec))
        out.sort(key=lambda kv: (kv[1]["en"] or kv[0]).lower())
        return out

    def _top_points(self, jp: str, points) -> int | None:
        """The pair with the most points this character has in this save."""
        if not points:
            return None
        best = 0
        for pa in supports.point_partners().get(jp, ()):
            best = max(best, supports.points_of(points, pa["hash"]) or 0)
        return best

    def _sup_partners(self, jp: str, rec: dict, points) -> list[dict]:
        """Every partner of ``jp`` with its points, richest support first."""
        chart = {p["jp"]: p for p in (rec.get("partners") or [])}
        table = supports.point_partners().get(jp) or []
        src = ([{"jp": p["jp"], "en": p["en"], "hash": p["hash"]} for p in table]
               or [{"jp": p["jp"], "en": p["en"], "hash": None}
                   for p in (rec.get("partners") or [])])
        out = []
        for p in src:
            out.append({"jp": p["jp"], "en": p["en"] or p["jp"], "hash": p["hash"],
                        "points": supports.points_of(points, p["hash"]),
                        "chart": chart.get(p["jp"]) or {}})
        out.sort(key=lambda r: (-(r["points"] or 0), r["en"].lower()))
        return out

    def refresh_supports(self):
        self._sup_flag_cache = None
        self._sup_pt_cache = None
        self.refresh_support_chars()
        self.on_support_char()

    @staticmethod
    def _support_name(jp, en):
        meta = layer.unit_ids().get(core.id_hash("PID_" + jp)) or {}
        return meta.get("name") or CHARACTERS.get(en, en or jp)

    def refresh_support_chars(self):
        if not hasattr(self, "sup_char_tree"):
            return
        q = self.sup_search.get_query().strip().lower()
        party = self._party_pids() if (self.sv and self.sup_party_only.get()) else None
        keep = getattr(self, "_sup_char", None)
        points = self._support_points()
        self.sup_char_tree.delete(*self.sup_char_tree.get_children())
        self._sup_rows = {}
        iids: dict[str, str] = {}
        first = None
        n = 0
        for jp, rec in self._sup_chars():
            original = rec["en"] or jp
            en = self._support_name(jp, original)
            if q and q not in en.lower() and q not in original.lower() and q not in jp.lower():
                continue
            if party is not None and ("PID_" + jp) not in party:
                continue
            top = self._top_points(jp, points)
            iid = self.sup_char_tree.insert("", "end", text=" " + en,
                                            image=self.unit_list_icon(en, "PID_" + jp, name_en=original),
                                            values=(len(rec.get("partners") or []),
                                                    "" if top is None else f"{top:,}"))
            self._sup_rows[iid] = (jp, rec)
            iids[jp] = iid
            n += 1
            first = first or iid
        self.sup_count.configure(text=f"{n} 个单位" + ("（本存档中）" if party is not None else ""))
        target = iids.get(keep or "") or first
        if target:
            self.sup_char_tree.selection_set(target)
            self.on_support_char()
        else:
            self._sup_char = None
            self.sup_tree.delete(*self.sup_tree.get_children())
            self.sup_name.configure(text='没有可显示的内容')
            self.sup_sub.configure(text="请打开存档，或取消“仅显示此存档中的单位”。")

    def on_support_char(self, _e=None):
        sel = self.sup_char_tree.selection() if hasattr(self, "sup_char_tree") else ()
        rows = getattr(self, "_sup_rows", {})
        if not sel or sel[0] not in rows:
            return
        jp, rec = rows[sel[0]]
        en = self._support_name(jp, rec["en"] or jp)
        if getattr(self, "_sup_char", None) != jp:
            self._sup_partner = None          # new character: don't keep the old partners
            self._sup_keep = []
        self._sup_char = jp
        self.sup_name.configure(text=en)
        self.sup_avatar.show(en, "PID_" + jp, name_en=rec["en"] or "")

        present = self._support_flags()
        points = self._support_points()
        party = self._party_pids()
        route, part, chapter = self._support_context()
        keep = getattr(self, "_sup_partner", None)
        plist = self._sup_partners(jp, rec, points)
        self.sup_tree.delete(*self.sup_tree.get_children())
        self._sup_pairs = {}
        self._sup_pt = {}
        self._sup_eval = {}
        iids: dict[str, str] = {}
        seen_pairs = 0
        for pa in plist:
            got = [r for r in supports.RANKS if supports.seen(pa["chart"], present).get(r)]
            if got:
                seen_pairs += 1
            pts = pa["points"]
            partner_name = self._support_name(pa["jp"], pa["en"])
            ev = supports.evaluate_pair(
                pa["chart"],
                points=pts,
                present_flags=present,
                party_pids=party,
                route=route,
                part=part,
                chapter=chapter,
                name_fn=self._support_name,
            )
            st = ev["status"]
            if st in ("locked_order", "expired"):
                tag = "sup_danger"
            elif st == "available":
                tag = "sup_avail"
            elif got or pts:
                tag = "sup_seen"
            else:
                tag = "sup_none"
            iid = self.sup_tree.insert(
                "", "end", text=partner_name,
                values=(pa["jp"],
                        "—" if pts is None else f"{pts:,}",
                        "—" if pts is None else supports.level_of(pts),
                        self._next_level_text(pts),
                        pa["chart"].get("max") or "—",
                        ", ".join(got) if got else "—",
                        ev["badge"],
                        "✓" if ("PID_" + pa["jp"]) in party else ""),
                tags=(tag,))
            self._sup_pairs[iid] = ({"a_en": en, "b_en": partner_name},
                                    dict(pa["chart"].get("flags") or {}))
            self._sup_eval[iid] = ev
            if pa["hash"] is not None:
                self._sup_pt[iid] = (pa["hash"], pts, pa["jp"])
            iids[pa["jp"]] = iid
        self._paint_support_tags()
        best = max((pa["points"] or 0) for pa in plist) if plist else 0
        self.sup_sub.configure(
            text=f"{len(plist)} 组支援 · 最高支援值 {best:,} · 本存档已记录 {seen_pairs} 组对话")
        if iids:
            # keep every partner that was selected (an edit rebuilds the list
            # and re-sorts it by points), else the remembered one, else the top
            kept = [iids[j] for j in (getattr(self, "_sup_keep", None) or []) if j in iids]
            if not kept:
                kept = [iids.get(keep or "") or next(iter(iids.values()))]
            target = kept[0]
            self._sup_partner = next(k for k, v in iids.items() if v == target)
            self.sup_tree.selection_set(kept)
            self.sup_tree.focus(target)
            self.sup_tree.see(target)
            self.on_support_pair()

    @staticmethod
    def _next_level_text(pts) -> str:
        if pts is None:
            return "—"
        nxt = supports.next_level_at(pts)
        return "已满级" if nxt is None else f"{nxt:,}"

    def _sync_support_checks(self, sel):
        if not hasattr(self, "sup_rank_checks"):
            return
        rows = getattr(self, "_sup_pairs", {})
        picked = [rows[i][1] for i in sel if i in rows]
        present = self._support_flags() if self.sv else set()
        for r in supports.RANKS:
            cb = self.sup_rank_checks[r]
            var = self.sup_rank_vars[r]
            hashes = [fl[r] for fl in picked if (fl or {}).get(r)]
            if not hashes:
                var.set(False)
                cb.state(["disabled"])
            else:
                cb.state(["!disabled"])
                var.set(bool(self.sv) and all(h in present for h in hashes))

    def on_support_pair(self, _e=None):
        """Mirror the selected pair's points, rank checkboxes, and unlock conditions."""
        if not hasattr(self, "sup_pt_var"):
            return
        sel = self.sup_tree.selection() if hasattr(self, "sup_tree") else ()
        pt = getattr(self, "_sup_pt", {})
        ev_map = getattr(self, "_sup_eval", {})
        if sel:
            # remember the selection by name so a rebuild can restore it
            self._sup_keep = [self.sup_tree.item(i, "values")[0] for i in sel]
            self._sup_partner = self._sup_keep[0]
        self._sync_support_checks(sel)

        # Update condition detail panel
        if hasattr(self, "sup_cond_lbl"):
            if not sel:
                self.sup_cond_lbl.configure(
                    text="选择一组支援对象，查看各等级（C / B / A / S）的路线、章节窗口与前置/顺序锁条件。")
            elif len(sel) > 1:
                self.sup_cond_lbl.configure(
                    text=f"已选择 {len(sel)} 组支援：直接勾选 C / B / A / S 可批量记录或清除对应支援对话。")
            else:
                ev = ev_map.get(sel[0]) or {}
                ranks = ev.get("ranks") or []
                if not ranks:
                    self.sup_cond_lbl.configure(text="解锁条件：这对角色在游戏数据中没有支援对话。")
                else:
                    lines = []
                    for rk in ranks:
                        tag = f"[{rk['status_label']}]" if rk["status"] != "unknown" else ""
                        line = f"• {rk['rank']} {tag}：{rk['summary']}"
                        if rk["reasons"] and rk["status"] not in ("seen", "available", "unknown"):
                            line += f"  → 未满足：{'；'.join(rk['reasons'])}"
                        elif rk["warnings"] and rk["status"] != "seen":
                            line += f"  ⚠ {'；'.join(rk['warnings'])}"
                        lines.append(line)
                    self.sup_cond_lbl.configure(text="\n".join(lines))

        if not sel or sel[0] not in pt:
            self.sup_pt_var.set("")
            self.sup_pt_lbl.configure(text="")
            return
        if len(sel) > 1:
            n = sum(1 for i in sel if i in pt)
            self.sup_pt_lbl.configure(
                text=f"已选择 {len(sel)} 组支援：应用 / 最大 / 0 会同时设置其中 {n} 组")
            return
        _h, pts, _jp = pt[sel[0]]
        self.sup_pt_var.set("" if pts is None else str(pts))
        name = self.sup_tree.item(sel[0], "text")
        self.sup_pt_lbl.configure(
            text=(f"{name}：支援值 {pts:,} / {supports.max_points():,} · "
                  f"支援LV {supports.level_of(pts)}"
                  + (f"，下一等级 {supports.next_level_at(pts):,}"
                     if supports.next_level_at(pts) else "（满级）"))
            if pts is not None else f"{name}：此存档中没有记录")

    def _paint_support_tags(self):
        p = self.p
        if hasattr(self, "sup_tree"):
            self.sup_tree.tag_configure("sup_seen", foreground=p["fg"])
            self.sup_tree.tag_configure("sup_none", foreground=p["muted"])
            self.sup_tree.tag_configure("sup_avail", foreground=p["ok"])
            self.sup_tree.tag_configure("sup_danger", foreground=p["danger"])

    def _bind_keys(self):
        self.bind_all("<Control-s>", lambda e: self.save_all())
        self.bind_all("<Control-o>", lambda e: self.open_dialog())
        self.bind_all("<Control-f>", self._focus_search)
        self.bind_all("<Control-a>", self._select_all_focused)
        self.bind_all("<F5>", lambda e: self.reload())
        self.bind_all("<Control-z>", lambda e: (self.undo(), "break")[1])
        self.bind_all("<Control-y>", lambda e: (self.redo(), "break")[1])
        self.bind("<FocusIn>", lambda e: self.after_idle(self._check_disk)
                  if e.widget is self else None)

    def _select_all_focused(self, _e=None):
        """Ctrl+A in a multi-select table selects every row; elsewhere it stays normal."""
        w = self.focus_get()
        if not isinstance(w, ttk.Treeview) or w is getattr(self, "nav_tree", None):
            w = last_tree()
        if isinstance(w, ttk.Treeview) and str(w.cget("selectmode")) == "extended":
            rows = w.get_children("")
            if rows:
                w.selection_set(rows)
                w.focus(rows[-1])
            return "break"
        return None

    def _focus_search(self, _e=None):
        """Ctrl+F jumps to the search box of whatever page is showing."""
        sel = self.pages.select()
        target = None
        if sel == str(self.units_page):
            tab = self.unit_tabs.index(self.unit_tabs.select())
            target = {2: self.inv_picker.search, 3: self.class_search}.get(tab, self.unit_search)
        elif sel == str(self.convoy_page):
            target = self.conv_picker.search
        elif sel == str(self.supports_page):
            target = self.sup_search
        elif sel == str(self.flags_page):
            target = self.flag_search
        elif sel == str(self.classes_page):
            target = self.cls_search
        elif sel == str(self.mounts_page):
            target = None
        elif sel == str(self.world_page):
            target = self.map_search
        elif sel == str(self.tutorials_page):
            target = self.tutorial_search
        elif sel == str(self.fast_travel_page):
            target = self.fast_travel_search
        elif sel == str(self.facilities_page):
            target = self.facility_search
        elif sel == str(self.deeds_page):
            target = self.deed_search
        elif sel == str(self.boons_page):
            target = self.boon_search
        elif sel == str(self.quests_page):
            target = self.quest_search
        if target is None:
            return
        target.focus_set()
        target.select_range(0, "end")
        return "break"

    def flash(self, msg: str, kind: str = "info"):
        lbl = getattr(self, "status_lbl", None)
        if lbl is None or not lbl.winfo_exists():
            return                      # window already closing
        style = {"info": "Status.TLabel", "warn": "StatusWarn.TLabel",
                 "ok": "StatusOk.TLabel", "error": "StatusErr.TLabel"}[kind]
        lbl.configure(text=msg, style=style)

    def _mirror_changed(self):
        self._save_settings()
        self.flash("编辑现在会同时写入此线路的其他存档（绝不写入章节检查点）。"
                   if self.mirror.get() else "编辑现在只写入当前选中的槽位。")

    def _show_welcome(self):
        self.unit_name.configure(text='未打开存档文件夹')
        self.unit_sub.configure(text="请使用“打开…”（Ctrl+O），选择存档文件夹中的"
                                     "任意槽位文件（Auto、Chapter_*、Route_*）。")
        self.flash("未自动找到 Eden 存档文件夹——请手动打开一个再开始。", "warn")

    # ============================================================== files
    def _slot_files(self, folder: str) -> list[str]:
        out = []
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if is_slot_name(name) and os.path.isfile(path):
                try:
                    out.append((path, os.path.getmtime(path)))
                except OSError:
                    continue  # A game save can disappear between listing and stat.
        # Auto (the live save) first, then the rest newest first
        out.sort(key=lambda row: (os.path.basename(row[0]) != "Auto", -row[1]))
        return [path for path, _ in out]

    def _get(self, path: str) -> SaveFile | None:
        """Cached SaveFile; re-read from disk when the game changed it (and we didn't)."""
        key = os.path.abspath(path)
        sv = self.files.get(key)
        try:
            mtime = os.path.getmtime(key)
        except OSError:
            return sv
        if sv is not None and (key in self.dirty or self.mtimes.get(key) == mtime):
            return sv
        try:
            sv = SaveFile(key)
        except Exception as exc:  # noqa: BLE001 — any unreadable slot is just skipped
            self.files.pop(key, None)
            self.flash(f"无法读取 {os.path.basename(key)}：{exc}", "error")
            return None
        self.files[key] = sv
        self.mtimes[key] = mtime
        return sv

    def open_dialog(self):
        if not self._confirm_discard("open another folder"):
            return
        start = self.folder or default_save_dir() or os.path.expanduser("~")
        # a directory picker, not a file picker: the slot files have no
        # extension, and the glob patterns Tk filetypes accept don't match
        # them in the macOS panel (every slot was greyed out), so there was
        # no way to pick anything at all
        folder = filedialog.askdirectory(parent=self, initialdir=start,
                                         title="选择存档文件夹")
        if not folder:
            return
        self.open_folder(folder)

    def open_folder(self, folder: str, prefer: str | None = None):
        slots = self._slot_files(folder)
        if not slots and prefer and os.path.isfile(prefer):
            slots = [prefer]
        if not slots:
            messagebox.showerror(APP_NAME, f"文件夹中没有 Auto / Chapter_* / Route_* 槽位：\n{folder}")
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            self.folder = folder
            self.files.clear()
            self.mtimes.clear()
            self.dirty.clear()
            self.slot_paths = [os.path.abspath(p) for p in slots]
            for pth in self.slot_paths:
                self._get(pth)
            self._save_settings()
            target = os.path.abspath(prefer) if prefer else self._default_slot()
            if target not in self.slot_paths:
                target = self._default_slot()
            self._populate_slot_box()
            self.load(target)
        finally:
            self.config(cursor="")

    def _default_slot(self) -> str:
        """Auto is the live save the game continues from; open it first."""
        for pth in self.slot_paths:
            if os.path.basename(pth) == "Auto":
                return pth
        return self.slot_paths[0]

    def _populate_slot_box(self):
        # closing the window with a modal dialog open lets the pending edit
        # callback run after the header is gone; nothing to update then
        if not getattr(self, "slot_box", None) or not self.slot_box.winfo_exists():
            return
        labels = []
        for pth in self.slot_paths:
            sv = self.files.get(pth)
            mark = "● " if pth in self.dirty else ""
            base = os.path.basename(pth)
            star = "★ " if base == "Auto" else ""          # the live save
            # which lord / part / chapter the slot was saved in, read from the save
            route = layer.route_label(sv.payload) if sv else ""
            try:
                stamp = _fmt_time(os.path.getmtime(pth))
            except FileNotFoundError:
                stamp = "磁盘上已丢失——使用缓存副本" if sv else "磁盘上已丢失"
            except OSError:
                stamp = "磁盘上无法读取"
            labels.append(f"{mark}{star}{base}  ·  {route + '  ·  ' if route else ''}"
                          f"{stamp}")
        self.slot_box.configure(values=labels)
        if self.sv is not None and self.sv.path in self.slot_paths:
            self.slot_box.current(self.slot_paths.index(self.sv.path))

    def on_slot_pick(self, _e=None):
        i = self.slot_box.current()
        if 0 <= i < len(self.slot_paths):
            self._commit_form()
            self.load(self.slot_paths[i])

    def load(self, path: str):
        path = os.path.abspath(path)
        sv = self._get(path)
        if sv is None:
            return
        keep = self.uid
        self.sv = sv
        self.uid = None
        if path in self.slot_paths:
            self.slot_box.current(self.slot_paths.index(path))
        self._update_file_label()
        peers = [os.path.basename(p) for p in self._peers()]
        if self._is_checkpoint(path):
            text = "同时编辑此线路的其他存档（检查点中不生效）"
        elif peers:
            text = f"同时编辑此线路的其他存档（+ {', '.join(peers)}）"
        else:
            text = "同时编辑此线路的其他存档（本文件夹没有）"
        self.mirror_chk.configure(text=text)
        self.refresh_units(keep=keep)
        self.refresh_convoy()
        self.locate_gold()
        self.refresh_supports()
        self.refresh_core_pages()
        self.refresh_mounts()
        self._update_dirty()
        self.flash(f"已打开 {os.path.basename(path)} —— {self._visible_units()} 个单位。")

    def _update_file_label(self):
        if not self.sv:
            self.file_lbl.configure(text="")
            return
        bad = self.sv.verify() if self.sv.path not in self.dirty else []
        folder = os.path.normpath(self.folder or os.path.dirname(self.sv.path))
        parts = folder.split(os.sep)
        shown = folder if len(parts) <= 4 else os.sep.join(["…"] + parts[-3:])
        self.file_lbl.configure(text=f"{shown}" + (f"   ⚠ {', '.join(bad)}" if bad else ""))

    def _changed_on_disk(self, path: str) -> bool:
        try:
            return os.path.getmtime(path) != self.mtimes.get(path)
        except OSError:
            return False

    def _check_disk(self, _e=None):
        """When the window comes back into focus, pick up saves the game made.

        A slot with no unsaved edits is simply re-read.  One with unsaved edits
        is left alone, and the warning in save_all() stops it overwriting the
        game's newer file.
        """
        if not self.folder or getattr(self, "_checking", False):
            return
        self._checking = True
        try:
            changed = [p for p in self.slot_paths if self._changed_on_disk(p)]
            if not changed:
                self._populate_slot_box()  # Refresh missing/renamed-file labels too.
                return
            clean = [p for p in changed if p not in self.dirty]
            if clean:
                for p in clean:
                    self.files.pop(p, None)
                    self._get(p)
                self._populate_slot_box()
                if self.sv is not None and self.sv.path in clean:
                    self._shown, self._skill_rows = {}, []
                    self.load(self.sv.path)
                self.flash("已读取游戏新保存的存档："
                           + ", ".join(os.path.basename(p) for p in clean), "ok")
            if len(clean) < len(changed):
                self.flash("游戏保存了你有未保存编辑的槽位。现在保存会覆盖游戏的进度；"
                           "请按“重新加载”，从游戏的存档重新开始。", "warn")
        finally:
            self._checking = False

    def _confirm_discard(self, what: str) -> bool:
        what = {"quit": "退出", "open another folder": "打开其他文件夹"}.get(what, what)
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel(
            APP_NAME, f"{len(self.dirty)} 个槽位有未保存的更改。\n\n"
                      f"要在{what}之前保存吗？", parent=self)
        if ans is None:
            return False
        if ans:
            return self.save_all()
        return True

    def reload(self):
        if not self.folder:
            return
        if self.dirty and not messagebox.askyesno(
                APP_NAME, f"从磁盘重新读取，并丢弃 {len(self.dirty)} 个槽位的未保存更改？",
                parent=self):
            return
        cur = self.sv.path if self.sv else None
        self._shown, self._skill_rows = {}, []    # don't write the form into fresh data
        self.open_folder(self.folder, prefer=cur)
        self.flash("已从磁盘重新读取全部槽位。", "ok")

    def save_all(self) -> bool:
        if not self.sv:
            return False
        self._commit_form()
        if not self.dirty:
            self.flash("没有未保存的更改。")
            return True
        missing = [p for p in self.dirty if not os.path.isfile(p)]
        if missing:
            messagebox.showerror(APP_NAME,
                "Nothing saved. These edited slots were removed or renamed on disk:\n\n"
                + "\n".join(os.path.basename(p) for p in sorted(missing))
                + "\n\nYour edits are still in memory. Restore the original file(s) before saving, "
                  "or use Reload to discard the edits and read the current folder.", parent=self)
            self._populate_slot_box()
            return False
        import story_files
        try:
            chapter_writes = story_files.prepare(self.files, self.dirty)
        except Exception as exc:  # noqa: BLE001 — preflight must not write anything
            messagebox.showerror(APP_NAME, f"未保存：\n\n{exc}", parent=self)
            return False
        for plan in chapter_writes.values():
            if plan.previous is not None and not messagebox.askyesno(
                    "替换章节起始文件？",
                    f"{os.path.basename(plan.target)} 已存在。\n\n"
                    "要用本次暂存的章节起始状态替换它吗？现有文件会先保留为 .bak"
                    "（若已存在更早的备份，则使用下一个编号 .bak）。\n\n"
                    "选“否”将取消本次保存；所有编辑和章节文件请求仍保持待处理。",
                    parent=self, icon="warning"):
                self.flash("未保存——已保留现有的章节文件。", "warn")
                return False
        newer = [p for p in self.dirty if self._changed_on_disk(p)]
        if newer and not messagebox.askyesno(
                APP_NAME, "自编辑器读取以来，游戏又保存了 "
                + ", ".join(os.path.basename(p) for p in newer)
                + "。\n\n现在保存会用编辑器手中的旧副本（加上你的编辑）覆盖那份更新的存档，"
                  "游戏内的进度将会丢失。\n\n仍要覆盖吗？（选“否”=保留游戏的存档；"
                  "然后按“重新加载”，重新做你的编辑。）", icon="warning", parent=self):
            self.flash("未保存——已保留游戏更新的存档。请按“重新加载”后重新编辑。", "warn")
            return False
        written, failed = [], []
        for pth in sorted(self.dirty, key=os.path.basename):
            sv = self.files.get(pth)
            if not sv:
                continue
            try:
                plan = chapter_writes.get(pth)
                if plan:
                    story_files.check_unchanged(plan)
                    if story_files.path_key(plan.target) != story_files.path_key(pth):
                        story_files.write(plan)
                        # A completed file is not repeated if the source save fails.
                        story_files.completed(self, sv)
                        self.files.pop(plan.target, None)
                        self._get(plan.target)
                        if plan.target not in self.slot_paths:
                            self.slot_paths.append(plan.target)
                        written.append(os.path.basename(plan.target))
                sv.save(pth)
                if plan:
                    story_files.completed(self, sv)
                self.mtimes[pth] = os.path.getmtime(pth)
                self.dirty.discard(pth)
                written.append(os.path.basename(pth))
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{os.path.basename(pth)}: {exc}")
        self._populate_slot_box()
        self._update_file_label()
        self._update_dirty()
        self.refresh_story()
        if failed:
            messagebox.showerror(APP_NAME, "以下槽位写入失败：\n\n"
                                 + "\n".join(failed) + "\n\n游戏是否仍在运行？")
            return False
        self.flash(f"已保存 {', '.join(written)}。被替换的存档副本会以编号 .bak 保存在旁边。",
                   "ok")
        return True

    def on_close(self):
        self._commit_form()
        if not self._confirm_discard("quit"):
            return
        self._save_settings()
        self.destroy()

    def _update_dirty(self):
        n = len(self.dirty)
        if n:
            self.dirty_lbl.configure(text=f"● {n} 个槽位有未保存更改  ·  Ctrl+S 保存",
                                     style="StatusWarn.TLabel")
            self.save_btn.configure(text=f"保存 ({n})", state="normal")
        else:
            self.dirty_lbl.configure(text="所有更改已保存" if self.sv else "",
                                     style="Status.TLabel")
            self.save_btn.configure(text='保存', state="normal" if self.sv else "disabled")
        self.revert_btn.configure(state="normal" if self.folder else "disabled")
        name = os.path.basename(self.sv.path) if self.sv else ""
        self.title(f"{'● ' if n else ''}{name + ' — ' if name else ''}{APP_NAME}")

    # ---------------------------------------------------------- targets
    @staticmethod
    def _party(sv: SaveFile) -> set[int]:
        return {u.uid for u in sv.units if not is_enemy(u)}

    def _same_route(self, other: SaveFile) -> bool:
        """Match explicit character, part and playthrough, not party overlap."""
        a, b = layer.route_info(self.sv.payload), layer.route_info(other.payload)
        if not a or not b or a[:2] != b[:2]:
            return False
        sa = core.read_settings(core.SaveFile(self.sv.payload))
        sb = core.read_settings(core.SaveFile(other.payload))
        return sa is not None and sb is not None and sa[0] == sb[0]

    @staticmethod
    def _is_checkpoint(path: str) -> bool:
        """Chapter_* slots are chapter checkpoints the game can rewind to."""
        return os.path.basename(path).startswith("Chapter_")

    def _peers(self) -> list[str]:
        """Other slots an edit is mirrored into: the same route's live saves.

        Chapter_* checkpoints are never included — they are the points the
        game's "go back in time" feature returns to, so each keeps its own
        state.  When the open slot is itself a checkpoint, nothing is
        mirrored: editing one point in time leaves the others alone.
        """
        if not self.sv or self._is_checkpoint(self.sv.path):
            return []
        out = []
        for pth in self.slot_paths:
            if pth == self.sv.path or self._is_checkpoint(pth) or not os.path.isfile(pth):
                continue
            sv = self._get(pth)
            if sv is not None and self._same_route(sv):
                out.append(pth)
        return out

    def _targets(self) -> list[SaveFile]:
        """The open slot, plus the same route's live saves when mirroring."""
        if not self.sv:
            return []
        out = [self.sv]
        if self.mirror.get():
            out += [self.files[p] for p in self._peers()]
        return out

    def _touch(self, svs):
        before = set(self.dirty)
        for sv in svs:
            self.dirty.add(sv.path)
        if self.dirty != before:
            self._populate_slot_box()
        self._update_dirty()

    def _read_only(self) -> bool:
        """Enemy / NPC records are only partly understood (their stat block is
        found one field off for some), so they can be viewed but not edited."""
        u = self._current()
        if u is not None and is_enemy(u):
            self.flash("敌人与 NPC 的记录为只读——其布局尚未完全解析，修改可能写错位置。",
                       "warn")
            return True
        return False

    def _edit_units(self, fn) -> tuple[int, int]:
        """fn(record, savefile) -> bool for every copy of the shown unit.

        Returns (copies changed, slots changed).
        """
        if self._read_only():
            return 0, 0
        self.push_undo()
        copies, touched = 0, []
        for sv in self._targets():
            hit = 0
            for r in sv.units:
                if r.uid == self.uid and fn(r, sv) is not False:
                    hit += 1
            if hit:
                copies += hit
                touched.append(sv)
        self._touch(touched)
        return copies, len(touched)

    def _where(self, copies: int, slots: int) -> str:
        c = f"{copies} 处记录"
        return c if slots <= 1 else f"{c}，{slots} 个槽位"

    # ============================================================== units
    def _unit_map(self) -> dict[int, object]:
        groups: dict[int, list] = {}
        for u in (self.sv.units if self.sv else []):
            groups.setdefault(u.uid, []).append(u)
        return {uid: pick_live(recs) for uid, recs in groups.items()}

    def refresh_units(self, keep: int | None = None):
        if keep is None:
            keep = self.uid
        tree = self.unit_tree
        tree.delete(*tree.get_children())
        q = self.unit_search.get_query().lower()
        units = sorted(self._unit_map().values(),
                       key=lambda u: (is_enemy(u), u.name.lower()))
        import unit_entries
        core_file = self.core_of() if self.sv else None
        try:
            numbers = {u.person: g.unit_number for u, g in unit_entries.roster(core_file)} if core_file else {}
        except core.SaveError:
            numbers = {}
        cflags = layer.costume_flags(core_file)
        shown = 0
        for u in units:
            enemy = is_enemy(u)
            if enemy and not self.show_enemies.get():
                continue
            original = (layer.unit_ids().get(u.uid) or {}).get("name_en", "")
            if q and q not in f"{u.name} {original} {u.pid} {u.job}".lower():
                continue
            cval = layer.unit_costume(cflags, u.pid, u.uid)
            ctext = "—" if (enemy and cval is None) else layer.costume_label(cval, short=True)
            tree.insert("", "end", iid=str(u.uid), text=" " + u.name,
                        image=self.unit_list_icon(u.name, u.pid, uid=u.uid, name_en=original),
                        values=(numbers.get(u.uid, '—'), ctext, u.level), tags=("enemy",) if enemy else ())
            shown += 1
        total = sum(1 for u in units if self.show_enemies.get() or not is_enemy(u))
        self.unit_count.configure(text=f"{shown}" if shown == total else f"{shown} / {total}")
        kids = tree.get_children()
        if keep is not None and tree.exists(str(keep)):
            pick = str(keep)
        else:
            pick = kids[0] if kids else None
        if pick:
            tree.selection_set(pick)
            tree.see(pick)
            if self.uid != int(pick):
                self.on_unit_select()
            else:
                self._update_costume_chip(self._current(), cflags)
        else:
            self._clear_unit_form()

    def _tree_lv(self, uid: int, lv: int):
        if self.unit_tree.exists(str(uid)):
            self.unit_tree.set(str(uid), "lv", lv)

    def _clear_unit_form(self):
        self.uid = None
        self._shown = {}
        self._skill_rows = []
        self.avatar.show("")
        self.unit_name.configure(text="未选择单位" if self.sv else "未打开存档文件夹")
        self.costume_chip.configure(text="")
        self.costume_chip.pack_forget()
        self.costume_btn.pack_forget()
        self.unit_sub.configure(text="" if self.sv else "请先打开存档文件夹。")
        self.copies_chip.configure(text="")
        self.sync_btn.pack_forget()
        for v in self.stat_vars + [self.lvl_var, self.exp_var]:
            v.set("")
        for tree in getattr(self, "_skill_trees", {}).values():
            tree.delete(*tree.get_children())
        self.inv_tree.delete(*self.inv_tree.get_children())
        self.class_now.configure(text="—")

    def _current(self):
        if not self.sv or self.uid is None:
            return None
        recs = [r for r in self.sv.units if r.uid == self.uid]
        return pick_live(recs) if recs else None

    def on_unit_select(self, _e=None):
        sel = self.unit_tree.selection()
        if not sel:
            return
        uid = int(sel[0])
        if uid != self.uid:
            self._commit_form()       # flush a half-typed value into the PREVIOUS unit
        self.uid = uid
        self.show_unit()

    def show_unit(self):
        u = self._current()
        if not u:
            self._clear_unit_form()
            return
        self.avatar.show(u.name, u.pid, uid=u.uid)
        self.unit_name.configure(text=u.name)
        pid = u.pid.replace("PID_", "")
        self.unit_sub.configure(text=f"{u.job}   ·   LV {u.level}   ·   {pid}")
        # what the form shows; a field only writes when it differs from this, so
        # tabbing through fields never copies untouched values into other slots
        self._shown = {"lvl": u.level, "exp": u.exp, **dict(enumerate(u.stats))}
        self.lvl_var.set(str(u.level))
        self.exp_var.set(str(u.exp))
        self.exp_bar.set(min(u.exp, 100) / 100)
        self.maxhp_lbl.configure(text=str(u.max_hp) if u.max_hp else "—")
        top = max(40, max(u.stats))
        for i, v in enumerate(self.stat_vars):
            v.set(str(u.stats[i]))
            self.stat_bars[i].set(u.stats[i] / top)
        self._update_copies_chip(u)
        self._update_costume_chip(u)
        self.refresh_skills()
        self.refresh_inventory()
        self.class_now.configure(text=u.job)
        self.refresh_classes()

    def _update_costume_chip(self, u, cflags: dict[int, int] | None = None):
        if not u or not self.sv:
            self.costume_chip.configure(text="")
            self.costume_chip.pack_forget()
            self.costume_btn.pack_forget()
            return
        if cflags is None:
            cflags = layer.costume_flags(self.core_of())
        val = layer.unit_costume(cflags, u.pid, u.uid)
        enemy = is_enemy(u)
        if enemy and val is None:
            self.costume_chip.configure(text="")
            self.costume_chip.pack_forget()
            self.costume_btn.pack_forget()
            return
        style = ("ChipOk.TLabel" if val in (1, 2)
                 else "Chip.TLabel" if val in (None, 0)
                 else "ChipWarn.TLabel")
        self.costume_chip.configure(text=f"着装：{layer.costume_label(val)}", style=style)
        if not self.costume_chip.winfo_manager():
            self.costume_chip.pack(side="left", padx=(10, 0), pady=(4, 0))
        if enemy:
            self.costume_btn.pack_forget()
        elif not self.costume_btn.winfo_manager():
            self.costume_btn.pack(side="left", padx=(8, 0), pady=(3, 0))

    def open_costume_dialog(self):
        u = self._current()
        if not u or not self.sv:
            return
        import costumes
        core_file = self.core_of()
        cu = layer.unit_in(core_file, u.uid) if core_file else None
        skill_lists = cu.skill_lists if cu else []
        info = layer.route_info(self.sv.payload)
        part = info[1] if info else None
        try:
            report = costumes.inspect(core_file, u.uid, u.pid, u.klass,
                                      enemy=is_enemy(u), skill_lists=skill_lists, part=part)
        except core.SaveError as exc:
            self.flash(str(exc), "error")
            return

        win = tk.Toplevel(self)
        win.withdraw()
        win.title(f"更改着装 — {u.name}")
        win.transient(self)
        win.configure(background=self.p["bg"])
        win.columnconfigure(0, weight=1)

        body = ttk.Frame(win, style="Card.TFrame", padding=16)
        body.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        body.columnconfigure(0, weight=1)

        ttk.Label(body, text=u.name, style="CardH3.TLabel").grid(row=0, column=0, sticky="w")
        part_text = f" · 第{part}部" if part is not None else ""
        key_text = f" · 标记 {report.key} (0x{report.hash:08x})" if report.key else ""
        ttk.Label(body, text=f"当前兵种：{u.job}{part_text}{key_text}",
                  style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 8))
        ttk.Label(body, text=f"当前着装：{costumes.label(report.value)}",
                  style="CardBold.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 8))

        current_choice = 0 if report.value is None else report.value
        choice = tk.IntVar(master=win, value=current_choice if current_choice in (0, 1, 2) else 0)

        opts = ttk.Frame(body, style="Card.TFrame")
        opts.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        for val, label in (
            (0, "默认（移除着装标记） — 按游戏及当前兵种默认规则显示"),
            (1, "职业外观（兵種衣装優先 = 1） — 优先使用当前职业服装"),
            (2, "固有外观（固有衣装優先 = 2） — 优先使用角色固有服装"),
        ):
            rb = ttk.Radiobutton(opts, text=label, value=val, variable=choice)
            rb.pack(anchor="w", pady=2)
            if not report.editable:
                rb.state(["disabled"])

        if not report.editable:
            ttk.Label(body, text=f"⚠ {report.read_only_reason}", style="CardWarn.TLabel",
                      wraplength=500, justify="left").grid(row=4, column=0, sticky="w", pady=(0, 6))
        elif report.active_warnings:
            warn_text = "⚠ 检测到以下着装限制：\n" + "\n".join(f"• {w}" for w in report.active_warnings)
            ttk.Label(body, text=warn_text, style="CardWarn.TLabel",
                      wraplength=500, justify="left").grid(row=4, column=0, sticky="w", pady=(0, 6))
        else:
            ttk.Label(body, text="✓ 当前角色、兵种与剧情部数均未命中禁用限制，可自由切换。",
                      style="CardOk.TLabel", wraplength=500, justify="left"
                      ).grid(row=4, column=0, sticky="w", pady=(0, 6))

        notes = "\n".join(report.character_notes)
        if notes:
            ttk.Label(body, text=notes, style="CardFaint.TLabel",
                      wraplength=500, justify="left").grid(row=5, column=0, sticky="w", pady=(2, 10))

        bar = ttk.Frame(body, style="Card.TFrame")
        bar.grid(row=6, column=0, sticky="ew")

        def apply():
            target = choice.get()
            desired = None if target == costumes.DEFAULT else target
            slots = []
            warnings = []
            for sv in self._targets():
                name = os.path.basename(sv.path)
                try:
                    sf = self.core_of(sv)
                    su = costumes._find_unit(sf, u.uid)
                    if not su:
                        continue
                    sinfo = layer.route_info(sv.payload)
                    srep = costumes.inspect(
                        sf, u.uid, u.pid, su.klass, enemy=is_enemy(u),
                        skill_lists=su.skill_lists, part=sinfo[1] if sinfo else None,
                    )
                    if not srep.editable:
                        raise costumes.CostumeError(srep.read_only_reason)
                except core.SaveError as exc:
                    messagebox.showerror("无法更改着装", f"{name}：{exc}", parent=win)
                    return
                if srep.value == desired:
                    continue
                slots.append(sv)
                if desired is not None and srep.active_warnings:
                    warnings.extend(f"{name}：{warning}" for warning in srep.active_warnings)

            if not slots:
                win.destroy()
                self.flash("着装设置未变化。", "info")
                return
            if warnings and not messagebox.askyesno(
                    "着装限制提示",
                    f"{u.name} 的着装限制：\n\n"
                    + "\n".join(f"• {warning}" for warning in warnings)
                    + "\n\n游戏可能忽略这些槽位的着装设置。仍要应用吗？",
                    icon="warning", default="no", parent=win):
                return

            ok = self._core_edit(
                lambda f, p=u.pid, t=target, i=u.uid: costumes.edited(f, p, t, uid=i),
                f"{u.name} 着装 → {costumes.label(target)}",
                slots=slots,
            )
            if ok:
                win.destroy()
                if warnings:
                    self.flash(f"{u.name} 着装 → {costumes.label(target)}（部分槽位存在限制，按 Ctrl+S 保存）", "warn")

        apply_btn = ttk.Button(bar, text='应用', style="Accent.TButton", command=apply)
        apply_btn.pack(side="right")
        if not report.editable:
            apply_btn.state(["disabled"])
        ttk.Button(bar, text='取消', command=win.destroy).pack(side="right", padx=(0, 8))

        win.bind("<Escape>", lambda _e: win.destroy())
        self.show_dialog(win, 560, 400)

    def _update_copies_chip(self, u):
        """One record per unit now: the chip says what the game will read."""
        self.sync_btn.pack_forget()
        enemy = is_enemy(u)
        for sp in self._stat_spins:
            sp.configure(state="disabled" if enemy else "normal")
        if enemy:
            self.copies_chip.configure(text='🔒 敌人 / NPC — 只读', style="ChipWarn.TLabel")
        else:
            self.copies_chip.configure(
                text=f"当前记录 · {u.job}", style="Chip.TLabel")


    def _num(self, var, lo, hi):
        try:
            v = int(str(var.get()).replace(",", "").strip())
        except (TypeError, ValueError):
            return None
        return v if lo <= v <= hi else None

    def _commit_form(self):
        """Write any value still sitting in a field (typed but not yet applied)."""
        if self.uid is None or not self.sv:
            return
        self.commit_level()
        self.commit_exp()
        for i in range(len(STAT_NAMES)):
            self.commit_stat(i)


    def _bad_input(self, var, lo, hi, current, label):
        var.set(str(current))
        self.flash(f"{label}必须是 {lo} 到 {hi} 之间的数字。", "warn")

    def commit_level(self):
        u = self._current()
        if not u or self.lvl_var.get() == "":
            return
        v = self._num(self.lvl_var, 1, 99)
        if v is None:
            return self._bad_input(self.lvl_var, 1, 99, u.level, "LV")
        if v == self._shown.get("lvl"):
            return
        self._shown["lvl"] = v

        def fn(r, sv):
            if r.level == v:
                return False
            r.set_level(v, sv.payload)
            r.level = v
        n, s = self._edit_units(fn)
        if n:
            self._tree_lv(u.uid, v)
            self.unit_sub.configure(text=f"{u.job}   ·   LV {v}   ·   {u.pid.replace('PID_', '')}")
            self._update_copies_chip(u)
            self.flash(f"{u.name}：LV → {v}（{self._where(n, s)}）")

    def commit_exp(self):
        u = self._current()
        if not u or self.exp_var.get() == "":
            return
        v = self._num(self.exp_var, 0, 99)
        if v is None:
            return self._bad_input(self.exp_var, 0, 99, u.exp, "EXP")
        if v == self._shown.get("exp"):
            return
        self._shown["exp"] = v

        def fn(r, sv):
            if r.exp == v:
                return False
            r.set_exp(v, sv.payload)
            r.exp = v
        n, s = self._edit_units(fn)
        self.exp_bar.set(min(v, 100) / 100)
        if n:
            self._update_copies_chip(u)
            self.flash(f"{u.name}：EXP → {v}（{self._where(n, s)}）")

    def commit_stat(self, i: int):
        u = self._current()
        var = self.stat_vars[i]
        if not u or var.get() == "":
            return
        v = self._num(var, 0, STAT_MAX)
        if v is None:
            return self._bad_input(var, 0, STAT_MAX, u.stats[i], STAT_LONG[STAT_NAMES[i]])
        if v == self._shown.get(i):
            return
        self._shown[i] = v

        def fn(r, sv):
            if r.stats[i] == v:
                return False
            if i == 0:
                self._move_max_hp(r, sv, v - r.stats[0])
            r.set_stat(i, v, sv.payload)
            r.stats[i] = v
        n, s = self._edit_units(fn)
        self.maxhp_lbl.configure(text=str(u.max_hp) if u.max_hp else "—")
        top = max(40, max(u.stats))
        for k, bar in enumerate(self.stat_bars):
            bar.set(u.stats[k] / top)
        if n:
            self._update_copies_chip(u)
            self.flash(f"{u.name}：{STAT_LONG[STAT_NAMES[i]]} → {v}（{self._where(n, s)}）")

    @staticmethod
    def _move_max_hp(r, sv, delta: int):
        """Keep max HP = base + HP gained (true in every unedited save checked)."""
        if delta and r.max_hp:
            new = max(1, min(999, r.max_hp + delta))
            r.set_hp(new, sv.payload)
            r.max_hp = new

    def sync_copies(self):
        u = self._current()
        if not u:
            return
        lv = self._num(self.lvl_var, 1, 99)
        ex = self._num(self.exp_var, 0, 255)
        stats = [self._num(v, 0, STAT_MAX) for v in self.stat_vars]
        if lv is None or ex is None or None in stats:
            self.flash("某个字段不是有效的数字。", "warn")
            return

        def fn(r, sv):
            r.set_level(lv, sv.payload)
            r.set_exp(ex, sv.payload)
            r.level, r.exp = lv, ex
            self._move_max_hp(r, sv, stats[0] - r.stats[0])
            for k, val in enumerate(stats):
                r.set_stat(k, val, sv.payload)
                r.stats[k] = val
        n, s = self._edit_units(fn)
        self._shown = {"lvl": lv, "exp": ex, **dict(enumerate(stats))}
        self._tree_lv(u.uid, lv)
        self._update_copies_chip(u)
        self.flash(f"{u.name}：所有副本已与显示的数值一致（{self._where(n, s)}）", "ok")

    # ---------------------------------------------------------- ranks
    def refresh_skills(self):
        self.refresh_skill_lists()







    # ------------------------------------------------------- inventory
    def refresh_inventory(self):
        tree = self.inv_tree
        prev = tree.selection()
        tree.delete(*tree.get_children())
        u = self._current()
        self._inv_items = []
        self._inv_spells = []
        if not u:
            self._inv_lock()
            return
        inv = u.inventory(bytes(self.sv.payload))
        names = item_ids()
        if inv is None:
            tree.insert("", "end", iid="none", text="  未找到此单位的道具列表",
                        tags=("empty",))
            self._inv_lock()
            return
        self._inv_items = inv.items
        self._inv_spells = inv.spells
        for n, (_off, iid, _tag) in enumerate(inv.items):
            meta = names.get(iid, {})
            kind = item_kind(meta) if meta else "Item"
            tree.insert("", "end", iid=f"i{n}", text=f" {n + 1}.  {item_label(iid, meta)}",
                        image=self.item_icon(iid, meta, kind),
                        values=(KIND_ZH.get(kind, kind),))
        if not inv.items:
            tree.insert("", "end", iid="none", text='  没有携带道具', tags=("empty",))
        if inv.spells:
            tree.insert("", "end", iid="spells", text='  魔法（只读）',
                        tags=("header",))
            for n, (_off, iid, _tag) in enumerate(inv.spells):
                meta = names.get(iid, {})
                tree.insert("", "end", iid=f"s{n}", text="      " + item_label(iid, meta),
                            image=self._dot(KIND_COLORS["Tome"]), values=("魔法",),
                            tags=("empty",))
        if prev and tree.exists(prev[0]) and prev[0] not in ("none", "spells"):
            tree.selection_set(prev[0])
        self._inv_lock()

    def _inv_slot(self) -> int | None:
        sel = self.inv_tree.selection()
        if not sel or not sel[0].startswith("i"):
            return None
        return int(sel[0][1:])

    def _inv_lock(self):
        idx = self._inv_slot()
        iid = None
        cat = None
        if idx is not None and idx < len(self._inv_items):
            iid = self._inv_items[idx][1]
            cat = item_category(item_ids().get(iid, {}))
        else:
            selection = self.inv_tree.selection()
            if selection and selection[0].startswith("s") and selection[0][1:].isdigit():
                spell_index = int(selection[0][1:])
                if spell_index < len(self._inv_spells):
                    iid = self._inv_spells[spell_index][1]
        self.inv_item_preview.configure(text=item_preview_text(iid))
        self.inv_picker.set_lock(cat, self.allow_any_item.get())

    def replace_item(self, new_id: int):
        idx = self._inv_slot()
        u = self._current()
        if u is None or idx is None:
            self.flash("请先选择一个背包槽位。", "warn")
            return
        old = self._inv_items[idx][1] if idx < len(self._inv_items) else None
        if new_id == old:
            self.flash("该槽位已经是这个道具。")
            return
        self.push_undo()
        changed = False
        for sv in self._targets():
            if layer.set_item(sv.payload, u.uid, idx, new_id):
                sv.scan()
                sv._core_cache = None
                changed = True
        if not changed:
            self.flash("没有更改——该单位在这个存档里没有这个道具栏。", "warn")
            return
        self._touch(self._targets())
        self.refresh_inventory()
        self.flash(f"{u.name}：槽位 {idx + 1} → {item_label(new_id)}")

    def add_item(self):
        u = self._current()
        if u is None:
            self.flash("请先选择一个单位。", "warn")
            return
        new_id = self.inv_picker.selected()
        if new_id is None:
            self.flash("请先在目录中选择一个道具。", "warn")
            return
        self.push_undo()
        for sv in self._targets():
            layer.add_item(sv.payload, u.uid, new_id)
            sv.scan()
            sv._core_cache = None
        self._touch(self._targets())
        self.refresh_inventory()
        self.flash(f"{u.name}：已添加 {item_label(new_id)}")

    def remove_item(self):
        idx = self._inv_slot()
        u = self._current()
        if u is None or idx is None:
            self.flash("请先选择一个背包槽位。", "warn")
            return
        self.push_undo()
        for sv in self._targets():
            layer.remove_item(sv.payload, u.uid, idx)
            sv.scan()
            sv._core_cache = None
        self._touch(self._targets())
        self.refresh_inventory()
        self.flash(f"{u.name}：已移除槽位 {idx + 1} 的道具")


    # ----------------------------------------------------------- class
    def refresh_classes(self):
        if not hasattr(self, "class_tree"):
            return
        tree = self.class_tree
        tree.delete(*tree.get_children())
        q = self.class_search.get_query().lower()
        show_all = self.show_bad_classes.get()
        u = self._current()
        cur = u.job_hash if u else None
        rows = []
        for h, m in job_ids().items():
            en = m.get("en") or ""
            jp = (m.get("jp") or "").replace("JID_", "")
            label = job_name(h)
            if not en and not show_all:
                continue
            if q and q not in f"{label} {en} {jp} {h:08x}".lower():
                continue
            rows.append((label.lower(), h, label, jp))
        rows.sort()
        for _k, h, en, jp in rows:
            tree.insert("", "end", iid=f"{h:08x}", text=("✓  " if h == cur else "    ") + en,
                        values=(jp,))
        if cur is not None and tree.exists(f"{cur:08x}"):
            tree.selection_set(f"{cur:08x}")
            tree.see(f"{cur:08x}")

    def apply_class(self):
        u = self._current()
        sel = self.class_tree.selection()
        if not u or not sel:
            self.flash("请先在列表中选择一个兵种。", "warn")
            return
        if self._read_only():
            return
        new_hash = int(sel[0], 16)
        if new_hash == u.job_hash:
            self.flash(f"{u.name} 已经是 {u.job} 了。")
            return
        name = job_name(new_hash)
        n = len(self._targets())
        scope = f"此线路的 {n} 个存档" if n > 1 else "此槽位"
        if not messagebox.askyesno(
                "更改兵种",
                f"将 {u.name} 从 {u.job} 改为 {name}（{scope}）？\n\n"
                "只写入兵种编号，不会重新计算移动力、可用武器、兵种技能和最大HP。"
                "保存之前不会写入磁盘。",
                parent=self):
            return
        touched, ids = [], 0
        for sv in self._targets():
            hit = sv.set_class(self.uid, new_hash)
            if hit:
                ids += hit
                touched.append(sv)
        self._touch(touched)
        self.flash(f"{u.name}：兵种 → {name}（{ids} 处记录，{len(touched)} 个槽位）")
        self.show_unit()

    # ========================================================== convoy
    def refresh_convoy(self, select: list[int] | None = None):
        """List the live convoy.  Tree ids are slot numbers."""
        tree = self.conv_tree
        prev = select if select is not None else [int(i) for i in tree.selection()
                                                  if i.isdigit()]
        tree.delete(*tree.get_children())
        conv = live_convoy(self.core_of()) if self.sv else None
        # live_convoy() returns every slot; only the filled ones are items
        self._conv_rows = [r for r in (conv.rows if conv else []) if r.qty and r.iid]
        names = item_ids()
        q = self.conv_filter.get_query().lower()
        shown = 0
        for row in self._conv_rows:
            meta = names.get(row.iid, {})
            kind = item_kind(meta) if meta else "Item"
            label = item_label(row.iid, meta)
            if q and not all(t in f"{label} {meta.get('jp', '')} {kind}".lower()
                             for t in q.split()):
                continue
            tree.insert("", "end", iid=str(row.slot), text=f" {row.slot + 1:>4} · {label}",
                        image=self.item_icon(row.iid, meta, kind),
                        values=(KIND_ZH.get(kind, kind), row.qty))
            shown += 1
        salvage = None
        if conv is None and self.sv:
            salvage = layer.salvage_convoy(bytes(self.sv.payload))
        if conv is None:
            tree.insert("", "end", iid="none",
                        text="  此槽位的道具袋列表已损坏——见上方说明" if salvage
                        else "  此槽位中没有道具袋列表", tags=("empty",))
        total = len(self._conv_rows)
        self.conv_count.configure(text="" if conv is None else
                                  f"{total} 件道具 · {conv.slots} 个槽位" if shown == total
                                  else f"{shown} / {total} 件道具")
        if salvage:
            n_items = sum(1 for sl in salvage[1] if sl.item)
            self.conv_problem_lbl.configure(
                text="⚠ 此存档的道具袋列表已损坏：旧版本的“添加”把道具直接写在空槽位上，"
                     "导致其后的列表错位，游戏无法读取。“修复道具袋”会把它重建为干净的 "
                     f"{salvage[0]} 槽位列表，并保留仍能找到的 {n_items} 件道具。"
                     "撤销可用，原始文件保留在槽位的 .bak 中。")
            self.conv_repair_btn.pack(anchor="w", pady=(6, 0))
            self.conv_problem.pack(fill="x", pady=(0, 6), before=self.conv_filter)
        else:
            self.conv_problem.pack_forget()
        keep = [str(i) for i in prev if tree.exists(str(i))]
        if keep:
            tree.selection_set(keep)
            tree.see(keep[-1])
        self._update_conv_item_preview()

    def repair_convoy_ui(self):
        got = layer.salvage_convoy(bytes(self.sv.payload)) if self.sv else None
        if not got:
            self.flash("此槽位的道具袋不需要修复。")
            return
        n_items = sum(1 for sl in got[1] if sl.item)
        if not messagebox.askyesno(
                "修复道具袋",
                f"将此槽位的道具袋重建为 {got[0]} 个槽位的干净列表，保留找到的 "
                f"{n_items} 件道具？\n\n保存之前不会写入磁盘，可以撤销，"
                "原始文件保留在槽位的 .bak 中。",
                parent=self):
            return
        if self._core_edit(lambda f: (layer.repair_convoy(f.payload) >= 0 and f.payload) or None,
                           "修复道具袋"):
            self.flash(f"道具袋已修复：{n_items} 件道具、{got[0]} 个槽位——按 Ctrl+S 写入。",
                       "ok")
        self.refresh_convoy()

    def _conv_row(self, slot: int):
        return next((r for r in self._conv_rows if r.slot == slot), None)

    def _conv_selected(self) -> list[int]:
        return sorted(int(i) for i in self.conv_tree.selection() if i.isdigit())

    def _conv_row_picked(self, _e=None):
        row = self._update_conv_item_preview()
        if row:
            self.qty_var.set(str(row.qty))

    def _update_conv_item_preview(self):
        slots = self._conv_selected()
        row = self._conv_row(slots[0]) if len(slots) == 1 else None
        self.conv_item_preview.configure(text=item_preview_text(row.iid if row else None))
        return row

    def _qty(self) -> int | None:
        v = self._num(self.qty_var, 1, QTY_MAX)
        if v is None:
            self.flash(f"数量必须是 1 – {QTY_MAX}。", "warn")
        return v

    def _convoy_edit(self, slots: list[int], fn) -> int:
        """fn(payload, row) for the given slots of the live list in every target.

        In other saves a slot is only touched when it holds the same item as
        here, so a list that has moved on is left alone.
        """
        self.push_undo()
        want = {s: self._conv_row(s).iid for s in slots if self._conv_row(s)}
        touched = []
        for sv in self._targets():
            conv = live_convoy(bytes(sv.payload))
            if conv is None:
                continue
            hit = False
            for row in conv.rows:
                if row.slot in want and row.iid == want[row.slot]:
                    fn(sv.payload, row)
                    hit = True
            if hit:
                touched.append(sv)
        self._touch(touched)
        return len(touched)

    def replace_convoy(self, iid: int):
        slots = self._conv_selected()
        if len(slots) != 1:
            self.flash("请选择一个要替换的道具袋行。", "warn")
            return
        row = self._conv_row(slots[0])
        if row.iid == iid:
            self.flash("该行已经是这个道具。")
            return
        old = item_label(row.iid)
        n = self._convoy_edit(slots, lambda p, r: set_convoy_item(p, r, iid))
        self.refresh_convoy(select=slots)
        self.flash(f"道具袋槽位 {slots[0] + 1}：{old} → {item_label(iid)}"
                   + (f"，共 {n} 个存档" if n > 1 else ""))

    def set_convoy_qty(self):
        slots = self._conv_selected()
        if not slots:
            self.flash("请先选择一个道具袋行。", "warn")
            return
        qty = self._qty()
        if qty is None:
            return
        # gear (weapons, tomes, shields…) never stacks: its quantity is the number of
        # slots holding a copy, so those rows are handled by adding/removing slots
        gear = sorted({self._conv_row(s).iid for s in slots
                       if self._conv_row(s) and not layer.stacks(self._conv_row(s).iid)})
        if gear:
            for iid in gear:
                if not self._core_edit(
                        lambda f, i=iid: (layer.set_convoy_copies(f.payload, i, qty)
                                          and f.payload) or None,
                        f"道具袋：{item_label(iid)} ×{qty}（每件一槽）"):
                    break
            slots = [s for s in slots if self._conv_row(s) and layer.stacks(self._conv_row(s).iid)]
            if not slots:
                self.refresh_convoy()
                return
        n = self._convoy_edit(slots, lambda p, r: set_convoy_qty(p, r, qty))
        self.refresh_convoy(select=slots)
        self.flash(f"数量 → {qty}（{len(slots)} 行）" + (f"，共 {n} 个存档" if n > 1 else ""))

    # ============================================================ gold
    def _show_gold(self, val: int | None, hint: str):
        self.gold_now.configure(text="—" if val is None else f"{val:,}")
        self.gold_hint.configure(text=hint)

    def locate_gold(self):
        self.gold_auto = False
        self.money_off = None
        if not self.sv:
            return self._show_gold(None, "")
        cur = current_gold(bytes(self.sv.payload))
        if cur is None:
            return self._show_gold(None, "此槽位没有 GameUserData 记录。")
        self.money_off = cur[1]
        self.gold_auto = True
        self._show_gold(cur[0], "从此槽位自身的记录读取。")


    def find_gold(self):
        """Gone: gold has a fixed address."""
        self.flash("金币直接从存档读取——无需搜索。", "info")


    def on_gold_pick(self, _e=None):
        return None


    def set_gold(self):
        if not self.sv:
            return
        try:
            val = int(self.gold_new.get().replace(",", "").strip())
        except ValueError:
            self.flash("请用数字输入新的金额。", "warn")
            return
        if not 0 <= val <= 0xFFFFFFFF:
            self.flash("金额必须在 0 到 4,294,967,295 之间。", "warn")
            return
        self.push_undo()
        touched = [sv for sv in self._targets() if write_gold(sv.payload, val)]
        self._touch(touched)
        self.locate_gold()
        self.flash(f"金币 → {val:,}" + (f"，共 {len(touched)} 个槽位" if len(touched) > 1 else ""))


    def _set_gold_auto(self):
        self.set_gold()



def _dpi_aware():
    """Ask Windows for real pixels instead of a blurry bitmap-stretched window:
    per-monitor (2), else system-wide (1), else the pre-8.1 call."""
    if sys.platform != "win32":
        return
    import ctypes
    for mode in (2, 1):
        try:
            if ctypes.windll.shcore.SetProcessDpiAwareness(mode) == 0:
                return
        except Exception:  # noqa: BLE001
            break
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _install_scaling(root) -> float:
    """The display's scale against 96 dpi (1.0, 1.25, 1.5, 2.0…).  Treeview
    column widths are plain pixels while fonts grow with the DPI, so widths
    set with .column() are scaled to match."""
    try:
        scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        scale = 1.0
    if scale > 1.0 and not getattr(ttk.Treeview, "_fefw_scaled", False):
        orig = ttk.Treeview.column

        def column(self, column, option=None, **kw):
            for k in ("width", "minwidth"):
                if isinstance(kw.get(k), int):
                    kw[k] = int(round(kw[k] * scale))
            return orig(self, column, option, **kw)

        ttk.Treeview.column = column
        ttk.Treeview._fefw_scaled = True
    return scale


def main():
    _dpi_aware()
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg and os.path.isfile(arg):
        arg = os.path.dirname(arg)
    App(arg).mainloop()


if __name__ == "__main__":
    main()
