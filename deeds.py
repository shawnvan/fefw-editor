"""Notable Deeds: bounded counter edits; claim states and rewards are read-only."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import struct

import fefw_core as core

HERE = Path(__file__).resolve().parent
MIN_ANCHORS = 3


class DeedError(core.SaveError):
    pass


@lru_cache(maxsize=1)
def catalog() -> dict[int, dict]:
    try:
        data = json.loads((HERE / "deed_names.json").read_text(encoding="utf8"))
        if data["schema"] != 1:
            raise ValueError("unsupported catalogue version")
        entries = {int(h, 16): row for h, row in data["entries"].items()}
        identities = set(entries)
        for h, row in entries.items():
            if (not row["name"].startswith("実績_") or core.id_hash(row["name"]) != h
                    or not row["label"] or type(row["editable"]) is not bool
                    or not row["milestones"] or not row["category"]
                    or (not row["editable"] and not row["read_only_reason"])):
                raise ValueError("invalid counter identity")
            if (type(row.get('inferred', False)) is not bool
                    or ('editing_note' in row and (not isinstance(row['editing_note'], str)
                                                   or not row['editing_note'].strip()))
                    or (row.get('inferred') and (not row['editable'] or not row.get('editing_note')))):
                raise ValueError('invalid counter editing evidence')
            last = 0
            for tier in row["milestones"]:
                sh = int(tier["hash"], 16)
                if (not tier["name"].startswith("実績状態_") or core.id_hash(tier["name"]) != sh
                        or sh in identities or type(tier["target"]) is not int
                        or not last < tier["target"] <= 2147483647
                        or type(tier["reward"]) is not int or not 0 <= tier["reward"] <= 65535):
                    raise ValueError("invalid milestone identity or threshold")
                identities.add(sh)
                last = tier["target"]
        if len(entries) < MIN_ANCHORS:
            raise ValueError("too few counters")
        return entries
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DeedError(f"无法加载伟业录目录 deed_names.json：{exc}") from exc


def display_name(row: dict) -> str:
    return row.get("zh") or row["label"]


def apply_labels(names: dict[int, dict], *, localized: bool = False) -> None:
    updates = {}
    for h, row in catalog().items():
        title = display_name(row) if localized else row["label"]
        progress = "伟业录进度：" if localized else "Deed progress: "
        claim = "伟业录领取状态：" if localized else "Deed claim: "
        updates[h] = {"name": row["name"], "label": progress + title, "kind": "deed"}
        for tier in row["milestones"]:
            updates[int(tier["hash"], 16)] = {
                "name": tier["name"], "label": f"{claim}{title} · {tier['target']:,}",
                "kind": "deed"}
    for h, row in updates.items():
        if names.get(h, {}).get("name", row["name"]) != row["name"]:
            raise DeedError(f"Conflicting flag name for {h:08x}")
    for h, row in updates.items():
        names[h] = {**names.get(h, {}), **row}


def locate(f: core.SaveFile) -> core.FlagMap:
    """Locate the live counter/state map by identities and object ancestry."""
    blocks = [b for b in f.blocks if b.name == "GameUserData"]
    if len(blocks) != 1:
        raise DeedError("GameUserData 数据块不唯一，无法编辑伟业录。")
    counters = catalog().keys()
    identities = set(counters) | {int(t["hash"], 16) for r in catalog().values() for t in r["milestones"]}
    candidates = [m for m in core.flag_maps(f) if any(fl.hash in identities for fl in m.flags)]
    if len(candidates) != 1:
        raise DeedError("找不到唯一的伟业录列表，此存档在此页只读。")
    fm = candidates[0]
    if len({fl.hash for fl in fm.flags} & counters) < MIN_ANCHORS:
        raise DeedError("至少需要三个已知计数项才能识别伟业录列表。")
    if len({fl.hash for fl in fm.flags}) != len(fm.flags):
        raise DeedError("伟业录列表中有重复的键，无法编辑。")
    tree = f.tree()
    node = tree.node_at(fm.start)
    user = tree.node_at(blocks[0].start)
    group = tree.nodes[node].parent if node >= 0 else -1
    if (node < 0 or user < 0 or group < 0 or tree.nodes[node].struct
            or not tree.nodes[group].struct or tree.nodes[group].parent != user
            or any(n.parent == node for n in tree.nodes)):
        raise DeedError("无法识别伟业录列表的结构，无法编辑。")
    return fm


def read(f: core.SaveFile) -> dict[int, int]:
    return {fl.hash: fl.value for fl in locate(f).flags}


def claim_label(value: int | None, *, zh: bool = False) -> str:
    if value is None:
        return "未记录" if zh else "Not stored"
    if value == 2:
        return "已达成，未领取（2）" if zh else "Complete, unclaimed (2)"
    if value == 4:
        return "已领取（4）" if zh else "Claimed (4)"
    return f"未知（{value}），已保留" if zh else f"Unknown ({value}); preserved"


def read_only_reason(h: int, values: dict[int, int], *, zh: bool = False) -> str:
    row = catalog()[h]
    if not row["editable"]:
        return row["read_only_reason"]
    if values.get(h, 0) < 0:
        return ("存档中的进度为负值，含义未知，已保留。" if zh
                else "Negative stored progress is not understood; preserved.")
    if any(values.get(int(t["hash"], 16)) not in (None, 2, 4) for t in row["milestones"]):
        return ("有目标的领取状态未知，此计数项只读。" if zh
                else "A milestone has an unknown claim state; this counter is read-only.")
    return ""


def maximum(h: int, values: dict[int, int]) -> int:
    """Allow up to the final mapped target, or retain/reduce a higher existing total."""
    return max(catalog()[h]["milestones"][-1]["target"], values.get(h, 0))


def editing_label(h: int, values: dict[int, int]) -> str:
    if read_only_reason(h, values):
        return 'Read-only'
    return 'Inferred' if catalog()[h].get('inferred') else 'Editable'


def claims_at_risk(h: int, value: int, values: dict[int, int]) -> list[dict]:
    """Claimed milestones left below target; the game may clear their claims.

    This is a preview only. Even an increase can leave a pre-existing
    inconsistent claim below target, so check the proposed total itself.
    """
    return [tier for tier in catalog()[h]["milestones"]
            if tier["target"] > value and values.get(int(tier["hash"], 16)) == 4]


def edited(f: core.SaveFile, h: int, value: int) -> bytearray:
    """Change exactly one allowlisted counter, never a milestone state or currency."""
    cat = catalog()
    if h not in cat or type(value) is not int or value < 0:
        raise DeedError("请选择一个已知计数项，并输入非负整数。")
    fm = locate(f)
    values = {fl.hash: fl.value for fl in fm.flags}
    reason = read_only_reason(h, values, zh=True)
    if reason:
        raise DeedError(reason)
    limit = maximum(h, values)
    if value > limit:
        raise DeedError(f"请输入 0 至 {limit:,} 范围内的数值。")
    # Missing zero counters are normal. A no-op must not create them.
    if values.get(h, 0) == value:
        return bytearray(f.payload)
    records = []
    for fl in fm.flags:
        if fl.hash == h:
            # Preserve wide encoding when it already exists.
            records.append(struct.pack("<I", h) + (b"\xff" + struct.pack("<i", value)
                           if fl.wide or value > 254 else bytes([value])))
        else:
            start = fl.offset - (5 if fl.wide else 4)
            records.append(bytes(f.payload[start:fl.offset + (4 if fl.wide else 1)]))
    if h not in values:
        records.append(struct.pack("<I", h) + (b"\xff" + struct.pack("<i", value)
                       if value > 254 else bytes([value])))
    if len(records) > 65535:
        raise DeedError("伟业录列表已满。")
    body = bytes([f.payload[fm.start + 4]]) + struct.pack("<H", len(records)) + b"".join(records)
    return core.replace_content(f, fm.start, body)
