"""
Support chart and point table for Fire Emblem: Fortune's Weave.

Three separate things live here:

* **The pair chart** — every support pair the game data defines, with the
  highest rank each pair can reach (C / B / A / S).  This comes from
  ``GameData/Reliance.bin`` and the ``支援会話_<A>_<B>_<rank>`` flag names, and
  is baked into ``supports.json`` so the editor needs no ROM access.

* **Conversation flags** — the save records ``支援会話_<A>_<B>_<rank>`` as an
  ordinary flag once that conversation has been seen, so the editor can show
  which of a pair's conversations this save has registered.

* **Support points** — the save holds a sparse table of 12-byte entries,
  ``[u32 pair hash][u32 points][u32 extra]``, inside ``GameUserData``.  The game
  derives the support *level* from the point value; 3200 is the maximum
  (level 5).  The pair hashes are precomputed constants — no formula maps
  character ids to them — so the whole 2628-pair table is baked into
  ``support_points.json``.

  The layout, the way the table is located and the write recipe come from
  https://github.com/Antiadvocate/fefw-editor/issues/19 (wingoflan).  The table
  does not start at a fixed offset, so it is found by looking for the entries
  themselves; only the ``points`` word is ever written, ``extra`` is left alone.
"""

from __future__ import annotations

import json
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "supports.json")
POINTS_PATH = os.path.join(HERE, "support_points.json")
RANKS = ("C", "B", "A", "S")

MAX_POINTS = 3200       # the maximum; confirmed in-game to be level 5
# Cumulative points at which support level 1..5 starts (400 for 1->2, then 1000,
# 1000 and 800 more).  3200 is confirmed in-game; the lower steps come from
# https://annasnotebook.net/en/bonds/ (issue #39) and are the same for every pair.
LEVEL_POINTS = (0, 400, 1400, 2400, 3200)
ENTRY_SIZE = 12         # [u32 pair hash][u32 points][u32 extra]

RANK_LEVEL = {"C": 2, "B": 3, "A": 4, "S": 5}
RANK_POINTS = {"C": 400, "B": 1400, "A": 2400, "S": 3200}
ROUTE_NAMES = {
    0: "全路线",
    1: "凯伊篇",
    2: "迪托利希篇",
    3: "赛奥朵拉篇",
    4: "蕾达篇",
    9: "伊修玛尔篇",
}

_SPECIAL_FLAG_LABELS = {
    "進行_開始_ダグシオン大剣闘祭_開催": "达古西翁大剑斗祭已开幕",
    "同期_日付_アレキサンドラ戦終了": "亚历珊德拉战结束后",
    "同期_日付_ベンディッツ戦終了": "班迪茨战结束后",
    "同期_日付_セオドラがダグシオンに帰還した": "赛奥朵拉返回达古西翁后",
    "同期_日付_ディートリヒとマーズの一騎討ちが知れ渡った": "迪托利希与玛兹单挑传开后",
    "同期_日付_大剣闘祭の追加募集の実力テストが終了": "大剑斗祭追加招募实力测试结束后",
    "同期_日付_海王ケートゥスが討伐されたことが知れ渡った": "讨伐海王刻托斯传开后",
    "ベルトラン外伝_クリア": "贝特兰外传已通关",
    "進行_到着_ダグシオン到着": "已抵达达古西翁",
    "進行_到着_条件会話_アウロラがダグシオンに戻ってきた": "欧若拉返回达古西翁后",
    "進行_変化_オルヘルの仮面が外れる": "欧露赫露摘下面具后",
    "進行_変化_フィアナの記憶が戻った": "绯亚娜恢复记忆",
    "進行_変化_マクシムがジェスターになる": "马克西姆变为杰斯特后",
    "進行_会話_ゲイツがブレザラントの将軍だと判明": "盖茨身份判明（布雷扎兰特将军）",
    "進行_会話_条件会話_ミカエラとムウが会話した": "米迦艾拉与穆完成条件对话",
    "ローカル_同期_会話_ピーテルとティアラがケンカしている": "皮特鲁与媞雅拉吵架期间",
    "ローカル_同期_第二部_4章が始まった": "第2部第4章开始",
    "ローカル_第一部_大剣闘祭_カイ敗退試合開始": "第1部大剑斗祭凯伊败退战开始",
    "進行_段階_第三部_ダグシオンの復興段階_01": "第3部达古西翁复兴阶段1",
    "進行_終了_ディートリヒとアナトリアの本戦終了": "迪托利希与爱娜特莉亚本战结束",
    "進行_終了_ディートリヒ篇_ファビオの正体を知った": "迪托利希篇知晓法毕欧真实身份",
    "進行_終了_ディートリヒ篇_石標セント・カタリナ終了": "迪托利希篇石标圣卡塔琳娜结束",
    "進行_終了_冥将ダグザ_怠惰_撃破": "击败冥将达古扎（怠惰）",
    "進行_終了_冥将ドレイク_嫉妬_撃破": "击败冥将德雷克（嫉妒）",
    "進行_誕生日_サラン": "沙兰生日事件后",
    "進行_開始_アナトリアがダグシオンで占いを開始": "爱娜特莉亚在达古西翁开始占卜后",
}

