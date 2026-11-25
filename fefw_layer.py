"""fefw_layer.py - the parts of fefw_save that guess, replaced by fefw_core.

`fefw_save` finds units, item slots, skills and the convoy by scanning the
payload for values that look right; that is why it reports "copies" of a unit,
loses a unit in some slots and refuses swaps it cannot place.  This module keeps
every public name of `fefw_save` (ids, labels, item catalogue, paths, file
defaults) but answers the structural questions from `fefw_core`, which reads the
save's own block table, unit pool and object tree.

`editor.py` imports from here instead of from `fefw_save`.
"""
from __future__ import annotations

import json
import os
import re
import struct
import unicodedata
from dataclasses import replace
from functools import lru_cache

import fefw_core as core
from fefw_save import *                      # noqa: F401,F403  (ids, labels, catalogue…)
from fefw_save import DATA_DIR
from fefw_save import (                      # noqa: F401
    Convoy, ConvoyRow, SaveError, SaveFile as _SaveFile, UnitRecord as _UnitRecord,
    item_ids, item_label, is_enemy, unit_ids,
)

# --------------------------------------------------------------------------
# units
# --------------------------------------------------------------------------
def _meta(person: int) -> dict:
    meta = unit_ids().get(person & 0xFFFFFFFF)
    if meta:
        return meta
    return {"pid": "PID_%08X" % (person & 0xFFFFFFFF),
            "name": "unknown 0x%08X" % (person & 0xFFFFFFFF),
            "kind": "npc"}


_BASE_SLOTS = ("base", "uid", "pid", "name", "stats_off", "level_off", "exp_off",
               "hp_off", "class_off", "stats", "level", "exp", "max_hp", "klass",
               "rank_offs", "skill_off", "job_hash", "hp_width")


class UnitRecord(_UnitRecord):
    """One unit, straight out of the save's UnitPool.

    `fefw_save.UnitRecord.__init__` walks `self.__slots__`, which in a subclass
    is the subclass's tuple, so the fields are set here by name.
    """

    __slots__ = ("start", "classes", "masteries")

    def __init__(self, **kw):
        for k in _BASE_SLOTS:
            setattr(self, k, kw.get(k))
        self.start = kw.get("start", -1)
        self.classes = kw.get("classes") or []
        self.masteries = kw.get("masteries") or []

    @property
    def copies(self) -> int:
        return 1

    def inventory(self, payload):
        """Carried items from the record's first section (no scanning)."""
        from fefw_save import Inventory
        f = core.SaveFile(bytes(payload))
        u = unit_in(f, self.uid)
        if u is None:
            return None
        secs = core.sections(f, u)
        items = [(i, it.id, 1 if it.magic else 0)
                 for i, it in enumerate(core.unit_items(f, u))]
        return Inventory(secs[0] if secs else -1, items, [])


def _record(f: core.SaveFile, u: core.SaveUnit) -> UnitRecord:
    meta = _meta(u.person)
    lvl = f.payload[u.level_off]
    raw = f.u16(u.exp_off)
    into = raw - (lvl - 1) * 100
    rec = UnitRecord(base=u.start + 10, uid=u.person & 0xFFFFFFFF,
                     pid=meta["pid"], name=meta.get("name") or meta["pid"],
                     stats_off=u.start + 37, level_off=u.start + 59,
                     exp_off=u.start + 60, class_off=u.start + 14,
                     hp_off=u.start + 64, stats=list(u.stats), level=lvl,
                     exp=into if 0 <= into <= 99 else raw % 100,
                     klass=u.klass, max_hp=f.u16(u.hp_off), hp_width=4,
                     rank_offs=[], skill_off=0, job_hash=u.klass,
                     start=u.start, classes=list(u.classes),
                     masteries=list(u.masteries))
    return rec


def find_units(payload: bytes) -> list:
    f = core.SaveFile(bytes(payload))
    return [_record(f, u) for u in f.units()]


def pick_live(recs):
    """One record per unit now, but the callers still ask."""
    return max(recs, key=lambda r: (r.level, r.exp)) if recs else None


class SaveFile(_SaveFile):
    def scan(self) -> None:
        self.units = find_units(bytes(self.payload))


def core_of(payload) -> core.SaveFile:
    return core.SaveFile(bytes(payload))


def unit_in(f: core.SaveFile, uid: int):
    return next((u for u in f.units() if u.person == (uid & 0xFFFFFFFF)), None)


# --------------------------------------------------------------------------
# unit costume preference (GameUserData general flag map: 衣装_<PID suffix>)
# --------------------------------------------------------------------------
def costume_flags(save_or_payload) -> dict[int, int]:
    """Read-only {flag_hash: value} from GameUserData's general flag map."""
    import costumes
    return costumes.read_flags(save_or_payload)[0]


def unit_costume(flags: dict[int, int], pid: str, uid: int | None = None) -> int | None:
    """Return the stored 衣装_<PID> preference (1=class, 2=unique) or None."""
    if not flags:
        return None
    import costumes
    _, h = costumes.flag_key(pid, uid, flags)
    return flags.get(h) if h else None


def costume_label(value: int | None, *, short: bool = False) -> str:
    """Human-readable label for a unit's costume setting."""
    import costumes
    return costumes.label(value, short=short)


