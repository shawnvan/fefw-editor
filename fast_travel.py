"""Known Dagsion destination flags, independent of world-map/facility state.

Only value 1 and absence are writable. Never infer the list from its index or
create unscoped lookalike flags. See https://github.com/Destorino5/fefw-editor/blob/feature/part2-story/docs/fast-travel.md for evidence and limits.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import struct

import fefw_core as core
from zh_names import FAST_TRAVEL

HERE = Path(__file__).resolve().parent
AVAILABLE = 1
MIN_ANCHORS = 3


class FastTravelError(core.SaveError):
    pass


@lru_cache(maxsize=1)
def catalog() -> dict[int, dict]:
    try:
        data = json.loads((HERE / "fast_travel_names.json").read_text(encoding="utf8"))
        if data["schema"] != 1:
            raise ValueError("unsupported catalogue version")
        entries = {int(h, 16): row for h, row in data["entries"].items()}
        for h, row in entries.items():
            if (not row["name"].startswith("拠点_FT_Hub_000_") or
                    "_ファストトラベル_" not in row["name"] or
                    core.id_hash(row["name"]) != h or not row["label"] or
                    row["area"] != "Dagsion" or type(row["locator_anchor"]) is not bool or
                    row["coverage"] not in ("Confirmed list", "Travel tested", "Effect unknown")):
                raise ValueError("invalid destination identity")
        if len(entries) < MIN_ANCHORS:
            raise ValueError("too few catalogue entries")
        return entries
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FastTravelError(f"无法加载 fast_travel_names.json：{exc}") from exc


def title(row) -> str:
    """Chinese label; falls back to the screenshot-confirmed English name."""
    return FAST_TRAVEL.get(row["label"], row["label"])


def apply_labels(names: dict[int, dict], *, localized: bool = False) -> None:
    """Label destinations without altering the original keys."""
    entries = catalog()
    for h, row in entries.items():
        if names.get(h, {}).get("name", row["name"]) != row["name"]:
            raise FastTravelError(f"Conflicting flag name for {h:08x}")
    for h, row in entries.items():
        names[h] = {**names.get(h, {}), "name": row["name"],
                    "label": f"快速传送：{title(row)}" if localized else f"Fast travel: {row['label']}",
                    "kind": "fast_travel"}


def identifying_hashes() -> set[int]:
    """Only the original 16 destinations are accepted locator anchors."""
    return {h for h, row in catalog().items() if row["locator_anchor"]}


def locate(f: core.SaveFile) -> core.FlagMap:
    """Require one structurally valid map with at least three known identities."""
    blocks = [b for b in f.blocks if b.name == "GameUserData"]
    if len(blocks) != 1:
        raise FastTravelError("GameUserData 数据块不唯一；编辑已禁用。")
    hashes = catalog().keys()
    candidates = [m for m in core.flag_maps(f)
                  if any(fl.hash in hashes for fl in m.flags)]
    if len(candidates) != 1:
        raise FastTravelError("找不到唯一的快速传送列表；此页对此存档只读。")
    fm = candidates[0]
    if len({fl.hash for fl in fm.flags} & identifying_hashes()) < MIN_ANCHORS:
        raise FastTravelError("列表中必须至少保留三个原始传送点才能被识别。")
    if len({fl.hash for fl in fm.flags}) != len(fm.flags):
        raise FastTravelError("快速传送列表键重复；编辑已禁用。")
    tree = f.tree()
    node = tree.node_at(fm.start)
    user = tree.node_at(blocks[0].start)
    group = tree.nodes[node].parent if node >= 0 else -1
    if (node < 0 or user < 0 or group < 0 or tree.nodes[node].struct or
            not tree.nodes[group].struct or tree.nodes[group].parent != user or
            any(n.parent == node for n in tree.nodes)):
        raise FastTravelError("无法识别的快速传送列表结构；编辑已禁用。")
    return fm


def read(f: core.SaveFile) -> dict[int, int]:
    return {fl.hash: fl.value for fl in locate(f).flags}


def state_label(value: int | None) -> str:
    if value is None:
        return "未记录"
    if value == AVAILABLE:
        return "可用 (1)"
    return f"未知（{value}）；保留"


COVERAGE_ZH = {"Confirmed list": "列表已确认", "Travel tested": "传送已测试",
               "Effect unknown": "效果未知"}


def coverage_label(row) -> str:
    return COVERAGE_ZH.get(row["coverage"], row["coverage"])


def edited(f: core.SaveFile, edits: dict[int, int | None]) -> bytearray:
    """Atomic allowlisted add/remove, preserving other records' exact encoding."""
    cat = catalog()
    for h, value in edits.items():
        if h not in cat or (value is not None and
                            (type(value) is not int or value != AVAILABLE)):
            raise FastTravelError("仅支持已知的传送点，以及设为可用或移除。")
    fm = locate(f)
    present = {fl.hash: fl.value for fl in fm.flags}
    for h in edits:
        if h in present and present[h] != AVAILABLE:
            raise FastTravelError(f"{title(cat[h])} 的状态未知（{present[h]}）；未改动。")
    effective = {h: v for h, v in edits.items() if present.get(h) != v}
    if not effective:
        return bytearray(f.payload)
    remaining = (present.keys() - effective.keys()) | {h for h, v in effective.items() if v is not None}
    if len(remaining & identifying_hashes()) < MIN_ANCHORS:
        raise FastTravelError("请至少保留三个原始传送点，列表才能继续被识别。")
    records = []
    for fl in fm.flags:
        if fl.hash in effective and effective[fl.hash] is None:
            continue
        start = fl.offset - (5 if fl.wide else 4)
        records.append(bytes(f.payload[start:fl.offset + (4 if fl.wide else 1)]))
    for h, v in effective.items():
        if h not in present and v is not None:
            records.append(struct.pack("<IB", h, v))
    if len(records) > 65535:
        raise FastTravelError("快速传送列表已满。")
    body = bytes([f.payload[fm.start + 4]]) + struct.pack("<H", len(records)) + b"".join(records)
    return core.replace_content(f, fm.start, body)
