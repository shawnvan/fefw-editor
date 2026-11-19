"""Diagnose known chapter prerequisites without replaying a chapter.

Rules are limited to the access/teaching/map repairs established in game tests.
Detection is read-only; applying a suggestion preserves the current story beat,
clock, restart snapshots and party. Unknown parts never inherit Part 1 rules.
"""
from dataclasses import dataclass

import facilities
import fefw_core as core
import story
import story_start
import story_prerequisites as dietrich
import story_theodora_prerequisites as theodora
import worldmap

ISSUES_URL = 'https://github.com/Antiadvocate/fefw-editor/issues'


@dataclass(frozen=True)
class Rule:
    note: str
    switches: tuple = ()
    secret: bool = False
    map_exit: bool = False


@dataclass(frozen=True)
class Check:
    label: str
    state: str
    needed: bool


@dataclass(frozen=True)
class Diagnosis:
    progress: story.Progress
    note: str
    checks: tuple = ()
    blocked: str = ''

    @property
    def actionable(self):
        return not self.blocked and any(row.needed for row in self.checks)


def rule_for(info):
    if info.route not in story.PREFIXES:
        return None
    if info.part == 2:
        if info.chapter == 5:
            return Rule('开放上层通行后可前往鞑古扎神殿。四条路线的第5章均已验证可继续推进。',
                        (story_start.PART2_UPPER_MOVEMENT,))
        if info.chapter == 6:
            return Rule('开放中层通行后可经中层的门前往鞑古扎的大釜。'
                        '赛奥朵拉已验证修复现有存档和直接进入章节。其他路线在通过测试时已开放该通道。',
                        (story_start.PART2_MIDDLE_MOVEMENT,))
        return None
    if info.part != 1:
        return None
    if info.route == 1 and 3 <= info.chapter <= 12:
        return Rule('补齐触发下水道入口对话的前置条件。凯伊第4章已验证进入和后续推进，'
                    '入口是否开放仍取决于当前剧情阶段。',
                    (facilities.SEWERS,))
    if info.route == 2 and info.chapter in (5, 6):
        switches = tuple(h for changes in dietrich.CHAPTER_EDITS[info.chapter].values()
                         for h in changes)
        note = ('开放上层通行后可继续资格考试目标。'
                if info.chapter == 5 else
                '开放下层通行、上下层区域和旺达尔商会后，可继续商会目标。上层行动限制仍按本章剧情生效。')
        return Rule(note + '已验证直接进入和后续推进。', switches)
    if info.route == 2 and info.chapter == 7:
        return Rule('开放「秘传·不动贯枪」的传授权限。已验证向艾丝梅拉尔达传授并推进至下一检查点。'
                    '应用后请在游戏内选择已有队员并完成传授。',
                    secret=True)
    if info.route == 3 and info.chapter in theodora.CITY:
        note = {4: '开放中层通行后可到达斗技场入口。',
                6: '开放城市通行后可绕行前往目标。如果 NPC 路障仍在，请走已开放的其他路线。',
                8: '开放城市通行后，测试存档中的 NPC 路障消失，剧情可继续。'
                   '其他剧情阶段或时间可能有不同表现。'}[info.chapter]
        return Rule(note + '已验证章节可继续推进。', theodora.CITY[info.chapter])
    if info.route == 3 and info.chapter == 9:
        return Rule('显示从特洛伊亚之祠通往迷途之森的三个地图点。'
                    '已验证地图移动和剧情推进，沿用各地点已有的到访状态。', map_exit=True)
    return None


def diagnose(f):
    info = story.read(f)
    rule = rule_for(info)
    if rule is None:
        note = '当前角色和章节暂无经过验证的进度修复。'
        if info.part == 3:
            note = '救世篇暂无经过验证的进度修复。'
        elif info.route == 2 and info.part == 1 and info.chapter in (1, 2):
            note += '迪托利希第2章重玩可能卡在艾丝梅拉尔达的救援目标，此前尝试的标记修复均未奏效。第1章也会进入该目标。'
        elif (info.route, info.part, info.chapter) == (4, 1, 1):
            note += '蕾达第1章重玩可能卡在首个目标。'
        return Diagnosis(info, note)
    checks = []
    try:
        if rule.switches:
            values = facilities.read(f)
            maps = {m: facilities.locate_member(f, m)
                    for m in {facilities.member_for(h) for h in rule.switches}}
            for h in rule.switches:
                label = facilities.label_zh(facilities.catalog()[h])
                if any(v.hash == h and v.wide for v in maps[facilities.member_for(h)].flags):
                    raise core.SaveError(f'「{label}」使用了未识别的宽数值格式。')
                state = facilities.enabled(h, values.get(h))
                if state is None:
                    raise core.SaveError(f'「{label}」的存档数值未知。')
                checks.append(Check(label, '已开放' if state else '受限 → 开放', not state))
        if rule.secret:
            rows = {v.hash: v for v in facilities.locate_member(f, dietrich.PROGRESSION).flags}
            row = rows.get(dietrich.SECRET)
            if row and (row.wide or row.value not in (0, 1)):
                raise core.SaveError('「秘传·不动贯枪」的传授权限数值未知。')
            active = row is not None and row.value == 1
            checks.append(Check('「秘传·不动贯枪」传授权限',
                                '已开放' if active else '未开放 → 开放', not active))
        if rule.map_exit:
            recs = worldmap.read(f)
            for key, label in zip(theodora.TROY_EXIT, ('特洛伊亚之祠', '分岔点 374', '迷途之森')):
                row = recs.get(key)
                if not row or not row.editable or row.state not in (1, 2, 3):
                    raise core.SaveError(f'无法识别特洛伊亚之祠出口的地图记录：{label}。')
                active = row.state == worldmap.SHOWN
                checks.append(Check(label, '已显示' if active else '未显示 → 显示', not active))
    except (core.SaveError, worldmap.MapError) as exc:
        return Diagnosis(info, rule.note, tuple(checks), '无法安全检查此修复：' + str(exc))
    try:
        story_start.validate(f)
    except core.SaveError:
        return Diagnosis(info, rule.note, tuple(checks),
                         '此存档只能查看建议。请使用支持的自由行动或章节开始存档来应用修复。')
    return Diagnosis(info, rule.note, tuple(checks))


def edited(f):
    report = diagnose(f)
    if report.blocked:
        raise core.SaveError(report.blocked)
    if not report.actionable:
        return bytearray(f.payload)
    rule = rule_for(report.progress)
    if rule.switches:
        result = facilities.edited(f, {h: True for h in rule.switches})
    elif rule.secret:
        result = dietrich.edited(f, report.progress.chapter)
    else:
        result = theodora.edited(f, report.progress.chapter)
    # Repair writers are scoped; independently guard story identity and all
    # unrelated roots, including live clocks and embedded restart snapshots.
    after = core.SaveFile(result)
    current = story.read(after)
    if (current.route, current.part, current.chapter, current.sequence) != (
            report.progress.route, report.progress.part, report.progress.chapter, report.progress.sequence):
        raise core.SaveError('修复会改变剧情进度，已拒绝应用。')
    before_roots, after_roots = story_start.normalized_blocks(f), story_start.normalized_blocks(after)
    allowed = {'GmapSpotMgr'} if rule.map_exit else {'GameUserData'}
    if before_roots.keys() != after_roots.keys() or any(
            before_roots[k] != after_roots[k] for k in before_roots.keys()-allowed):
        raise core.SaveError('修复会改变无关数据，已拒绝应用。')
    return bytearray(result)