_STATUS_PRIORITY = {
    "available": 0,
    "low_points": 1,
    "locked": 2,
    "too_early": 3,
    "locked_order": 4,
    "wrong_route": 5,
    "expired": 6,
    "unknown": 7,
}

_STATUS_LABELS = {
    "seen": "已确认",
    "available": "可触发",
    "low_points": "支援值不足",
    "wrong_route": "路线不符",
    "too_early": "未到章节",
    "expired": "已过期",
    "locked_order": "顺序锁",
    "locked": "前置未满",
    "unknown": "—",
}

_pairs: list[dict] | None = None
_by_char: dict[str, dict] | None = None
_points: dict | None = None
_known: set[int] | None = None
_partners: dict[str, list[dict]] | None = None


def pairs() -> list[dict]:
    global _pairs
    if _pairs is None:
        try:
            with open(DATA_PATH, encoding="utf-8") as fh:
                _pairs = list(json.load(fh).get("pairs", []))
        except (OSError, ValueError):
            _pairs = []
    return _pairs


def by_char() -> dict[str, dict]:
    """jp name -> {jp, en, partners:[{jp, en, max, flags}]}"""
    global _by_char
    if _by_char is not None:
        return _by_char
    out: dict[str, dict] = {}
    for pr in pairs():
        for me, other in (("a", "b"), ("b", "a")):
            rec = out.setdefault(pr[me], {"jp": pr[me], "en": pr.get(me + "_en", ""),
                                          "partners": []})
            rec["partners"].append({
                "jp": pr[other],
                "en": pr.get(other + "_en", ""),
                "max": pr.get("max", ""),
                "flags": dict(pr.get("flags", {})),
                "conditions": dict(pr.get("conditions", {})),
            })
    for rec in out.values():
        rec["partners"].sort(key=lambda p: (p["en"] or p["jp"]).lower())
    _by_char = out
    return out


def chars() -> list[tuple[str, dict]]:
    return sorted(by_char().items(), key=lambda kv: (kv[1]["en"] or kv[1]["jp"]).lower())


def present_flags(payload: bytes) -> set[int]:
    """Every hash the save records as a set flag (``01 <u32 hash>`` entries)."""
    out: set[int] = set()
    for off in range(1, len(payload) - 4):
        if payload[off - 1] == 1:
            out.add(struct.unpack_from("<I", payload, off)[0])
    return out


def seen(partner: dict, present: set[int]) -> dict[str, bool]:
    return {r: h in present for r, h in partner.get("flags", {}).items()}


def rank_of(n_seen: int) -> str:
    return RANKS[max(0, n_seen - 1)] if n_seen else ""


# --------------------------------------------------------------------------
# the point table (issue #19's layout)
# --------------------------------------------------------------------------
def points_data() -> dict:
    global _points
    if _points is None:
        try:
            with open(POINTS_PATH, encoding="utf-8") as fh:
                _points = json.load(fh)
        except (OSError, ValueError):
            _points = {}
    return _points if _points is not None else {}