# --------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------
def item_slots(payload, uid: int) -> list:
    """[(index, item id)] for the unit's carried items, in record order."""
    f = core.SaveFile(bytes(payload))
    u = unit_in(f, uid)
    if u is None:
        return []
    return [(i, it.id) for i, it in enumerate(core.unit_items(f, u))]


def set_item(payload: bytearray, uid: int, index: int, item_id: int) -> bool:
    """Swap one carried item; the record is rebuilt, so nothing shifts out of line."""
    f = core.SaveFile(bytes(payload))
    u = unit_in(f, uid)
    if u is None:
        return False
    items = core.unit_items(f, u)
    if not (0 <= index < len(items)):
        return False
    old = items[index]
    items[index] = core.Item(item_id & 0xFFFFFFFF, old.extra, old.magic)
    payload[:] = core.with_items(f, u, items)
    return True


def add_item(payload: bytearray, uid: int, item_id: int) -> bool:
    f = core.SaveFile(bytes(payload))
    u = unit_in(f, uid)
    if u is None:
        return False
    items = core.unit_items(f, u)
    if len(items) >= 255:
        return False
    payload[:] = core.with_items(f, u, items + [core.Item(item_id & 0xFFFFFFFF)])
    return True


def remove_item(payload: bytearray, uid: int, index: int) -> bool:
    f = core.SaveFile(bytes(payload))
    u = unit_in(f, uid)
    if u is None:
        return False
    items = core.unit_items(f, u)
    if not (0 <= index < len(items)):
        return False
    payload[:] = core.with_items(f, u, [x for i, x in enumerate(items) if i != index])
    return True


# --------------------------------------------------------------------------
# arts / skills  (the record's sections after the items)
# --------------------------------------------------------------------------
SKILL_LIST_NAMES = ("装备战技", "装备特技", "列表 3", "已习得战技",
                    "列表 5", "已习得（全部）")


def skill_lists(payload, uid: int) -> list:
    """[[id, …], …] for each skill list the unit has."""
    f = core.SaveFile(bytes(payload))
    u = unit_in(f, uid)
    if u is None:
        return []
    # section 0 is the item list; every section after it is a skill list
    return [core.skill_list(f, u, i) for i in range(1, len(core.sections(f, u)))]


def set_skill_list(payload: bytearray, uid: int, index: int, ids) -> bool:
    f = core.SaveFile(bytes(payload))
    u = unit_in(f, uid)
    if u is None:
        return False
    payload[:] = core.with_skill_list(f, u, index + 1, list(ids))
    return True


_SKILLS = None


def _skills() -> dict:
    global _SKILLS
    if _SKILLS is None:
        try:
            with open(os.path.join(DATA_DIR, "skill_ids.json"), encoding="utf8") as fh:
                _SKILLS = {int(k, 16): v for k, v in json.load(fh).items()}
        except Exception:                     # noqa: BLE001 — names are optional
            _SKILLS = {}
    return _SKILLS


# 派生键后缀：X+ / X[戦技] / X効果 / X_敵 / X(味方) 之类在数据里是独立条目，
# 翻译表只收基底名，这里剥掉后缀复用基底译名，再把中文后缀拼回去。
_SKILL_SUFFIXES = (("[戦技]", "（战技）"), ("効果", "效果"), ("_敵", "（敌）"),
                   ("(味方)", "（友方）"))


def _skill_zh(jp: str):
    from zh_names import SKILLS
    hit = SKILLS.get(jp)
    if hit:
        return hit
    # per-chapter variants (デスΓ_SC15_09 …) share the base skill's name
    base = re.sub(r"_(SC|KL|DB|WD|EU)[0-9Xx_]*$", "", jp)
    tail = ""
    for _ in range(4):
        if base.endswith("+") and len(base) > 1:
            base, tail = base[:-1], "+" + tail
            continue
        for suf, zh in _SKILL_SUFFIXES:
            if base.endswith(suf) and len(base) > len(suf):
                base, tail = base[:-len(suf)], zh + tail
                break
        else:
            break
    hit = SKILLS.get(base)
    return hit + tail if hit else None


def skill_label(sid: int) -> str:
    """Prefer game-derived and verified Chinese names, then original labels."""
    official = chinese_name("skills", sid) or chinese_name("skill_variants", sid)
    if official:
        return official
    meta = item_ids().get(sid & 0xFFFFFFFF)
    if meta:
        return item_label(sid, meta)
    s = _skills().get(sid & 0xFFFFFFFF)
    if s:
        jp = unicodedata.normalize("NFKC", s.get("jp") or "").strip()
        return _skill_zh(jp) or s.get("en") or s.get("jp") or s.get("sid")
    return "0x%08X" % (sid & 0xFFFFFFFF)


# --------------------------------------------------------------------------
# convoy
# --------------------------------------------------------------------------
def live_convoy(payload) -> "Convoy | None":
    """Read fresh rows, reusing a caller's validated decoded save when available."""
    f = payload if isinstance(payload, core.SaveFile) else core.SaveFile(bytes(payload))
    at = core.convoy_at(f)
    if at < 0:
        return None
    p = f.payload
    slots = core.convoy_read(f, at)
    rows = []
    i = at + 7
    for k, s in enumerate(slots):
        qty_off = i
        i += 2
        if s.item is None:
            rows.append(ConvoyRow(k, qty_off, 0, 0, 0))
            continue
        rows.append(ConvoyRow(k, qty_off, p[qty_off + 2], s.item.id, s.qty))
        i += 5 + len(s.item.extra)
    c = Convoy.__new__(Convoy)
    c.header = at
    c.slots = len(slots)
    c.rows = rows
    c.end = f.ptr(at)
    c.problem = None
    c.broken_slot = -1
    return c


