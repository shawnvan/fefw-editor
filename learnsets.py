"""Catalogue-driven auto-completion of earned skills, combat arts, magic, and class masteries.

Reads learnset_catalog.json (built from GameData/Job.bin, Growth.bin, Person.bin,
Skill*.bin, Item*.bin) and computes missing permanent unlocks for:
  - Unit level + mastery ranks (universal LID_汎用 + character-specific LID_*)
  - Mastered classes (class EXP >= Job.bin mastery_exp)

Writes ONLY:
  - skill_lists[5] ("已习得（全部）", section index 6) for permanent skills/arts/magic/masteries
  - items (magic=True) for missing Black/White Magic spells (checking both normal and magic slots)

Never touches skill_lists[3] (active battle arts/gambits/weapon arts), equipped lists,
Secret Books (SID_秘伝・*), or unverified level-0/rank-0 story/system entries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import struct
from typing import Callable, Sequence

import fefw_core as core

HERE = Path(__file__).resolve().parent
LEARNED_ALL_SECTION = 6  # sections()[0]=items, sections()[6]=skill_lists[5]
_OTHER_SKILL_LIST_NAMES = {
    1: "装备战技",
    2: "装备特技",
    3: "列表 3",
    4: "已习得战技",
    5: "列表 5",
}
SKILL_THRESHOLDS = (0, 20, 35, 80, 220, 440, 690, 910, 1215, 1510, 1960, 2560)
MAX_CARRIED_ITEMS = 255


class LearnsetError(core.SaveError):
    pass


class StalePreviewError(LearnsetError):
    pass


class CancelledError(LearnsetError):
    pass


@dataclass(frozen=True)
class PlannedItem:
    hash: int
    sid: str
    name: str
    category: str
    reason: str
    item_hash: int | None = None
    upgrade_group: str = ""
    prio: int = 0


@dataclass(frozen=True)
class CompletionPlan:
    person: int
    unit_name: str
    level: int
    mastered_classes: int
    add_skills: tuple[PlannedItem, ...]
    remove_skills: tuple[PlannedItem, ...]
    add_magic: tuple[PlannedItem, ...]
    skipped_magic_full: int = 0
    kept_in_use: tuple[PlannedItem, ...] = ()

    @property
    def has_changes(self) -> bool:
        return bool(self.add_skills or self.remove_skills or self.add_magic)


@dataclass(frozen=True)
class SlotPlan:
    slot_key: str
    slot_label: str
    digest: bytes
    plans: tuple[CompletionPlan, ...]
    error: str = ""
    staged: bytes | None = field(default=None, repr=False, compare=False)
    scope: str = field(default="unit", repr=False, compare=False)
    opts_key: tuple[bool, ...] = field(default=(), repr=False, compare=False)

    @property
    def changed_plans(self) -> tuple[CompletionPlan, ...]:
        return tuple(p for p in self.plans if p.has_changes)

    @property
    def has_changes(self) -> bool:
        return not self.error and any(p.has_changes for p in self.plans)


def mastery_rank_of(exp: int) -> int:
    """1-based rank index (1=E .. 12=S+) matching Growth.bin 技能レベル."""
    rank = 1
    for i, threshold in enumerate(SKILL_THRESHOLDS, 1):
        if exp >= threshold:
            rank = i
    return rank


@lru_cache(maxsize=1)
def catalog() -> dict:
    try:
        from fefw_save import DATA_DIR
        roots = (Path(DATA_DIR), HERE)
    except ImportError:
        roots = (HERE,)
    for folder in dict.fromkeys(roots):
        path = folder / "learnset_catalog.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema") != 1:
                raise ValueError("unsupported learnset_catalog.json schema")
            classes = {int(k, 16): v for k, v in data["classes"].items()}
            characters = {int(k, 16): v for k, v in data["characters"].items()}
            universal = tuple(data["universal_mastery"])
            # Pre-index all entries by 32-bit skill hash for upgrade-group / label lookups.
            by_hash: dict[int, dict] = {}
            for entry in universal:
                by_hash.setdefault(int(entry["hash"], 16), entry)
            for ch in characters.values():
                for entry in ch["entries"]:
                    by_hash.setdefault(int(entry["hash"], 16), entry)
            for cls in classes.values():
                for entry in cls["master_skills"]:
                    by_hash.setdefault(int(entry["hash"], 16), entry)
            enhancements = {
                int(k, 16): tuple(int(v, 16) for v in vals)
                for k, vals in (data.get("enhancements") or {}).items()
            }
            return {
                "mastery_ranks": tuple(data["mastery_ranks"]),
                "mastery_types": {int(k): v for k, v in data["mastery_types"].items()},
                "enhancements": enhancements,
                "classes": classes,
                "universal": universal,
                "characters": characters,
                "by_hash": by_hash,
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise LearnsetError(f"无法加载习得目录 learnset_catalog.json：{exc}") from exc
    raise LearnsetError("找不到习得目录 learnset_catalog.json。")


def _canonical_person(person: int) -> int:
    import unit_entries
    return unit_entries.canonical(person & 0xFFFFFFFF)


def _player_units(f: core.SaveFile) -> list[core.SaveUnit]:
    """Return all player/reserve units directly from UnitPool groups without gear dependency.

    Never falls back to f.units() on failure, so unrecognized or corrupt UnitPool blocks
    surface cleanly as errors rather than silently dropping reserve units.
    """
    import unit_entries
    return unit_entries.roster_units(f)


def _resolve_unit(f: core.SaveFile, person: int) -> core.SaveUnit:
    pid = person & 0xFFFFFFFF
    hits = [u for u in _player_units(f) if u.person == pid]
    if not hits:
        raise LearnsetError("当前存档中找不到该我方单位记录。")
    if len(hits) > 1:
        raise LearnsetError("该单位在当前存档中存在多条记录，已拒绝自动补全。")
    u = hits[0]
    if len(core.sections(f, u)) <= LEARNED_ALL_SECTION:
        raise LearnsetError("该单位缺少完整的已习得技能列表结构。")
    return u


def _entry_reason(entry: dict, mtypes: dict[int, str], source_label: str = "") -> str:
    if source_label:
        return source_label
    rk = entry.get("mastery_rank", 0)
    lv = entry.get("level", 0)
    if rk > 0:
        mt = mtypes.get(entry["mastery_type"], f"技能{entry['mastery_type']}")
        return f"{mt} {entry.get('rank_label', '')}"
    if lv > 0:
        return f"等级 LV {lv}"
    return "角色配置"


def plan(f: core.SaveFile, person: int, *, include_part3: bool = False,
         replace_lower: bool = True, include_magic: bool = True,
         include_initial: bool = False) -> CompletionPlan:
    """Compute missing learned skills/arts/magic and lower-tier replacements for one unit."""
    cat = catalog()
    u = _resolve_unit(f, person)
    canon = _canonical_person(u.person)
    char_meta = cat["characters"].get(canon)
    if char_meta is None:
        raise LearnsetError("该单位不在可招募角色习得目录中（敌人或通用 NPC 为只读）。")

    unit_level = f.payload[u.level_off]
    ranks = {mtype: mastery_rank_of(exp) for mtype, exp in u.masteries}
    mtypes = cat["mastery_types"]

    # Gather all entries currently earned by level, mastery ranks, and mastered classes.
    earned: dict[int, tuple[dict, str]] = {}

    def record(entry: dict, reason: str) -> None:
        h = int(entry["hash"], 16)
        earned.setdefault(h, (entry, reason))

    if char_meta["uses_universal"]:
        for entry in cat["universal"]:
            rk = entry["mastery_rank"]
            if rk > 0 and ranks.get(entry["mastery_type"], 0) >= rk:
                record(entry, _entry_reason(entry, mtypes))

    min_lv = 1 if include_initial else 2
    for entry in char_meta["entries"]:
        if entry["category"] == "system":
            continue
        conds = entry.get("conditions") or ()
        if "3部" in conds and not include_part3:
            continue
        rk = entry["mastery_rank"]
        lv = entry["level"]
        # Exclude level-0/rank-0 Secret Books & unverified story/initial markers.
        # Level-1 personal/prologue skills are excluded unless include_initial=True
        # (e.g. Eshmel Part 0 intentionally lacks Part-3 skills).
        if rk == 0 and lv == 0:
            continue
        if rk > 0:
            if ranks.get(entry["mastery_type"], 0) >= rk:
                record(entry, _entry_reason(entry, mtypes))
        elif lv >= min_lv and unit_level >= lv:
            record(entry, _entry_reason(entry, mtypes))

    mastered_classes = 0
    for cid, exp, _flags in u.classes:
        cls_meta = cat["classes"].get(cid & 0xFFFFFFFF)
        if cls_meta and exp >= cls_meta["mastery_exp"]:
            mastered_classes += 1
            for entry in cls_meta["master_skills"]:
                record(entry, f"精通：{cls_meta['name']}")

    current_s5 = core.skill_list(f, u, LEARNED_ALL_SECTION)
    current_s5_set = set(current_s5)
    by_hash = cat["by_hash"]

    # Resolve upgrade-group winners across (existing skill_lists[5] ∪ newly earned)
    group_candidates: dict[str, list[tuple[int, int, dict, str]]] = {}
    for h in current_s5:
        meta = by_hash.get(h)
        if meta and meta.get("upgrade_group"):
            group_candidates.setdefault(meta["upgrade_group"], []).append(
                (meta.get("prio", 0), h, meta, "已有")
            )
    for h, (meta, reason) in earned.items():
        ug = meta.get("upgrade_group")
        if ug:
            group_candidates.setdefault(ug, []).append(
                (meta.get("prio", 0), h, meta, reason)
            )

    # Superseded tiers are always left out of the additions: a tier that a higher one
    # (already in skill_lists[5] or newly earned) covers is never added back.
    superseded: set[int] = set()
    for ug, cands in group_candidates.items():
        # Only supersede within a group when at least one earned item triggers it,
        # or when multiple tiers from the same group already sit in skill_lists[5].
        distinct_existing = {h for _, h, _, src in cands if src == "已有"}
        has_earned = any(src != "已有" for _, _, _, src in cands)
        if not has_earned and len(distinct_existing) <= 1:
            continue
        max_prio = max(prio for prio, _, _, _ in cands)
        winner_hashes = {h for prio, h, _, _ in cands if prio == max_prio}
        # Keep one deterministic winner if multiple share max_prio; drop strictly lower tiers.
        if winner_hashes:
            superseded.update(h for prio, h, _, _ in cands if prio < max_prio)

    enhancements = cat["enhancements"]
    add_skills: list[PlannedItem] = []
    for h, (meta, reason) in earned.items():
        if h in current_s5_set or h in superseded:
            continue
        if any(eh in current_s5_set for eh in enhancements.get(h, ())):
            continue
        ih = int(meta["item_hash"], 16) if meta.get("item_hash") else None
        add_skills.append(PlannedItem(
            hash=h, sid=meta["sid"], name=meta["name"], category=meta["category"],
            reason=reason, item_hash=ih, upgrade_group=meta.get("upgrade_group", ""),
            prio=meta.get("prio", 0),
        ))

    # replace_lower only decides whether superseded tiers already owned are removed,
    # and never drops entries still referenced in equipped/active sub-lists.
    referenced_names: dict[int, list[str]] = {}
    secs = core.sections(f, u)
    for sec_idx in range(1, len(secs)):
        if sec_idx == LEARNED_ALL_SECTION:
            continue
        list_name = _OTHER_SKILL_LIST_NAMES.get(sec_idx, f"列表 {sec_idx}")
        for sh in core.skill_list(f, u, sec_idx):
            names = referenced_names.setdefault(sh, [])
            if list_name not in names:
                names.append(list_name)

    remove_skills: list[PlannedItem] = []
    kept_in_use: list[PlannedItem] = []
    seen_removed: set[int] = set()
    if replace_lower:
        for h in current_s5:
            if h in superseded and h not in seen_removed:
                seen_removed.add(h)
                meta = by_hash.get(h, {"sid": f"0x{h:08X}", "name": f"0x{h:08X}", "category": "skill", "prio": 0})
                if h in referenced_names:
                    refs = "、".join(referenced_names[h])
                    kept_in_use.append(PlannedItem(
                        hash=h, sid=meta["sid"], name=meta["name"], category=meta["category"],
                        reason=f"低阶仍在使用，已保留（{refs}）", upgrade_group=meta.get("upgrade_group", ""),
                        prio=meta.get("prio", 0),
                    ))
                else:
                    remove_skills.append(PlannedItem(
                        hash=h, sid=meta["sid"], name=meta["name"], category=meta["category"],
                        reason="被更高阶版本替换", upgrade_group=meta.get("upgrade_group", ""),
                        prio=meta.get("prio", 0),
                    ))

    # Missing magic spells -> unit items (magic=True), checking both normal and magic slots.
    add_magic: list[PlannedItem] = []
    skipped_magic = 0
    if include_magic:
        items_now = core.unit_items(f, u)
        existing_iids = {it.id for it in items_now}
        magic_slots_used = sum(1 for it in items_now if it.magic)
        seen_magic: set[int] = set()
        for h, (meta, reason) in earned.items():
            if meta["category"] != "magic" or not meta.get("item_hash"):
                continue
            ih = int(meta["item_hash"], 16)
            if ih in existing_iids or ih in seen_magic:
                continue
            seen_magic.add(ih)
            if magic_slots_used >= MAX_CARRIED_ITEMS:
                skipped_magic += 1
                continue
            magic_slots_used += 1
            add_magic.append(PlannedItem(
                hash=h, sid=meta["sid"], name=meta["name"], category="magic",
                reason=reason, item_hash=ih,
            ))

    return CompletionPlan(
        person=u.person,
        unit_name=char_meta["name"],
        level=unit_level,
        mastered_classes=mastered_classes,
        add_skills=tuple(add_skills),
        remove_skills=tuple(remove_skills),
        add_magic=tuple(add_magic),
        skipped_magic_full=skipped_magic,
        kept_in_use=tuple(kept_in_use),
    )


def apply(f: core.SaveFile, person: int, *, include_part3: bool = False,
          replace_lower: bool = True, include_magic: bool = True,
          include_initial: bool = False) -> tuple[bytearray, CompletionPlan]:
    """Apply planned skill/art/magic completion to one unit and verify read-back."""
    p = plan(f, person, include_part3=include_part3, replace_lower=replace_lower,
             include_magic=include_magic, include_initial=include_initial)
    if not p.has_changes:
        return bytearray(f.payload), p

    work = f.copy()
    if p.add_skills or p.remove_skills:
        u = _resolve_unit(work, person)
        old_s5 = core.skill_list(work, u, LEARNED_ALL_SECTION)
        drop_set = {x.hash for x in p.remove_skills}
        new_s5 = [x for x in old_s5 if x not in drop_set]
        existing = set(new_s5)
        for item in p.add_skills:
            if item.hash not in existing:
                new_s5.append(item.hash)
                existing.add(item.hash)
        work = core.SaveFile(core.with_skill_list(work, u, LEARNED_ALL_SECTION, new_s5))

    if p.add_magic:
        u = _resolve_unit(work, person)
        items_now = core.unit_items(work, u)
        existing_iids = {it.id for it in items_now}
        new_items = list(items_now)
        for item in p.add_magic:
            if item.item_hash is not None and item.item_hash not in existing_iids:
                new_items.append(core.Item(item.item_hash, magic=True))
                existing_iids.add(item.item_hash)
        work = core.SaveFile(core.with_items(work, u, new_items))

    # Read-back verification
    u_check = _resolve_unit(work, person)
    s5_after = set(core.skill_list(work, u_check, LEARNED_ALL_SECTION))
    if any(x.hash not in s5_after for x in p.add_skills):
        raise LearnsetError("补全后的技能未通过读回校验。")
    if any(x.hash in s5_after for x in p.remove_skills):
        raise LearnsetError("低阶技能替换未通过读回校验。")
    if p.add_magic:
        iids_after = {it.id for it in core.unit_items(work, u_check)}
        if any(x.item_hash not in iids_after for x in p.add_magic if x.item_hash is not None):
            raise LearnsetError("补全后的魔法未通过读回校验。")

    return bytearray(work.payload), p


def eligible_party_persons(f: core.SaveFile) -> list[int]:
    """Unique player/reserve unit person hashes present in learnset_catalog.json."""
    chars = catalog()["characters"]
    counts: dict[int, int] = {}
    order: list[int] = []
    for u in _player_units(f):
        counts[u.person] = counts.get(u.person, 0) + 1
        if _canonical_person(u.person) in chars and len(core.sections(f, u)) > LEARNED_ALL_SECTION:
            order.append(u.person)
    return [pid for pid in order if counts.get(pid, 0) == 1]


def plan_party(f: core.SaveFile, *, include_part3: bool = False,
               replace_lower: bool = True, include_magic: bool = True,
               include_initial: bool = False) -> list[CompletionPlan]:
    return [
        plan(f, pid, include_part3=include_part3, replace_lower=replace_lower,
             include_magic=include_magic, include_initial=include_initial)
        for pid in eligible_party_persons(f)
    ]


def apply_party(f: core.SaveFile, *, include_part3: bool = False,
                replace_lower: bool = True, include_magic: bool = True,
                include_initial: bool = False,
                cancel_check: Callable[[], bool] | None = None) -> tuple[bytearray, list[CompletionPlan]]:
    work = f.copy()
    plans: list[CompletionPlan] = []
    for pid in eligible_party_persons(work):
        if cancel_check is not None and cancel_check():
            raise CancelledError("已取消写入校验。")
        out, p = apply(work, pid, include_part3=include_part3, replace_lower=replace_lower,
                       include_magic=include_magic, include_initial=include_initial)
        if p.has_changes:
            work = core.SaveFile(out)
        plans.append(p)
    return bytearray(work.payload), plans


def payload_digest(payload: bytes | bytearray) -> bytes:
    """Stable SHA-256 fingerprint of a decompressed save payload."""
    return hashlib.sha256(bytes(payload)).digest()


def _dry_run_slot(
    f: core.SaveFile,
    plans: tuple[CompletionPlan, ...],
    *,
    scope: str,
    opts: dict[str, bool],
    cancel_check: Callable[[], bool] | None = None,
) -> bytes:
    """Execute the full in-memory splice and verify the written result matches plans."""
    if cancel_check is not None and cancel_check():
        raise CancelledError("已取消写入校验。")
    if scope == "unit":
        target_p = plans[0]
        out, actual_p = apply(f, target_p.person, **opts)
        if actual_p != target_p:
            raise StalePreviewError("实际写入结果与预览计划不一致。")
        return bytes(out)
    out, actual_ps = apply_party(f, cancel_check=cancel_check, **opts)
    if tuple(actual_ps) != plans:
        raise StalePreviewError("实际写入结果与预览计划不一致。")
    return bytes(out)


def _validate_slot_plan(
    f: core.SaveFile,
    sp: SlotPlan,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> SlotPlan:
    """Validate one pending SlotPlan by running _dry_run_slot and attaching staged bytes."""
    if sp.error or not any(p.has_changes for p in sp.plans) or sp.staged is not None:
        return sp
    if payload_digest(f.payload) != sp.digest:
        return SlotPlan(
            slot_key=sp.slot_key, slot_label=sp.slot_label, digest=sp.digest,
            plans=sp.plans, error="自预览后内容已发生变更。", scope=sp.scope, opts_key=sp.opts_key,
        )
    inc_p3, rep_lo, inc_mag, inc_init = (
        sp.opts_key if len(sp.opts_key) == 4 else (False, True, True, False)
    )
    opts = {
        "include_part3": inc_p3,
        "replace_lower": rep_lo,
        "include_magic": inc_mag,
        "include_initial": inc_init,
    }
    try:
        staged = _dry_run_slot(f, sp.plans, scope=sp.scope, opts=opts, cancel_check=cancel_check)
        return SlotPlan(
            slot_key=sp.slot_key, slot_label=sp.slot_label, digest=sp.digest,
            plans=sp.plans, staged=staged, scope=sp.scope, opts_key=sp.opts_key,
        )
    except CancelledError:
        raise
    except (LearnsetError, core.SaveError) as exc:
        return SlotPlan(
            slot_key=sp.slot_key, slot_label=sp.slot_label, digest=sp.digest,
            plans=sp.plans, error=f"无法写回该存档：{exc}", scope=sp.scope, opts_key=sp.opts_key,
        )


def validate_slot_plans(
    slots: Sequence[tuple[str, str, bytes | bytearray | core.SaveFile]],
    preview: Sequence[SlotPlan],
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[SlotPlan]:
    """Thread-safe write-preflight pass for previewed SlotPlans with cancellation support."""
    by_key = {k: src for k, _lbl, src in slots}
    validated: list[SlotPlan] = []
    for sp in preview:
        if cancel_check is not None and cancel_check():
            raise CancelledError("已取消写入校验。")
        if sp.error or not any(p.has_changes for p in sp.plans) or sp.staged is not None:
            validated.append(sp)
            continue
        src = by_key.get(sp.slot_key)
        if src is None:
            validated.append(SlotPlan(
                slot_key=sp.slot_key, slot_label=sp.slot_label, digest=sp.digest,
                plans=sp.plans, error="找不到待校验的目标槽位。", scope=sp.scope, opts_key=sp.opts_key,
            ))
            continue
        f = src if isinstance(src, core.SaveFile) else core.SaveFile(bytes(src))
        validated.append(_validate_slot_plan(f, sp, cancel_check=cancel_check))
    return validated


def plan_slots(
    slots: Sequence[tuple[str, str, core.SaveFile]],
    person: int | None,
    *,
    scope: str = "unit",
    include_part3: bool = False,
    replace_lower: bool = True,
    include_magic: bool = True,
    include_initial: bool = False,
    validate_write: bool = True,
) -> list[SlotPlan]:
    """Compute per-slot completion previews and optionally preflight writable payloads."""
    out: list[SlotPlan] = []
    opts = {
        "include_part3": include_part3,
        "replace_lower": replace_lower,
        "include_magic": include_magic,
        "include_initial": include_initial,
    }
    opts_key = (include_part3, replace_lower, include_magic, include_initial)
    for slot_key, slot_label, f in slots:
        digest = payload_digest(f.payload)
        try:
            if scope == "unit":
                if person is None:
                    raise LearnsetError("未选择一个单位。")
                plans = (plan(f, person, **opts),)
            else:
                plans = tuple(plan_party(f, **opts))
        except (LearnsetError, core.SaveError) as exc:
            out.append(SlotPlan(slot_key=slot_key, slot_label=slot_label, digest=digest, plans=(),
                                error=str(exc), scope=scope, opts_key=opts_key))
            continue

        sp = SlotPlan(
            slot_key=slot_key, slot_label=slot_label, digest=digest, plans=plans,
            scope=scope, opts_key=opts_key,
        )
        if validate_write and any(p.has_changes for p in plans):
            sp = _validate_slot_plan(f, sp)
        out.append(sp)
    return out


def apply_slot_plan(
    f: core.SaveFile,
    expected: SlotPlan,
    *,
    scope: str = "unit",
    include_part3: bool = False,
    replace_lower: bool = True,
    include_magic: bool = True,
    include_initial: bool = False,
) -> bytearray | None:
    """Apply edits to one slot only if its payload and resulting plan still match preview."""
    if payload_digest(f.payload) != expected.digest:
        raise StalePreviewError(f"{expected.slot_label}：自预览后已发生变更，请刷新预览后重试。")
    if expected.error:
        raise LearnsetError(f"{expected.slot_label}：{expected.error}")
    if not expected.has_changes:
        return None
    opts_key = (include_part3, replace_lower, include_magic, include_initial)
    if expected.staged is not None and scope == expected.scope and opts_key == expected.opts_key:
        return bytearray(expected.staged)
    opts = {
        "include_part3": include_part3,
        "replace_lower": replace_lower,
        "include_magic": include_magic,
        "include_initial": include_initial,
    }
    try:
        return bytearray(_dry_run_slot(f, expected.plans, scope=scope, opts=opts))
    except StalePreviewError as exc:
        raise StalePreviewError(f"{expected.slot_label}：{exc}") from exc


def apply_slots(
    slots: Sequence[tuple[str, str, core.SaveFile]],
    preview: Sequence[SlotPlan],
    *,
    scope: str = "unit",
    include_part3: bool = False,
    replace_lower: bool = True,
    include_magic: bool = True,
    include_initial: bool = False,
) -> dict[str, bytearray]:
    """Atomically verify all previewed slots and return changed slot payloads by slot_key."""
    if len(slots) != len(preview):
        raise StalePreviewError("目标槽位列表自预览后已发生变化。")
    for (slot_key, _slot_label, f), sp in zip(slots, preview):
        if slot_key != sp.slot_key or payload_digest(f.payload) != sp.digest:
            raise StalePreviewError(f"{sp.slot_label}：自预览后已发生变更。")
        if sp.error:
            raise LearnsetError(f"{sp.slot_label}：{sp.error}")
    results: dict[str, bytearray] = {}
    for (_slot_key, _slot_label, f), sp in zip(slots, preview):
        out = apply_slot_plan(
            f, sp, scope=scope, include_part3=include_part3,
            replace_lower=replace_lower, include_magic=include_magic,
            include_initial=include_initial,
        )
        if out is not None:
            results[sp.slot_key] = out
    return results
