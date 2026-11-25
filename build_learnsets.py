"""Build learnset_catalog.json from dumped GameData (Job.bin, Growth.bin, Person.bin, Skill*.bin, Item*.bin).

Usage: python build_learnsets.py [path to dumped GameData folder]
       (defaults to ./GameData if present, or FEFW_ROM/GameData)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
from pathlib import Path

from build_item_stack import U16, U64, _str, fnv1_64_low32, raw_rows_of

HERE = Path(__file__).resolve().parent

MASTERY_RANKS = ["E", "E+", "D", "D+", "C", "C+", "B", "B+", "A", "A+", "S", "S+"]
MASTERY_TYPES = {
    0: "剑术", 1: "枪术", 2: "斧术", 3: "箭术", 4: "格斗术",
    5: "黑魔术", 6: "白魔术", 7: "特殊",
    8: "步兵术", 9: "马术", 10: "重装术", 11: "飞行术", 12: "指挥术",
}
JOB_CC_FIELDS = (
    (0, "剣#2"), (1, "槍#2"), (2, "斧#2"), (3, "弓#2"), (4, "格闘#2"),
    (5, "黒魔#2"), (6, "白魔#2"), (7, "特殊#2"), (8, "歩兵#2"),
    (9, "騎兵#2"), (10, "重装#2"), (11, "飛行#2"), (12, "指揮#2"),
)
JOB_BONUS_FIELDS = (
    (0, "剣"), (1, "槍"), (2, "斧"), (3, "弓"), (4, "格闘"),
    (5, "黒魔"), (6, "白魔"), (7, "特殊"), (8, "歩兵"),
    (9, "騎兵"), (10, "重装"), (11, "飛行"), (12, "指揮"),
)
HIDDEN_CLASS_TAGS = frozenset({
    "SID_踊る選択不可", "SID_戦象兵専用", "SID_戦車兵専用", "SID_大型", "SID_ＺＯＣ無視",
})
WEAPON_MASTERY_RE = re.compile(r"^SID_(剣術|槍術|斧術|弓術|格闘術|黒魔術|白魔術)(?:ＬＶ[１-５]|の深奥)$")


def _str_array(data: bytes, off: int) -> list[str]:
    cnt, stride, ptr = struct.unpack_from("<HH4xQ", data, off)
    return [_str(data, U64(data, 32 + ptr + i * stride)) for i in range(cnt)]


def _str_matrix(data: bytes, off: int) -> list[list[str]]:
    cnt, stride, ptr = struct.unpack_from("<HH4xQ", data, off)
    out = []
    for idx in range(cnt):
        sub_off = 32 + ptr + idx * stride
        sc, ss, sp = struct.unpack_from("<HH4xQ", data, sub_off)
        out.append([_str(data, U64(data, 32 + sp + j * ss)) for j in range(sc)])
    return out


def _read_msbt_zst(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        import zstandard
        raw = zstandard.ZstdDecompressor().decompressobj().decompress(path.read_bytes())
    except ImportError:
        raw = subprocess.run(["zstd", "-dc", str(path)], capture_output=True, check=True).stdout
    lbl, txt = raw.find(b"LBL1"), raw.find(b"TXT2")
    if lbl < 0 or txt < 0:
        return {}
    body, labels = lbl + 16, {}
    for k in range(struct.unpack_from("<I", raw, body)[0]):
        cnt, off = struct.unpack_from("<II", raw, body + 4 + 8 * k)
        at = body + off
        for _ in range(cnt):
            ln = raw[at]
            labels[raw[at + 1:at + 1 + ln].decode("ascii", "replace")] = struct.unpack_from("<I", raw, at + 1 + ln)[0]
            at += 1 + ln + 4
    tbody = txt + 16
    count = struct.unpack_from("<I", raw, tbody)[0]
    offs = [struct.unpack_from("<I", raw, tbody + 4 + 4 * k)[0] for k in range(count)]
    texts = [
        raw[tbody + offs[k]:raw.find(b"\x00", tbody + offs[k])].decode("utf-8", "replace").split("\x0e")[0].strip()
        for k in range(count)
    ]
    return {name: texts[i] for name, i in labels.items() if 0 <= i < len(texts)}


def _load_growth_lids(growth_bytes: bytes) -> dict[str, list[tuple[int, int, int, str, list[str]]]]:
    """Parse the LID_* learnset sheet starting at 0x3100 in Growth.bin."""
    groups: dict[str, list[tuple[int, int, int, str, list[str]]]] = {}
    i = 0x3100
    idx = 0
    while i + 48 <= len(growth_bytes) and U64(growth_bytes, i) == 0 and U16(growth_bytes, i + 16) == idx:
        key = _str(growth_bytes, U64(growth_bytes, i + 8))
        cnt = U16(growth_bytes, i + 18)
        ptr = U64(growth_bytes, i + 32)
        base = 32 + ptr
        rows = []
        for r in range(cnt):
            at = base + r * 72
            lv = growth_bytes[at + 32]
            stype = growth_bytes[at + 33]
            srank = growth_bytes[at + 34]
            sk = _str(growth_bytes, U64(growth_bytes, at + 40))
            conds = _str_array(growth_bytes, at + 56)
            rows.append((lv, stype, srank, sk, conds))
        groups[key] = rows
        i += 48
        idx += 1
    return groups


def _plus_base(sid: str) -> str:
    if sid.endswith("＋＋"):
        return sid[:-2]
    if sid.endswith("＋"):
        return sid[:-1]
    return ""


def build_catalogue(game_data: Path) -> dict:
    for req in ("Job.bin", "Growth.bin", "Person.bin", "Skill.bin", "Item.bin"):
        if not (game_data / req).is_file():
            raise FileNotFoundError(f"Missing {req} in {game_data}")

    zh_json = json.loads((HERE / "chinese_names.json").read_text(encoding="utf-8"))
    unit_cat = json.loads((HERE / "unit_catalog.json").read_text(encoding="utf-8"))["entries"]
    from zh_names import CHARACTERS, CLASSES, ITEMS as ZH_ITEMS, SKILLS as ZH_SKILLS

    msg_cn = game_data.parent / "CNzh"
    cn_skill = _read_msbt_zst(msg_cn / "Skill.msbt.zst")
    cn_job = _read_msbt_zst(msg_cn / "Job.msbt.zst")
    cn_item = _read_msbt_zst(msg_cn / "Item.msbt.zst")

    # Item lookup for magic spells (SID_X -> IID_X) with official item Chinese names
    item_by_iid: dict[str, str] = {}
    item_name_by_iid: dict[str, str] = {}
    for fn in ("Item.bin", "Item_P0.bin", "Item_P1.bin"):
        p = game_data / fn
        if not p.is_file():
            continue
        d = p.read_bytes()
        for key, off, flds in raw_rows_of(d):
            if not key.startswith("IID_"):
                continue
            ih = f"{fnv1_64_low32(key):08x}"
            item_by_iid[key] = ih
            nl = _str(d, U64(d, off + flds["名前"][2]))
            item_name_by_iid[key] = (
                zh_json.get("items", {}).get(ih)
                or cn_item.get(nl)
                or ZH_ITEMS.get(key[4:])
                or ""
            )

    # Skill lookup across Skill*.bin (including 強化スキル links for forged/enhanced arts)
    skills: dict[str, dict] = {}
    enhance_next: dict[str, str] = {}
    for fn in ("Skill.bin", "Skill_P0.bin", "Skill_P1.bin"):
        p = game_data / fn
        if not p.is_file():
            continue
        d = p.read_bytes()
        for key, off, flds in raw_rows_of(d):
            if not key.startswith("SID_"):
                continue
            h_hex = f"{fnv1_64_low32(key):08x}"
            nl = _str(d, U64(d, off + flds["名前"][2]))
            upg = _str(d, U64(d, off + flds["強化スキル"][2]))
            if upg:
                enhance_next[key] = upg
            name_zh = (
                zh_json.get("skills", {}).get(h_hex)
                or zh_json.get("skill_variants", {}).get(h_hex)
                or cn_skill.get(nl)
                or ZH_SKILLS.get(key[4:])
                or cn_item.get("MIID_" + key[4:])
                or ZH_ITEMS.get(key[4:])
                or key[4:]
            )
            suffix = ""
            if key.endswith("＋＋") and not name_zh.endswith(("＋＋", "++")):
                suffix = "＋＋"
            elif key.endswith("＋") and not name_zh.endswith(("＋", "+")):
                suffix = "＋"
            skills[key] = {
                "hash": h_hex,
                "sid": key,
                "name": name_zh + suffix,
                "kind": d[off + flds["種類"][2]],
                "prio": d[off + flds["習得優先"][2]],
                "disp": struct.unpack_from("<H", d, off + flds["表示"][2])[0],
                "has_label": bool(nl),
            }

    # Load Growth.bin LID_* and Person.bin first so upgrade groups are strictly
    # scoped to skills that actually have multi-tier priority progression in learnsets.
    lid_groups = _load_growth_lids((game_data / "Growth.bin").read_bytes())
    pdata = (game_data / "Person.bin").read_bytes()
    person_lids: dict[str, list[str]] = {}
    for key, off, flds in raw_rows_of(pdata):
        person_lids[key] = _str_array(pdata, off + flds["技能習得"][2])

    catalog_lids = {"LID_汎用"}
    for row in unit_cat.values():
        catalog_lids.update(person_lids.get(row["pid"], []))
    learnable_sids = {
        sk for lid in catalog_lids for _, _, _, sk, _ in lid_groups.get(lid, []) if sk
    }

    # Evidence-backed ＋/＋＋ replacement bases: both base and ＋/＋＋ exist in learnable
    # tables AND every member has 習得優先 (prio) > 0 (excludes prio=0 personal/innate traits).
    plus_bases: set[str] = set()
    by_plus_base: dict[str, list[str]] = {}
    for sid in learnable_sids:
        b = _plus_base(sid) or sid
        if b in learnable_sids and skills.get(sid, {}).get("prio", 0) > 0:
            by_plus_base.setdefault(b, []).append(sid)
    for b, members in by_plus_base.items():
        if b in members and len(members) > 1 and all(skills.get(m, {}).get("prio", 0) > 0 for m in members):
            plus_bases.add(b)

    def _resolve_magic_item(sid: str) -> tuple[str, str]:
        exact_iid = "IID_" + sid[4:]
        if exact_iid in item_by_iid:
            return item_by_iid[exact_iid], item_name_by_iid.get(exact_iid, "")
        stem_iid = "IID_" + sid[4:].split("[")[0]
        if stem_iid in item_by_iid:
            return item_by_iid[stem_iid], item_name_by_iid.get(stem_iid, "")
        return "", ""

    def skill_entry(sid: str, *, stype: int | None = None) -> dict:
        info = skills.get(sid, {"hash": f"{fnv1_64_low32(sid):08x}", "sid": sid, "name": sid[4:],
                                "kind": 0, "prio": 0, "disp": 0, "has_label": False})
        item_hash, item_name = _resolve_magic_item(sid)
        kind = info["kind"]
        if item_hash and ((stype in (5, 6) and kind == 0) or (stype == 0 and kind == 0)):
            cat = "magic"
        elif kind == 2:
            cat = "art"
        elif kind == 3:
            cat = "blaze"
        elif kind in (1, 10, 11, 12):
            cat = "skill"
        else:
            cat = "system"

        name = (item_name or info["name"]) if cat == "magic" else info["name"]

        m = WEAPON_MASTERY_RE.match(sid)
        if m and info["prio"] > 0:
            ug = f"mastery_lv:{m.group(1)}"
        else:
            b = _plus_base(sid) or sid
            ug = f"plus:{b}" if b in plus_bases and info["prio"] > 0 else ""

        out = {
            "hash": info["hash"],
            "sid": sid,
            "name": name,
            "category": cat,
            "kind": kind,
            "prio": info["prio"],
        }
        if ug:
            out["upgrade_group"] = ug
        if item_hash and cat == "magic":
            out["item_hash"] = item_hash
        return out

    # 1. Playable/learnable classes (成長経験 > 0)
    jdata = (game_data / "Job.bin").read_bytes()
    classes: dict[str, dict] = {}
    dismount_map: dict[str, str] = {}
    raw_jobs = list(raw_rows_of(jdata, keep_duplicate_fields=True))
    for key, off, flds in raw_jobs:
        ptr = U64(jdata, off + flds["下馬先"][2])
        if ptr:
            dm = _str(jdata, U64(jdata, 32 + ptr + 8))
            if dm:
                dismount_map[dm] = key

    for key, off, flds in raw_jobs:
        mexp = U16(jdata, off + flds["成長経験"][2])
        if mexp == 0:
            continue
        h_hex = f"{fnv1_64_low32(key):08x}"
        nl = _str(jdata, U64(jdata, off + flds["名前"][2]))
        name_zh = (
            zh_json.get("classes", {}).get(h_hex)
            or zh_json.get("class_variants", {}).get(h_hex)
            or cn_job.get(nl)
            or CLASSES.get(key)
            or key[4:]
        )
        cc_all = {
            str(mtype): jdata[off + flds[fld][2]]
            for mtype, fld in JOB_CC_FIELDS
            if jdata[off + flds[fld][2]] > 0
        }
        choice_ids = {
            str(jdata[off + flds[fld][2]])
            for fld in ("共有１", "共有２", "共有３")
            if jdata[off + flds[fld][2]] < 15
        }
        cc_main = {k: v for k, v in cc_all.items() if k not in choice_ids}
        cc_choice = {k: v for k, v in cc_all.items() if k in choice_ids}
        cc_item = _str(jdata, U64(jdata, off + flds["CC必要アイテム"][2]))
        cc_item_zh = item_name_by_iid.get(cc_item, cc_item[4:] if cc_item.startswith("IID_") else cc_item)
        cc_fail_penalty = struct.unpack_from("<b", jdata, off + flds["不合格係数"][2])[0]
        cc_unlock = _str_matrix(jdata, off + flds["CC必要条件"][2])
        cc_reveal = _str_matrix(jdata, off + flds["CC秘匿条件"][2])
        cc_banned = _str_array(jdata, off + flds["CC禁止キャラ"][2])

        skill_bonuses = {
            str(mtype): struct.unpack_from("<b", jdata, off + flds[fld][2])[0]
            for mtype, fld in JOB_BONUS_FIELDS
            if struct.unpack_from("<b", jdata, off + flds[fld][2])[0] >= 0
        }
        innate = [skill_entry(s) for s in _str_array(jdata, off + flds["所持スキル"][2]) if s not in HIDDEN_CLASS_TAGS]
        class_arts = [skill_entry(s) for s in _str_array(jdata, off + flds["戦技スキル"][2]) if s not in HIDDEN_CLASS_TAGS]
        master = [skill_entry(s) for s in _str_array(jdata, off + flds["マスタースキル"][2])]
        classes[h_hex] = {
            "jid": key,
            "name": name_zh,
            "rank": jdata[off + flds["ランク"][2]],
            "mastery_exp": mexp,
            "dismount_of": dismount_map.get(key, ""),
            "cc_main": cc_main,
            "cc_choice": cc_choice,
            "cc_item": cc_item_zh,
            "cc_fail_penalty": cc_fail_penalty,
            "cc_unlock_conditions": cc_unlock,
            "cc_reveal_conditions": cc_reveal,
            "cc_banned_units": cc_banned,
            "skill_bonuses": skill_bonuses,
            "master_skills": master,
            "innate_skills": innate,
            "class_arts": class_arts,
        }

    def convert_lid_rows(rows: list[tuple[int, int, int, str, list[str]]]) -> list[dict]:
        out = []
        for lv, stype, srank, sk, conds in rows:
            if not sk:
                continue
            entry = skill_entry(sk, stype=stype)
            entry["level"] = lv
            entry["mastery_type"] = stype
            entry["mastery_rank"] = srank
            entry["rank_label"] = MASTERY_RANKS[srank - 1] if 1 <= srank <= len(MASTERY_RANKS) else ""
            if conds:
                entry["conditions"] = conds
            out.append(entry)
        return out

    universal = convert_lid_rows(lid_groups.get("LID_汎用", []))

    characters: dict[str, dict] = {}
    for h_hex, row in unit_cat.items():
        pid = row["pid"]
        lids = person_lids.get(pid, [])
        uses_universal = "LID_汎用" in lids
        entries: list[dict] = []
        for lid in lids:
            if lid == "LID_汎用":
                continue
            entries.extend(convert_lid_rows(lid_groups.get(lid, [])))
        name_zh = (
            zh_json.get("units", {}).get(h_hex)
            or CHARACTERS.get(row["name"], row["name"])
        )
        characters[h_hex] = {
            "pid": pid,
            "name": name_zh,
            "name_en": row["name"],
            "lids": lids,
            "uses_universal": uses_universal,
            "entries": entries,
        }

    # Transitive 強化スキル closure (base -> [enhanced_1, ...]) so auto-completion
    # never re-adds a base art when the unit already owns its enhanced ＋ variant.
    enhancements: dict[str, list[str]] = {}
    for base_sid, nxt in enhance_next.items():
        chain: list[str] = []
        seen = {base_sid}
        cur = nxt
        while cur and cur not in seen:
            seen.add(cur)
            chain.append(f"{fnv1_64_low32(cur):08x}")
            cur = enhance_next.get(cur, "")
        if chain:
            enhancements[f"{fnv1_64_low32(base_sid):08x}"] = chain

    return {
        "schema": 1,
        "mastery_ranks": MASTERY_RANKS,
        "mastery_types": {str(k): v for k, v in MASTERY_TYPES.items()},
        "enhancements": enhancements,
        "classes": classes,
        "universal_mastery": universal,
        "characters": characters,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_data", nargs="?", type=Path,
                        help="Path to dumped GameData folder (defaults to ./GameData or FEFW_ROM/GameData)")
    args = parser.parse_args()
    game_data = args.game_data
    if game_data is None:
        if (HERE / "GameData").is_dir():
            game_data = HERE / "GameData"
        elif os.environ.get("FEFW_ROM"):
            game_data = Path(os.environ["FEFW_ROM"]) / "GameData"
    if game_data is None or not game_data.is_dir():
        parser.error("Provide <GameData folder> or set FEFW_ROM")
    cat = build_catalogue(game_data)
    out = HERE / "learnset_catalog.json"
    out.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(cat['classes'])} classes, {len(cat['universal_mastery'])} universal mastery entries, "
          f"{len(cat['characters'])} characters -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