def max_points() -> int:
    return int(points_data().get("max_points") or MAX_POINTS)


def point_chars() -> list[tuple[str, str]]:
    """``(jp, en)`` for every character, in the game's canonical index order."""
    d = points_data()
    jp, en = d.get("chars_jp") or [], d.get("chars") or []
    return [(a, b or a) for a, b in zip(jp, en)]


def _known_hashes() -> set[int]:
    global _known
    if _known is None:
        _known = {h for _, _, h in (points_data().get("pairs") or [])}
    return _known


def point_pairs() -> list[tuple[int, str, str]]:
    """``(hash, jp_a, jp_b)`` for every pair the table covers."""
    jp = points_data().get("chars_jp") or []
    out = []
    for a, b, h in (points_data().get("pairs") or []):
        if a < len(jp) and b < len(jp):
            out.append((h, jp[a], jp[b]))
    return out


def point_partners() -> dict[str, list[dict]]:
    """jp -> ``[{jp, en, hash}]`` for every other character."""
    global _partners
    if _partners is not None:
        return _partners
    en = dict(point_chars())
    out: dict[str, list[dict]] = {}
    for h, a, b in point_pairs():
        out.setdefault(a, []).append({"jp": b, "en": en.get(b, b), "hash": h})
        out.setdefault(b, []).append({"jp": a, "en": en.get(a, a), "hash": h})
    for v in out.values():
        v.sort(key=lambda p: (p["en"] or p["jp"]).lower())
    _partners = out
    return out


def find_table(payload, start: int, end: int) -> dict | None:
    """Locate the support point table inside ``payload[start:end]``.

    Returns ``{pair hash: (points, extra, offset of the entry)}``, or ``None``
    when the block holds no table this build can recognise.  The table does not
    begin at a fixed offset — its alignment differs between saves — so the phase
    is derived from the entries themselves: every recognised pair hash sits at
    the same residue modulo 12.
    """
    known = _known_hashes()
    if not known or payload is None:
        return None
    start = max(0, int(start))
    stop = min(len(payload), int(end)) - (ENTRY_SIZE - 1)
    if stop <= start:
        return None
    unpack = struct.unpack_from
    hits: list[int] = []
    # the entries are not 4-byte aligned in the file, so scan byte by byte
    for off in range(start, stop):
        if unpack("<I", payload, off)[0] in known:
            hits.append(off)
    if len(hits) < 64:
        return None
    phases: dict[int, int] = {}
    for off in hits:
        r = (off - start) % ENTRY_SIZE
        phases[r] = phases.get(r, 0) + 1
    phase, count = max(phases.items(), key=lambda kv: kv[1])
    if count < 64:
        return None
    offs = [o for o in hits if (o - start) % ENTRY_SIZE == phase]
    out: dict[int, tuple[int, int, int]] = {}
    for off in range(offs[0], offs[-1] + 1, ENTRY_SIZE):
        h, pts, extra = unpack("<III", payload, off)
        if h in known:
            out[h] = (pts, extra, off)
    return out or None


def set_points(payload, offset: int, value: int) -> None:
    """Write the point value of the entry at ``offset`` (in place)."""
    struct.pack_into("<I", payload, offset + 4, max(0, min(int(value), 0xFFFFFFFF)))


def level_of(points: int | None) -> int | None:
    """Support level (1-5) that ``points`` reaches, or None when unknown."""
    if points is None:
        return None
    return sum(1 for need in LEVEL_POINTS if points >= need) or 1


def next_level_at(points: int | None) -> int | None:
    """Points needed for the next level, or None at the top / when unknown."""
    if points is None:
        return None
    for need in LEVEL_POINTS:
        if points < need:
            return need
    return None


def points_of(table: dict | None, pair_hash: int) -> int | None:
    if not table:
        return None
    row = table.get(pair_hash)
    return None if row is None else row[0]


