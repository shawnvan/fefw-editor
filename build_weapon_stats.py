"""Build weapon_stats.json from a dumped GameData directory."""

import argparse
import json
import struct
from pathlib import Path

import msbt
from build_item_stack import U64, _str, fnv1_64_low32, raw_rows_of, string_refs

BASE_FIELDS = {
    "might": "威力", "hit": "命中", "crit": "必殺",
    "weight": "重さ", "durability": "耐久",
}
BONUS_FIELDS = {
    "physical_attack": "物理攻撃", "magic_attack": "魔法攻撃",
    "physical_defense": "物理防御", "magic_defense": "魔法防御",
    "avoid": "回避", "dodge": "必避", "attack_speed": "攻速",
    "hp": "HP", "strength": "力", "magic": "魔力", "dexterity": "技",
    "speed": "速さ", "luck": "幸運", "defense": "守備",
    "resistance": "魔防", "charm": "魅力", "vision": "視界", "move": "移動",
}
NUMBER_FORMATS = {"u8": "<B", "s16": "<h", "int": "<i"}
ART_FIELDS = {
    "cost": "耐久消費", "physical_attack": "物攻", "magic_attack": "魔攻",
    "hit": "命中", "crit": "必殺",
}


def table_stats(path, arts):
    data = path.read_bytes()
    for key, offset, fields in raw_rows_of(data, keep_duplicate_fields=True):
        def number(name):
            field_type, _, field_offset = fields[name]
            return struct.unpack_from(NUMBER_FORMATS[field_type], data, offset + field_offset)[0]

        def reference(name):
            return _str(data, U64(data, offset + fields[name][2]))

        # Types 0–7 are the game's weapons, magic and special attacks.
        if not key.startswith("IID_") or number("種類") > 7:
            continue
        stats = {label: number(field) for label, field in BASE_FIELDS.items()}
        stats["range"] = [number("内射程"), number("外射程")]
        bonuses = {label: number(field) for label, field in BONUS_FIELDS.items()}
        stats["bonuses"] = {label: value for label, value in bonuses.items() if value}
        if not stats["bonuses"]:
            del stats["bonuses"]
        art = arts.get(reference("使用スキル"))
        if art:
            stats["art"] = art
        target = reference("錬成先")
        if target.startswith("IID_"):
            material = reference("錬成素材")
            stats["forge"] = {
                "target": f"{fnv1_64_low32(target):08x}",
                "material": f"{fnv1_64_low32(material):08x}" if material else "",
                "quantity": number("素材数#2"),
                "cost": number("費用#2"),
                "condition": reference("条件"),
            }
        yield f"{fnv1_64_low32(key):08x}", stats


def load_arts(game_data, messages_path):
    rows = {}
    for path in (game_data / "Skill.bin", *sorted(game_data.glob("Skill_P*.bin"))):
        data = path.read_bytes()
        for key, offset, fields in raw_rows_of(data):
            if not key.startswith("SID_") or data[offset + fields["種類"][2]] != 2:
                continue
            name, help_label = (_str(data, U64(data, offset + fields[field][2]))
                                for field in ("名前", "ヘルプ"))
            values = string_refs(data, offset + fields["引数0"][2])
            numbers = {}
            for label, field in ART_FIELDS.items():
                kind, _, relative = fields[field]
                numbers[label] = struct.unpack_from(NUMBER_FORMATS[kind], data, offset + relative)[0]
            numbers["range"] = [data[offset + fields[field][2]] for field in ("内射程", "外射程")]
            rows[key] = name, help_label, values, numbers
    # Only item-linked arts need templates. Other skills may use unrelated tags.
    linked = set()
    for path in (game_data / "Item.bin", *sorted(game_data.glob("Item_P*.bin"))):
        data = path.read_bytes()
        for key, offset, fields in raw_rows_of(data):
            if key.startswith("IID_") and data[offset + fields["種類"][2]] <= 7:
                linked.add(_str(data, U64(data, offset + fields["使用スキル"][2])))
    rows = {key: rows[key] for key in linked if key in rows}
    messages = msbt.read(messages_path, argument_labels={row[1] for row in rows.values()})
    return {
        key: {"name": messages[name], **numbers,
              "description": messages[help_label].format(*values).replace("\n", "")}
        for key, (name, help_label, values, numbers) in rows.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_data", type=Path)
    parser.add_argument("skill_messages", help="CNzh/Skill.msbt.zst")
    args = parser.parse_args()
    base = args.game_data / "Item.bin"
    if not base.is_file():
        parser.error(f"missing {base}")
    arts = load_arts(args.game_data, args.skill_messages)
    catalogue = dict(table_stats(base, arts))
    if not catalogue:
        parser.error(f"no weapon records found in {base}")
    for path in sorted(args.game_data.glob("Item_P*.bin")):
        catalogue.update(table_stats(path, arts))
    output = Path(__file__).with_name("weapon_stats.json")
    with output.open("w", encoding="utf-8") as stream:
        stream.write("{\n")
        lines = [f'  "{key}": {json.dumps(stats, ensure_ascii=False, separators=(", ", ": "))}'
                 for key, stats in sorted(catalogue.items())]
        stream.write(",\n".join(lines) + "\n}\n")
    print(f"{len(catalogue)} weapon entries -> {output.name}")


if __name__ == "__main__":
    main()