def damaged_convoy_at(payload) -> int:
    """Start of a convoy list that is there but can't be read, else -1.

    Older versions of this editor did "Add" by writing a 7-byte item over empty
    2-byte slots, which knocks every later slot out of line; the game then
    can't read those items.  Such a list keeps its header (01, slot count).
    """
    f = core.SaveFile(bytes(payload))
    if core.convoy_at(f) >= 0:
        return -1
    blk = f.block("Transporter")
    if not blk:
        return -1
    p = f.payload
    for n in f.tree().nodes:
        if blk.start <= n.start and n.end <= blk.end and n.end - n.start > 8 \
                and p[n.start + 4] == 1 and 1 <= struct.unpack_from("<H", p, n.start + 5)[0] <= 20000:
            return n.start
    return -1


def salvage_convoy(payload) -> "tuple[int, list] | None":
    """(slot count, slots) rebuilt from a damaged list, or None.

    Reads the list slot by slot; where a slot can't be read (the list fell out
    of line), it steps one byte and tries again, which picks the rest of the
    items back up.  Empty slots are kept where they were found; if that makes
    the list longer than its slot count, empty slots are dropped from the end
    so every item survives.
    """
    at = damaged_convoy_at(payload)
    if at < 0:
        return None
    f = core.SaveFile(bytes(payload))
    p = f.payload
    n = struct.unpack_from("<H", p, at + 5)[0]
    end = f.ptr(at)
    ids = item_ids()
    out, i = [], at + 7
    while i + 2 <= end:
        q = struct.unpack_from("<H", p, i)[0]
        if q == 0:
            out.append(core.Slot(0, None))
            i += 2
            continue
        tag = p[i + 2] if i + 2 < end else None
        iid = struct.unpack_from("<I", p, i + 3)[0] if i + 7 <= end else None
        if tag in (0x41, 0x61) and iid in ids and q <= 999:
            extra = bytes(p[i + 7:i + 15]) if tag == 0x61 else b""
            out.append(core.Slot(q, core.Item(iid, extra)))
            i += 7 + len(extra)
        else:
            i += 1                       # out of line: step one byte and resync
    items = sum(1 for sl in out if sl.item)
    if items > n:
        return None
    while len(out) > n:                  # drop empty slots from the end first
        k = max(k for k, sl in enumerate(out) if not sl.item)
        out.pop(k)
    out += [core.Slot(0, None)] * (n - len(out))
    return n, out


def repair_convoy(payload: bytearray) -> int:
    """Rewrite a damaged list as a clean one.  Returns the items kept, or -1."""
    got = salvage_convoy(payload)
    if got is None:
        return -1
    _n, slots = got
    at = damaged_convoy_at(payload)
    f = core.SaveFile(bytes(payload))
    payload[:] = core.convoy_write(f, at, slots)
    return sum(1 for sl in slots if sl.item)


def convoy_rows(payload) -> list:
    c = live_convoy(payload)
    return c.rows if c else []


# The convoy is a fixed row of slots (1329 in every save seen), one per item:
# slot k belongs to the item in row k - 24 of item_ids.json (Item.bin order;
# seen on a real Auto save: Smithing Stone in slot 1008, Wolf Meat in 1028,
# Bakul in 1071).  A stack in another item's slot is ignored by the game, which
# is why 1.3.1's "Add" and "All materials" (first empty slot) didn't show up.
# The layout is game data, the same in every save, so the offset is fixed.
# The offset is 23, not 24: in all 28 game-written convoys checked (issue #38) the
# stack of an item sits at slot = row + 23, counting slots from 0 ("slot 1008" on
# screen is index 1007).  1.3.2 used 24 and so wrote into the neighbour's slot.
CONVOY_SLOT_OFFSET = 23


def convoy_home(iid: int, n_slots: int) -> "int | None":
    """The slot that belongs to an item, or None if it has none."""
    pos = _item_rows().get(iid & 0xFFFFFFFF)
    if pos is None:
        return None
    k = pos + CONVOY_SLOT_OFFSET
    return k if 0 <= k < n_slots else None


_ROWS: dict | None = None


def _item_rows() -> dict:
    global _ROWS
    if _ROWS is None:
        _ROWS = {iid: i for i, iid in enumerate(item_ids())}
    return _ROWS


# Gear (weapons, tomes, staves, shields, anything with 2+ uses) never stacks: every
# copy takes a slot of its own with quantity 1, and the game puts it in the first
# empty slot of the low part of the list - the slots whose "own item" (row - 23) is
# itself gear, which no stack ever uses.  item_stack.json lists those items
# (build_item_stack.py); without the file everything is treated as stackable.
_SINGLE: "set | None" = None
_POOL: "set | None" = None


def single_ids() -> set:
    """Item ids that take one slot per copy."""
    global _SINGLE
    if _SINGLE is None:
        try:
            with open(os.path.join(DATA_DIR, "item_stack.json"), encoding="utf8") as fh:
                _SINGLE = {int(x, 16) for x in json.load(fh).get("single", [])}
        except (OSError, ValueError):
            _SINGLE = set()
    return _SINGLE


