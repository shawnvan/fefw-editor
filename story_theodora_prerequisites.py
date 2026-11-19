"""Exact Theodora repairs accepted in Test 2; no broad progression unlocks.

Chapter 6 uses the alternate city route. Its NPC barrier may remain present.
Chapter 9 uses only the accepted three-point Troy exit, not the 09B fallback.
"""
import facilities
import fefw_core as core
import story
import worldmap

CITY = {
    4: (0x16b679c4,),
    6: (0x16b679c4, 0x8386d712, 0xa9d310ac),
    8: (0x16b679c4, 0x8386d712, 0xa9d310ac),
}
# Troy's Shrine, Junction 374, Wandering Wood: the accepted 09A connection.
TROY_EXIT = (0x0e91378e, 0xce7df919, 0x9a3e2078)


def edited(f, chapter):
    info = story.read(f)
    if (info.route, info.part) != (3, 1):
        raise core.SaveError('赛奥朵拉的前置条件修复仅适用于其本路线。')
    if type(chapter) is not int or chapter not in range(1, 13):
        raise core.SaveError('无法识别赛奥朵拉的目标章节。')
    if chapter in CITY:
        return facilities.edited(f, {h: True for h in CITY[chapter]})
    if chapter != 9:
        return bytearray(f.payload)
    try:
        recs = worldmap.read(f)
        if any(k not in recs or not recs[k].editable or
               recs[k].state not in (1, 2, 3) for k in TROY_EXIT):
            raise core.SaveError('特洛伊亚之祠出口的地图结构未知，已拒绝修复。')
        result = core.SaveFile(f.payload)
        _, skipped = worldmap.apply(result, {k: (worldmap.SHOWN, None) for k in TROY_EXIT})
    except worldmap.MapError as exc:
        raise core.SaveError('无法校验特洛伊亚之祠出口：' + str(exc)) from exc
    allowed = {recs[k].at+4 for k in TROY_EXIT}
    if skipped or len(result.payload) != len(f.payload) or any(
            a != b and i not in allowed for i, (a, b) in enumerate(zip(f.payload, result.payload))):
        raise core.SaveError('特洛伊亚之祠出口的修改出现意外，已拒绝修复。')
    return bytearray(result.payload)
