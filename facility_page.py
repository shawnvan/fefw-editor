"""Facility switches and read-only access diagnostics for the selected save."""
from tkinter import ttk

import facilities
import facility_access
import fefw_core as core


class FacilityPageMixin:
    def _build_facilities(self):
        self.facility_views = ttk.Notebook(self.facilities_page)
        self.facility_views.pack(fill='both', expand=True)
        switches = ttk.Frame(self.facility_views)
        access = ttk.Frame(self.facility_views)
        self.facility_views.add(switches, text='已保存的开关')
        self.facility_views.add(access, text='进入条件')
        top = ttk.Frame(switches, style='Card.TFrame', padding=(14, 10))
        top.pack(fill='x', pady=(8, 0))
        row = ttk.Frame(top, style='Card.TFrame'); row.pack(fill='x')
        ttk.Label(row, text='功能开关', style='CardH3.TLabel').pack(side='left')
        self.facility_buttons = []
        for label, state in (('禁用所选', False), ('启用所选', True)):
            b = ttk.Button(row, text=label, style='Small.TButton',
                           command=lambda s=state: self.set_facilities(s))
            b.pack(side='right', padx=(6, 0)); self.facility_buttons.append(b)
        self.facility_count = ttk.Label(top, style='CardMuted.TLabel', wraplength=920)
        self.facility_count.pack(anchor='w', pady=(6, 0))
        search_row = ttk.Frame(top, style='Card.TFrame'); search_row.pack(fill='x', pady=(8, 0))
        self.facility_kind = ttk.Combobox(search_row, state='readonly', width=19,
                                        values=('全部开关', '服务设施', '神道之门登记', '冒险手册', '区域进入'))
        self.facility_kind.current(0); self.facility_kind.pack(side='left', padx=(0, 8))
        self.facility_kind.bind('<<ComboboxSelected>>', lambda e: self.refresh_facilities())
        self.facility_search = ttk.Entry(search_row); self.facility_search.pack(fill='x', expand=True)
        self.facility_search.bind('<KeyRelease>', lambda e: self.refresh_facilities())
        ttk.Label(switches, style='CardFaint.TLabel', wraplength=950, justify='left', text=(
            '启用/禁用只改动所选的已保存开关。启用移动会解除其限制；区域解锁开关是分开的。'
            '路线、剧情和 NPC 条件仍可能限制进入。神道之门登记与普通快速传送相互独立。\n'
            '厩舍除非被选中，否则保持不变。使用同路线镜像选择。Ctrl+Z 撤销；Ctrl+S 保存。'
        )).pack(side='bottom', anchor='w', pady=(8, 0))
        self.facility_detail = ttk.Label(switches, style='CardMuted.TLabel', wraplength=950,
                                         justify='left', padding=(12, 8), text='选择一个开关以查看其验证情况与限制。')
        self.facility_detail.pack(side='bottom', fill='x', pady=(8, 0))
        self.facility_tree = self._facility_table(switches, (
            ('#0', '功能 / 设施', 245), ('kind', '种类', 125), ('state', '保存的开关', 160),
            ('evidence', '验证程度', 135), ('jp', '游戏内名称', 390), ('hash', '哈希', 90)))
        self.facility_tree.bind('<<TreeviewSelect>>', lambda e: self._facility_selected())

        top = ttk.Frame(access, style='Card.TFrame', padding=(14, 12)); top.pack(fill='x', pady=(8, 0))
        ttk.Button(top, text='复制诊断信息', style='Small.TButton', command=self.copy_facility_diagnostics).pack(side='right')
        self.facility_route = ttk.Label(top, text='打开存档以查看进入条件。', style='CardH3.TLabel')
        self.facility_route.pack(anchor='w')
        ttk.Label(top, text='只读的数据条件。斗技场尚无经验证的保存开关。已记录的标记不保证能够进入。',
                  style='CardMuted.TLabel', wraplength=920).pack(anchor='w', pady=(6, 0))
        self.access_detail = ttk.Label(access, style='CardMuted.TLabel', wraplength=950, justify='left',
                                      padding=(12, 10), text='选择一个传送点以查看已知条件。')
        self.access_detail.pack(side='bottom', fill='x', pady=(8, 0))
        self.facility_access_tree = self._facility_table(access, (
            ('#0', '传送点', 200), ('stored', '保存的证据', 140),
            ('route', '路线 / 前置条件', 265), ('entry', '进入 / 入口', 330)))
        self.facility_access_tree.configure(selectmode='browse')
        self.facility_access_tree.bind('<<TreeviewSelect>>', lambda e: self._facility_access_selected())

    @staticmethod
    def _facility_table(page, columns):
        frame = ttk.Frame(page, style='Card.TFrame'); frame.pack(fill='both', expand=True, pady=(8, 0))
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=tuple(c[0] for c in columns[1:]), show='tree headings', selectmode='extended')
        for col, label, width in columns:
            tree.heading(col, text=label, anchor='w'); tree.column(col, width=width, minwidth=width, stretch=False)
        vs = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        hs = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky='nsew'); vs.grid(row=0, column=1, sticky='ns'); hs.grid(row=1, column=0, sticky='ew')
        return tree

    def refresh_facilities(self):
        if not hasattr(self, 'facility_tree'):
            return
        tree = self.facility_tree; keep = tree.selection(); view = tree.yview()
        tree.delete(*tree.get_children()); self.facility_detail.configure(text='选择一个开关以查看其验证情况与限制。')
        for b in self.facility_buttons: b.state(['disabled'])
        self._refresh_facility_access()
        try:
            cat = facilities.catalog(); f = self.core_of()
            if f is None:
                self.facility_count.configure(text='打开存档以查看其已保存的功能开关。'); return
            values = facilities.read(f)
        except core.SaveError as exc:
            self.facility_count.configure(text=str(exc)); return
        q = self.facility_search.get().strip().casefold(); kind = self.facility_kind.get()
        for h, row in cat.items():
            zh = facilities.label_zh(row)
            if kind != '全部开关' and facilities.kind_label(row) != kind: continue
            if q and q not in f"{zh} {row['label']} {row['name']} {h:08x}".casefold(): continue
            tree.insert('', 'end', iid=f'{h:08x}', text=zh, values=(facilities.kind_label(row),
                facilities.state_label(h, values.get(h)), facilities.coverage_label(row), row['name'], f'{h:08x}'))
        tree.selection_set([i for i in keep if tree.exists(i)])
        if keep and any(tree.exists(i) for i in keep) and view:
            tree.yview_moveto(view[0])
        self.facility_count.configure(text=f"显示 {len(tree.get_children())} / {len(cat)} 个开关 · 推断 {sum(r['coverage'] == 'Inferred' for r in cat.values())} 个 · 选择一行查看其测试范围")
        for b in self.facility_buttons: b.state(['!disabled'])
        self._facility_selected()

    def _facility_selected(self):
        picked = self.facility_tree.selection()
        if not picked: return
        rows = facilities.catalog()
        if len(picked) == 1:
            row = rows[int(picked[0], 16)]
            text = f"{facilities.label_zh(row)} · {facilities.coverage_label(row)}\n{row['note']}"
            if int(picked[0], 16) == facility_access.YU_PHAS_GATE:
                text += '\n' + facility_access.yu_phas_gate_status(self.core_of())
            self.facility_detail.configure(text=text)
        else:
            self.facility_detail.configure(text=f'已选择 {len(picked)} 个开关，将一起改动。其他开关、普通传送点和剧情进度保持不变。')

    def set_facilities(self, state):
        if not self.sv: return
        picked = self.facility_tree.selection()
        if not picked:
            self.flash('请先选择一个或多个功能开关。', 'info'); return
        self._commit_form()
        try:
            edits = {int(i, 16): state for i in picked}
            before = self.core_of(); after = facilities.edited(before, edits)
        except (ValueError, core.SaveError) as exc:
            self.flash(str(exc), 'error'); return
        self._core_edit(lambda f: facilities.edited(f, edits),
                        f"{'已启用' if state else '已禁用'} {len(edits)} 个功能开关")

    def _refresh_facility_access(self):
        tree = self.facility_access_tree; keep = tree.selection(); tree.delete(*tree.get_children())
        self.access_detail.configure(text='选择一个传送点以查看已知条件。')
        self._access_rows = {}
        f = self.core_of()
        if f is None:
            self.facility_route.configure(text='打开存档以查看进入条件。'); return
        route = facility_access.route_info(f)
        route_txt = (f'进入条件 · {facility_access._lord(route[0])} 第{route[1]}部 第{route[2]}章'
                     if route else '进入条件 · 路线未知')
        self.facility_route.configure(text=route_txt)
        try:
            for row in facility_access.report(f):
                iid = row.get('id') or f"{row['hash']:08x}"; self._access_rows[iid] = row
                tree.insert('', 'end', iid=iid, text=row['label'], values=(row['stored'], row['route_check'], row['entry']))
        except core.SaveError as exc:
            self.access_detail.configure(text=str(exc)); return
        tree.selection_set([i for i in keep if tree.exists(i)])
        self._facility_access_selected()

    def _facility_access_selected(self):
        picked = self.facility_access_tree.selection()
        if picked:
            row = self._access_rows[picked[0]]
            self.access_detail.configure(text=row['detail'] + ('\n' + row['error'] if row['error'] else ''))

    def copy_facility_diagnostics(self):
        f = self.core_of()
        if f is None: return
        try:
            text = facility_access.diagnostics(f)
        except core.SaveError as exc:
            self.flash(str(exc), 'error'); return
        self.clipboard_clear(); self.clipboard_append(text)
        self.flash('已复制进入条件及相关保存标记。', 'ok')