def stacks(item_id: int) -> bool:
    """True if copies of the item share one slot (with a quantity)."""
    return (item_id & 0xFFFFFFFF) not in single_ids()


def _pool_slots() -> set:
    """Slots the game fills with non-stacking items."""
    global _POOL
    if _POOL is None:
        ids = list(item_ids())
        single = single_ids()
        _POOL = {k for k in range(len(ids) + CONVOY_SLOT_OFFSET)
                 if k - CONVOY_SLOT_OFFSET < 1 or ids[k - CONVOY_SLOT_OFFSET] in single}
    return _POOL


def convoy_free_gear_slots(payload) -> int:
    """How many more non-stacking items the convoy has room for."""
    f = core.SaveFile(bytes(payload))
    at = core.convoy_at(f)
    if at < 0:
        return 0
    slots = core.convoy_read(f, at)
    pool = _pool_slots()
    return sum(1 for k, s in enumerate(slots) if k in pool and not (s.item and s.qty))


def add_convoy_copies(payload: bytearray, item_id: int, qty: int = 1) -> bool:
    """Add ``qty`` copies of a non-stacking item, each in its own empty slot
    (always new slots, even when one is already in the convoy).  Raises
    SaveError, changing nothing, if there is not room for all of them."""
    f = core.SaveFile(bytes(payload))
    at = core.convoy_at(f)
    if at < 0:
        return False
    slots = core.convoy_read(f, at)
    pool = _pool_slots()
    free = [k for k, s in enumerate(slots) if k in pool and not (s.item and s.qty)]
    qty = max(1, int(qty))
    if len(free) < qty:
        raise core.SaveError(f"the convoy has room for {len(free)} more non-stacking "
                             f"item(s), not {qty}")
    for k in free[:qty]:
        slots[k] = core.Slot(1, core.Item(item_id & 0xFFFFFFFF))
    payload[:] = core.convoy_write(f, at, slots)
    return True


def set_convoy_copies(payload: bytearray, item_id: int, qty: int) -> bool:
    """Make the convoy hold exactly ``qty`` plain copies of a non-stacking item
    (forged copies, which carry extra data, are left alone).  Extra copies are
    taken from the highest slots.  Raises SaveError if it doesn't fit."""
    f = core.SaveFile(bytes(payload))
    at = core.convoy_at(f)
    if at < 0:
        return False
    iid = item_id & 0xFFFFFFFF
    slots = core.convoy_read(f, at)
    have = [k for k, s in enumerate(slots)
            if s.item and s.qty and s.item.id == iid and not s.item.extra]
    if not have and any(s.item and s.qty and s.item.id == iid for s in slots):
        raise core.SaveError("those are forged copies, each one unique - use Add item "
                             "to add plain ones")
    qty = max(0, int(qty))
    total = sum(slots[k].qty for k in have)
    if qty == total and all(slots[k].qty == 1 for k in have):
        return False
    for k in have[qty:]:
        slots[k] = core.Slot(0, None)
    for k in have[:qty]:            # one copy per slot; splits a stack the game wouldn't make
        slots[k] = core.Slot(1, slots[k].item)
    payload[:] = core.convoy_write(f, at, slots)
    need = qty - len(have[:qty])
    return add_convoy_copies(payload, iid, need) if need > 0 else True


def add_convoy_slot(payload: bytearray, item_id: int, qty: int = 1) -> bool:
    """Put a stack in the item's own slot (added to what is there, max 999).
    Copies of it sitting in other items' slots are moved home with it.  False
    if the item has no slot or its slot holds another item.  Items that don't
    stack get one slot per copy instead (add_convoy_copies)."""
    if not stacks(item_id):
        return add_convoy_copies(payload, item_id, qty)
    f = core.SaveFile(bytes(payload))
    at = core.convoy_at(f)
    if at < 0:
        return False
    slots = core.convoy_read(f, at)
    iid = item_id & 0xFFFFFFFF
    home = convoy_home(iid, len(slots))
    if home is None:
        return False
    cur = slots[home]
    if cur.item and cur.qty and cur.item.id != iid:
        return False
    total = max(1, int(qty)) + (cur.qty if cur.item and cur.qty else 0)
    item = cur.item if cur.item and cur.qty else core.Item(iid)
    for k, sl in enumerate(slots):
        if k != home and sl.item and sl.qty and sl.item.id == iid:
            total += sl.qty
            slots[k] = core.Slot(0, None)
    slots[home] = core.Slot(min(999, total), item)
    payload[:] = core.convoy_write(f, at, slots)
    return True


# Forging/cooking materials sit in one run of Item.bin (and so of
# item_ids.json): ores from 錬成石 (Smithing Stone), then meat, fish, plants
# and leaves, ending with バウのフン (Bau Manure).  The two tutorial freebies
# (試供餌, 試供種) inside that run are left out.
_MAT_FIRST, _MAT_LAST = "錬成石", "バウのフン"
_MATERIALS: list | None = None


