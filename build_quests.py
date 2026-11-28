"""build_quests.py - build quest_catalog.json from GameData and CNzh MSBT files.

Usage: python build_quests.py [<path to GameData>] [<path to CNzh>]
Defaults to ./GameData and ./CNzh in the repository root.
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from build_item_stack import U64, _str, fnv1_64_low32, raw_rows_of  # noqa: E402
import fefw_core as core                                          # noqa: E402
import msbt                                                       # noqa: E402


def _u8_array(data: bytes, off: int) -> list[int]:
    cnt, stride, ptr = struct.unpack_from("<HH4xQ", data, off)
    return [data[32 + ptr + i * stride] for i in range(cnt)]


def _str_array(data: bytes, off: int) -> list[str]:
    cnt, stride, ptr = struct.unpack_from("<HH4xQ", data, off)
    return [_str(data, U64(data, 32 + ptr + i * stride)) for i in range(cnt)]


def _data_array_strs(data: bytes, off: int) -> list[str]:
    cnt, stride, ptr = struct.unpack_from("<HH4xQ", data, off)
    return [_str(data, U64(data, 32 + ptr + i * stride)) for i in range(cnt)]


def _load_unit_zh(here: Path) -> dict[str, str]:
    """Map PID_* -> Chinese/localized name from unit_ids.json + chinese_names.json."""
    out: dict[str, str] = {}
    unit_ids_path = here / "unit_ids.json"
    zh_path = here / "chinese_names.json"
    zh_units: dict[str, str] = {}
    if zh_path.is_file():
        zh_units = json.loads(zh_path.read_text(encoding="utf-8")).get("units", {})
    if unit_ids_path.is_file():
        units = json.loads(unit_ids_path.read_text(encoding="utf-8"))
        for h_hex, meta in units.items():
            pid = (meta or {}).get("pid") or ""
            if pid:
                out[pid] = zh_units.get(h_hex) or meta.get("name") or pid
    return out


def _extract_fame_thresholds(game_data: Path) -> list[dict]:
    """Extract renown level thresholds from Reward.bin's 名声 sheet."""
    reward_path = game_data / "Reward.bin"
    if not reward_path.is_file():
        return []
    d = reward_path.read_bytes()
    # Locate the first row of the 名声 sheet (idx=0, u16=1, +32=(1, 1, 0))
    marker = d.find("名声".encode("utf-8") + b"\x00")
    if marker < 0:
        return []
    base = None
    for off in range(0xD000, min(len(d), 0x10000), 8):
        if (struct.unpack_from("<Q", d, off)[0] == 0
                and struct.unpack_from("<HHI", d, off + 16)[:2] == (0, 1)
                and d[off + 32:off + 36] == b"\x01\x01\x00\x00"):
            base = off
            break
    if base is None:
        return []
    thresholds = []
    total = 0
    for i in range(21):
        off = base + i * 120
        if off + 36 > len(d):
            break
        lv, part = d[off + 32], d[off + 33]
        delta = struct.unpack_from("<H", d, off + 34)[0]
        if lv != i + 1:
            break
        total += delta
        thresholds.append({"level": lv, "part": part, "points": total})
    return thresholds