# --------------------------------------------------------------------------
# unlock conditions & live save evaluation (from GameData/Reliance.bin)
# --------------------------------------------------------------------------
def _char_label(jp: str, name_fn=None) -> str:
    if name_fn:
        return name_fn(jp, "")
    return jp


def format_flag_condition(flag: dict, name_fn=None) -> str:
    """Turn a Reliance.bin condition flag entry into concise Chinese text."""
    raw = flag.get("name", "")
    neg = bool(flag.get("negated"))

    if raw.startswith("加入_"):
        who = _char_label(raw[3:], name_fn)
        return f"未加入 {who}" if neg else f"已加入 {who}"
    if raw.startswith("ローカル_同期_死別退場_"):
        who = _char_label(raw[len("ローカル_同期_死別退場_"):], name_fn)
        return f"{who} 未死别退场" if neg else f"{who} 已死别退场"
    if raw.startswith("ローカル_同期_ルート判定_"):
        who = raw[len("ローカル_同期_ルート判定_"):].removesuffix("篇")
        who = _char_label(who, name_fn)
        return f"非{who}篇" if neg else f"路线：{who}篇"
    if raw.startswith("ローカル_支援会話判定_真焔将_×限_"):
        who = _char_label(raw[len("ローカル_支援会話判定_真焔将_×限_"):], name_fn)
        return f"{who} 非限定参战" if not neg else f"{who} 处于限定参战"
    if raw.startswith("進行_特殊_支援会話_"):
        parts = raw.split("_")
        if len(parts) >= 6:
            a = _char_label(parts[3], name_fn)
            b = _char_label(parts[4], name_fn)
            rk = parts[5]
            if neg:
                return f"未观看 {a}×{b}({rk})（顺序锁）"
            return f"已观看 {a}×{b}({rk})"

    base = _SPECIAL_FLAG_LABELS.get(raw, raw)
    if neg:
        if base.endswith("后"):
            return base[:-1] + "前"
        return f"未触发：{base}"
    return base


def format_window(branch: dict) -> str:
    sp = int(branch.get("start_part") or 0)
    sc = int(branch.get("start_chapter") or 0)
    ep = int(branch.get("end_part") or 0)
    ec = int(branch.get("end_chapter") or 0)
    if not sp and not ep:
        return ""
    s_txt = f"第{sp}部" + (f"第{sc}章" if sc else "")
    e_txt = f"第{ep}部" + (f"第{ec}章" if ec else "")
    if sp == ep and sc == 0 and ec == 0:
        return f"仅第{sp}部"
    if s_txt == e_txt:
        return s_txt
    if sp == ep and sc > 0 and ec == 0:
        return f"第{sp}部第{sc}章起"
    if sp == ep and sc > 0 and ec > 0:
        return f"第{sp}部第{sc}～{ec}章"
    return f"{s_txt}～{e_txt}"


def describe_branch(branch: dict, name_fn=None) -> str:
    """Human-readable single-line description of one OR-branch."""
    parts: list[str] = []
    route = int(branch.get("route") or 0)
    if route:
        parts.append(ROUTE_NAMES.get(route, f"路线{route}"))
    win = format_window(branch)
    if win:
        parts.append(win)
    if branch.get("any_join"):
        parts.append("双方任一加入")
    for fl in branch.get("flags") or ():
        name = fl.get("name", "")
        # Avoid repeating route when branch["route"] already shows it
        if route and name.startswith("ローカル_同期_ルート判定_") and not fl.get("negated"):
            continue
        parts.append(format_flag_condition(fl, name_fn))
    return " · ".join(parts) if parts else "无特殊限制"


