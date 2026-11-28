"""Unit outfit preference editing and Costume.bin restriction checks."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

import facilities
import fefw_core as core
from fefw_save import DATA_DIR, job_name, unit_ids

HERE = Path(__file__).resolve().parent

DEFAULT = 0
CLASS_OUTFIT = 1
UNIQUE_OUTFIT = 2

LABELS = {None: "默认", 0: "默认", 1: "职业外观", 2: "固有外观"}
SHORT_LABELS = {None: "默认", 0: "默认", 1: "职业", 2: "固有"}
FLAG_LABELS = {0: "默认（移除标记）", 1: "职业外观（兵種衣装優先 = 1）", 2: "固有外观（固有衣装優先 = 2）"}

DISABLE_FLAG = core.id_hash("衣装_着せ替え無効")


class CostumeError(core.SaveError):
    pass


@dataclass(frozen=True)
class CostumeReport:
    key: str
    hash: int
    value: int | None
    editable: bool
    read_only_reason: str
    global_disabled: bool
    part: int | None
    active_warnings: tuple[str, ...]
    character_notes: tuple[str, ...]
    in_catalog: bool


@lru_cache(maxsize=1)
def catalog() -> dict:
    for folder in dict.fromkeys((Path(DATA_DIR), HERE)):
        path = folder / "costume_rules.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema") != 1:
                raise ValueError("unsupported costume_rules.json schema")
            mount_skill = int(data["mount_skill"], 16)
            mount_jobs = frozenset(int(j, 16) for j in data["mount_jobs"])
            rules = []
            for row in data["rules"]:
                rules.append({
                    "person": int(row["person"], 16),
                    "pid": row["pid"],
                    "job": int(row["job"], 16) if row.get("job") else None,
                    "job_key": row.get("job_key") or "",
                    "skill": int(row["skill"], 16) if row.get("skill") else None,
                    "skill_key": row.get("skill_key") or "",
                    "conds": tuple(row.get("conds") or ()),
                })
            return {"mount_skill": mount_skill, "mount_jobs": mount_jobs, "rules": tuple(rules)}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CostumeError(f"无法加载服装限制目录 costume_rules.json：{exc}") from exc
    raise CostumeError("找不到服装限制目录 costume_rules.json。")


def _canonical_pid(pid: str, uid: int | None = None) -> tuple[str, int | None, bool]:
    if uid is None and pid:
        uid = core.id_hash(pid)
    if uid is None:
        return pid, None, False
    try:
        import unit_entries
        canon = unit_entries.canonical(uid & 0xFFFFFFFF)
        row = unit_entries.catalog().get(canon)
        if row and row.get("pid"):
            return row["pid"], canon, True
    except Exception:  # noqa: BLE001 — alias lookup is best-effort
        pass
    return pid, uid & 0xFFFFFFFF, False


def flag_key(pid: str, uid: int | None = None, flags: dict[int, int] | None = None) -> tuple[str, int]:
    """Resolve the exact 衣装_<PID> key and 32-bit FNV-1 hash for a unit."""
    if not pid or not pid.startswith("PID_"):
        return "", 0
    if uid is None:
        uid = core.id_hash(pid)
    if unit_ids().get(uid & 0xFFFFFFFF, {}).get("pid") != pid:
        return "", 0
    direct_key = "衣装_" + pid[4:]
    direct_hash = core.id_hash(direct_key)
    if flags is not None and direct_hash in flags:
        return direct_key, direct_hash
    cpid, _, _ = _canonical_pid(pid, uid)
    target_pid = cpid if (cpid and cpid.startswith("PID_")) else pid
    if not target_pid or not target_pid.startswith("PID_"):
        return "", 0
    key = "衣装_" + target_pid[4:]
    return key, core.id_hash(key)


def label(value: int | None, *, short: bool = False) -> str:
    table = SHORT_LABELS if short else LABELS
    if value in table:
        return table[value]
    return f"未知({value})" if short else f"未知 ({value})"


def read_flags(save_or_payload) -> tuple[dict[int, int], bool, str]:
    """Return (flag_values, structurally_editable, read_only_reason)."""
    if save_or_payload is None:
        return {}, False, "未打开存档。"
    try:
        f = (save_or_payload if isinstance(save_or_payload, core.SaveFile)
             else core.SaveFile(bytes(save_or_payload)))
    except Exception as exc:  # noqa: BLE001
        return {}, False, f"无法解析存档结构：{exc}"
    try:
        fm = facilities.locate_member(f, facilities.GENERAL_MEMBER)
        return {fl.hash: fl.value for fl in fm.flags}, True, ""
    except core.SaveError:
        maps = core.flag_maps(f)
        hits = [m for m in maps
                if len({fl.hash for fl in m.flags} & facilities.ANCHORS) >= facilities.MIN_ANCHORS]
        if len(hits) != 1 and maps:
            costume_hashes = {core.id_hash("衣装_" + meta["pid"][4:])
                              for meta in unit_ids().values()
                              if meta.get("kind") == "char" and (meta.get("pid") or "").startswith("PID_")}
            costume_hashes.add(DISABLE_FLAG)
            hits = [m for m in maps if any(fl.hash in costume_hashes for fl in m.flags)]
        if len(hits) == 1:
            return ({fl.hash: fl.value for fl in hits[0].flags}, False,
                    "当前存档的通用标记表结构不可写（如部分战斗中或早期存档），着装仅限只读查看。")
        if maps:
            return ({}, False, "当前存档无法唯一定位通用标记表，着装仅限只读查看。")
        return {}, False, "当前存档没有可用的通用标记表。"


def _cond_matches(conds: tuple[str, ...], part: int | None) -> tuple[bool, str]:
    if not conds:
        return True, ""
    if "3部" in conds:
        if part is None:
            return True, "（若处于第 3 部）"
        return part == 3, "（第 3 部）"
    if "!3部" in conds:
        if part is None:
            return True, "（若处于第 1/2 部）"
        return part != 3, "（第 1/2 部）"
    return True, f"（条件：{', '.join(conds)}）"


def _describe_rule(rule: dict) -> str:
    if rule["job"] is not None:
        target = f"兵种「{job_name(rule['job'])}」"
    elif rule["skill_key"] == "SID_乗り降り":
        target = "含「骑乘/下马」的骑乘/飞行兵种"
    elif rule["skill_key"]:
        target = f"带有技能 {rule['skill_key']}"
    else:
        target = "特定状态"
    conds = rule["conds"]
    if "3部" in conds:
        when = "（第 3 部）"
    elif "!3部" in conds:
        when = "（非第 3 部）"
    elif conds:
        when = f"（{', '.join(conds)}）"
    else:
        when = ""
    return target + when


def inspect(f: core.SaveFile, uid: int, pid: str, klass: int, *,
            enemy: bool = False, skill_lists: list | None = None,
            part: int | None = None) -> CostumeReport:
    """Inspect a unit's current costume flag and all Costume.bin restrictions."""
    flags, map_editable, map_reason = read_flags(f)
    key, h = flag_key(pid, uid, flags)
    value = flags.get(h) if h else None

    editable = True
    reason = ""
    if not key:
        editable = False
        reason = "无法识别该单位的内部标识（PID），无法定位着装标记。"
    elif enemy or unit_ids()[uid & 0xFFFFFFFF]["kind"] != "char":
        editable = False
        reason = "敌人与 NPC 记录为只读，无法修改着装设置。"
    elif not map_editable:
        editable = False
        reason = map_reason
    else:
        fm = facilities.locate_member(f, facilities.GENERAL_MEMBER)
        existing = [fl for fl in fm.flags if fl.hash == h]
        if len(existing) > 1:
            editable = False
            reason = "通用标记表中存在重复的着装键，已设为只读。"
        elif existing and (existing[0].wide or existing[0].value not in (0, 1, 2)):
            editable = False
            reason = f"当前着装标记值未知（{existing[0].value}），已保留为只读。"

    global_disabled = flags.get(DISABLE_FLAG) == 1
    cpid, canon_uid, in_catalog = _canonical_pid(pid, uid)
    person_ids = {uid & 0xFFFFFFFF}
    if canon_uid is not None:
        person_ids.add(canon_uid)
    if cpid:
        person_ids.add(core.id_hash(cpid))

    cat = catalog()
    unit_skills = {sid & 0xFFFFFFFF for lst in (skill_lists or ()) for sid in lst}
    has_mount = (klass & 0xFFFFFFFF) in cat["mount_jobs"] or cat["mount_skill"] in unit_skills

    active_warnings: list[str] = []
    if global_disabled:
        active_warnings.append("当前存档已开启全局禁换装标记（衣装_着せ替え無効 = 1），游戏内可能禁止更改服装。")

    char_rules = [r for r in cat["rules"] if r["person"] in person_ids]
    for r in char_rules:
        cond_ok, cond_note = _cond_matches(r["conds"], part)
        if not cond_ok:
            continue
        if r["job"] is not None and r["job"] == (klass & 0xFFFFFFFF):
            active_warnings.append(
                f"当前兵种「{job_name(klass)}」{cond_note}命中游戏服装限制（Costume.bin）：该状态下游戏内无法更改服装。"
            )
        elif r["skill"] is not None and r["skill"] == cat["mount_skill"] and has_mount:
            where = "当前兵种带有骑乘/下马（SID_乗り降り）" if (klass & 0xFFFFFFFF) in cat["mount_jobs"] else "当前单位持有技能「骑乘/下马」"
            active_warnings.append(
                f"{where}（当前兵种「{job_name(klass)}」）{cond_note}命中游戏服装限制（Costume.bin）：该状态下游戏内无法更改服装。"
            )

    if not in_catalog and not enemy:
        active_warnings.append("该单位不在已知可招募角色目录中，可能没有独立的固有外观模型。")

    if char_rules:
        summary = "；".join(dict.fromkeys(_describe_rule(r) for r in char_rules))
        notes = (f"限制表（Costume.bin）记录的受限情形：{summary}。",)
    else:
        notes = ("限制表（Costume.bin）中未记录该角色的兵种/章节服装限制。",)

    return CostumeReport(
        key=key,
        hash=h,
        value=value,
        editable=editable,
        read_only_reason=reason,
        global_disabled=global_disabled,
        part=part,
        active_warnings=tuple(dict.fromkeys(active_warnings)),
        character_notes=notes,
        in_catalog=in_catalog,
    )


