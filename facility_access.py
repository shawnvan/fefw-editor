"""Read-only Dagsion access evidence from Hub/HubDispos, separate from flags.

The four locators' protagonist filters explain the previous Leda travel test.
A matching route is necessary for a filtered locator, not a full access check.
"""
import fefw_core as core
import fefw_layer as layer
import fast_travel
import facilities
from zh_names import CHARACTERS


def _lord(name):
    return CHARACTERS.get(name, name)

ROOM = 0x38574e83
GARDEN = 0x1e4d4a23
SALAMIS = 0xefb57d93
YU_PHAS = 0x65411bed
YU_PHAS_GATE = 0x7d2c41eb

CONDITIONS = {
    '行動範囲制限_上層': '移动受限：鞑古席翁上层（与功能解锁相互独立）',
    '行動範囲制限_中層': '移动受限：鞑古席翁中层（与功能解锁相互独立）',
    '行動範囲制限_下層': '移动受限：鞑古席翁下层/港口（与功能解锁相互独立）',
    '行動範囲制限_富裕区-商業区': '移动受限：富裕区—商业区通道',
    '拠点_カイ篇1部2章_行動制限3': '剧情限制：凯伊 第1部第2章，第 3 组',
    '拠点_カイ篇1部11章_行動制限2': '剧情限制：凯伊 第1部第11章，第 2 组',
    '拠点_カイ篇1部11章_行動制限3': '剧情限制：凯伊 第1部第11章，第 3 组',
    '拠点_カイ篇1部11章_行動制限4': '剧情限制：凯伊 第1部第11章，第 4 组',
    '拠点_セオドラ篇1部6章_行動制限1': '剧情限制：赛奥朵拉 第1部第6章，第 1 组',
    '拠点_セオドラ篇1部11章_行動制限1': '剧情限制：赛奥朵拉 第1部第11章，第 1 组',
    '拠点_セオドラ篇1部11章_行動制限3': '剧情限制：赛奥朵拉 第1部第11章，第 3 组',
    '2部5章_行動制限1': '剧情限制：第2部第5章，第 1 组',
    '2部6章_行動制限1': '剧情限制：第2部第6章，第 1 组',
    '大釜部屋解放': '大釜房间已解锁（进入条件标记）',
    '拠点_Hub_000_A60_アウロラ神殿側ドア開放': '门已打开：A60，欧若拉神殿一侧',
    '拠点_Hub_000_A60_地下牢側ドア開放': '门已打开：A60，地下牢一侧',
}

DETAILS = {
    ROOM: ('Cai', '步行进入另有剧情条件',
           '快速传送定位器需要凯伊（KAI）。A60 的步行入口另行使用剧情限制。两条 A60 开门标记只被凯伊专属的门事件引用；它们不是通用的房间解锁开关。'),
    GARDEN: ('Cai', '步行进入另有剧情条件',
             '快速传送定位器需要凯伊（KAI）。A60 有普通、红线和隐藏名称三种变体，由剧情限制选择。仅保存传送标记无法移除这个路线过滤。'),
    SALAMIS: ('Theodora', '入口同样需要赛奥朵拉',
              '快速传送定位器和 A1 到 A9 的入口都需要赛奥朵拉（SEO）。入口还会检查第 1 部第 6/11 章的行动限制。登记传送标记不会改变这些条件。'),
    YU_PHAS: (None, '正门有路线/章节检查',
              '传送定位器没有主角过滤。正门条目包括自由行动期间的赛奥朵拉 第1部第12章，以及伊修玛尔（ISM）。凯伊、迪托利希和蕾达另有各自的封锁条目。A140 的内门有自由行动条件。传送/离开已在蕾达 第1部第9章 测试通过；步行返回被封锁。这里不会改动任何路线、章节或行动限制标记。'),
}


def apply_labels(names):
    updates = {}
    for key, label in CONDITIONS.items():
        h = core.id_hash(key)
        if names.get(h, {}).get('name', key) != key:
            raise core.SaveError(f'Conflicting access-condition identity for {h:08x}')
        updates[h] = {**names.get(h, {}), 'name': key, 'label': label}
    names.update(updates)