def material_ids() -> list:
    """Item ids of every forging/cooking material, in the game's order."""
    global _MATERIALS
    if _MATERIALS is None:
        out, inside = [], False
        for iid, meta in item_ids().items():
            jp = meta.get("jp") or ""
            inside = inside or jp == _MAT_FIRST
            if inside and not jp.startswith("試供"):
                out.append(iid)
            if jp == _MAT_LAST:
                break
        _MATERIALS = out if inside else []
    return _MATERIALS


def fill_materials(payload: bytearray, qty: int) -> "tuple[int, int, int, int]":
    """Every material to `qty`, each in its own slot.  Stacks sitting in some
    other item's slot (1.3.1 put them in the first empty one) are moved home.
    The slot count stays the same.  Returns (already there and set, added,
    moved home, blocked because their slot holds something else)."""
    f = core.SaveFile(bytes(payload))
    at = core.convoy_at(f)
    if at < 0:
        return 0, 0, 0, 0
    qty = max(1, min(0xFFFF, int(qty)))
    slots = core.convoy_read(f, at)
    mats = material_ids()
    home = {iid: convoy_home(iid, len(slots)) for iid in mats}
    stray = set()
    for k, sl in enumerate(slots):
        if sl.item and sl.qty and sl.item.id in home and home[sl.item.id] != k:
            stray.add(sl.item.id)
            slots[k] = core.Slot(0, None)
    kept = added = moved = blocked = 0
    for iid in mats:
        k = home[iid]
        if k is None:
            blocked += 1
            continue
        sl = slots[k]
        if sl.item and sl.qty and sl.item.id == iid:
            slots[k] = core.Slot(qty, sl.item)
            kept += 1
        elif sl.item and sl.qty:
            blocked += 1
        else:
            slots[k] = core.Slot(qty, core.Item(iid))
            if iid in stray:
                moved += 1
            else:
                added += 1
    payload[:] = core.convoy_write(f, at, slots)
    return kept, added, moved, blocked


def remove_convoy_slot(payload: bytearray, slot: int) -> bool:
    """Empty one slot; the slots after it stay where they are."""
    f = core.SaveFile(bytes(payload))
    at = core.convoy_at(f)
    if at < 0:
        return False
    slots = core.convoy_read(f, at)
    if not (0 <= slot < len(slots)) or not slots[slot].item:
        return False
    slots[slot] = core.Slot(0, None)
    payload[:] = core.convoy_write(f, at, slots)
    return True


# --------------------------------------------------------------------------
# gold (fixed address: GameUserData + 6, in every slot)
# --------------------------------------------------------------------------
# GameUserData starts with the route, part and chapter the slot was saved in
# (e.g. 03 01 0c = Theodora's route, part 1, chapter 12).  The lord names stay
# English here: story_page and facility_access compare them against English
# keys.  Chinese display names come from zh_names.CHARACTERS in route_label;
# unknown codes keep the game's own label.
ROUTE_LORDS = {1: "Cai", 2: "Dietrich", 3: "Theodora", 4: "Leda", 9: "Eshmel"}


def route_info(payload) -> "tuple[str, int, int] | None":
    """(lord, part, chapter) the slot was saved in, or None."""
    try:
        f = core.SaveFile(bytes(payload))
        ud = core.user_data(f)
    except Exception:                         # noqa: BLE001 — a label is optional
        return None
    if ud < 0:
        return None
    p = f.payload
    code = p[ud + 3]
    return ROUTE_LORDS.get(code, core.ROUTE_CODES.get(code, f"route {code}")), p[ud + 4], p[ud + 5]


def route_label(payload) -> str:
    info = route_info(payload)
    if not info:
        return ""
    lord, part, chapter = info
    from zh_names import CHARACTERS
    lord = CHARACTERS.get(lord, lord)
    return f"{lord} 第{part}部 第{chapter}章"


def current_gold(payload) -> "tuple[int, int] | None":
    f = core.SaveFile(bytes(payload))
    ud = core.user_data(f)
    if ud < 0:
        return None
    return f.u32(ud + core.GOLD_AT), ud + core.GOLD_AT


def set_gold(payload: bytearray, value: int) -> bool:
    f = core.SaveFile(bytes(payload))
    ud = core.user_data(f)
    if ud < 0:
        return False
    core.set_u32(f, ud + core.GOLD_AT, int(value))
    payload[:] = f.payload
    return True


def gold_offset(payload) -> int:
    cur = current_gold(payload)
    return cur[1] if cur else -1


# the amount-matching locator is gone: keep the names so old imports work
def find_money_candidates(payload, value) -> list:      # noqa: ARG001
    return []


def money_signature(payload, off):                      # noqa: ARG001
    return {}


def locate_money(payload, sig):                         # noqa: ARG001
    return gold_offset(payload) if gold_offset(payload) >= 0 else None


def money_matches(payload, sig):                        # noqa: ARG001
    return []


def load_money_sig() -> None:
    return None


def save_money_sig(sig) -> None:                        # noqa: ARG001
    return None


# --------------------------------------------------------------------------
# units: copy in, remove, mount / battalion
# --------------------------------------------------------------------------
def copy_unit_in(payload: bytearray, origin, uid: int) -> bool:
    """Copy a unit from another save file into this one."""
    dst = core.SaveFile(bytes(payload))
    src = core.SaveFile(core.unpack(open(origin, "rb").read()))
    u = unit_in(src, uid)
    if u is None:
        return False
    # Keep the guarded importer: it already allocates a unique number and
    # rejects duplicate identities and unsupported equipment/context layouts.
    import unit_entries
    payload[:] = unit_entries.copy_in(src, u, dst)
    return True


