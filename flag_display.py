"""Display names for Flags, independent of the stored keys and English catalogue."""
from functools import lru_cache

import audiences
import boons
import deeds
import facilities
import facility_access
import fast_travel
import fefw_core as core
import supports
import tutorials
from fefw_save import chinese_name, job_chinese_name, unit_ids
from zh_names import CHARACTERS, CLASSES


PREFIX_ZH = {
    "加入_": "加入记录", "面識_": "会面记录", "支援会話_": "支援对话",
    "TUTO_": "教程", "実績_": "伟业录进度", "実績状態_": "伟业录领取状态",
    "機能開放_": "功能解锁", "兵種解放_": "兵种解锁", "共有_兵種解放_": "共享兵种解锁",
    "禁止_": "限制", "因果報酬_": "救世的祝福", "ファストトラベル_": "快速传送",
    "サブクエ_": "支线任务", "進行_": "剧情进度", "共有_": "共享记录",
    "拠点_": "据点记录", "状況_": "状态记录", "会話_": "对话记录",
}

DEED_COUNTS = {
    "3部ダンジョン宝箱全開封数": "第3部迷宫宝箱全部开启",
    "おびき寄せ回数": "进行诱捕", "テルマエ回数": "在公共浴场入浴",
    "友好レベル2達成数": "达到友好LV2", "友好レベル3達成数": "达到友好LV3",
    "友好レベル4達成数": "达到友好LV4", "友好レベル最大達成数": "达到友好LV上限",
    "天冠の神殿の扉開いた数": "打开天冠神殿内的门",
    "復興回数_アマルテア": "复兴阿玛尔忒娅州", "復興回数_イデュイア": "复兴伊德伊亚州",
    "復興回数_エレクトラ": "复兴艾雷克特拉州", "復興回数_オーマ": "复兴奥玛州",
    "復興回数_カスタリア": "复兴卡斯塔利亚州", "復興回数_サヴェロン": "复兴萨维隆州",
    "復興回数_ソリダス": "复兴索利达斯州", "復興回数_ソロモン古王国": "复兴索罗门之地",
    "復興回数_タレイア": "复兴塔雷亚州", "復興回数_ニュシアデス": "复兴纽西亚德斯州",
    "復興回数_バシテア": "复兴巴希泰雅州", "復興回数_ブラクシテア": "复兴布拉克辛泰雅州",
    "復興回数_メリアス": "复兴梅利亚斯州", "復興回数_ランバス": "复兴兰巴斯州",
    "演劇回数": "在剧场出演", "畑作成回数": "进行栽种",
    "瘴気破壊数": "驱散场所的瘴气", "飲み物回数": "交换饮料",
}


@lru_cache(maxsize=1)
def _feature_labels() -> dict:
    names = {}
    for feature in (tutorials, fast_travel, audiences, deeds, boons, facilities, facility_access):
        rows = {}
        try:
            feature.apply_labels(rows, localized=True)
        except core.SaveError:
            continue
        names.update(rows)
    return names


@lru_cache(maxsize=1)
def _support_labels() -> dict:
    names = {}
    for pair in supports.pairs():
        a = _character(pair["a"], pair.get("a_en", ""))
        b = _character(pair["b"], pair.get("b_en", ""))
        if a and b:
            for rank, h in (pair.get("flags") or {}).items():
                key = f"支援会話_{pair['a']}_{pair['b']}_{rank}"
                names[h & 0xFFFFFFFF] = (key, f"支援对话：{a}与{b}（{rank}）")
    return names


def _character(token: str, english: str = "") -> str:
    h = core.id_hash("PID_" + token)
    meta = unit_ids().get(h, {})
    if meta.get("pid") == "PID_" + token and meta.get("name_en"):
        return meta["name"]
    return CHARACTERS.get(english, "")


def chinese_label(h: int, meta: dict | None) -> str:
    """Empty means no reliable display name has been matched."""
    if not meta:
        return ""
    key = meta["name"]
    english = meta.get("label", "")
    feature = _feature_labels().get(h, {})
    label = feature.get("label", "")
    if label and label != english and (feature["name"] == key or
                                       not feature["name"] and feature["kind"] == "boon"):
        return label
    official = chinese_name("flags", h)
    if official:
        return official

    prefix, _, rest = key.partition("_")
    if prefix in ("加入", "面識"):
        base = english.partition(": ")[2]
        suffix = ""
        for qualifier, translated in ((" (alternate record)", "（变体记录）"),
                                      (" (unverified alias)", "（别名未验证）")):
            if base.endswith(qualifier):
                base = base.removesuffix(qualifier)
                suffix = translated
                break
        name = _character(rest, base)
        if name:
            return f"{'已加入' if prefix == '加入' else '已会面'}：{name}{suffix}"
    if prefix == "支援会話":
        record = _support_labels().get(h)
        if record and record[0] == key:
            return record[1]
    if prefix == "実績" and rest in DEED_COUNTS:
        return "伟业录进度：" + DEED_COUNTS[rest]
    if key.startswith(("兵種解放_", "共有_兵種解放_")):
        job = "JID_" + key.split("兵種解放_", 1)[1]
        name = job_chinese_name(core.id_hash(job)) or CLASSES.get(job)
        if name:
            return f"{'共享' if prefix == '共有' else ''}兵种解锁：{name}"
    return ""


def unknown_label(h: int, key: str) -> str:
    for prefix, label in PREFIX_ZH.items():
        if key.startswith(prefix):
            return f"{label}（名称待核对）· 0x{h:08x}"
    status = "名称待核对" if key else "未识别标记"
    return f"{status} · 0x{h:08x}"
