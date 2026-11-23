"""Build item_details.json from GameData and the Simplified Chinese item messages."""

import argparse
import json
import struct
from pathlib import Path

import msbt
from build_item_stack import U64, _str, fnv1_64_low32, raw_rows_of, string_refs

# HP / LP recovery, temporary effects, stat boosters, spell records and boxes.
CONSUMABLE_USES = {2, 3, 5, 15, 16, 17}
EQUIPMENT_USES = {26, 27, 28, 29}
# Keys, forging materials, exam permits, gifts, ingredients, valuables,
# conversion items and quest items.
GENERAL_ITEM_USES = {12, 13, 14, 19, 20, 22, 23, 24, 25}


def item_details_rows(path):
    data = path.read_bytes()
    for key, offset, fields in raw_rows_of(data):
        kind = data[offset + fields["種類"][2]]
        use = data[offset + fields["用途"][2]]
        consumable = kind == 14 and use in CONSUMABLE_USES
        equipment = (kind == 13 and use in EQUIPMENT_USES) or (kind, use) == (14, 21)
        general = kind == 14 and use in GENERAL_ITEM_USES
        if not key.startswith("IID_") or not (consumable or equipment or general):
            continue
        # Cinematic props share the quest-item category but have no item help.
        if (kind, use) == (14, 25) and key.startswith("IID_デモ_"):
            continue
        label = _str(data, U64(data, offset + fields["ヘルプ"][2]))
        uses = data[offset + fields["耐久"][2]] if consumable else 0
        args = string_refs(data, offset + fields["引数0"][2])
        sell_price = struct.unpack_from("<I", data, offset + fields["売値"][2])[0] if (
            kind, use) == (14, 22) else 0
        yield f"{fnv1_64_low32(key):08x}", (label, uses, args, sell_price)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_data", type=Path)
    parser.add_argument("item_messages", help="CNzh/Item.msbt.zst")
    args = parser.parse_args()
    base = args.game_data / "Item.bin"
    rows = dict(item_details_rows(base))
    for path in sorted(args.game_data.glob("Item_P*.bin")):
        rows.update(item_details_rows(path))
    if not rows:
        parser.error(f"no supported item records found in {base}")
    templates = msbt.read(args.item_messages, argument_labels={row[0] for row in rows.values()})
    details = {}
    for key, (label, uses, values, sell_price) in sorted(rows.items()):
        if not templates.get(label):
            parser.error(f"missing Chinese item help for {key} ({label})")
        description = templates[label].format(*values).replace("\n", "")
        details[key] = {"description": description, "uses": uses}
        if sell_price:
            details[key]["sell_price"] = sell_price
    output = Path(__file__).with_name("item_details.json")
    output.write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(details)} item entries -> {output.name}")


if __name__ == "__main__":
    main()