def renumber_unit(payload: bytearray, index: int) -> int | None:
    """Give unit ``index`` of the pool a unit number no other unit uses.

    Every unit carries a number the game identifies it by (Cai 1, Peter 7, ...;
    battalions and mounts name their owner by it).  A unit copied in from another
    slot brings the number it had *there*, which another unit here may already
    own - the game then treats both as the same unit: selecting one activates the
    other (issue #33).  Returns the new number, or None if it was already unique.
    """
    f = core.SaveFile(bytes(payload))
    units = f.units()
    u = units[index]
    g = core.gear(f, u)
    if g is None:
        return None
    taken = set()
    for o in units:
        if o.start == u.start:
            continue
        og = core.gear(f, o)
        if og is not None:
            taken.add(og.unit_number)
    st = core.party_store(f)
    if st:
        taken.update(l.owner for l in core.legions(f, st[2]) if l.owner != g.unit_number)
    if g.unit_number not in taken:
        return None
    new = next(n for n in range(1, 256) if n not in taken)
    p = f.payload
    p[core.tail_at(p, u.start, u.end) - 9] = new
    if g.ride or g.legion:
        ride = g.ride and core.Ride(g.ride.id, new, g.ride.serial, g.ride.flag, g.ride.date,
                                    g.ride.name, g.ride.allies, g.ride.last)
        legion = g.legion and core.Legion(g.legion.row, new, g.legion.id)
        g2 = core.gear(f, u)
        p[:] = core.with_gear(f, u, g2, ride, legion)
    payload[:] = f.payload
    return new


def remove_unit(payload: bytearray, uid: int) -> bool:
    f = core.SaveFile(bytes(payload))
    units = f.units()
    idx = next((i for i, u in enumerate(units) if u.person == (uid & 0xFFFFFFFF)), None)
    if idx is None:
        return False
    payload[:] = core.remove_unit(f, idx)
    return True


def unit_gear(payload, uid: int):
    f = core.SaveFile(bytes(payload))
    u = unit_in(f, uid)
    return core.gear(f, u) if u else None


def set_unit_gear(payload: bytearray, uid: int, ride, legion) -> bool:
    f = core.SaveFile(bytes(payload))
    u = unit_in(f, uid)
    if u is None:
        return False
    g = core.gear(f, u)
    if g is None:
        return False
    payload[:] = core.with_gear(f, u, g, ride, legion)
    return True


def battalions(payload) -> list:
    f = core.SaveFile(bytes(payload))
    st = core.party_store(f)
    return core.legions(f, st[2]) if st else []


def mounts(payload) -> list:
    f = core.SaveFile(bytes(payload))
    st = core.party_store(f)
    return core.rides(f, st[1]) if st else []


@lru_cache(maxsize=1)
def ride_catalogue():
    with open(os.path.join(DATA_DIR, "ride_details.json"), encoding="utf-8") as source:
        return json.load(source)


def _ride_pools(f):
    store = core.party_store(f)
    if store is None:
        raise core.SaveError("无法定位主坐骑资源池。")
    at = store[1]
    pools = {at: core.ride_slots(f, at)}
    secondary = core.reliquary_store(f)
    if secondary and secondary[1] != at:
        pools[secondary[1]] = core.ride_slots(f, secondary[1])
    return pools


def _ride_edit_pools(f):
    import unit_entries

    if unit_entries.context(f) not in ("free-time", "chapter-start"):
        raise core.SaveError("请在自由行动或章节开始存档中编辑坐骑。")
    return _ride_pools(f)


def with_added_ride(f: core.SaveFile, ride_id: int) -> bytearray:
    import unit_entries

    details = ride_catalogue().get(f"{ride_id:016x}")
    if not details or not details.get("addable"):
        raise core.SaveError("此坐骑类型无法从目录添加。")
    pools = _ride_edit_pools(f)
    at = next(iter(pools))
    if not pools[at]:
        raise core.SaveError("主坐骑资源池尚未初始化。请使用已有主坐骑池的存档。")
    used = {ride.serial for slots in pools.values() for _, _, ride in slots if ride is not None}
    used.update(gear.ride.serial for _, _, _, rows in unit_entries.pool_groups(f)
                for _, gear in rows if gear.ride is not None)
    serial = next((number for number in range(1, 255) if number not in used), None)
    if serial is None:
        raise core.SaveError("没有可用的坐骑个体序号。")
    # Inferred starter record. Wild capture flags and skill selection still
    # need a native Cai save. Empty progress is safer than copying another ride.
    return core.with_added_ride(f, at, core.Ride(ride_id, 0, serial, 0, 0, "", b"", 0))


