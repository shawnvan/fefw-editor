"""Display base weapon stats from the bundled catalogue, keyed by save item ID."""

import json
from functools import lru_cache
from pathlib import Path

from fefw_save import DATA_DIR, item_label

BONUS_LABELS = {
    "physical_attack": "物攻", "magic_attack": "魔攻",
    "physical_defense": "物防", "magic_defense": "魔防", "avoid": "回避",
    "dodge": "必杀回避", "attack_speed": "攻速", "hp": "HP",
    "strength": "力量", "magic": "魔力", "dexterity": "技巧",
    "speed": "速度", "luck": "幸运", "defense": "防守",
    "resistance": "魔防", "charm": "魅力", "vision": "视野", "move": "移动",
}
ART_MODIFIERS = {"physical_attack": "物攻", "magic_attack": "魔攻",
                 "hit": "命中", "crit": "必杀"}


def _range_text(near, far):
    return str(near) if near == far else f"{near}–{far}"


@lru_cache(maxsize=2)
def catalogue(filename="weapon_stats.json"):
    path = Path(DATA_DIR) / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def weapon_stats_text(item_id: int | None) -> str:
    """Return the read-only preview, or an empty string for other items."""
    stats = catalogue().get(f"{item_id:08x}") if item_id is not None else None
    if stats is None:
        return ""
    reach = _range_text(*stats["range"])
    lines = [
        f"威力 {stats['might']}  ·  命中 {stats['hit']}  ·  必杀 {stats['crit']}",
        f"重量 {stats['weight']}  ·  射程 {reach}  ·  基础耐久 {stats['durability']}",
    ]
    bonuses = stats.get("bonuses", {})
    if bonuses:
        lines.append("  ·  ".join(f"{label} {bonuses[key]:+d}"
                                   for key, label in BONUS_LABELS.items() if key in bonuses))
    if art := stats.get("art"):
        summary = [f"战技「{art['name']}」", f"耐久消耗 {art['cost']}"]
        if art["range"] != [0, 0]:
            summary.append(f"射程 {_range_text(*art['range'])}")
        lines.append("  ·  ".join(summary))
        modifiers = [f"{label} {art[key]:+d}" for key, label in ART_MODIFIERS.items() if art[key]]
        if modifiers:
            lines.append("战技修正  " + "  ·  ".join(modifiers))
        if art["description"]:
            lines.append(art["description"])
    if forge := stats.get("forge"):
        target = item_label(int(forge["target"], 16))
        cost = [f"锻造 → {target}"]
        if forge["material"]:
            material = item_label(int(forge["material"], 16))
            cost.append(f"{material} ×{forge['quantity']}")
        cost.append(f"{forge['cost']:,} G")
        if forge["condition"]:
            condition = {"3部": "第3部"}.get(forge["condition"], forge["condition"])
            cost.append(f"条件 {condition}")
        lines.append("  ·  ".join(cost))
    return "\n".join(lines)


def item_preview_text(item_id: int | None) -> str:
    """Return the read-only item preview for a save item ID."""
    if item_id is None:
        return ""
    details = catalogue("item_details.json").get(f"{item_id:08x}")
    if details is None:
        return weapon_stats_text(item_id)
    lines = [details["description"]]
    if uses := details["uses"]:
        lines.append(f"基础使用次数 {uses}")
    if price := details.get("sell_price"):
        lines.append(f"基础卖价 {price:,} G")
    return "\n".join(lines)
