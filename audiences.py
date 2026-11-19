"""First-audience flags, separate from blessing EXP and travel.

Six exact keys are referenced by the MIR audience-condition fields. Fortuna's
condition is empty. Leda Chapter 9 acceptance and its limits are in https://github.com/Destorino5/fefw-editor/blob/feature/part2-story/docs/audiences.md.
"""
from __future__ import annotations

import struct

import blessings
import fefw_core as core
from zh_names import CHARACTERS


class AudienceError(core.SaveError):
    pass


# Full MIR identity -> exact condition key from Skill.bin (not a guessed name).
KEYS = {
    0xa1e2e07e81a0a45b: "共有_行動_初めてマーズと話した",
    0x976774b9a9818925: "共有_行動_初めてクレールと話した",
    0xf4fe5a5c9aab9f81: "共有_行動_初めてカーラと話した",
    0xb980ecffb61b6541: "共有_行動_初めてジュラと話した",
    0xbdafb8af054f07e0: "共有_行動_初めてスミルノスと話した",
    0x72fc44e0a9217c6a: "共有_行動_初めてアウロラと話した",
}
HASHES = {god: core.id_hash(key) for god, key in KEYS.items()}
# Unedited shared-map identities keep the map recognisable with every audience
# removed. Sparse/earlier saves without two of these remain read-only here.
ANCHORS = frozenset(core.id_hash(key) for key in (
    "方尖塔の間_カメライベント済", "拠点_経由_方尖塔の間",
    "共有_実績リスト表示_拠点興行", "共有_実績リスト表示_酒場興行",
))
MIN_ANCHORS = 2


def apply_labels(names: dict[int, dict]) -> None:
    for god, key in KEYS.items():
        if names.get(HASHES[god], {}).get("name", key) != key:
            raise AudienceError(f"Conflicting audience identity for {HASHES[god]:08x}")
    for god, key in KEYS.items():
        h = HASHES[god]
        names[h] = {**names.get(h, {}), "name": key, "kind": "audience",
                    "label": f"初谒：{CHARACTERS.get(blessings.BY_HASH[god].name,
                                                   blessings.BY_HASH[god].name)}"}


def locate(f: core.SaveFile) -> core.FlagMap:
    blocks = [b for b in f.blocks if b.name == "GameUserData"]
    if len(blocks) != 1:
        raise AudienceError("GameUserData 数据块不唯一；谒见编辑已禁用。")
    identities = ANCHORS | set(HASHES.values())
    candidates = [m for m in core.flag_maps(f)
                  if any(fl.hash in identities for fl in m.flags)]
    if len(candidates) != 1:
        raise AudienceError("共享谒见标记表不唯一；谒见编辑已禁用。")
    fm = candidates[0]
    hashes = {fl.hash for fl in fm.flags}
    if len(hashes & ANCHORS) < MIN_ANCHORS or len(hashes) != len(fm.flags):
        raise AudienceError("谒见标记表需要两个未改动的锚点，且不能有重复键。")
    tree = f.tree()
    node, user = tree.node_at(fm.start), tree.node_at(blocks[0].start)
    group = tree.nodes[node].parent if node >= 0 else -1
    if (node < 0 or user < 0 or group < 0 or tree.nodes[node].struct or
            not tree.nodes[group].struct or tree.nodes[group].parent != user or
            any(n.parent == node for n in tree.nodes)):
        raise AudienceError("无法识别的共享谒见标记表结构；编辑已禁用。")
    return fm


def read(f: core.SaveFile) -> dict[int, int]:
    """Raw shared-map values; absence is not the same as a stored zero."""
    return {fl.hash: fl.value for fl in locate(f).flags}


def state_label(god: int, values: dict[int, int]) -> str:
    if god not in KEYS:
        return "无需谒见"  # 芙托娜：数据中没有谒见条件。
    value = values.get(HASHES[god])
    if value is None:
        return "未记录"
    if value == 1:
        return "已记录 (1)"
    return f"未知（{value}）；保留"


def edited(f: core.SaveFile, edits: dict[int, int | None]) -> bytearray:
    """One/absent only, preserving all other records and their encoding."""
    for god, value in edits.items():
        if type(god) is not int or god not in KEYS:
            raise AudienceError("只能编辑已识别的六个谒见标记；芙托娜没有。")
        if value is not None and (type(value) is not int or value != 1):
            raise AudienceError("谒见只能记录（1）或移除。")
    fm = locate(f)
    present = {fl.hash: fl.value for fl in fm.flags}
    requested = {HASHES[god]: value for god, value in edits.items()}
    for h in requested:
        if h in present and present[h] != 1:
            raise AudienceError(f"谒见 {h:08x} 的数值未知；未改动。")
    effective = {h: v for h, v in requested.items() if present.get(h) != v}
    if not effective:
        return bytearray(f.payload)
    records = []
    for fl in fm.flags:
        if fl.hash in effective and effective[fl.hash] is None:
            continue
        start = fl.offset - (5 if fl.wide else 4)
        records.append(bytes(f.payload[start:fl.offset + (4 if fl.wide else 1)]))
    for h, value in effective.items():
        if h not in present and value is not None:
            records.append(struct.pack("<IB", h, value))
    if len(records) > 65535:
        raise AudienceError("共享标记表已满。")
    body = bytes([f.payload[fm.start + 4]]) + struct.pack("<H", len(records)) + b"".join(records)
    return core.replace_content(f, fm.start, body)