def _find_unit(f: core.SaveFile, person: int):
    target = person & 0xFFFFFFFF
    try:
        import unit_entries
        return next((u for u in unit_entries.roster_units(f) if u.person == target), None)
    except core.SaveError:
        return core.unit_by_person(f, target)


def edited(f: core.SaveFile, pid: str, target: int | None, *,
           uid: int | None = None) -> bytearray:
    """Atomically set (1/2) or clear (None/0) 衣装_<PID> in GENERAL_MEMBER."""
    if target not in (None, DEFAULT, CLASS_OUTFIT, UNIQUE_OUTFIT):
        raise CostumeError("着装设置仅支持默认、职业外观（1）或固有外观（2）。")
    person = (core.id_hash(pid) if uid is None else uid) & 0xFFFFFFFF
    meta = unit_ids().get(person)
    if not meta or meta["pid"] != pid or not pid.startswith("PID_"):
        raise CostumeError("无法识别该单位的内部标识（PID），无法定位着装标记。")
    if meta["kind"] != "char" or pid.startswith("PID_EN_"):
        raise CostumeError("敌人与 NPC 记录为只读，无法修改着装设置。")
    if _find_unit(f, person) is None:
        raise CostumeError("此槽位没有该单位。")
    try:
        fm = facilities.locate_member(f, facilities.GENERAL_MEMBER)
    except core.SaveError as exc:
        raise CostumeError("当前存档的通用标记表无法唯一识别，无法安全修改着装设置。") from exc

    flags = {fl.hash: fl.value for fl in fm.flags}
    key, h = flag_key(pid, person, flags)

    hits = [fl for fl in fm.flags if fl.hash == h]
    if len(hits) > 1:
        raise CostumeError(f"标记 {key} 存在重复条目，已拒绝修改。")
    if hits and (hits[0].wide or hits[0].value not in (0, 1, 2)):
        raise CostumeError(f"标记 {key} 的当前值（{hits[0].value}）格式或含义未知，已保留。")

    desired = None if target in (None, DEFAULT) else int(target)
    if desired is None:
        if not hits:
            return bytearray(f.payload)
        out = core.flag_remove(f, fm, hits[0])
    elif not hits:
        out = core.flag_add(f, fm, h, desired)
    elif hits[0].value == desired:
        return bytearray(f.payload)
    else:
        out = bytearray(f.payload)
        out[hits[0].offset] = desired

    check_fm = facilities.locate_member(core.SaveFile(bytes(out)), facilities.GENERAL_MEMBER)
    actual = next((fl.value for fl in check_fm.flags if fl.hash == h), None)
    if actual != desired:
        raise CostumeError("着装标记修改未通过读回校验。")
    return out