def with_ride_assignment(f: core.SaveFile, uid: int,
                         identity: tuple[int, int] | None) -> bytearray:
    """Set or clear a ride and update the pool and unit copies together."""
    import unit_entries

    pools = _ride_edit_pools(f)
    pool = [entry for slots in pools.values() for entry in slots if entry[2] is not None]
    roster = unit_entries.roster(f)
    targets = [(u, gear) for u, gear in roster if u.person == (uid & 0xFFFFFFFF)]
    if len(targets) != 1:
        raise core.SaveError("未找到唯一的我方单位记录。")
    target, target_gear = targets[0]
    number = target_gear.unit_number
    by_number = {}
    for unit, gear in roster:
        by_number.setdefault(gear.unit_number, []).append((unit, gear))
    if not 1 <= number <= 254 or len(by_number[number]) != 1:
        raise core.SaveError("所选单位的编号无效或重复。")

    def key(ride):
        return ride.id, ride.serial

    references = {}
    for unit, gear in roster:
        if gear.ride is not None:
            references.setdefault(key(gear.ride), []).append((unit, gear))

    def pool_entry(ride_key):
        hits = [entry for entry in pool if key(entry[2]) == ride_key]
        if len(hits) != 1:
            raise core.SaveError("无法在主资源池和备用装备库中唯一识别该坐骑。")
        return hits[0]

    def current_entry(unit, gear):
        owned = [entry for entry in pool if entry[2].owner == gear.unit_number]
        if gear.ride is None and not owned:
            return None
        if (gear.ride is None or len(owned) != 1 or owned[0][2] != gear.ride
                or references.get(key(gear.ride)) != [(unit, gear)]
                or pool_entry(key(gear.ride)) != owned[0]):
            raise core.SaveError("单位的坐骑归属与装备库不一致，请先在游戏内核对。")
        return owned[0]

    previous = current_entry(target, target_gear)
    owners = {previous[0]: 0} if previous else {}
    updates = {target.start: (target, target_gear, None, b"\0")}
    if identity is not None:
        start, stop, selected = pool_entry(identity)
        details = ride_catalogue().get(f"{selected.id:016x}", {})
        if f"{target.klass:08x}" not in details.get("classes", ()):
            species = details.get("type", "未知种类")
            raise core.SaveError(f"当前兵种「{job_name(target.klass)}」无法配属「{species}」坐骑。")
        refs = references.get(identity, [])
        if selected.owner:
            holders = by_number.get(selected.owner, [])
            if len(holders) != 1 or refs != holders or current_entry(*holders[0]) != (start, stop, selected):
                raise core.SaveError("所选坐骑的原配属记录不一致。")
            if selected.owner == number:
                return f.payload
            holder, gear = holders[0]
            updates[holder.start] = (holder, gear, None, b"\0")
        elif refs:
            raise core.SaveError("所选坐骑仍被单位引用，请先在游戏内核对。")

        record = bytearray(f.payload[start:stop])
        record[9] = number
        owners[start] = number
        updates[target.start] = (target, target_gear, replace(selected, owner=number), bytes(record))
    elif previous is None:
        return f.payload

    out = f.copy()
    for start, owner in owners.items():
        out.payload[start + 9] = owner
    for _, (unit, gear, _, record) in sorted(updates.items(), reverse=True):
        out = core.SaveFile(core.with_unit_ride(out, unit, gear, record))

    expected_pools = [
        [None if ride is None else replace(ride, owner=owners.get(start, ride.owner))
         for start, _, ride in slots]
        for slots in pools.values()
    ]
    actual_pools = [[ride for _, _, ride in slots] for slots in _ride_pools(out).values()]
    expected_gear = [(u.person, g.unit_number, updates[u.start][2] if u.start in updates else g.ride, g.legion)
                     for u, g in roster]
    actual_gear = [(u.person, g.unit_number, g.ride, g.legion) for u, g in unit_entries.roster(out)]
    if actual_pools != expected_pools or actual_gear != expected_gear:
        raise core.SaveError("坐骑归属或单位装备未通过读回校验。")
    return out.payload


def with_removed_ride(f: core.SaveFile, identity: tuple[int, int]) -> bytearray:
    """Clear the matching unit reference and remove the mount from its own pool."""
    import unit_entries

    pools = _ride_edit_pools(f)
    matches = [(pool_index, slot_index, ride)
               for pool_index, slots in enumerate(pools.values())
               for slot_index, (_, _, ride) in enumerate(slots)
               if ride is not None and (ride.id, ride.serial) == identity]
    if len(matches) != 1:
        raise core.SaveError("无法在资源池中唯一识别要移除的坐骑。")
    pool_index, slot_index, ride = matches[0]
    holders = [(u, gear) for _, _, _, rows in unit_entries.pool_groups(f) for u, gear in rows
               if gear.ride is not None and (gear.ride.id, gear.ride.serial) == identity]
    if ride.owner:
        if len(holders) != 1 or holders[0][1].ride != ride or holders[0][1].unit_number != ride.owner:
            raise core.SaveError("坐骑归属与单位装备不一致，无法移除。")
        unit, _ = holders[0]
        f = core.SaveFile(with_ride_assignment(f, unit.person, None))
        pools = _ride_pools(f)
    elif holders:
        raise core.SaveError("所选坐骑仍被单位引用，无法移除。")

    # Unequipping can move the pool, but it keeps the pool and slot order.
    return core.with_removed_ride(f, list(pools)[pool_index], slot_index)


def add_battalion(payload: bytearray, legion) -> bool:
    f = core.SaveFile(bytes(payload))
    st = core.party_store(f)
    if not st:
        return False
    payload[:] = core.with_added_legion(f, st[2], legion)
    return True