def _evaluate_branch(
    branch: dict,
    rank: str,
    *,
    points: int | None,
    present_flags: set[int],
    party_pids: set[str],
    route: int | None,
    part: int | None,
    chapter: int | None,
    prev_seen: bool,
    name_fn=None,
) -> tuple[str, list[str]]:
    """Return (status, reasons) for a single OR-branch against live save state."""
    reasons: list[str] = []
    b_route = int(branch.get("route") or 0)
    sp = int(branch.get("start_part") or 0)
    sc = int(branch.get("start_chapter") or 0)
    ep = int(branch.get("end_part") or 0)
    ec = int(branch.get("end_chapter") or 0)

    wrong_route = False
    if b_route and route is not None:
        # In Part 3 (route == 9), route-tagged branches rely on their route flag
        if part != 3 and route != b_route:
            wrong_route = True
            reasons.append(f"限{ROUTE_NAMES.get(b_route, f'路线{b_route}')}")

    too_early = False
    expired = False
    if part is not None and chapter is not None:
        if sp and (part < sp or (part == sp and sc > 0 and chapter < sc)):
            too_early = True
            reasons.append(f"需至第{sp}部" + (f"第{sc}章" if sc else ""))
        elif ep and (part > ep or (part == ep and ec > 0 and chapter > ec)):
            expired = True
            reasons.append(f"已超过截止（{format_window(branch)}）")

    any_join = branch.get("any_join") or []
    join_missing = False
    if any_join:
        joined = False
        for pid in any_join:
            if pid in party_pids:
                joined = True
                break
            jp_name = pid.removeprefix("PID_")
            # Also accept the save's 加入_<jp> flag if unit is temporarily out of roster
            h_join = 0x811C9DC5
            for b in ("加入_" + jp_name).encode("utf-8"):
                h_join = ((h_join * 0x01000193) & 0xFFFFFFFF) ^ b
            if h_join in present_flags:
                joined = True
                break
        if not joined:
            join_missing = True
            names = "/".join(_char_label(p.removeprefix("PID_"), name_fn) for p in any_join)
            reasons.append(f"需先加入 {names} 之一")

    locked_order = False
    flag_missing = False
    for fl in branch.get("flags") or ():
        name = fl.get("name", "")
        neg = bool(fl.get("negated"))
        h = int(fl["hash"], 16)
        is_set = h in present_flags
        if not is_set and name.startswith("加入_") and ("PID_" + name[3:]) in party_pids:
            is_set = True
        ok = (not is_set) if neg else is_set
        if not ok:
            if b_route and name.startswith("ローカル_同期_ルート判定_") and not neg:
                if not wrong_route:
                    wrong_route = True
                    reasons.append(format_flag_condition(fl, name_fn))
                continue
            if neg and name.startswith("進行_特殊_支援会話_"):
                locked_order = True
                reasons.append("已触发互斥对话：" + format_flag_condition({"name": name, "negated": False}, name_fn))
            else:
                flag_missing = True
                reasons.append(format_flag_condition(fl, name_fn))

    if not prev_seen:
        flag_missing = True
        reasons.append("需先观看上一级支援对话")

    need_pts = RANK_POINTS.get(rank, 0)
    low_pts = points is not None and points < need_pts
    if low_pts:
        reasons.append(f"支援值需 {need_pts:,}（当前 {points:,}）")

    if wrong_route:
        return "wrong_route", reasons
    if expired:
        return "expired", reasons
    if locked_order:
        return "locked_order", reasons
    if too_early:
        return "too_early", reasons
    if join_missing or flag_missing:
        return "locked", reasons
    if low_pts:
        return "low_points", reasons
    return "available", []


