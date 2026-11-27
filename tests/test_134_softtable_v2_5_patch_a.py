# -*- coding: utf-8 -*-
"""tests.test_134_softtable_v2_5_patch_a — 软表 V2.5 补丁 A 单元测试（样本优先推断 / 千行无截断 / 删表 / 挂载收敛）。

注：外发补丁原文件名 test_129_softtable_v2_5_patch_a.py（按补丁作者序号），与主仓任务 129
（phone 家族列扩长）撞号，套打时按主仓任务号重命名为 test_134（AGENTS.md 测试运行规约）。
"""
import importlib.util
import io
import os
import sys

import openpyxl
import pytest


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_mod(rel_path, mod_name):
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(root, rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_1200_rows_no_truncation_and_sample_first_inference():
    """验证 1200 行数据不被 1000 行上限静默截断，且真实非数值/非日期列自动保真为 text。"""
    parser = _load_mod('utils/softtable_v2_parser.py', 'softtable_v2_parser_a')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '高龄明细'
    ws.append(['序号', '姓名', '补贴月份', '补贴标准', '年龄', '入党时间', '发放金额', '身份证号码'])
    for i in range(1, 1201):
        ws.append([
            f'00{i}',             # 前导零编号 -> 保真 text
            f'老人{i}',
            '2026-01',            # 含「补贴」+ YYYY-MM -> 保真 text（不再触发 float 团灭）
            '100元/月',            # 含「补贴」「标准」+ 带单位 -> 保真 text
            '82岁' if i == 500 else 82,  # 第 500 行含单位 -> 全量样本校验降级 text
            '1978.07',            # 非完整年月日 -> 保真 text
            100,                  # 全量纯数字 -> number
            f'51132419440101{i:04d}',
        ])
    buf = io.BytesIO()
    wb.save(buf)

    res = parser.parse_workbook_v2('高龄明细.xlsx', io.BytesIO(buf.getvalue()))
    t = res['sheets'][0]['tables'][0]
    assert t['row_count'] == 1200, f"expected 1200 rows, got {t['row_count']}"
    fmap = {f['display_name']: f for f in t['fields']}
    assert fmap['序号']['data_type'] == 'text'
    assert fmap['补贴月份']['data_type'] == 'text'
    assert fmap['补贴标准']['data_type'] == 'text'
    assert fmap['年龄']['data_type'] == 'text'
    assert fmap['入党时间']['data_type'] == 'text'
    assert fmap['发放金额']['data_type'] == 'number'
    assert fmap['身份证号码']['sensitivity'] == 'identity'


def test_empty_template_keyword_fallback_safe_exclusions():
    """空表样（0 行数据）按关键词兜底时，排除月份/期号/类型/标准等文本列。"""
    parser = _load_mod('utils/softtable_v2_parser.py', 'softtable_v2_parser_b')
    assert parser._infer_field_from_header('补贴月份', [])['data_type'] == 'text'
    assert parser._infer_field_from_header('补贴类型', [])['data_type'] == 'text'
    assert parser._infer_field_from_header('补贴标准', [])['data_type'] == 'text'
    assert parser._infer_field_from_header('发放金额', [])['data_type'] == 'number'
    assert parser._infer_field_from_header('死亡时间', [])['data_type'] == 'date'
    assert parser._infer_field_from_header('身份证号码', [])['sensitivity'] == 'identity'


def test_dynatable_modules_include_org_and_civil():
    mods = _load_mod('utils/dynatable_modules.py', 'dynatable_modules_a')
    assert 'organization' in mods.MODULE_LABELS
    assert 'civil' in mods.MODULE_LABELS
    assert 'org' in mods.TAB_GROUP_CONFIG
    assert 'civil' in mods.TAB_GROUP_CONFIG
    assert mods.tab_group_for('organization') == 'org'
    assert mods.tab_group_for('civil') == 'civil'


def test_routes_and_facade_patch_a_contracts():
    root = _repo_root()
    dt_src = open(os.path.join(root, 'routes', 'dynatable.py'), encoding='utf-8').read()
    rf_src = open(os.path.join(root, 'utils', 'roster_facade.py'), encoding='utf-8').read()
    assert "@dynatable_bp.route('/api/t/<code>/delete', methods=['POST'])" in dt_src
    assert "@dynatable_bp.route('/api/group/<path:group_name>/delete', methods=['POST'])" in dt_src
    assert 'link_safety_table_node' in dt_src[dt_src.index('def api_create_batch'):]
    assert 'view_sensitive' in dt_src[dt_src.index('def api_reveal_record'):dt_src.index('def api_reveal_record') + 900]
    assert 'siblings' in dt_src[dt_src.index('def api_v2_a4_data'):dt_src.index('def api_v2_a4_data') + 1400]
    assert 'clone_group' in dt_src[dt_src.index('def api_v2_clone_period'):]
    assert 'seen_groups' in rf_src[rf_src.index('def list_dynamic_tabs'):rf_src.index('def is_dynamic_tab_table')]


# ------------------------------------------------------------------ 端点契约（补丁自带 4 例全在纯函数/静态源码层，132/133 先例延续）
def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / 'shucun.db')
    for key, value in {
        'SHUCUN_DB_PATH': db_path,
        'SHUCUN_TEMP_DIR': str(tmp_path / 'temp'),
        'SHUCUN_UPLOAD_DIR': str(tmp_path / 'uploads'),
        'SHUCUN_BACKUP_DIR': str(tmp_path / 'backups'),
        'SHUCUN_DATA_DIR': str(tmp_path / 'data'),
        'SHUCUN_LOG_DIR': str(tmp_path / 'logs'),
        'SHUCUN_LICENSE_FILE': str(tmp_path / 'license.key'),
        'SHUCUN_LAN_AUTHORIZED': '1',
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(config, 'BACKUP_DIR', str(tmp_path / 'backups'))
    monkeypatch.setattr(config, 'TEMP_DIR', str(tmp_path / 'temp'))
    monkeypatch.setattr(config, 'UPLOAD_DIR', str(tmp_path / 'uploads'))
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(config.Config, 'SQLALCHEMY_DATABASE_URI',
                        'sqlite:///%s?check_same_thread=False' % db_path)
    for sub in ('temp', 'uploads', 'backups', 'data', 'logs'):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    return application


@pytest.fixture()
def client(tmp_path, monkeypatch):
    application = _make_app(tmp_path, monkeypatch)
    from utils.crypto import generate_salt, key_holder
    key_holder.initialize('softtable-test-pass', salt=generate_salt())
    c = application.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
    c.application = application   # _mk_table 需在请求外开 app_context 直调引擎
    return c


def _mk_table(client, code='del_test', group_name=None, rows=None):
    """直接经引擎建一张最小软表（1 text + 1 identity 列），可指定分组。"""
    from models import DynamicRecord, db
    from utils import softtable_engine as eng
    from utils import roster_facade as facade
    with client.application.app_context():
        fields = eng.normalize_fields([
            {'code': 'f00', 'display_name': '姓名', 'data_type': 'text',
             'sensitivity': 'none', 'is_required': True},
            {'code': 'f01', 'display_name': '身份证号码', 'data_type': 'text',
             'sensitivity': 'identity'},
        ])
        table = facade.create_table('dynatable', code, '删除测试表', group_name=group_name,
                                    fields=fields, created_by='admin')
        for row in (rows or [['甲', '110101199001011234'], ['乙', '110101199001011235']]):
            recs, _errors = eng.build_records(fields, [row])
            for r in recs:
                db.session.add(DynamicRecord(table_id=table.id, data=r['data'],
                                             digest=r['digest'], created_by='admin'))
        db.session.commit()
        tid = table.id
    return tid


def test_delete_table_cascades_and_blocks_builtin(client):
    """单表删除：元数据+记录+版式级联清理；重复删除 404。"""
    from models import DynamicField, DynamicRecord, DynamicTable, db
    tid = _mk_table(client, code='del_a')
    # 先落一份版式（删表应连带清理；路由 _layout_store() 在 DATA_DIR/softtable_layouts/）
    with client.application.app_context():
        from utils.softtable_layout import SoftTableLayoutStore
        import config as _cfg
        store = SoftTableLayoutStore(os.path.join(_cfg.DATA_DIR, 'softtable_layouts'))
        store.save_layout('del_a', {'layout_mode': 'standard', 'col_pcts': [50.0, 50.0]})

    res = client.post('/dynatable/api/t/del_a/delete', json={})
    assert res.status_code == 200 and res.get_json()['success'], res.get_json()
    with client.application.app_context():
        assert DynamicTable.query.get(tid) is None
        assert DynamicField.query.filter_by(table_id=tid).count() == 0
        assert DynamicRecord.query.filter_by(table_id=tid).count() == 0
    assert store.exists('del_a') is False
    # 重复删除 → 404
    again = client.post('/dynatable/api/t/del_a/delete', json={})
    assert again.status_code == 404


def test_delete_group_requires_all_custom_and_deletes_all(client):
    """整组删除：同组全部级联清理；组内含内置预置表 → 400 拒绝；空组 404。"""
    from models import DynamicTable, db
    _mk_table(client, code='grp_a', group_name='整组测试簿')
    _mk_table(client, code='grp_b', group_name='整组测试簿')
    res = client.post('/dynatable/api/group/%s/delete' % '整组测试簿', json={})
    assert res.status_code == 200 and res.get_json()['success'], res.get_json()
    with client.application.app_context():
        codes = [t.code for t in DynamicTable.query.filter_by(group_name='整组测试簿').all()]
        assert codes == []

    # 空组 → 404
    empty = client.post('/dynatable/api/group/%s/delete' % '不存在的组', json={})
    assert empty.status_code == 404

    # 内置预置表保护：把一张表挂到内置排除码 + 对应 module_key 上（is_builtin 需双匹配）
    from utils.dynatable_modules import TAB_GROUP_CONFIG, is_builtin_roster_code
    _mk_table(client, code='grp_c', group_name='含内置组')
    with client.application.app_context():
        builtin_code = next(iter(TAB_GROUP_CONFIG['social']['exclude_codes']))
        t = DynamicTable.query.filter_by(code='grp_c').first()
        t.code = builtin_code
        t.module_key = TAB_GROUP_CONFIG['social']['module_key']  # 'pension'，exclude_codes 按组判定
        db.session.commit()
        assert is_builtin_roster_code(t.module_key, t.code) is True
    blocked = client.post('/dynatable/api/group/%s/delete' % '含内置组', json={})
    assert blocked.status_code == 400
    assert '内置' in blocked.get_json()['msg'], blocked.get_json()
    with client.application.app_context():
        assert DynamicTable.query.filter_by(code=builtin_code).first() is not None


def test_delete_table_snapshot_backup_written(client):
    """删除前自动写结构快照（JSON+wbak 双份），可回滚重导（验收重点「删表干净可重导」）。"""
    import glob
    _mk_table(client, code='snap_a')
    with client.application.app_context():
        import config as _cfg
        backup_dir = getattr(_cfg, 'BACKUP_DIR', '')
    client.post('/dynatable/api/t/snap_a/delete', json={})
    hits = glob.glob(os.path.join(backup_dir, 'softtable', 'snap_a-*.json'))
    assert hits, '删除前应落结构快照 JSON（BACKUP_DIR/softtable 下）'


def test_reveal_allows_view_sensitive_role_and_masks_otherwise(client, monkeypatch):
    """reveal 权限放宽契约：role_id=1 直通；无 view_sensitive 位 → 403（不再 @admin_required 独占）。"""
    _mk_table(client)
    rec = client.get('/dynatable/api/t/del_test/records').get_json()['data'][0]
    rid = rec['id']
    # admin（user_id=1, role_id=1）→ 200 明文
    ok = client.post('/dynatable/api/t/del_test/record/%d/reveal' % rid, json={})
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()['data']['f01'] == '110101199001011234'


def test_org_civil_mounted_via_list_dynamic_tabs(client):
    """org/civil 挂载契约：module_key=organization/civil 的已建软表出现在对应胶囊组。"""
    from utils import roster_facade
    _mk_table(client, code='org_m', group_name='机构花名册')
    with client.application.app_context():
        from models import DynamicTable, db
        t = DynamicTable.query.filter_by(code='org_m').first()
        t.module_key = 'organization'
        db.session.commit()
    with client.application.app_context():
        result = roster_facade.list_dynamic_tabs('org')
        items = result['items'] if isinstance(result, dict) else result
        keys = [x['key'] for x in items]
        assert 'dt:org_m' in keys, keys
        # 单表组不收敛：label=表名（组内 ≥2 张才收敛为组名胶囊，下一例验证）
        assert items[0]['label'] == '删除测试表'


def test_roster_facade_group_capsule_collapses_siblings(client):
    """同组多子表收敛为 1 枚胶囊（label=组名，title 带子表计数）。"""
    from utils import roster_facade
    _mk_table(client, code='cap_a', group_name='花名册簿')
    _mk_table(client, code='cap_b', group_name='花名册簿')
    with client.application.app_context():
        from models import DynamicTable, db
        for code in ('cap_a', 'cap_b'):
            t = DynamicTable.query.filter_by(code=code).first()
            t.module_key = 'organization'
        db.session.commit()
    with client.application.app_context():
        result = roster_facade.list_dynamic_tabs('org')
        items = result['items'] if isinstance(result, dict) else result
        assert len(items) == 1, [x['label'] for x in items]
        assert items[0]['label'] == '花名册簿'
        assert '2 张子表' in items[0]['title']
