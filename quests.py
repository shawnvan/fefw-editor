"""Read-only quest progress, prerequisites, expiration, and recruitment reader."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import struct

import fefw_core as core
import fefw_save as fs
import story
import supports
import unit_entries

HERE = Path(__file__).resolve().parent
CATALOG_PATH = Path(fs.DATA_DIR) / "quest_catalog.json"

ROUTE_LORD_JP = {
    1: "カイ",
    2: "ディートリヒ",
    3: "セオドラ",
    4: "レダ",
    9: "イシュマール",
}
SUPPORT_RANK_BY_LEVEL = {1: "C", 2: "B", 3: "A", 4: "S"}
SUBQUEST_STATE_LABELS = {
    0: "未触发/未接取",
    1: "可接取",
    2: "进行中",
    3: "可汇报",
    4: "已完成",
    5: "已过期/已关闭",
}


class QuestError(core.SaveError):
    pass


@lru_cache(maxsize=1)
def catalog() -> dict:
    path = CATALOG_PATH if CATALOG_PATH.is_file() else HERE / "quest_catalog.json"
    if not path.is_file():
        raise QuestError("缺少任务目录文件 quest_catalog.json。")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class SaveContext:
    route: int
    part: int
    chapter: int
    year: int
    month: int
    day: int
    hour: int
    minute: int
    playthrough: int | None = None
    fame_points: int | None = None
    fame_level: int | None = None

    @property
    def route_label(self) -> str:
        return story.route_name(self.route)

    @property
    def part_label(self) -> str:
        return story.part_name(self.part)

    @property
    def chapter_label(self) -> str:
        return story.chapter_name(self.part, self.chapter)

    @property
    def date_label(self) -> str:
        return f"{self.year}/{self.month}/{self.day} {self.hour:02d}:{self.minute:02d}"


def _fame_level_for(points: int | None, part: int, thresholds: list[dict]) -> int | None:
    if points is None or not thresholds:
        return None
    lv = 1
    for row in thresholds:
        if row.get("part", 1) > max(part, 1) and part in (1, 2):
            continue
        if points >= row["points"]:
            lv = row["level"]
    return lv


def _read_context(f: core.SaveFile, thresholds: list[dict]) -> SaveContext:
    headers = [b for b in f.blocks if b.name == "Header"]
    if len(headers) != 1:
        raise QuestError("无法识别存档 Header 块。")
    h = headers[0]
    if h.end - h.start < 28:
        raise QuestError("存档 Header 长度异常。")
    route, part, chapter = f.payload[h.start + 12:h.start + 15]
    year, month, day, hour, minute = struct.unpack_from("<HBBBB", f.payload, h.start + 22)
    settings = core.read_settings(f)
    playthrough = settings[0] if settings else None
    ud = core.user_data(f)
    fame_points = None
    if ud >= 0 and ud + core.RENOWN_AT + 4 <= len(f.payload):
        fame_points = f.u32(ud + core.RENOWN_AT)
    fame_level = _fame_level_for(fame_points, part, thresholds)
    return SaveContext(route, part, chapter, year, month, day, hour, minute,
                       playthrough, fame_points, fame_level)


def _read_sidestory_block(f: core.SaveFile, expected_hashes: set[int]) -> dict[int, tuple[int, int, tuple | None]]:
    blocks = [b for b in f.blocks if b.name == "SideStoryData"]
    if len(blocks) != 1:
        raise QuestError("存档缺少 SideStoryData 数据块，无法判定外传进度。")
    b = blocks[0]
    d = f.payload[b.start:b.end]
    if len(d) < 9 or d[4] != 4:
        raise QuestError("SideStoryData 数据块版本或长度不受支持，无法判定外传进度。")
    cnt = struct.unpack_from("<I", d, 5)[0]
    if cnt < len(expected_hashes):
        raise QuestError(f"SideStoryData 记录数异常（{cnt} < {len(expected_hashes)}），无法判定外传进度。")
    pos = 9
    out: dict[int, tuple[int, int, tuple | None]] = {}
    for _ in range(cnt):
        if pos + 11 > len(d):
            raise QuestError("SideStoryData 数据块记录截断，无法判定外传进度。")
        h64 = struct.unpack_from("<Q", d, pos)[0]
        pos += 8
        state, flag, has_ts = d[pos], d[pos + 1], d[pos + 2]
        pos += 3
        ts = None
        if has_ts:
            if pos + 7 > len(d):
                raise QuestError("SideStoryData 时间戳记录截断，无法判定外传进度。")
            ts = struct.unpack_from("<HBBBB", d, pos)
            pos += 7
        out[h64] = (state, flag, ts)
    if not expected_hashes.issubset(out.keys()):
        raise QuestError("SideStoryData 缺少目录中的外传记录，无法判定外传进度。")
    return out


def _read_subquest_block(f: core.SaveFile, expected_hashes: set[int]) -> dict[tuple[int, int], tuple[int, int]]:
    blocks = [b for b in f.blocks if b.name == "SubquestData"]
    if len(blocks) != 1:
        raise QuestError("存档缺少 SubquestData 数据块，无法判定支线进度。")
    b = blocks[0]
    d = f.payload[b.start:b.end]
    if len(d) < 9 or d[4] != 4:
        raise QuestError("SubquestData 数据块版本或长度不受支持，无法判定支线进度。")
    cnt = struct.unpack_from("<I", d, 5)[0]
    if cnt < len(expected_hashes):
        raise QuestError(f"SubquestData 记录数异常（{cnt} < {len(expected_hashes)}），无法判定支线进度。")
    if 9 + cnt * 7 > len(d):
        raise QuestError("SubquestData 数据块记录截断，无法判定支线进度。")
    pos = 9
    out: dict[tuple[int, int], tuple[int, int]] = {}
    for _ in range(cnt):
        h32, route, state, sub = struct.unpack_from("<IBBB", d, pos)
        pos += 7
        if h32:
            out[(h32, route)] = (state, sub)
    present_hashes = {h for h, _ in out.keys()}
    if not expected_hashes.issubset(present_hashes):
        raise QuestError("SubquestData 缺少目录中的支线记录，无法判定支线进度。")
    return out


def _read_all_flags(f: core.SaveFile) -> dict[int, int]:
    flags: dict[int, int] = {}
    for fm in core.flag_maps(f):
        for fl in fm.flags:
            flags[fl.hash] = fl.value
    return flags


def _support_points_lookup(f: core.SaveFile) -> dict[frozenset[str], int]:
    gud = f.block("GameUserData")
    if not gud:
        return {}
    table = supports.find_table(f.payload, gud.start, gud.end)
    if not table:
        return {}
    out: dict[frozenset[str], int] = {}
    for pair_hash, a, b in supports.point_pairs():
        pts = supports.points_of(table, pair_hash)
        if pts is not None:
            out[frozenset((a, b))] = pts
    return out


def _support_met(
    flag_values: dict[int, int],
    point_lookup: dict[frozenset[str], int],
    route: int,
    support_pid: str,
    level: int,
    already_triggered: bool,
) -> tuple[bool | None, str]:
    if not support_pid or level <= 0:
        return True, "无支援要求"
    rank = SUPPORT_RANK_BY_LEVEL.get(level, f"Lv.{level}")
    target_jp = support_pid.removeprefix("PID_")
    lord_jp = ROUTE_LORD_JP.get(route, "")
    if not lord_jp or target_jp == lord_jp:
        return None, f"需支援 {rank}"
    if already_triggered:
        return True, f"支援 {rank}（已达成）"
    pts = point_lookup.get(frozenset((lord_jp, target_jp)))
    for pr in supports.pairs():
        if {pr.get("a"), pr.get("b")} == {lord_jp, target_jp}:
            pair_flags = pr.get("flags") or {}
            h = pair_flags.get(rank)
            if h is not None and flag_values.get(h & 0xFFFFFFFF, 0) > 0:
                return True, f"支援 {rank}（已达成对话）"
            break
    if pts is not None:
        threshold = supports.LEVEL_POINTS[min(level, len(supports.LEVEL_POINTS) - 1)]
        if pts >= threshold:
            return True, f"支援 {rank}（好感点数 {pts} 已达标）"
        return False, f"需与{lord_jp}达到支援 {rank}（好感 {pts}/{threshold}，未达成）"
    return None, f"需与{lord_jp}达到支援 {rank}（无好感记录）"


def _cmp_md_time(month: int, day: int, hour: int, minute: int,
                 end_m: int, end_d: int, time_hhmm: int) -> bool:
    """Return True if (month, day, hhmm) is strictly past (end_m, end_d, time_hhmm)."""
    if end_m <= 0 or end_d <= 0:
        return False
    hhmm = hour * 100 + minute
    if (month, day) != (end_m, end_d):
        return (month, day) > (end_m, end_d)
    if time_hhmm >= 0:
        return hhmm > time_hhmm
    return False


def _fmt_md(m: int, d: int) -> str:
    return f"{m}/{d}" if m and d else "—"


def read(f: core.SaveFile) -> dict:
    """Return read-only context, sidestory, subquest, and recruitment statuses."""
    cat = catalog()
    ctx = _read_context(f, cat.get("fame_thresholds", []))
    expected_ss = {int(row["hash64"], 16) for row in cat["sidestories"]}
    expected_sq = {int(row["hash32"], 16) for row in cat["subquests"]}
    ss_block = _read_sidestory_block(f, expected_ss)
    sq_block = _read_subquest_block(f, expected_sq)
    flags = _read_all_flags(f)
    point_lookup = _support_points_lookup(f)

    # Index subquest state by SQID key for prerequisite checks
    sq_by_key = {row["key"]: row for row in cat["subquests"]}
    sq_state_by_key: dict[str, int | None] = {}
    for row in cat["subquests"]:
        h32 = int(row["hash32"], 16)
        entry = sq_block.get((h32, ctx.route))
        if entry is None:
            entry = sq_block.get((h32, 0))
        sq_state_by_key[row["key"]] = entry[0] if entry is not None else None

    sidestories = []
    ss_by_key: dict[str, dict] = {}
    for row in cat["sidestories"]:
        h64 = int(row["hash64"], 16)
        raw_state, raw_flag, ts = ss_block.get(h64, (0, 0, None))
        acc_h = row["flags"]["accept_hash"]
        clr_h = row["flags"]["clear_hash"]
        f_acc = bool(acc_h and flags.get(int(acc_h, 16), 0) > 0)
        f_clr = bool(clr_h and flags.get(int(clr_h, 16), 0) > 0)

        route_ok = bool(row["route_mask"] & (1 << (ctx.route - 1))) if ctx.route > 0 else False
        r_dl = row["route_deadlines"].get(str(ctx.route), {})
        eff_end_m = r_dl.get("occur_month") or row["end_month"]
        eff_end_d = r_dl.get("occur_day") or row["end_day"]
        acc_end_m = r_dl.get("accept_month") or row["end_month"]
        acc_end_d = r_dl.get("accept_day") or row["end_day"]

        already_triggered = f_clr or f_acc or raw_state in (1, 3, 4)
        sup_ok, sup_note = _support_met(
            flags, point_lookup, ctx.route,
            row["support_pid"], row["support_level"], already_triggered)

        if ctx.part > 1 or (ctx.part == 1 and ctx.year > 1449):
            past_occur = True
            past_accept = True
            before_start = False
        elif ctx.part < 1 or (ctx.part == 1 and ctx.year < 1449):
            past_occur = False
            past_accept = False
            before_start = True
        else:
            past_occur = _cmp_md_time(ctx.month, ctx.day, ctx.hour, ctx.minute,
                                      eff_end_m, eff_end_d, row["time_hhmm"])
            past_accept = _cmp_md_time(ctx.month, ctx.day, ctx.hour, ctx.minute,
                                       acc_end_m, acc_end_d, row["time_hhmm"])
            before_start = (ctx.month, ctx.day) < (row["start_month"], row["start_day"]) if row["start_month"] else False

        if f_clr:
            status_code, status_text = "cleared", "已完成"
        elif raw_state == 3 or f_acc:
            status_code, status_text = "in_progress", "进行中"
        elif not route_ok:
            status_code, status_text = "wrong_route", "非当前路线"
        elif raw_state == 4 or past_occur or past_accept:
            status_code, status_text = "expired", "已过期失效"
        elif raw_state == 1:
            status_code, status_text = "available", "可接取"
        elif before_start:
            status_code, status_text = "locked", "未到触发日期"
        elif sup_ok is False:
            status_code, status_text = "locked", "前置条件未满足"
        else:
            status_code, status_text = "locked", "未触发"

        win_str = f"{_fmt_md(row['start_month'], row['start_day'])} ~ {_fmt_md(row['end_month'], row['end_day'])}"
        if row["time_hhmm"] >= 0:
            win_str += f" ({row['time_hhmm'] // 100:02d}:{row['time_hhmm'] % 100:02d})"
        if r_dl:
            win_str += f" [路线{ctx.route}截止 {_fmt_md(acc_end_m, acc_end_d)}/{_fmt_md(eff_end_m, eff_end_d)}]"

        item = {
            "kind": "sidestory",
            "row": row,
            "status_code": status_code,
            "status_text": status_text,
            "window": win_str,
            "prereq_text": sup_note,
            "prereq_quest_keys": [],
            "raw_state": raw_state,
            "raw_flag": raw_flag,
            "ts": ts,
            "cleared": f_clr,
        }
        sidestories.append(item)
        ss_by_key[row["key"]] = item

    subquests = []
    sq_item_by_key: dict[str, dict] = {}
    for row in cat["subquests"]:
        h32 = int(row["hash32"], 16)
        entry = sq_block.get((h32, ctx.route))
        if entry is None:
            entry = sq_block.get((h32, 0))
        raw_state, raw_sub = entry if entry is not None else (None, None)

        route_ok = (row["route"] == 0 or row["route"] == ctx.route) and (ctx.route not in row["exclude_routes"])

        prereq_parts = []
        prereq_missing = False
        prereq_unknown = False
        for pq in row["prereq_quests"]:
            pq_title = sq_by_key.get(pq, {}).get("title", pq)
            pq_state = sq_state_by_key.get(pq)
            if pq_state == 4:
                prereq_parts.append(f"{pq_title}(✓)")
            elif pq_state is None:
                prereq_unknown = True
                prereq_parts.append(f"{pq_title}(?)")
            else:
                prereq_missing = True
                prereq_parts.append(f"{pq_title}(✗)")
        for pf in row["prereq_flags"]:
            ok = flags.get(int(pf["hash"], 16), 0) > 0
            if not ok:
                prereq_missing = True
            prereq_parts.append(f"旗标:{pf['name']}({'✓' if ok else '✗'})")
        if row["fame_level"] > 0:
            if ctx.fame_level is not None:
                ok = ctx.fame_level >= row["fame_level"]
                if not ok:
                    prereq_missing = True
                prereq_parts.append(
                    f"名声Lv.{row['fame_level']}({'✓' if ok else f'当前Lv.{ctx.fame_level}✗'})")
            else:
                prereq_parts.append(f"名声Lv.{row['fame_level']}(需游戏内确认)")
        if row["min_playthrough"] > 1:
            if ctx.playthrough is not None:
                ok = ctx.playthrough >= row["min_playthrough"]
                if not ok:
                    prereq_missing = True
                prereq_parts.append(
                    f"第{row['min_playthrough']}周目起({'✓' if ok else f'当前第{ctx.playthrough}周目✗'})")
            else:
                prereq_parts.append(f"第{row['min_playthrough']}周目起(需游戏内确认)")

        sp, sc = row["start_part"], row["start_chapter"]
        ep, ec = row["end_part"], row["end_chapter"]
        before_ch = (ctx.part, ctx.chapter) < (sp, sc) if sp > 0 else False
        after_ch = (ctx.part, ctx.chapter) > (ep, ec) if ep > 0 else False

        if raw_state == 4:
            status_code, status_text = "cleared", "已完成"
        elif raw_state == 3:
            status_code, status_text = "reportable", "可汇报"
        elif raw_state == 2:
            status_code, status_text = "in_progress", "进行中"
        elif raw_state == 1:
            status_code, status_text = "available", "可接取"
        elif raw_state == 5:
            status_code, status_text = "expired", "已过期/已关闭"
        elif not route_ok:
            status_code, status_text = "wrong_route", "非当前路线"
        elif before_ch:
            status_code, status_text = "locked", "未到开放章节"
        elif raw_state is None:
            status_code, status_text = "unknown", "无法判定"
        elif after_ch:
            status_code, status_text = "expired", "已过期失效"
        elif prereq_missing:
            status_code, status_text = "locked", "前置条件未满足"
        elif prereq_unknown:
            status_code, status_text = "unknown", "无法判定"
        else:
            status_code, status_text = "locked", "未触发"

        if sp > 0 and ep > 0:
            win_str = f"第{sp}部第{sc}章 ~ 第{ep}部第{ec}章"
        elif sp > 0:
            win_str = f"第{sp}部第{sc}章起"
        elif ep > 0:
            win_str = f"至第{ep}部第{ec}章"
        else:
            win_str = "—"

        item = {
            "kind": "subquest",
            "row": row,
            "status_code": status_code,
            "status_text": status_text,
            "window": win_str,
            "prereq_text": " · ".join(prereq_parts) if prereq_parts else "无",
            "prereq_quest_keys": list(row["prereq_quests"]),
            "raw_state": raw_state,
            "raw_sub": raw_sub,
        }
        subquests.append(item)
        sq_item_by_key[row["key"]] = item

    recruitments = []
    unit_pool_hashes = {unit_entries.canonical(u.person) for u in f.units()}
    for rec in cat.get("recruitments", []):
        p_hash = int(rec["person_hash"], 16)
        in_pool = p_hash in unit_pool_hashes
        rec_flag_on = bool(rec["recruit_hash"] and flags.get(int(rec["recruit_hash"], 16), 0) > 0)
        met_flag_on = bool(rec["met_hash"] and flags.get(int(rec["met_hash"], 16), 0) > 0)
        scout_flag_on = bool(rec["scout_hash"] and flags.get(int(rec["scout_hash"], 16), 0) > 0)
        lockouts = [lf["name"] for lf in rec.get("lockout_flags", []) if flags.get(int(lf["hash"], 16), 0) > 0]

        rel_items = []
        rel_keys = []
        for sqk in rec.get("related_subquests", []):
            sq_item = sq_item_by_key.get(sqk)
            if sq_item and sq_item["status_code"] != "wrong_route":
                rel_items.append(f"{sq_item['row']['title']}({sq_item['status_text']})")
                rel_keys.append(sqk)
        if rec.get("related_sidestory"):
            ss_item = ss_by_key.get(rec["related_sidestory"])
            if ss_item:
                rel_items.append(f"外传:{ss_item['row']['title']}({ss_item['status_text']})")
                rel_keys.append(rec["related_sidestory"])

        special_val = flags.get(int(rec["special_hash"], 16), 0) if rec.get("special_hash") else 0

        if in_pool or rec_flag_on:
            status_code, status_text = "cleared", "已入队"
        elif lockouts:
            status_code, status_text = "expired", "已被其他阵营招募"
        elif any("可汇报" in t or "进行中" in t or "可接取" in t for t in rel_items):
            status_code, status_text = "in_progress", "关联任务可接/进行中"
        elif scout_flag_on:
            status_code, status_text = "available", "据点可招募"
        else:
            status_code, status_text = "locked", "未解锁/未触发"

        prereq_parts = []
        if rec.get("scout_flag"):
            prereq_parts.append(f"可招募标记({'✓' if scout_flag_on else '✗'})")
        if rec.get("met_flag"):
            prereq_parts.append(f"面识({'✓' if met_flag_on else '✗'})")
        if rec.get("special_flag"):
            prereq_parts.append(f"特殊谈判进度={special_val}")
        if lockouts:
            prereq_parts.append("锁定:" + ",".join(lockouts))
        if rel_items:
            prereq_parts.append(" · ".join(rel_items))

        recruitments.append({
            "kind": "recruitment",
            "row": {
                "key": rec["pid"],
                "title": rec["name"],
                "jp_name": rec["jp_name"],
                "target_name": rec["name"],
                "goal": "；".join(rel_items) if rel_items else "通过剧情/据点对话或谈判任务加入",
            },
            "status_code": status_code,
            "status_text": status_text,
            "window": " · ".join(rel_items) if rel_items else "剧情/据点",
            "prereq_text": " · ".join(prereq_parts) if prereq_parts else "无额外标记",
            "prereq_quest_keys": rel_keys,
            "in_pool": in_pool,
            "rec_flag": rec_flag_on,
            "met_flag": met_flag_on,
            "scout_flag": scout_flag_on,
            "lockouts": lockouts,
        })

    return {
        "context": ctx,
        "sidestories": sidestories,
        "subquests": subquests,
        "recruitments": recruitments,
    }