def _build_recruitments(here: Path, sidestories: list[dict], subquests: list[dict],
                        unit_zh: dict[str, str]) -> list[dict]:
    uc_path = here / "unit_catalog.json"
    fn_path = here / "flag_names.json"
    if not uc_path.is_file():
        return []
    uc = json.loads(uc_path.read_text(encoding="utf-8")).get("entries", {})
    fn_names: dict[str, str] = {}
    if fn_path.is_file():
        fn_raw = json.loads(fn_path.read_text(encoding="utf-8"))
        fn_names = {v["name"]: h for h, v in fn_raw.items() if v.get("name")}

    ss_by_pid = {s["target_pid"]: s["key"] for s in sidestories if s.get("target_pid")}
    sq_keys = [s["key"] for s in subquests]

    out = []
    for h_hex, meta in uc.items():
        pid = meta.get("pid", "")
        jp = pid.removeprefix("PID_")
        if not jp:
            continue
        scout_name = f"ローカル_第一部_スカウト可能_{jp}"
        scout_hash = fn_names.get(scout_name, "")
        lockout_flags = [
            {"name": name, "hash": f_hex}
            for name, f_hex in sorted(fn_names.items())
            if re.search(rf"(?:_|と){re.escape(jp)}(?:が|と)", name) and "加入した" in name
        ]
        # Match negotiation subquests like SQID_<jp>交渉*
        rel_sq = [k for k in sq_keys if k.startswith(f"SQID_{jp}交渉")]
        rel_ss = ss_by_pid.get(pid, "")
        special = ""
        if jp == "マジーデ" and "イベント_スカウト_マジーデ特殊交渉_進行度" in fn_names:
            special = "イベント_スカウト_マジーデ特殊交渉_進行度"

        out.append({
            "person_hash": h_hex,
            "pid": pid,
            "jp_name": jp,
            "name": unit_zh.get(pid) or meta.get("name") or jp,
            "recruit_flag": meta.get("recruit_flag", ""),
            "recruit_hash": fn_names.get(meta.get("recruit_flag", ""), ""),
            "met_flag": meta.get("met_flag", ""),
            "met_hash": fn_names.get(meta.get("met_flag", ""), ""),
            "scout_flag": scout_name if scout_hash else "",
            "scout_hash": scout_hash,
            "lockout_flags": lockout_flags,
            "related_subquests": rel_sq,
            "related_sidestory": rel_ss,
            "special_flag": special,
            "special_hash": fn_names.get(special, "") if special else "",
        })
    return out