def route_info(f):
    try:
        return layer.route_info(f.payload)
    except (IndexError, core.SaveError):
        return None


def yu_phas_gate_status(f):
    """Report the separately saved switches, without granting either one."""
    try:
        gate = facilities.read(f).get(YU_PHAS_GATE)
    except core.SaveError:
        return '幽·法丝神道之门：无法识别设施标记表。'
    if gate not in (None, 0, 1):
        return f'幽·法丝神道之门：登记值未知（{gate}）；保留。'
    if gate != 1:
        return '幽·法丝神道之门：登记未启用。'
    try:
        travel = fast_travel.read(f).get(YU_PHAS)
    except core.SaveError:
        return '幽·法丝神道之门：已登记；无法检查其普通传送标记。'
    if travel is None:
        return '幽·法丝神道之门：已登记；还需在 快速传送 中启用幽·法丝神殿。'
    if travel != 1:
        return f'幽·法丝神道之门：已登记；普通传送值未知（{travel}）；保留。'
    return '幽·法丝神道之门：两条标记都已记录。列表与传送门的使用已在 蕾达 第1部第9章 测试通过。'


def report(f):
    route = route_info(f)
    try:
        values, error = fast_travel.read(f), ''
    except core.SaveError as exc:
        values, error = {}, str(exc)
    rows = []
    for h, (required, entry, detail) in DETAILS.items():
        gate = ('路线未知' if route is None else
                '定位器无路线过滤' if required is None else
                '路线相符；其他条件可能仍适用' if route[0] == required else
                f'路线不符：需要{_lord(required)}')
        value = values.get(h)
        stored = ('不可用' if error else '未记录' if value is None else
                  '已记录 (1)' if value == 1 else f'未知（{value}）')
        gate_status = yu_phas_gate_status(f) if h == YU_PHAS else ''
        if gate_status:
            detail += '\n' + gate_status
        rows.append({'hash': h, 'label': fast_travel.title(fast_travel.catalog()[h]),
                     'stored': stored, 'route_check': gate, 'entry': entry,
                     'detail': detail, 'error': error, 'gate_status': gate_status})
    try:
        values = facilities.read(f)
        training = facilities.state_label(0xd70afcb2, values.get(0xd70afcb2))
        practice = facilities.state_label(0x728ccd68, values.get(0x728ccd68))
        detail = f'训练所开关：{training}。稽古开关：{practice}。'
    except core.SaveError:
        detail = '相关的已保存开关无法读取。'
    rows.append({'id': 'arena', 'hash': None, 'label': '斗技场',
                 'stored': '开关未映射', 'route_check': '与训练所相互独立',
                 'entry': '剧情与营业时间适用', 'error': '', 'gate_status': '',
                 'detail': detail + '游戏数据将斗技场与训练所分开定义。普通斗技场接待员检查的是斗技场进入权；两个设施的营业时间都会在 00:00 至 06:00 关闭。尚未发现独立的斗技场保存标记，训练所也不是经验证的替代品。第 9 章曾同时开启多个功能，因此那次观察无法隔离原因。第 4 章的目标位于地下区域，不能证明观赛服务是必需的。'})
    return rows


def diagnostics(f):
    route = route_info(f)
    lines = ['功能进入诊断', '路线：' + (
        f'{_lord(route[0])} 第{route[1]}部 第{route[2]}章' if route else '未知'),
        '已保存的开关和数据条件并不保证游戏内能够进入。', '']
    for row in report(f):
        lines.append(f"{row['label']}: {row['stored']}; {row['route_check']}; {row['entry']}")
        if row['gate_status']:
            lines.append(row['gate_status'])
        if row['error']:
            lines.append(row['error'])
    lines += ['', '相关的已保存标记（只读；缺失表示没有保存记录）：']
    maps = core.flag_maps(f)
    for key, label in CONDITIONS.items():
        h = core.id_hash(key)
        hits = [(m.index, v.value) for m in maps for v in m.flags if v.hash == h]
        state = ', '.join(f'列表 {i}：{v}' for i, v in hits) or '缺失'
        lines.append(f'{label}: {state} [{h:08x}]')
    return '\n'.join(lines)
