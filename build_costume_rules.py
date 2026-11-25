"""Build costume_rules.json from dumped GameData (Costume.bin and Job.bin).

Usage: python build_costume_rules.py <path to your dumped romfs/GameData folder>
       (or set FEFW_ROM to the romfs folder)
"""
from __future__ import annotations

import argparse
import json
import os
import struct
from pathlib import Path

from build_item_stack import U64, _str, fnv1_64_low32, raw_rows_of

MOUNT_SKILL = "SID_乗り降り"


def _str_array(data: bytes, off: int) -> list[str]:
    cnt, stride, ptr = struct.unpack_from("<HH4xQ", data, off)
    return [_str(data, U64(data, 32 + ptr + i * stride)) for i in range(cnt)]


def build_catalogue(game_data: Path) -> dict:
    costume_path = game_data / "Costume.bin"
    job_path = game_data / "Job.bin"
    if not costume_path.is_file() or not job_path.is_file():
        raise FileNotFoundError(f"Missing Costume.bin or Job.bin in {game_data}")

    job_bytes = job_path.read_bytes()
    mount_jobs: list[str] = []
    for key, off, fields in raw_rows_of(job_bytes):
        if not key.startswith("JID_"):
            continue
        skills = _str_array(job_bytes, off + fields["所持スキル"][2])
        if MOUNT_SKILL in skills:
            mount_jobs.append(f"{fnv1_64_low32(key):08x}")

    costume_bytes = costume_path.read_bytes()
    rules: list[dict] = []
    for _key, off, fields in raw_rows_of(costume_bytes):
        pid = _str(costume_bytes, U64(costume_bytes, off + fields["禁止キャラ"][2]))
        jid = _str(costume_bytes, U64(costume_bytes, off + fields["禁止兵種"][2]))
        sid = _str(costume_bytes, U64(costume_bytes, off + fields["禁止スキル"][2]))
        conds = _str_array(costume_bytes, off + fields["禁止条件"][2])
        if not pid:
            continue
        rules.append({
            "person": f"{fnv1_64_low32(pid):08x}",
            "pid": pid,
            "job": f"{fnv1_64_low32(jid):08x}" if jid else "",
            "job_key": jid,
            "skill": f"{fnv1_64_low32(sid):08x}" if sid else "",
            "skill_key": sid,
            "conds": conds,
        })

    return {
        "schema": 1,
        "mount_skill": f"{fnv1_64_low32(MOUNT_SKILL):08x}",
        "mount_skill_key": MOUNT_SKILL,
        "mount_jobs": sorted(set(mount_jobs)),
        "rules": rules,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_data", nargs="?", type=Path,
                        help="Path to dumped GameData folder (or set FEFW_ROM)")
    args = parser.parse_args()
    game_data = args.game_data
    if game_data is None:
        rom = os.environ.get("FEFW_ROM", "")
        if rom:
            game_data = Path(rom) / "GameData"
    if game_data is None or not game_data.is_dir():
        parser.error("Provide <GameData folder> or set FEFW_ROM")
    catalogue = build_catalogue(game_data)
    out = Path(__file__).with_name("costume_rules.json")
    out.write_text(json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(catalogue['rules'])} costume rules, {len(catalogue['mount_jobs'])} mount jobs -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
