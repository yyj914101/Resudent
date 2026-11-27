# -*- coding: utf-8 -*-
"""tests/test_133_softtable_v2_phase2.py — 软表引擎 V2 第二阶段单元测试与前端契约守卫。

注：外发补丁原文件名 test_128_softtable_v2_phase2.py（按补丁作者序号），与主仓任务 128
（居民单格多电话方案 A）撞号，套打时按主仓任务号重命名为 test_133（AGENTS.md 测试运行规约）。
"""
import importlib.util
import pytest
import os
import re
import tempfile


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_layout_mod():
    full = os.path.join(_repo_root(), 'utils', 'softtable_layout.py')
    spec = importlib.util.spec_from_file_location('softtable_layout', full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_masked_identity_overwrite_guard_helper():
    mod = _load_layout_mod()
    assert mod.is_masked_placeholder('5113****1234') is True
    assert mod.is_masked_placeholder('138***1') is True
    assert mod.is_masked_placeholder('511324199001011234') is False
    assert mod.is_masked_placeholder('') is False


def test_clone_layout_for_period_and_optimistic_lock():
    mod = _load_layout_mod()
    with tempfile.TemporaryDirectory() as tmp:
        store = mod.SoftTableLayoutStore(tmp)
        orig = store.save_layout('elder_2026_11', {
            'layout_mode': 'side_by_side',
            'workbook_group': '高龄津贴',
            'period_tag': '2026-11',
            'col_pcts': [50.0, 50.0],
        })
        cloned = store.clone_layout_for_period('elder_2026_11', 'elder_2026_12', '2026-12')
        assert cloned['period_tag'] == '2026-12'
        assert cloned['layout_mode'] == 'side_by_side'
        assert cloned['version_ts'] != orig['version_ts']
        assert store.exists('elder_2026_12') is True


def test_resident_autofill_column_mapper():
    mod = _load_layout_mod()
    fields = [
        {'code': 'f00', 'display_name': '序号'},
        {'code': 'f01', 'display_name': '姓名'},
        {'code': 'f02', 'display_name': '身份证号码'},
        {'code': 'f03', 'display_name': '联系电话'},
        {'code': 'f04', 'display_name': '所在小组'},
    ]
    res_info = {
        'name': '陈明贵',
        'id_card': '511324194403124518',
        'phone': '13890814518',
        'village_group': '二组',
    }
    mapped = mod.map_resident_to_softtable_row(fields, res_info, next_seq=5)
    assert mapped['f00'] == '5'
    assert mapped['f01'] == '陈明贵'
    assert mapped['f02'] == '511324194403124518'
    assert mapped['f03'] == '13890814518'
    assert mapped['f04'] == '二组'


def test_list_html_preserves_r096_snapshot_and_v2_hooks():
    root = _repo_root()
    tpl = open(os.path.join(root, 'templates', 'dynatable', 'list.html'), encoding='utf-8').read()
    js = open(os.path.join(root, 'static', 'js', 'pages', 'dynatable_a4_view.js'), encoding='utf-8').read()

    # R-096 列表页快照正则要求 fields 是 window.__ST_LIST__ 的末键
    assert re.search(r'fields:\s*\{\{\s*fields\s*\|\s*tojson\s*\}\}\s*\};', tpl), 'window.__ST_LIST__ 末键契约被破坏'

    # 校验 dynatable_a4_view.js 中引用的所有关键 DOM ID 均存在于渲染链——
    # 135 起 18 个 dtV2 ID 收敛进共享片段 _softtable_workbench.html（list.html 与
    # roster_page.html include 同一份），源码断言随架构走：片段必含全部 ID，
    # list.html 必含 include 行（两宿主契约一致，防双份残留）。
    wb = open(os.path.join(root, 'templates', 'components', 'roster', '_softtable_workbench.html'),
              encoding='utf-8').read()
    for dom_id in (
        'dtV2SwitchBar', 'dtV2A4Wrap', 'dtV2Canvas', 'dtV2PageStyle',
        'dtV2FillBar', 'dtV2FmtBar', 'dtV2ResidentQuery', 'dtV2ResidentSelect',
        'dtV2BtnBackfill', 'dtV2BtnAddRow', 'dtV2BtnPrint', 'dtV2BtnSaveLayout',
        'dtV2PaperOrient', 'dtV2PaperMargin', 'dtV2BtnFitA4',
        'dtV2PrintOverlay', 'dtV2PrintPaper', 'dtV2BtnDoPrint',
    ):
        assert f'id="{dom_id}"' in wb, f'_softtable_workbench.html 缺少 DOM ID: {dom_id}'
        assert dom_id in js, f'dynatable_a4_view.js 未使用 DOM ID: {dom_id}'
    # 137（补丁 B-2）：套打预览关闭按钮改 data-dtv2-close 委托（原 dtV2BtnClosePrint 退役）
    assert 'data-dtv2-close="dtV2PrintOverlay"' in wb, '套打预览缺 data-dtv2-close 关闭入口'
    assert 'data-dtv2-close' in js, 'a4_view.js 未实现 data-dtv2-close 弹窗关闭委托'
    assert "include 'components/roster/_softtable_workbench.html'" in tpl, \
        'list.html 丢失共享工作台片段 include'
    # 共享片段收敛后，宿主模板不得再出现与片段重名的 dtV2 元素（防双份 ID 渲染）。
    # 137（补丁 B-2）：list.html 新增自有容器 id=dtV2ListCard（列表视图卡片），
    # 判据由「禁止一切 id=dtV2」改为「与片段 ID 集合不相交」，仍能拦双份渲染。
    wb_ids = set(re.findall(r'id="(dtV2[^"]+)"', wb))
    tpl_ids = set(re.findall(r'id="(dtV2[^"]+)"', tpl))
    dup = wb_ids & tpl_ids
    assert not dup, 'list.html 与共享片段重名 dtV2 ID（双份渲染）：%s' % sorted(dup)




# ------------------------------------------------------------------ 端点契约（补丁自带 4 例未覆盖任何路由，132 先例延续）
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


def _mk_table(client, code='a4t_test', rows=None, identity=True):
    """直接经引擎建一张最小软表（含 1 个 identity 列），在 client.application 上下文内执行。"""
    from models import DynamicRecord, db
    from utils import softtable_engine as eng
    from utils import roster_facade as facade
    with client.application.app_context():
        fields = eng.normalize_fields([
            {'code': 'f00', 'display_name': '姓名', 'data_type': 'text',
             'sensitivity': 'none', 'is_required': True},
            {'code': 'f01', 'display_name': '身份证号码', 'data_type': 'text',
             'sensitivity': 'identity' if identity else 'none'},
            {'code': 'f02', 'display_name': '备注', 'data_type': 'text', 'sensitivity': 'none'},
        ])
        table = facade.create_table('dynatable', code, 'A4 测试表', fields=fields, created_by='admin')
        for row in (rows or [['甲', '110101199001011234', ''], ['乙', '110101199001011235', '']]):
            recs, _errors = eng.build_records(fields, [row])
            for r in recs:
                db.session.add(DynamicRecord(table_id=table.id, data=r['data'],
                                             digest=r['digest'], created_by='admin'))
        db.session.commit()
        tid = table.id
    return tid


def test_a4_data_masks_identity_and_caps_300(client):
    """A4 数据包：identity 列默认脱敏；total/truncated 语义正确；300 行上限截断。"""
    from models import DynamicRecord, db
    tid = _mk_table(client)
    with client.application.app_context():
        many = [{'data': '{"f00":"行%d","f02":""}' % i, 'digest': None}
                for i in range(299)]  # 已有 2 行 + 299 = 301
        db.session.add_all([DynamicRecord(table_id=tid, created_by='admin', **r) for r in many])
        db.session.commit()

    res = client.get('/dynatable/api/t/a4t_test/a4-data')
    assert res.status_code == 200
    data = res.get_json()['data']
    assert data['total'] == 301 and data['truncated'] is True
    assert len(data['records']) == 300
    ident_field = next(f for f in data['fields'] if f['code'] == 'f01')
    assert ident_field['sensitivity'] == 'identity'
    for rec in data['records']:
        v = str(rec.get('f01') or '')
        assert not (v.isdigit() and len(v) == 18), v
        if v:
            assert '***' in v or '****' in v
    _mk_table(client, code='a4t_plain', identity=False)
    plain = client.get('/dynatable/api/t/a4t_plain/a4-data').get_json()['data']
    assert all((rec.get('f01') or '').startswith('110101')
               for rec in plain['records'] if rec.get('f01'))


def test_inline_edit_masked_value_blocked_and_reveal_then_edit(client):
    """防覆写链路：脱敏态提交含 *** 值 → 400 拒绝；/reveal 明文后编辑 → 正常加密落库。"""
    _mk_table(client)
    rec = client.get('/dynatable/api/t/a4t_test/records').get_json()['data'][0]
    rid = rec['id']
    blocked = client.post('/dynatable/api/t/a4t_test/record/%d' % rid,
                          json={'code': 'f01', 'value': '110101****1234'})
    assert blocked.status_code == 400
    assert '脱敏' in blocked.get_json()['msg'], blocked.get_json()
    # 非敏感列不受拦截（备注列里写星号是合法内容）
    ok = client.post('/dynatable/api/t/a4t_test/record/%d' % rid,
                     json={'code': 'f02', 'value': '**** 备注内容 ****'})
    assert ok.status_code == 200 and ok.get_json()['success'], ok.get_json()
    revealed = client.post('/dynatable/api/t/a4t_test/record/%d/reveal' % rid, json={})
    assert revealed.status_code == 200
    plain = revealed.get_json()['data']['f01']
    edit = client.post('/dynatable/api/t/a4t_test/record/%d' % rid,
                       json={'code': 'f01', 'value': plain, 'allow_masked': True})
    assert edit.status_code == 200 and edit.get_json()['success'], edit.get_json()


def test_v2_create_record_with_resident_autofill_and_delete(client):
    """行级新增：空白手填 + resident_id 一键回填（姓名来自主档）；删除后 records 减一。"""
    from models import Resident, db
    with client.application.app_context():
        r = Resident(name='测试居民', gender='男', status='normal')
        db.session.add(r)
        db.session.commit()
        rid = r.id
    _mk_table(client)

    made = client.post('/dynatable/api/t/a4t_test/record/create',
                       json={'values': {'f00': '丙', 'f01': '110101199001011299'}})
    assert made.status_code == 200 and made.get_json()['success'], made.get_json()
    n1 = len(client.get('/dynatable/api/t/a4t_test/records').get_json()['data'])

    filled = client.post('/dynatable/api/t/a4t_test/record/create',
                         json={'resident_id': rid})
    assert filled.status_code == 200 and filled.get_json()['success'], filled.get_json()
    n2 = len(client.get('/dynatable/api/t/a4t_test/records').get_json()['data'])
    assert n2 == n1 + 1
    new_rec = filled.get_json()['data']
    assert new_rec.get('f00') == '测试居民', new_rec

    deleted = client.post('/dynatable/api/t/a4t_test/record/%d/delete' % new_rec['id'])
    assert deleted.status_code == 200 and deleted.get_json()['success']
    n3 = len(client.get('/dynatable/api/t/a4t_test/records').get_json()['data'])
    assert n3 == n1


def test_clone_period_copies_layout_and_optional_records(client):
    """期次结转：新表生成、名称带期次、LayoutSpec 克隆且 period_tag 落盘、copy_records 可选。"""
    _mk_table(client, code='clone_src')
    clone = client.post('/dynatable/api/t/clone_src/clone-period',
                        json={'period_tag': '2026-12', 'copy_records': True})
    assert clone.status_code == 200 and clone.get_json()['success'], clone.get_json()
    out = clone.get_json()['data']
    assert out['name'] == 'A4 测试表（2026-12）'
    assert out['copied'] == 2
    layout = client.get('/dynatable/api/t/%s/layout' % out['code']).get_json()['data']
    assert layout.get('period_tag') == '2026-12'
    clone2 = client.post('/dynatable/api/t/clone_src/clone-period',
                         json={'period_tag': '2027-01'})
    assert clone2.get_json()['success'] and clone2.get_json()['data']['copied'] == 0
    assert client.post('/dynatable/api/t/clone_src/clone-period', json={}).status_code == 400


def test_resident_search_endpoint(client):
    from models import Resident, db
    with client.application.app_context():
        db.session.add(Resident(name='王建国', gender='男', status='normal'))
        db.session.add(Resident(name='王建华', gender='女', status='normal'))
        db.session.add(Resident(name='李强', gender='男', status='retired'))
        db.session.commit()
    res = client.get('/dynatable/api/residents/search?q=王建').get_json()['data']
    assert len(res) == 2 and all('王建' in r['name'] for r in res)
    assert all('label' in r and 'id' in r for r in res)
