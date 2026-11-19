"""Build exact name mappings from a user's own Japanese and Chinese MSBT files.

Usage: python build_chinese_names.py ROMFS CHINESE_LANGUAGE_FOLDER
Example: python build_chinese_names.py /path/to/romfs CNzh

Matches the same message label in corresponding files. Ambiguous translations
are excluded, rather than guessing. Does not alter the original ID tables.

Categories written to chinese_names.json:
  items / skills / classes / units — text pairing (as before) merged with
      direct label identity (PID_Ursula <-> MPID_Ursula, JID_/MIID_ likewise);
  skill_variants — known skill IDs using level templates, numeric modifiers
      or a different Japanese spelling from their displayed name in Skill.msbt;
  class_variants — internal copies and other known variants of class names;
  tutorials — official titles via each entry's title_id in Tutorial.msbt;
  places — official Spot.msbt names, generic road-network points by pattern,
      and the hand list below for spots whose message spelling differs;
  spkinds — Chinese map-point kind names (hand list using the game's own
      vocabulary from System_Gmap.msbt, e.g. 神殿 / 驿站 / 堡垒).
  flags — exact quest titles, locations and menu terms used by known keys.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
from pathlib import Path
import re
import unicodedata

import msbt

HERE = Path(__file__).resolve().parent


def norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


# spot names whose map spelling differs from the message text, resolved by the
# exact message label (same table as build_map_spots.py; the first five take
# the label's official text, minus the " 主殿" suffix of the temple's main hall)
PLACE_LABELS = {
    "フォトナ神殿": ("MID_Spot_TempleFotona", " 主殿"),
    "ヘカトンケイル大要塞": ("MID_Spot_Hecatoncheir", ""),
    "コーエンの廊": ("MID_Spot_CoenGallery", ""),
    "叫獣峡谷": ("MID_Spot_BeastGorge", ""),
    "コロイオス砦跡": ("MID_Spot_KoloiosFortress", ""),
}

# places with no message entry at all: derived from the game's own spellings
# (エリッサ峠 -> 埃利萨岭, 竜の目の塔 -> 龙眼之塔); marked provisional in the doc
HAND_PLACES = {
    "エリッサ平原": "埃利萨平原",
    "竜の目の祠": "龙眼之祠",
    "ノアの洞窟": "诺亚洞窟",
    "イアベトス大洞窟": "伊阿贝托斯大洞窟",
}

# internal names of the unnamed road-network points -> Chinese (mirrors the
# GENERIC list in build_map_spots.py)
GENERIC_ZH = [
    (r"^中スポット(\d+)$", "中间点 {0}"),
    (r"^分岐点(\d+)$", "分岔点 {0}"),
    (r"^砂漠用分岐(\d+)$", "沙漠分岔点 {0}"),
    (r"^ソロモン用砂漠(\d+)$", "所罗门沙漠点 {0}"),
    (r"^リル海分岐(\d+)$", "里尔海分岔点 {0}"),
    (r"^ダナン海分岐(\d+)$", "达南海分岔点 {0}"),
    (r"^イベント用(\d+)$", "事件点 {0}"),
]

# editor-side kind groupings (map_spots.json "kinds"), translated with the
# game's own words where it has them (System_Gmap SpotKind: 神殿/驿站/堡垒/迷宫)
KIND_ZH = {
    0: "主要城市", 1: "城镇", 2: "神殿", 3: "驿站", 4: "堡垒", 5: "地标",
    6: "原野/山道", 7: "祠堂/石碑", 9: "洞窟/矿山", 10: "原野/山道",
    11: "中间点", 12: "分岔点", 13: "道路点",
}


def zh_table(zh_dir: Path, filename: str, prefix: str) -> dict[str, str]:
    """{label suffix: Chinese text} from one message file, names only."""
    path = zh_dir / filename
    if not path.is_file():
        return {}
    return {label[len(prefix) + 1:]: text.strip()
            for label, text in msbt.read(str(path)).items()
            if label.startswith(prefix + "_") and "_H_" not in label and text.strip()}


def generic_zh(jp: str) -> str:
    for pattern, fmt in GENERIC_ZH:
        m = re.match(pattern, jp)
        if m:
            return fmt.format(*(str(int(g)) for g in m.groups()))
    return ""


def compound_zh(jp: str, known: dict[str, str]) -> str:
    """Road points named after their chain: 中スポット12_分岐点3_4.

    Translate every part (official place, generic pattern or bare index) and
    join with a middle dot; anything untranslatable keeps the whole name out,
    rather than half-translating it.
    """
    parts = jp.split("_")
    if len(parts) < 2:
        return ""
    out = []
    for part in parts:
        zh = known.get(part) or HAND_PLACES.get(part) or generic_zh(part)
        if not zh:
            if part.isdigit():
                zh = part
            else:
                return ""
        out.append(zh)
    return "·".join(out)


def unique_pairs(japanese: dict[str, str], chinese: dict[str, str]) -> dict[str, str]:
    pairs: dict[str, set[str]] = collections.defaultdict(set)
    for label, text in japanese.items():
        # Description/help strings are not names.
        if "_H_" in label:
            continue
        jp, zh = norm(text), chinese.get(label, "").strip()
        if jp and zh and re.search(r"[\u3400-\u9fff]", zh):
            pairs[jp].add(zh)
    return {jp: next(iter(values)) for jp, values in pairs.items() if len(values) == 1}


def skill_variants_zh(jp_dir: Path, zh_dir: Path, skills: dict, exact: dict) -> dict[str, str]:
    """Resolve names whose stored Japanese keys differ from the displayed text."""
    jp = zh_table(jp_dir, "Skill.msbt.zst", "MSID")
    zh = zh_table(zh_dir, "Skill.msbt.zst", "MSID")
    titles = unique_pairs(jp, zh)
    # These seven titles take a level argument in the game message.
    level_labels = ("SwordSkill", "SpearSkill", "Axeskill", "Bowskill",
                    "GauntletSkill", "BlackMagicSkill", "WhiteMagicSkill")
    bases = {norm(jp[label]): zh[label] for label in level_labels if label in jp and label in zh}
    aliases = {}
    # The internal key uses 戦略; the name shown in the Japanese UI uses 戦術.
    if jp.get("AntiSniper") == "対狙撃戦術" and "AntiSniper" in zh:
        aliases["対狙撃戦略"] = zh["AntiSniper"]

    result = {}
    for h, record in skills.items():
        if h in exact:
            continue
        name = norm(record.get("jp", ""))
        if name in aliases:
            result[h] = aliases[name]
            continue
        match = re.fullmatch(r"(.+)LV([1-5])", name)
        if match and (base := bases.get(match[1])):
            result[h] = f"{base} LV{match[2]}"
            continue
        # Keys such as 天風[＋１] carry a numeric modifier absent from the
        # title message. Only reuse a base with an unambiguous skill title.
        match = re.fullmatch(r"(.+)\[\+([1-9][0-9]*)\]", name)
        if match and (base := titles.get(match[1])):
            result[h] = f"{base} +{match[2]}"
    return result


def class_variants_zh(zh_dir: Path, jobs: dict, exact: dict) -> dict[str, str]:
    """Use the official names of known JID aliases without changing their IDs."""
    titles = zh_table(zh_dir, "Job.msbt.zst", "MJID")
    rides = zh_table(zh_dir, "Ride.msbt.zst", "MRID")
    people = zh_table(zh_dir, "Person.msbt.zst", "MPID")
    # These older Japanese JIDs name the same class as the indicated message.
    job_aliases = {
        "バロール竜": "NabataeanDragon_Balor", "ダグザ竜": "NabataeanDragon_Dagda",
        "マーズ竜": "NabataeanDragon_Mars", "ライラー竜_2部": "NabataeanDragon_Lyra",
        "ライラー繭": "NabataeanDragon_LyraCradle", "バロールの影": "shadowofbalor",
        "アトラスベア小": "atlasbearS", "巨鳥ルフ": "giantrukh",
        "クイン・ヒュドラー": "queenhydra", "サンドヴィーグル": "sandvegle",
    }
    # Animal and named monster variants without a Job title use their name
    # from the official Ride or Person message rather than an English gloss.
    ride_aliases = {
        "ウマ": "DefaultHorse", "黒馬": "BlackHorse", "汗血馬": "BloodHorse",
        "モノケロース": "Unicorn", "ペガサス": "DefaultPegasus", "ファルコン": "Falcon",
        "オルニウス": "DefaultOrnius", "オルニウス白": "WhiteOrnius",
        "メガニウス": "Meganius", "マジニウス": "Maginius",
        "バウ": "DefaultBaw", "ダグザンゾウ": "DefaultDagdaElephant",
    }
    person_aliases = {"ネコ": "Cat", "マミー金": "GoldMummy", "マミー銀": "SilverMummy"}
    by_key = {norm(row["jp"]): h for h, row in jobs.items()}
    result = {}
    for h, row in jobs.items():
        if h in exact:
            continue
        key = norm(row["jp"])
        if not key.startswith("JID_"):
            continue
        stem = key.removeprefix("JID_").removeprefix("H_")
        on_foot = stem.endswith("下馬")
        stem = stem.removesuffix("下馬")
        base_key = "JID_" + stem
        if base_key not in by_key:
            continue
        name = (exact.get(by_key[base_key]) or titles.get(job_aliases.get(stem, ""))
                or rides.get(ride_aliases.get(stem, "")) or people.get(person_aliases.get(stem, "")))
        if name:
            result[h] = name + ("（徒步）" if on_foot else "")
    return result


def flag_names_zh(jp_dir: Path, zh_dir: Path) -> dict[str, str]:
    def terms(files: tuple[str, ...], prefix: str = "") -> dict[str, str]:
        candidates = collections.defaultdict(set)
        for file in files:
            jp_path, zh_path = (folder / (file + ".msbt.zst") for folder in (jp_dir, zh_dir))
            if not jp_path.is_file() or not zh_path.is_file():
                continue
            jp, zh = msbt.read(str(jp_path)), msbt.read(str(zh_path))
            for label in jp.keys() & zh.keys():
                source, target = jp[label].strip(), zh[label].strip()
                if (label.startswith(prefix) and "_H_" not in label
                        and 0 < len(source) <= 50 and 0 < len(target) <= 60
                        and "\n" not in source + target and re.search(r"[\u3400-\u9fff]", target)):
                    candidates[norm(source)].add(target)
        return {key: next(iter(values)) for key, values in candidates.items() if len(values) == 1}

    quests = terms(("Quest",), "MSQID_Title_")
    places = terms(("Spot",))
    menus = terms(("System", "System_Hub", "System_Bmap", "System_Gmap", "System_Facility", "Tutorial"))
    flags = json.loads((HERE / "flag_names.json").read_text(encoding="utf8"))
    result = {}
    for h, row in flags.items():
        key = row["name"]
        prefix, _, rest = key.partition("_")
        if prefix == "サブクエ":
            title, _, state = rest.rpartition("_")
            status = {"受注中": "正在进行", "クリア済": "已完成"}.get(state)
            name = quests.get(norm(title))
            if status and name:
                result[h] = f"支线任务：{name}（{status}）"
        elif prefix in ("機能開放", "禁止"):
            # Internal save abbreviations that differ from the official menu wording.
            term = {"保存": "セーブ"}.get(rest, rest)
            name = menus.get(norm(term))
            if name:
                action = "功能解锁" if prefix == "機能開放" else "禁用"
                result[h] = f"{action}：{name}"
        elif prefix == "ファストトラベル":
            name = places.get(norm(rest))
            if name:
                result[h] = f"快速传送：{name}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("romfs", type=Path)
    parser.add_argument("chinese_language_folder", help="folder name inside Msg, e.g. CNzh")
    args = parser.parse_args()
    msg = args.romfs / "Msg"
    jp_dir, zh_dir = msg / "JPja", msg / args.chinese_language_folder
    if not jp_dir.is_dir() or not zh_dir.is_dir():
        parser.error("Japanese or Chinese message folder not found inside ROMFS/Msg")

    result = {}
    report = []
    # Limit each mapping to its own message file, so the same Japanese word in
    # unrelated contexts cannot silently select the wrong Chinese translation.
    for category, prefix, filename, field in (
        ("items", "Item", "item_ids.json", "jp"),
        ("skills", "Skill", "skill_ids.json", "jp"),
        ("classes", "Job", "job_ids.json", "jp"),
        ("units", "Person", "unit_ids.json", "pid"),
    ):
        candidates = collections.defaultdict(set)
        for jp_path in sorted(jp_dir.glob(prefix + "*.msbt*")):
            zh_path = zh_dir / jp_path.name
            if not zh_path.is_file():
                continue
            for jp, zh in unique_pairs(msbt.read(str(jp_path)), msbt.read(str(zh_path))).items():
                candidates[jp].add(zh)
        unique = {k: next(iter(v)) for k, v in candidates.items() if len(v) == 1}
        table = json.loads((HERE / filename).read_text(encoding="utf8"))
        translated = {}
        for key, record in table.items():
            original = norm(record.get(field, ""))
            if category == "classes" and original.startswith("JID_"):
                original = original[4:]
            if category == "units" and original.startswith("PID_"):
                original = original[4:]
            # Forged items use the base name and keep their level in the UI.
            if category == "items":
                original = re.sub(r"\+\d+$", "", original)
            if original in unique:
                translated[key] = unique[original]
        # Direct label identity: the message label itself names the entity
        # (PID_Ursula <-> MPID_Ursula), which no Japanese text can contradict.
        direct = {"units": zh_table(zh_dir, "Person.msbt.zst", "MPID"),
                  "classes": zh_table(zh_dir, "Job.msbt.zst", "MJID"),
                  "items": zh_table(zh_dir, "Item.msbt.zst", "MIID")}.get(category, {})
        for key, record in table.items():
            suffix = norm(record.get(field, ""))
            if category == "classes" and suffix.startswith("JID_"):
                suffix = suffix[4:]
            elif category != "classes" and suffix.startswith(("PID_", "MIID_")):
                suffix = suffix[4:]
            if category == "items":
                suffix = re.sub(r"\+\d+$", "", suffix)
            official = direct.get(suffix)
            if official and key in translated and translated[key] != official:
                parser.error(f"{category} {key}: label and text pairing disagree "
                             f"({official!r} vs {translated[key]!r})")
            if official:
                translated[key] = official
        result[category] = translated
        for key, label in translated.items():
            record = table[key]
            report.append((category, key, record.get(field, ""),
                           record.get("en") or record.get("name") or "", label))
        print(f"{category}: {len(translated)} / {len(table)} entries matched")
        if category in ("skills", "classes"):
            if category == "skills":
                variant_category = "skill_variants"
                variants = skill_variants_zh(jp_dir, zh_dir, table, translated)
            else:
                variant_category = "class_variants"
                variants = class_variants_zh(zh_dir, table, translated)
            result[variant_category] = variants
            for key, label in variants.items():
                record = table[key]
                report.append((variant_category, key, record["jp"], record.get("en", ""), label))
            print(f"{variant_category}: {len(variants)} names resolved from official titles")

    # Tutorial titles: each catalogue entry carries the message label of its
    # own title, so the official text is a direct lookup.
    tut_labels = msbt.read(str(zh_dir / "Tutorial.msbt.zst")) if (zh_dir / "Tutorial.msbt.zst").is_file() else {}
    tut = json.loads((HERE / "tutorial_names.json").read_text(encoding="utf8"))["entries"]
    result["tutorials"] = {f"{int(h, 16) & 0xFFFFFFFF:08x}": tut_labels[row["title_id"]].strip()
                           for h, row in tut.items()
                           if row.get("title_id") and tut_labels.get(row["title_id"], "").strip()}
    print(f"tutorials: {len(result['tutorials'])} / {len(tut)} official titles")

    # Map places: pair Spot.msbt by label, then the label/hand/pattern lists.
    spot_jp = msbt.read(str(jp_dir / "Spot.msbt.zst")) if (jp_dir / "Spot.msbt.zst").is_file() else {}
    spot_zh = msbt.read(str(zh_dir / "Spot.msbt.zst")) if (zh_dir / "Spot.msbt.zst").is_file() else {}
    jp2zh: dict[str, str] = {}
    for label, text in spot_jp.items():
        zh = spot_zh.get(label, "").strip()
        if zh and text.strip() and text.strip() not in jp2zh:
            jp2zh[text.strip()] = zh
    spots = json.loads((HERE / "map_spots.json").read_text(encoding="utf8"))["spots"]
    place_known = dict(jp2zh)
    for jp, (label, drop) in PLACE_LABELS.items():
        text = spot_zh.get(label, "").strip()
        if text:
            place_known[jp] = text[: -len(drop)] if drop and text.endswith(drop) else text
    result["places"] = {}
    for h, jp, _en, _kind, _x, _y in spots:
        zh = place_known.get(jp) or HAND_PLACES.get(jp) or generic_zh(jp)
        if not zh:
            zh = compound_zh(jp, place_known)
        if zh:
            result["places"][f"{int(h, 16) & 0xFFFFFFFF:08x}"] = zh
    print(f"places: {len(result['places'])} / {len(spots)} named "
          f"({sum(1 for *_r, k, _x, _y in spots if k < 11)} places, rest road network)")

    result["spkinds"] = {f"{k:08x}": v for k, v in sorted(KIND_ZH.items())}
    result["flags"] = flag_names_zh(jp_dir, zh_dir)
    if not any(result.values()):
        parser.error("no names matched; verify that the chosen folders contain corresponding game messages")
    output = HERE / "chinese_names.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    with (HERE / "chinese_names_report.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(("category", "id", "original", "english", "chinese"))
        writer.writerows(report)
    print(f"Created {output}; restart the editor to load it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
