"""build_item_stack.py - which items can NOT share a convoy slot (item_stack.json).

    python build_item_stack.py <path to your dumped romfs folder>      (or set FEFW_ROM)

The convoy keeps a stackable item (materials, books...) in one slot with a
quantity, but gear takes a slot of its own for every copy, each with quantity 1.
Measured on 28 game-written convoys (issue #38): every item with 耐久 (uses) >= 2
(weapons, tomes, vulneraries, antitoxin, concoctions) and every shield (種類 13 /
用途 26) only ever appeared one-per-slot; the only stacks of such items sat in a
Route_* slot an editor had written.  Items with 耐久 0 or 1 outside the weapon
classes (種類 0-7: swords ... tomes, staves, monster attacks) appeared as one
stack.  Tomes and staves that have 耐久 0/1 follow the weapon classes: they are
gear, and the issue reports each tome using its own slot.

Writes item_stack.json: {"single": ["<8-hex item id>", ...]}.
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROM = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("FEFW_ROM", "")

U64 = lambda d, o: struct.unpack_from("<Q", d, o)[0]   # noqa: E731
I32 = lambda d, o: struct.unpack_from("<i", d, o)[0]   # noqa: E731
U16 = lambda d, o: struct.unpack_from("<H", d, o)[0]   # noqa: E731


def _str(d, ptr):
    if not ptr:
        return ""
    i = 32 + ptr
    j = d.find(b"\0", i)
    return d[i:j].decode("utf-8", "replace")


def raw_rows_of(d: bytes, *, keep_duplicate_fields=False):
    """Yield (row key, row offset, field layout) for the first GameData sheet."""
    fields, i = {}, 32
    record_end = 0
    while i + 32 <= len(d):
        np_, tp = U64(d, i), U64(d, i + 8)
        size, off = I32(d, i + 24), I32(d, i + 28)
        if not np_ or not tp or not 0 < size <= 4096:
            break
        name = _str(d, np_)
        if keep_duplicate_fields and name in fields:
            first_name = name
            occurrence = 2
            while name in fields:
                name = f"{first_name}#{occurrence}"
                occurrence += 1
        fields.setdefault(name, (_str(d, tp), size, off))
        record_end = max(record_end, off + size)
        i += 32
    if not record_end:
        return
    rec = (record_end + 7) & ~7
    idx = 0
    while i + rec <= len(d) and U64(d, i) == 0 and U16(d, i + 16) == idx:
        yield _str(d, U64(d, i + 8)), i, fields
        i += rec
        idx += 1


def string_refs(d: bytes, offset: int) -> list[str]:
    """Read an array of pointers to strings, such as a table's help arguments."""
    count, stride, pointer = struct.unpack_from("<HH4xQ", d, offset)
    return [_str(d, U64(d, 32 + pointer + index * stride)) for index in range(count)]


def rows_of(d: bytes):
    """Yield (row key, {field: u8 value}) for the first sheet of a GameData table."""
    for key, offset, fields in raw_rows_of(d):
        yield key, {name: d[offset + field_offset]
                    for name, (kind, _, field_offset) in fields.items()
                    if kind in ("u8", "b8")}


def fnv1_64_low32(text: str) -> int:
    x = 0xCBF29CE484222325
    for c in text.encode("utf-8"):
        x = (x * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        x ^= c
    return x & 0xFFFFFFFF


def main() -> int:
    base = os.path.join(ROM, "GameData")
    if not os.path.isfile(os.path.join(base, "Item.bin")):
        print("usage: python build_item_stack.py <path to your dumped romfs folder>\n"
              "       (or set FEFW_ROM to it)")
        return 2
    single = set()
    for fn in ("Item.bin", "Item_P0.bin", "Item_P1.bin"):
        path = os.path.join(base, fn)
        if not os.path.isfile(path):
            continue
        for key, row in rows_of(open(path, "rb").read()):
            if (row.get("耐久", 0) >= 2 or row.get("種類", 99) <= 7
                    or (row.get("種類") == 13 and row.get("用途") == 26)):
                single.add(fnv1_64_low32(key))
    out = {"single": sorted("%08x" % h for h in single)}
    with open(os.path.join(HERE, "item_stack.json"), "w", encoding="utf8") as fh:
        json.dump(out, fh, indent=0)
    print(f"{len(single)} items take a slot of their own -> item_stack.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
