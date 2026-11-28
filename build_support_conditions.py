"""build_support_conditions.py - enrich supports.json with Reliance.bin unlock rules.

Usage: python build_support_conditions.py [<path to GameData>]
Defaults to ./GameData in the repository root (or $FEFW_ROM/GameData).

Reads Sheet 2 (支援会話_* condition table) from GameData/Reliance.bin and
updates supports.json in place:
  * Adds S-rank conversation flags (48 Eshmel pairs) and updates ``max`` to "S".
  * Attaches ``conditions`` per rank (C / B / A / S), preserving multi-branch
    OR rows (empty-key continuation rows), route restrictions, part/chapter
    windows, ``any_join`` (いずれか加入), prerequisite/lockout ``flags``
    (including ``!`` negated flags), ``add_points``, and ``link_flag``
    (連動フラグ).
"""
from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_item_stack import I32, U16, U64, _str  # noqa: E402
import fefw_core as core                         # noqa: E402

RANK_MAP = {1: "C", 2: "B", 3: "A", 4: "S"}
RANK_ORDER = ("C", "B", "A", "S")
VALID_TYPES = {
    "u8", "s8", "u16", "s16", "u32", "s32", "i32", "f32", "b8",
    "string", "u8[]", "s8[]", "u16[]", "s16[]", "u32[]", "s32[]",
    "f32[]", "string[]", "data", "data[]", "bit",
}


def _read_data_str(d: bytes, off: int) -> str:
    p = U64(d, off)
    return _str(d, p) if 0 < p < len(d) - 32 else ""


def _read_ptr_array_strs(d: bytes, off: int) -> list[str]:
    cnt, stride, ptr = struct.unpack_from("<HH4xQ", d, off)
    return [_str(d, U64(d, 32 + ptr + i * stride)) for i in range(cnt)]


def _is_field_def(d: bytes, o: int, end_data: int, file_sz: int) -> bool:
    if o + 32 > end_data:
        return False
    np_, tp = U64(d, o), U64(d, o + 8)
    sz, fo = I32(d, o + 24), I32(d, o + 28)
    if not (0 < np_ < file_sz - 32 and 0 < tp < file_sz - 32):
        return False
    if not (0 < sz <= 4096 and 32 <= fo < 4096):
        return False
    ts = _str(d, tp)
    if ts in VALID_TYPES or ts.endswith("[]"):
        ns = _str(d, np_)
        return bool(ns) and len(ns) < 80
    return False


def _locate_talk_sheet(d: bytes) -> tuple[int, int, int, dict[str, tuple[str, int, int]]]:
    """Locate the 支援会話_* sheet in Reliance.bin dynamically."""
    file_sz, data_sz = struct.unpack_from("<II", d, 0)
    end_data = 32 + data_sz
    o = 32
    while o < end_data - 32:
        if _is_field_def(d, o, end_data, file_sz):
            fields: dict[str, tuple[str, int, int]] = {}
            rec_end = 0
            while _is_field_def(d, o, end_data, file_sz):
                np_, tp = U64(d, o), U64(d, o + 8)
                sz, fo = I32(d, o + 24), I32(d, o + 28)
                name = _str(d, np_)
                if name in fields:
                    n = 2
                    while f"{name}#{n}" in fields:
                        n += 1
                    name = f"{name}#{n}"
                fields[name] = (_str(d, tp), sz, fo)
                rec_end = max(rec_end, fo + sz)
                o += 32
            rec_sz = (rec_end + 7) & ~7
            row_start = o
            idx = 0
            first_key = ""
            while o + rec_sz <= end_data and U64(d, o) == 0 and U16(d, o + 16) == idx:
                if idx == 0:
                    first_key = _str(d, U64(d, o + 8))
                o += rec_sz
                idx += 1
            if first_key.startswith("支援会話_") and "ランク" in fields and "連動フラグ" in fields:
                return row_start, rec_sz, idx, fields
        else:
            o += 8
    raise RuntimeError("Could not locate 支援会話_* sheet in Reliance.bin")


