# -*- coding: utf-8 -*-
"""tests.test_138_softtable_v2_5_patch_b3 — 补丁 B-3（去左侧树 + 单行精简查询 + 后端通用 q 搜索）测试。"""
import importlib.util
import os
import sys

from jinja2 import Environment, FileSystemLoader


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    return open(os.path.join(_root(), rel), encoding='utf-8').read()


def test_sidebar_removed_and_templates_parse():
    wb = _read('templates/components/roster/_softtable_workbench.html')
    a4 = _read('static/js/pages/dynatable_a4_view.js')
    assert 'dtV2Sider' not in wb
    assert 'dtV2WbLayout' not in wb
    assert 'dtV2Sider' not in a4

    env = Environment(loader=FileSystemLoader(os.path.join(_root(), 'templates')))
    for tpl in [
        'components/roster/_softtable_workbench.html',
        'components/roster/roster_page.html',
        'dynatable/list.html',
    ]:
        env.parse(_read(os.path.join('templates', tpl)))


def test_compact_search_and_seq_excluded():
    for tpl in ('templates/dynatable/list.html', 'templates/components/roster/roster_page.html'):
        src = _read(tpl)
        assert 'name="q"' in src
        assert 'id="dtV2ToggleAdvFilter"' in src
        assert 'id="dtV2AdvFilterPanel" hidden' in src
        assert "!= '序号'" in src


def test_engine_universal_q_search_text_and_identity():
    root = _root()
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location('st_eng_b3', os.path.join(root, 'utils/softtable_engine.py'))
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)

    fields = [
        {'code': 'f1', 'display_name': '姓名', 'data_type': 'text', 'sensitivity': 'none'},
        {'code': 'f2', 'display_name': '身份证号码', 'data_type': 'text', 'sensitivity': 'identity'},
    ]
    # 138 契约对齐：本仓 digest_fn 为 (code, value) 两参（identity_digest 按字段域分离），
    # 补丁原作按 (value) 一参断言，会使真实路由 500（缺陷 #20）。
    dig = lambda code, v: 'H:%s:%s' % (code, v)
    rows = [
        {'id': 1, 'f1': '陈明贵', 'f2': '5113****4518', '__digest__': {'f2': dig('f2','511324194403124518')}},
        {'id': 2, 'f1': '李桂兰', 'f2': '5113****2246', '__digest__': {'f2': dig('f2','511324194109082246')}},
    ]
    assert len(eng.select_rows(rows, fields, {'__q__': '陈明'}, digest_fn=dig)) == 1
    assert len(eng.select_rows(rows, fields, {'__q__': '511324194109082246'}, digest_fn=dig)) == 1
    assert len(eng.select_rows(rows, fields, {'__q__': '不存在'}, digest_fn=dig)) == 0


def test_q_query_wiring_and_digest_contract():
    """138 缺陷 #19/#20 回归守卫（源码级）。

    #19 roster_routes.py 的 q 三行必须缩进在 inner _roster_filters 内（补丁原稿落在外层
        4 空格，import 期即取 request → 全站 500）。
    #20 本仓 digest_fn 契约为 (code, value) 两参；engine 若按补丁原稿一参调用 →
        真实路由 q 一问即 TypeError。
    """
    import re
    # 三个入口都要把 q 收进 __q__
    for rel in ('utils/roster_routes.py', 'routes/shop.py', 'routes/dynatable.py'):
        src = _read(rel)
        assert "'__q__'" in src, rel + ' 未接线 __q__'
    # #19：roster_routes 的 q 行缩进必须是 8 空格（inner 函数体内）
    rr = _read('utils/roster_routes.py')
    m = re.search(r"^(\s*)q_kw = \(request\.args\.get\('q'\)", rr, re.M)
    assert m, 'roster_routes 缺 q_kw'
    assert len(m.group(1)) == 8, 'roster_routes q_kw 缩进应为 8 空格（当前 %d）' % len(m.group(1))
    # #20：engine 的 identity 指纹必须按 (code, value) 两参调用
    eng = _read('utils/softtable_engine.py')
    # 两参契约（作者热修结构：按字段预计算 q_dig_map）
    assert "digest_fn(f['code'], q_kw)" in eng, 'engine q 搜索须按本仓两参契约调用 digest_fn'
    assert 'digest_fn(q_kw)' not in eng, 'engine 仍有单参 digest_fn(q_kw) 调用（会 500）'


def test_route_q_search_identity_exact(isolated_app_factory):
    """路由级 q 回归：普通列子串 + 加密列完整值精确（缺陷 #20 的真实路径验证）。

    引擎单测用桩 digest 只能证明算法；本用例经真实路由 → api_records → _digest_fn()
    全链路，覆盖「单参调用即 500」的失败形态。
    """
    from utils.crypto import generate_salt, key_holder
    app = isolated_app_factory()
    key_holder.initialize('softtable-q-pass', salt=generate_salt())

    from models import DynamicRecord, db
    from utils import softtable_engine as eng
    from utils import roster_facade as facade

    with app.app_context():
        fields = eng.normalize_fields([
            {'code': 'f00', 'display_name': '姓名', 'data_type': 'text', 'sensitivity': 'none'},
            {'code': 'f01', 'display_name': '身份证号码', 'data_type': 'text', 'sensitivity': 'identity'},
        ])
        table = facade.create_table('dynatable', 'q_test', 'q 搜索测试',
                                    group_name=None, fields=fields, created_by='admin')
        recs, _errs = eng.build_records(fields, [['测试甲', '511324199001011234']])
        for r in recs:
            db.session.add(DynamicRecord(table_id=table.id, data=r['data'],
                                         digest=r['digest'], created_by='admin'))
        db.session.commit()
        code = table.code

    c = app.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'

    def _total(q):
        resp = c.get('/dynatable/api/t/%s/records' % code, query_string={'q': q})
        assert resp.status_code == 200, resp.status_code
        return (resp.get_json() or {}).get('total')

    assert _total('测试甲') == 1, '普通文本列子串未命中'
    assert _total('511324199001011234') == 1, '加密列完整值精确未命中'
    assert _total('5113241990') == 0, '加密列不应对片段命中'