def with_battalion_assignment(f: core.SaveFile, uid: int, index: int | None, *,
                              required_rank: str | None = None) -> bytearray:
    """Equip one party-pool record or clear a unit, with both copies kept in sync."""
    import unit_entries

    if unit_entries.context(f) not in ("free-time", "chapter-start"):
        raise core.SaveError("请在自由行动或章节开始存档中分配或卸下军团。")
    store = core.party_store(f)
    if store is None:
        raise core.SaveError("无法定位此存档的军团资源池。")
    pool = [(at, record) for at, record in core.editable_legion_slots(f, store[2])
            if record is not None]
    roster = unit_entries.roster(f)
    targets = [(u, gear) for u, gear in roster if u.person == (uid & 0xFFFFFFFF)]
    if len(targets) != 1:
        raise core.SaveError("未找到唯一的我方单位记录。")
    target, target_gear = targets[0]
    target_number = target_gear.unit_number
    by_number = {}
    for unit, gear in roster:
        by_number.setdefault(gear.unit_number, []).append((unit, gear))
    if not target_number or len(by_number[target_number]) != 1:
        raise core.SaveError("所选单位的编号无效或重复。")

    previous = [(at, record) for at, record in pool if record.owner == target_number]
    if len(previous) > 1:
        raise core.SaveError("资源池中有多支军团指向所选单位，无法确定原配属。")
    secondary = core.reliquary_store(f)
    other_records = core.legions(f, secondary[2]) if secondary and secondary[2] != store[2] else []
    pool_records = [record for _, record in pool]
    if target_gear.legion in other_records:
        raise core.SaveError("所选单位的军团来自另一个装备库，请先在游戏内卸下。")

    # Ownership bytes are fixed-width. Only equipment slots themselves can grow or shrink.
    owners = {at: 0 for at, _ in previous}
    units = {target.start: (target_gear, None, b"\x00")}
    if index is not None:
        if not 0 <= index < len(pool):
            raise core.SaveError("选中的军团已不在当前资源池中。")
        selected_at, selected = pool[index]
        if selected.owner and selected in other_records:
            raise core.SaveError("这支军团在两个装备库中有相同的配属记录，请先在游戏内卸下。")
        tactics = dict(target.masteries).get(12, 0)
        if required_rank in GRADES and grade_of(tactics) < GRADES.index(required_rank):
            raise core.SaveError(f"该单位的指挥术为 {grade_label(tactics)}，"
                                 f"此军团需要 {required_rank}。")
        if selected.owner and selected.owner != target_number:
            holders = by_number.get(selected.owner, [])
            if len(holders) != 1:
                raise core.SaveError("无法唯一识别这支军团的原配属单位。")
            holder, holder_gear = holders[0]
            if holder_gear.legion == selected:
                units[holder.start] = (holder_gear, None, b"\x00")

        # Old editor versions wrote a unit reference without claiming a pool entry.
        # Clean up only dangling references to this type, leaving valid same-type copies alone.
        known = pool_records + other_records
        for unit, gear in roster:
            old = gear.legion
            if (unit.start != target.start and old and old.owner == gear.unit_number
                    and (old.row, old.id) == (selected.row, selected.id) and old not in known):
                if not gear.unit_number or len(by_number[gear.unit_number]) != 1:
                    raise core.SaveError("旧配属涉及编号无效或重复的单位，无法安全修复。")
                units[unit.start] = (gear, None, b"\x00")

        owners[selected_at] = target_number
        equipped = core.Legion(selected.row, target_number, selected.id)
        if target_gear.legion == equipped and selected.owner == target_number:
            del units[target.start]
        else:
            record = bytearray(f.payload[selected_at:selected_at + 17])
            record[2] = target_number
            units[target.start] = (target_gear, equipped, bytes(record))

    out = f.copy()
    for at, owner in owners.items():
        out.payload[at + 2] = owner
    # Descending order keeps the recorded offsets of units still to edit valid.
    for start, (gear, _, record) in sorted(units.items(), reverse=True):
        if out.payload[gear.legion_at:gear.legion_at + gear.legion_len] == record:
            continue
        tree = out.tree()
        node = tree.node_at(start)
        if node < 0:
            raise core.SaveError("无法定位需要更新的单位记录。")
        out = core.SaveFile(tree.splice(node, gear.legion_at, gear.legion_len, record))

    actual_store = core.party_store(out)
    expected_pool = [core.Legion(record.row, owners.get(at, record.owner), record.id)
                     for at, record in pool]
    expected_gear = [(u.person, gear.unit_number, gear.ride,
                      units[u.start][1] if u.start in units else gear.legion)
                     for u, gear in roster]
    actual_gear = [(u.person, gear.unit_number, gear.ride, gear.legion)
                   for u, gear in unit_entries.roster(out)]
    if (not actual_store or core.legions(out, actual_store[2]) != expected_pool
            or actual_gear != expected_gear):
        raise core.SaveError("军团归属或单位装备未通过读回校验。")
    return out.payload


def remove_battalion(payload: bytearray, index: int) -> bool:
    f = core.SaveFile(bytes(payload))
    st = core.party_store(f)
    if not st:
        return False
    pool = core.legions(f, st[2])
    if not (0 <= index < len(pool)):
        return False
    payload[:] = core.with_legions(f, st[2], [l for i, l in enumerate(pool) if i != index])
    return True