def parse_reliance_talks(reliance_path: Path) -> dict[frozenset[str], dict[str, dict]]:
    """Parse Reliance.bin Sheet 2 into {frozenset({jp_a, jp_b}): {rank: cond_dict}}."""
    d = reliance_path.read_bytes()
    row_start, rec_sz, nrows, fields = _locate_talk_sheet(d)

    off_p1 = fields["対象１"][2]
    off_p2 = fields["対象２"][2]
    off_rank = fields["ランク"][2]
    off_sp = fields["部"][2]
    off_sc = fields["章"][2]
    off_ep = fields["部#2"][2]
    off_ec = fields["章#2"][2]
    off_route = fields["ルート"][2]
    off_any_join = fields["いずれか加入"][2]
    off_flags = fields["フラグ"][2]
    off_add_pts = fields["支援加算値"][2]
    off_link = fields["連動フラグ"][2]

    groups: list[list[dict]] = []
    for i in range(nrows):
        o = row_start + i * rec_sz
        key = _str(d, U64(d, o + 8))
        raw_flags = [fl for fl in _read_ptr_array_strs(d, o + off_flags) if fl]
        parsed_flags = []
        for fl in raw_flags:
            neg = fl.startswith("!")
            base_name = fl[1:] if neg else fl
            parsed_flags.append({
                "name": base_name,
                "hash": f"{core.id_hash(base_name):08x}",
                "negated": neg,
            })
        branch = {
            "route": d[o + off_route],
            "start_part": d[o + off_sp],
            "start_chapter": d[o + off_sc],
            "end_part": d[o + off_ep],
            "end_chapter": d[o + off_ec],
            "any_join": [p for p in _read_ptr_array_strs(d, o + off_any_join) if p],
            "flags": parsed_flags,
        }
        if key:
            p1 = _read_data_str(d, o + off_p1)
            p2 = _read_data_str(d, o + off_p2)
            rank = RANK_MAP.get(d[o + off_rank], "")
            link_flag = _str(d, U64(d, o + off_link))
            groups.append([{
                "key": key,
                "hash": core.id_hash(key),
                "p1": p1,
                "p2": p2,
                "rank": rank,
                "add_points": U16(d, o + off_add_pts),
                "link_flag": link_flag,
                "link_hash": f"{core.id_hash(link_flag):08x}" if link_flag else "",
                "branch": branch,
            }])
        else:
            groups[-1].append({"branch": branch})

    by_pair: dict[frozenset[str], dict[str, dict]] = {}
    for g in groups:
        head = g[0]
        jp1 = head["p1"].removeprefix("PID_")
        jp2 = head["p2"].removeprefix("PID_")
        rank = head["rank"]
        if not jp1 or not jp2 or not rank:
            continue
        pair_key = frozenset((jp1, jp2))
        entry = {
            "key": head["key"],
            "hash": head["hash"],
            "add_points": head["add_points"],
            "branches": [item["branch"] for item in g],
        }
        if head["link_flag"]:
            entry["link_flag"] = head["link_flag"]
            entry["link_hash"] = head["link_hash"]
        by_pair.setdefault(pair_key, {})[rank] = entry
    return by_pair


def enrich_supports_json(supports_path: Path, reliance_path: Path) -> tuple[int, int, int]:
    """Update supports.json in place with S flags and Reliance.bin conditions."""
    data = json.loads(supports_path.read_text(encoding="utf-8"))
    by_pair = parse_reliance_talks(reliance_path)

    updated_pairs = 0
    total_convs = 0
    added_s = 0
    for pr in data.get("pairs", []):
        pair_key = frozenset((pr["a"], pr["b"]))
        ranks = by_pair.get(pair_key)
        if not ranks:
            continue
        updated_pairs += 1
        flags = dict(pr.get("flags") or {})
        conds: dict[str, dict] = {}
        for r in RANK_ORDER:
            if r not in ranks:
                continue
            c = ranks[r]
            if r not in flags:
                flags[r] = c["hash"]
                if r == "S":
                    added_s += 1
            conds[r] = c
            total_convs += 1
        # Keep deterministic rank key order
        pr["flags"] = {r: flags[r] for r in RANK_ORDER if r in flags}
        if conds:
            pr["max"] = [r for r in RANK_ORDER if r in conds][-1]
            pr["conditions"] = conds

    supports_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return updated_pairs, total_convs, added_s


def main() -> int:
    if len(sys.argv) > 1:
        game_data = Path(sys.argv[1])
    elif os.environ.get("FEFW_ROM"):
        game_data = Path(os.environ["FEFW_ROM"]) / "GameData"
    else:
        game_data = HERE / "GameData"
    reliance_path = game_data / "Reliance.bin"
    if not reliance_path.is_file():
        print("usage: python build_support_conditions.py [<GameData dir>]")
        return 2
    supports_path = HERE / "supports.json"
    pairs_n, convs_n, s_n = enrich_supports_json(supports_path, reliance_path)
    print(f"Updated {pairs_n} pairs, {convs_n} conversations (+{s_n} S-rank) -> {supports_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