def evaluate_rank(
    rank: str,
    cond: dict,
    *,
    points: int | None,
    present_flags: set[int],
    party_pids: set[str],
    route: int | None,
    part: int | None,
    chapter: int | None,
    prev_seen: bool = True,
    name_fn=None,
) -> dict:
    """Evaluate one support rank (C/B/A/S) and return status + readable details."""
    branches = cond.get("branches") or []
    branch_texts = [describe_branch(b, name_fn) for b in branches]
    summary = " 或 ".join(branch_texts) if branch_texts else "无特殊限制"

    warnings: list[str] = []
    for b in branches:
        for fl in b.get("flags") or ():
            if fl.get("negated") and (fl.get("name") or "").startswith("進行_特殊_支援会話_"):
                w = "顺序锁：" + format_flag_condition(fl, name_fn)
                if w not in warnings:
                    warnings.append(w)

    if cond.get("hash") in present_flags:
        return {
            "rank": rank,
            "status": "seen",
            "status_label": _STATUS_LABELS["seen"],
            "reasons": [],
            "summary": summary,
            "warnings": warnings,
        }

    if route is None and part is None:
        return {
            "rank": rank,
            "status": "unknown",
            "status_label": _STATUS_LABELS["unknown"],
            "reasons": [],
            "summary": summary,
            "warnings": warnings,
        }

    if not branches:
        need_pts = RANK_POINTS.get(rank, 0)
        if points is not None and points < need_pts:
            return {
                "rank": rank,
                "status": "low_points",
                "status_label": _STATUS_LABELS["low_points"],
                "reasons": [f"支援值需 {need_pts:,}（当前 {points:,}）"],
                "summary": summary,
                "warnings": warnings,
            }
        return {
            "rank": rank,
            "status": "available",
            "status_label": _STATUS_LABELS["available"],
            "reasons": [],
            "summary": summary,
            "warnings": warnings,
        }

    results = [
        _evaluate_branch(
            b, rank, points=points, present_flags=present_flags,
            party_pids=party_pids, route=route, part=part, chapter=chapter,
            prev_seen=prev_seen, name_fn=name_fn,
        )
        for b in branches
    ]
    best_status, best_reasons = min(results, key=lambda r: _STATUS_PRIORITY.get(r[0], 99))
    return {
        "rank": rank,
        "status": best_status,
        "status_label": _STATUS_LABELS.get(best_status, best_status),
        "reasons": best_reasons,
        "summary": summary,
        "warnings": warnings,
    }


def evaluate_pair(
    partner: dict,
    *,
    points: int | None,
    present_flags: set[int],
    party_pids: set[str],
    route: int | None,
    part: int | None,
    chapter: int | None,
    name_fn=None,
) -> dict:
    """Evaluate all conversation ranks of a pair and build a table badge."""
    conds = partner.get("conditions") or {}
    if not conds:
        return {"badge": "—", "status": "none", "ranks": [], "has_order_lock": False}

    rank_results: list[dict] = []
    prev_seen = True
    has_order_lock = False
    for r in RANKS:
        if r not in conds:
            continue
        res = evaluate_rank(
            r, conds[r], points=points, present_flags=present_flags,
            party_pids=party_pids, route=route, part=part, chapter=chapter,
            prev_seen=prev_seen, name_fn=name_fn,
        )
        rank_results.append(res)
        if res["warnings"]:
            has_order_lock = True
        prev_seen = res["status"] == "seen"

    unseen = [x for x in rank_results if x["status"] != "seen"]
    if not unseen:
        return {
            "badge": "全已确认",
            "status": "seen",
            "ranks": rank_results,
            "has_order_lock": has_order_lock,
        }

    nxt = unseen[0]
    if nxt["status"] == "unknown":
        # No save open: surface notable static tags (order lock / route)
        tags = []
        if has_order_lock:
            tags.append("含顺序锁")
        routes = sorted({
            int(b.get("route") or 0)
            for c in conds.values()
            for b in (c.get("branches") or ())
            if int(b.get("route") or 0)
        })
        if routes and all(
            all(int(b.get("route") or 0) for b in (c.get("branches") or ()))
            for c in conds.values()
        ):
            tags.append("/".join(ROUTE_NAMES.get(r, f"路线{r}") for r in routes))
        badge = " · ".join(tags) if tags else "—"
        return {
            "badge": badge,
            "status": "unknown",
            "ranks": rank_results,
            "has_order_lock": has_order_lock,
        }

    short = {
        "available": "可触发",
        "low_points": "差支援值",
        "wrong_route": "路线不符",
        "too_early": "未到章节",
        "expired": "已过期",
        "locked_order": "顺序锁",
        "locked": "前置未满",
    }.get(nxt["status"], nxt["status_label"])
    badge = f"{nxt['rank']} {short}"
    if has_order_lock and nxt["status"] != "locked_order":
        badge += " ⚠"
    return {
        "badge": badge,
        "status": nxt["status"],
        "ranks": rank_results,
        "has_order_lock": has_order_lock,
    }
