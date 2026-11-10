"""Build exact name mappings from a user's own Japanese and Chinese MSBT files.

Usage: python build_chinese_names.py ROMFS CHINESE_LANGUAGE_FOLDER
Example: python build_chinese_names.py /path/to/romfs CNzh

Matches the same message label in corresponding files. Ambiguous translations
are excluded, rather than guessing. Does not alter the original ID tables.
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


def unique_pairs(japanese: dict[str, str], chinese: dict[str, str]) -> dict[str, str]:
    pairs: dict[str, set[str]] = collections.defaultdict(set)
    for label, text in japanese.items():
        # Description/help strings are not names.
        if "_H_" in label:
            continue
        jp, zh = norm(text), norm(chinese.get(label, ""))
        if jp and zh and re.search(r"[\u3400-\u9fff]", zh):
            pairs[jp].add(zh)
    return {jp: next(iter(values)) for jp, values in pairs.items() if len(values) == 1}


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
                report.append((category, key, record.get(field, ""),
                               record.get("en") or record.get("name") or "", unique[original]))
        result[category] = translated
        print(f"{category}: {len(translated)} / {len(table)} entries matched")
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