def build_catalog(game_data: Path, cn_dir: Path) -> dict:
    quest_msgs = msbt.read(str(cn_dir / "Quest.msbt.zst"))
    battle_title_path = cn_dir / "BattleTitle.msbt.zst"
    if battle_title_path.is_file():
        quest_msgs.update(msbt.read(str(battle_title_path)))
    unit_zh = _load_unit_zh(Path(HERE))

    sidestories = []
    ss_bytes = (game_data / "SideStory.bin").read_bytes()
    for key, off, fields in raw_rows_of(ss_bytes):
        name_key = _str(ss_bytes, U64(ss_bytes, off + fields["名前"][2]))
        detail_key = _str(ss_bytes, U64(ss_bytes, off + fields["内容"][2]))
        goal_key = _str(ss_bytes, U64(ss_bytes, off + fields["達成条件"][2]))
        target_pid = _str(ss_bytes, U64(ss_bytes, off + fields["対象者"][2]))
        sup_pid = _str(ss_bytes, U64(ss_bytes, off + fields["支援対象"][2]))
        spot = _str(ss_bytes, U64(ss_bytes, off + fields["スポット"][2]))
        accept_flag = _str(ss_bytes, U64(ss_bytes, off + fields["受注フラグ"][2]))
        prep_flag = _str(ss_bytes, U64(ss_bytes, off + fields["準備フラグ"][2]))
        clear_flag = _str(ss_bytes, U64(ss_bytes, off + fields["フラグ"][2]))

        routes = _u8_array(ss_bytes, off + fields["ルート"][2])
        acc_m = _u8_array(ss_bytes, off + fields["受注終了（月）"][2])
        acc_d = _u8_array(ss_bytes, off + fields["受注終了（日）"][2])
        occ_m = _u8_array(ss_bytes, off + fields["発生終了（月）"][2])
        occ_d = _u8_array(ss_bytes, off + fields["発生終了（日）"][2])
        route_deadlines = {}
        for idx, r in enumerate(routes):
            route_deadlines[str(r)] = {
                "accept_month": acc_m[idx] if idx < len(acc_m) else 0,
                "accept_day": acc_d[idx] if idx < len(acc_d) else 0,
                "occur_month": occ_m[idx] if idx < len(occ_m) else 0,
                "occur_day": occ_d[idx] if idx < len(occ_d) else 0,
            }

        sidestories.append({
            "key": key,
            "hash64": f"{core.fnv1_64(key.encode('utf-8')):016x}",
            "hash32": f"{fnv1_64_low32(key):08x}",
            "title": quest_msgs.get(name_key, name_key),
            "goal": quest_msgs.get(goal_key, "").replace("\n", " "),
            "detail": quest_msgs.get(detail_key, "").replace("\n", " "),
            "target_pid": target_pid,
            "target_name": unit_zh.get(target_pid, target_pid.removeprefix("PID_")),
            "support_pid": sup_pid,
            "support_name": unit_zh.get(sup_pid, sup_pid.removeprefix("PID_")) if sup_pid else "",
            "support_level": ss_bytes[off + fields["支援レベル"][2]],
            "route_mask": ss_bytes[off + fields["発生するルート"][2]],
            "start_month": ss_bytes[off + fields["開始(月)"][2]],
            "start_day": ss_bytes[off + fields["開始(日)"][2]],
            "end_month": ss_bytes[off + fields["終了(月)"][2]],
            "end_day": ss_bytes[off + fields["終了(日)"][2]],
            "time_hhmm": struct.unpack_from("<h", ss_bytes, off + fields["時刻"][2])[0],
            "route_deadlines": route_deadlines,
            "spot": spot,
            "flags": {
                "accept": accept_flag,
                "accept_hash": f"{fnv1_64_low32(accept_flag):08x}" if accept_flag else "",
                "prep": prep_flag,
                "prep_hash": f"{fnv1_64_low32(prep_flag):08x}" if prep_flag else "",
                "clear": clear_flag,
                "clear_hash": f"{fnv1_64_low32(clear_flag):08x}" if clear_flag else "",
            },
        })

    subquests = []
    for fn in ("Subquest.bin", "Subquest_P1.bin"):
        path = game_data / fn
        if not path.is_file():
            continue
        sq_bytes = path.read_bytes()
        for key, off, fields in raw_rows_of(sq_bytes):
            name_key = _str(sq_bytes, U64(sq_bytes, off + fields["名前"][2]))
            detail_key = _str(sq_bytes, U64(sq_bytes, off + fields["内容"][2]))
            goal_key = _str(sq_bytes, U64(sq_bytes, off + fields["達成条件"][2]))
            client_key = _str(sq_bytes, U64(sq_bytes, off + fields["依頼人"][2]))
            prereq_flags = _str_array(sq_bytes, off + fields["必要フラグ"][2])
            prereq_quests = [q for q in _data_array_strs(sq_bytes, off + fields["クリア済み"][2]) if q]

            subquests.append({
                "key": key,
                "hash32": f"{fnv1_64_low32(key):08x}",
                "title": quest_msgs.get(name_key, name_key),
                "client": quest_msgs.get(client_key, client_key),
                "goal": quest_msgs.get(goal_key, "").replace("\n", " "),
                "detail": quest_msgs.get(detail_key, "").replace("\n", " "),
                "kind": sq_bytes[off + fields["種類"][2]],
                "subkind": sq_bytes[off + fields["種類（サブ）"][2]],
                "rec_level": sq_bytes[off + fields["推奨レベル"][2]],
                "route": sq_bytes[off + fields["ルート"][2]],
                "exclude_routes": _u8_array(sq_bytes, off + fields["除外ルート"][2]),
                "min_playthrough": sq_bytes[off + fields["開始（○ルート目）"][2]],
                "start_part": sq_bytes[off + fields["開始(部)"][2]],
                "start_chapter": sq_bytes[off + fields["開始(章)"][2]],
                "end_part": sq_bytes[off + fields["終了(部)"][2]],
                "end_chapter": sq_bytes[off + fields["終了(章)"][2]],
                "fame_level": sq_bytes[off + fields["名声レベル"][2]],
                "prereq_quests": prereq_quests,
                "prereq_flags": [
                    {"name": fl, "hash": f"{fnv1_64_low32(fl):08x}"}
                    for fl in prereq_flags if fl
                ],
                "and_or": sq_bytes[off + fields["AND/OR"][2]],
            })

    fame_thresholds = _extract_fame_thresholds(game_data)
    recruitments = _build_recruitments(Path(HERE), sidestories, subquests, unit_zh)

    return {
        "schema": 1,
        "fame_thresholds": fame_thresholds,
        "sidestories": sidestories,
        "subquests": subquests,
        "recruitments": recruitments,
    }


def main() -> int:
    game_data = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(HERE) / "GameData"
    cn_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(HERE) / "CNzh"
    if not (game_data / "SideStory.bin").is_file():
        print("usage: python build_quests.py [<GameData dir>] [<CNzh dir>]")
        return 2
    cat = build_catalog(game_data, cn_dir)
    out = Path(HERE) / "quest_catalog.json"
    out.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(cat['sidestories'])} sidestories, {len(cat['subquests'])} subquests -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
