# -*- coding: utf-8 -*-
"""tests/test_141_softtable_v2_5_patch_c2_remove_legacy.py — Patch C-2 彻底移除第一代 GovernanceLedger 验收。

（补丁自带文件原名 test_134_softtable_v2_5_patch_c2_remove_legacy.py，与本仓 134 补丁 A 撞号，
 按「测试文件命名 = 主仓任务号」规约改名；141 套打补缺陷 #21/#22 修后补测。）

覆盖：
1. 运行时代码零 GovernanceLedger 引用（模型、路由、指标、备份、前端死文件均已清理）；
2. 8 类综治台账固定 code 映射、内置白名单防误删、智能传表防重路由、胶囊/侧栏/权限选择器全对齐；
3. 历史 /governance/<category>/ 保持 302 跳转至软表 V2 工作台，未知类别仍 404；
4. GovernancePetition / GovernancePetitionHandle 物理表完全不受影响；
5. 幂等无损迁移：
   5a 向旧 SQLite governance_ledgers 表写入历史数据，连跑两次 ensure_governance_v2_tables() 零重复、
      零丢字段，大屏 metrics（governance_ledgers / governance_key_personnel）计数精确；
   5b 加密字段完整性：旧密文行 + 未加密老行均迁为软表密文 + HMAC 指纹，明文零落库、可解密还原；
   5c 密钥锁定态整批推迟（不写空、不写明文、不标记），解锁后补迁完整；
   5d 解密失败行跳过待重试且不重复、不落完成标记；
   5e 重复执行不覆盖已编辑行、不复活已删除行；
   5f 迁移行经真实引擎读写链路（列表打码 / 编辑保存保留哨兵 / 通用搜索命中）正常；
6. 入口切换回归：8 个入口全部进统一双视图工作台（A4 套打 / 增行增列 / 期次结转 / 单行搜索），
   旧 CRUD 读写路由不复存在。
"""
import glob
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ID_CARD = '511324199304120112'
PLAIN_ID_CARD = '330101199001011234'

LEGACY_DDL = """
    CREATE TABLE IF NOT EXISTS governance_ledgers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category VARCHAR(40) NOT NULL,
        subtype VARCHAR(40) DEFAULT '',
        name VARCHAR(50) DEFAULT '',
        id_card VARCHAR(18),
        id_card_enc TEXT,
        id_card_hash VARCHAR(64),
        phone VARCHAR(120) DEFAULT '',
        record_date DATE,
        payload TEXT DEFAULT '{}',
        created_by VARCHAR(50) DEFAULT '',
        created_at DATETIME,
        updated_at DATETIME
    )
"""
WORKBENCH_MARKERS = (
    'id="dtV2A4Wrap"',          # A4 双视图
    'id="dtV2BtnPrint"',        # A4 套打预览
    'id="dtV2BtnClonePeriod"',  # 期次结转
    'id="dtV2ListAddRow"',      # 增行
    'id="dtV2ListAddCol"',      # 增列
    'name="q"',                 # 单行通用搜索
)


def _unlock_keyholder():
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # admin 全权限
    return client


def _legacy_db(app):
    """在隔离库里模拟「老库仍带 governance_ledgers 物理表」。"""
    from models import db
    from sqlalchemy import text
    with app.app_context():
        db.session.execute(text(LEGACY_DDL))
        db.session.commit()
    return db


def _insert_legacy(db, rows):
    """rows: list[dict]，键 = 旧表列名。调用方须处于 app context 内。"""
    from sqlalchemy import text
    for r in rows:
        cols = ', '.join(r.keys())
        binds = ', '.join(':' + k for k in r)
        db.session.execute(
            text('INSERT INTO governance_ledgers (%s) VALUES (%s)' % (cols, binds)), r)
    db.session.commit()


def _legacy_db_with(app, rows):
    """老库物理表 + 历史行一步到位（自带 app context）。"""
    db = _legacy_db(app)
    with app.app_context():
        _insert_legacy(db, rows)
    return db


def _run(app, migrate_legacy=True):
    from utils.governance_v2_migration import ensure_governance_v2_tables
    with app.app_context():
        return ensure_governance_v2_tables(migrate_legacy=migrate_legacy)


def _records(app, category):
    from models import DynamicRecord, DynamicTable
    from utils.governance_catalog import GOV_CATEGORY_CODES
    with app.app_context():
        tbl = DynamicTable.query.filter_by(code=GOV_CATEGORY_CODES[category]).first()
        return [(r.id, json.loads(r.data), json.loads(r.digest or '{}'))
                for r in DynamicRecord.query.filter_by(table_id=tbl.id).all()]


# ───────────────────────── 1-2. 清理面与接线面 ─────────────────────────

def test_zero_legacy_governance_ledger_references():
    assert not os.path.exists(os.path.join(ROOT, 'models', 'governance.py'))
    assert not os.path.exists(os.path.join(ROOT, 'templates', 'governance', 'ledger.html'))
    assert not os.path.exists(os.path.join(ROOT, 'static', 'js', 'pages', 'governance_ledger.js'))

    scan_dirs = ['app.py', 'models', 'routes', 'utils', 'static/js/pages', 'scripts']
    hits = []
    for rel in scan_dirs:
        full = os.path.join(ROOT, rel)
        files = [full] if os.path.isfile(full) else glob.glob(os.path.join(full, '**', '*.*'), recursive=True)
        for fp in files:
            if not fp.endswith(('.py', '.js')):
                continue
            # 迁移引擎本身合法读写旧表；历史迁移脚本 m044 是只读/归档性质的原生 SQL
            if os.path.basename(fp) == 'governance_v2_migration.py':
                continue
            if os.path.sep + 'migrations' + os.path.sep in fp:
                continue
            txt = open(fp, encoding='utf-8').read()
            if 'GovernanceLedger(' in txt or 'import GovernanceLedger' in txt or 'governance_ledgers' in txt:
                # utils/metrics.py 仅为看板兼容保留指标键名/处理器名 'governance_ledgers'（取数已切 DynamicRecord）
                if fp.endswith(os.path.join('utils', 'metrics.py')) and 'GovernanceLedger' not in txt:
                    continue
                hits.append(os.path.relpath(fp, ROOT))
    assert hits == [], f'发现残留的一代 GovernanceLedger 引用: %s' % hits


def test_eight_governance_codes_registered_and_wired():
    from utils.governance_catalog import CATEGORY_CONFIG, GOV_CATEGORY_CODES, GOV_SOFT_TABLE_CODES
    from utils.dynatable_modules import TAB_GROUP_CONFIG, is_builtin_roster_code
    from utils.softtable_smart_route import _BUILTIN_RULES

    assert set(GOV_CATEGORY_CODES.keys()) == set(CATEGORY_CONFIG.keys())
    assert len(GOV_SOFT_TABLE_CODES) == 8
    assert GOV_SOFT_TABLE_CODES.issubset(TAB_GROUP_CONFIG['governance']['exclude_codes'])
    for code in GOV_SOFT_TABLE_CODES:
        assert is_builtin_roster_code('governance', code) is True

    routed_codes = {r['code'] for r in _BUILTIN_RULES}
    assert GOV_SOFT_TABLE_CODES.issubset(routed_codes)

    mt_html = open(os.path.join(ROOT, 'templates', 'components', '_macros', 'module_tabs.html'), encoding='utf-8').read()
    sb_html = open(os.path.join(ROOT, 'templates', 'components', '_partials', 'sidebar_menu.html'), encoding='utf-8').read()
    base_html = open(os.path.join(ROOT, 'templates', 'base.html'), encoding='utf-8').read()
    assert "governance.ledger" not in mt_html
    assert "governance.ledger" not in sb_html
    assert "governance.ledger" not in base_html
    for code in GOV_SOFT_TABLE_CODES:
        assert code in mt_html
    # 侧栏现行扁平布局的单一真源 nav_spec.py 不得残留旧端点
    nav_src = open(os.path.join(ROOT, 'utils', 'nav_spec.py'), encoding='utf-8').read()
    assert 'governance.ledger' not in nav_src


def test_smart_route_governance_rules_do_not_misfire_on_generic_rosters():
    """#25 回归（141）：综治 8 条内置规则收紧——通用名单不得被泛化列指纹（职务/电话/地址）
    误判成内置表（否则挂载模块被预选成 governance，组织架构名单就不再走反哺）；
    专属列指纹 ≥3 个同时命中、或文件名命中时仍须正确识别。"""
    from utils.softtable_smart_route import detect_smart_route

    def route(filename, headers):
        parsed = {'workbook_group': filename.rsplit('.', 1)[0],
                  'sheets': [{'tables': [{'fields': [{'display_name': h} for h in headers]}]}]}
        r = detect_smart_route(filename, parsed)
        return r['kind'], r.get('target_code') or r.get('recommended_module')

    # 反例：通用名单 → 不得命中内置表
    assert route('社区党委及小组长备案表及居委人员名单.xlsx', ['姓名', '职务', '联系电话', '身份证号']) \
        == ('softtable_v2', 'organization')
    assert route('网格员花名册.xlsx', ['姓名', '职务', '居住地址', '联系电话']) == ('softtable_v2', 'dynatable')
    assert route('产业分红明细.xlsx', ['姓名', '金额', '工作措施']) == ('softtable_v2', 'dynatable')

    # 正例：专属列指纹（≥3）或文件名命中
    for fn, hs, code in (
        ('2026年10月走访记录.xlsx', ['姓名', '身份证号', '走访日期', '包保人', '走访情况'], 'gov_special_visit'),
        ('重点人员台账.xlsx', ['姓名', '人员状态', '刑满释放日期', '帮教类型'], 'gov_criminal'),
        ('车辆台账.xlsx', ['姓名', '号牌号码', '车辆类型', '在册状态'], 'gov_key_vehicle'),
        ('治保委员会花名册.xlsx', ['姓名', '职务', '联系电话'], 'gov_security_committee'),
        ('道路交通安全隐患排查表.xlsx', ['序号', '位置', '整改状态'], 'gov_road_hazard'),
    ):
        assert route(fn, hs) == ('builtin_softtable', code), (fn, route(fn, hs))


def test_smart_route_governance_rules_do_not_misfire_on_generic_rosters():
    """#25 回归（141）：综治 8 条内置规则收紧——通用名单不得被泛化列指纹（职务/电话/地址）
    误判成内置表（否则挂载模块被预选成 governance，组织架构名单就不再走反哺）；
    专属列指纹 ≥3 个同时命中、或文件名命中时仍须正确识别。"""
    from utils.softtable_smart_route import detect_smart_route

    def route(filename, headers):
        parsed = {'workbook_group': filename.rsplit('.', 1)[0],
                  'sheets': [{'tables': [{'fields': [{'display_name': h} for h in headers]}]}]}
        r = detect_smart_route(filename, parsed)
        return r['kind'], r.get('target_code') or r.get('recommended_module')

    # 反例：通用名单 → 不得命中内置表
    assert route('社区党委及小组长备案表及居委人员名单.xlsx', ['姓名', '职务', '联系电话', '身份证号'])         == ('softtable_v2', 'organization')
    assert route('网格员花名册.xlsx', ['姓名', '职务', '居住地址', '联系电话']) == ('softtable_v2', 'dynatable')
    assert route('产业分红明细.xlsx', ['姓名', '金额', '工作措施']) == ('softtable_v2', 'dynatable')

    # 正例：专属列指纹（≥3）或文件名命中
    for fn, hs, code in (
        ('2026年10月走访记录.xlsx', ['姓名', '身份证号', '走访日期', '包保人', '走访情况'], 'gov_special_visit'),
        ('重点人员台账.xlsx', ['姓名', '人员状态', '刑满释放日期', '帮教类型'], 'gov_criminal'),
        ('车辆台账.xlsx', ['姓名', '号牌号码', '车辆类型', '在册状态'], 'gov_key_vehicle'),
        ('治保委员会花名册.xlsx', ['姓名', '职务', '联系电话'], 'gov_security_committee'),
        ('道路交通安全隐患排查表.xlsx', ['序号', '位置', '整改状态'], 'gov_road_hazard'),
    ):
        assert route(fn, hs) == ('builtin_softtable', code), (fn, route(fn, hs))


def test_petition_tables_untouched():
    from models import GovernancePetition, GovernancePetitionHandle
    assert GovernancePetition.__tablename__ == 'governance_petition'
    assert GovernancePetitionHandle.__tablename__ == 'governance_petition_handle'


# ───────────────────────── 3 + 6. 入口切换回归 ─────────────────────────

def test_eight_entries_redirect_to_v2_workbench_and_old_crud_gone(isolated_app_factory):
    from utils.governance_catalog import GOV_CATEGORY_CODES
    app = isolated_app_factory()
    client = _admin_client(app)
    for category, code in GOV_CATEGORY_CODES.items():
        # 旧入口：302 兼容重定向，目标精确指向 V2 工作台
        r = client.get(f'/governance/{category}/')
        assert r.status_code == 302, category
        assert f'/governance/roster/{code}/' in (r.headers.get('Location') or ''), category
        # 跟随跳转：200 且统一双视图工作台能力全在
        r2 = client.get(f'/governance/{category}/', follow_redirects=True)
        assert r2.status_code == 200, category
        html = r2.get_data(as_text=True)
        for marker in WORKBENCH_MARKERS:
            assert marker in html, f'{category} 工作台缺 {marker}'
        # 内置固定表：删除当前表按钮不得出现
        assert 'id="dtV2BtnDeleteTable"' not in html, f'{code} 为内置表，不得提供删表'
        # V2 读链路通
        r3 = client.get(f'/governance/roster/{code}/api/list')
        assert r3.status_code == 200 and r3.get_json()['success'] is True, code
        # 旧 CRUD 读写路径不复存在（不得 200 返回旧 JSON）
        for legacy_api in (f'/governance/{category}/api/list', f'/governance/{category}/api/save'):
            for method in ('GET', 'POST'):
                resp = client.open(legacy_api, method=method)
                assert resp.status_code in (404, 405), f'{method} {legacy_api} 旧路径仍可达 ({resp.status_code})'
    assert client.get('/governance/not-exist/').status_code == 404


def test_roster_page_self_heals_when_tables_missing(isolated_app_factory):
    """启动钩子未跑/失败时，直接访问 V2 工作台不得 404（懒自愈建 8 张固定表）。"""
    from utils.governance_catalog import GOV_SOFT_TABLE_CODES
    app = isolated_app_factory()
    from models import DynamicField, DynamicRecord, DynamicTable, db
    with app.app_context():
        tbls = DynamicTable.query.filter(DynamicTable.code.in_(list(GOV_SOFT_TABLE_CODES))).all()
        for t in tbls:
            DynamicRecord.query.filter_by(table_id=t.id).delete()
            DynamicField.query.filter_by(table_id=t.id).delete()
        DynamicTable.query.filter(DynamicTable.code.in_(list(GOV_SOFT_TABLE_CODES))).delete(
            synchronize_session=False)
        db.session.commit()
    client = _admin_client(app)
    r = client.get('/governance/roster/gov_criminal/')
    assert r.status_code == 200
    assert 'id="dtV2A4Wrap"' in r.get_data(as_text=True)


# ───────────────────────── 5a-5f. 幂等无损迁移 ─────────────────────────

def test_idempotent_legacy_migration_and_metrics(isolated_app_factory):
    from sqlalchemy import text
    from models import DynamicRecord, DynamicTable, db
    from utils.governance_catalog import GOV_CATEGORY_CODES
    from utils.metrics import MetricsEngine

    app = isolated_app_factory()
    with app.app_context():
        db.session.execute(text(LEGACY_DDL))
        db.session.execute(text("""
            INSERT INTO governance_ledgers
                (category, name, phone, record_date, payload, created_by)
            VALUES
                ('criminal', '张三', '13800001111', '2026-10-15',
                 '{"personnel_status": "刑满释放人员", "village_group": "一组"}', 'admin'),
                ('road-hazard', '北环路口', '', '2026-10-16',
                 '{"road_name": "北环路", "fix_status": "整改中"}', 'admin')
        """))
        db.session.commit()

    # 第一次迁移
    st1 = _run(app)
    assert st1['rows_migrated'] == 2, st1
    assert 'error' not in st1 and not st1.get('rows_failed'), st1
    # 第二次运行必须 100% 幂等，0 重复
    st2 = _run(app)
    assert st2['rows_migrated'] == 0, st2

    with app.app_context():
        crim_tbl = DynamicTable.query.filter_by(code=GOV_CATEGORY_CODES['criminal']).first()
        assert crim_tbl is not None
        crim_rows = DynamicRecord.query.filter_by(table_id=crim_tbl.id).all()
        assert len(crim_rows) == 1
        data = json.loads(crim_rows[0].data)
        assert data['name'] == '张三'
        assert data['personnel_status'] == '刑满释放人员'

        # 验证大屏指标引擎已完全切到 V2 软表取数
        with app.app_context():
            res_all = MetricsEngine.query('governance_ledgers')
            assert res_all['total'] == 2
            res_kp = MetricsEngine.query('governance_key_personnel')
            assert res_kp['total'] == 1
            buckets = {b['key']: b['count'] for b in
                       MetricsEngine.query('governance_ledgers', dims=['category'])['buckets']}
            assert buckets['criminal'] == 1 and buckets['road-hazard'] == 1
            # 日期筛选口径与旧实现一致：只认 record_date；非法格式结构化报错
            assert MetricsEngine.query(
                'governance_ledgers', filters={'from_date': '2026-10-16'})['total'] == 1
            with pytest.raises(ValueError):
                MetricsEngine.query('governance_ledgers', filters={'from_date': '2026/10/16'})
        buckets = {b['key']: b['count'] for b in MetricsEngine.query('governance_ledgers', dims=['category'])['buckets']}
        assert buckets['criminal'] == 1 and buckets['road-hazard'] == 1


def test_migration_preserves_encrypted_identity_and_all_fields(isolated_app_factory):
    """5b：旧密文行 + 未加密老行 → 软表密文 + HMAC 指纹；明文零落库；扩展键/审计列零丢失。"""
    from models import db
    from utils import softtable_engine as eng
    from utils.crypto import key_holder, sha256_hash

    _unlock_keyholder()
    app = isolated_app_factory()
    legacy_cipher = key_holder.encrypt(ID_CARD)
    _legacy_db_with(app, [
        {   # R12 后的行：原列 NULL，仅密文
            'category': 'special-visit', 'name': '密文人', 'id_card': None,
            'id_card_enc': legacy_cipher, 'id_card_hash': sha256_hash(ID_CARD),
            'phone': '13800002222', 'record_date': '2026-09-03',
            'payload': json.dumps({'village_group': '二组', 'responsible_person': '李四',
                                   'visit_situation': '正常走访', 'custom_ext': '扩展键值'}, ensure_ascii=False),
            'created_by': 'clerk01', 'created_at': '2026-09-03 10:20:30', 'updated_at': '2026-09-04 08:00:00',
        },
        {   # R12 前的老行：身份证仍是明文列
            'category': 'special-visit', 'name': '明文老行', 'id_card': PLAIN_ID_CARD,
            'id_card_enc': None, 'phone': '', 'record_date': '2026-08-01',
            'payload': json.dumps({'village_group': '三组'}, ensure_ascii=False), 'created_by': 'admin',
            'created_at': '2026-08-01 09:00:00', 'updated_at': '2026-08-01 09:00:00',
        },
    ])

    st = _run(app)
    assert st['rows_migrated'] == 2 and not st.get('rows_failed'), st
    rows = _records(app, 'special-visit')
    assert len(rows) == 2

    hmac_key = eng.derive_hmac_key(key_holder.get_key_bytes())
    by_name = {d['name']: (rid, d, dg) for rid, d, dg in rows}
    rid1, d1, dg1 = by_name['密文人']
    assert d1['_legacy_gov_id'] == 1
    assert d1['id_card'].startswith('gAAAA') and d1['id_card'] != ID_CARD
    assert key_holder.decrypt(d1['id_card']) == ID_CARD                 # 可解密还原
    assert dg1['id_card'] == eng.identity_digest(hmac_key, 'id_card', ID_CARD)  # 指纹可精确检索
    assert d1['phone'] == '13800002222' and d1['record_date'] == '2026-09-03'
    assert d1['village_group'] == '二组' and d1['responsible_person'] == '李四'
    assert d1['custom_ext'] == '扩展键值'                                # 目录外扩展键零丢失
    rid2, d2, dg2 = by_name['明文老行']
    assert key_holder.decrypt(d2['id_card']) == PLAIN_ID_CARD
    assert dg2['id_card'] == eng.identity_digest(hmac_key, 'id_card', PLAIN_ID_CARD)
    # 明文零落库（两份身份证在 data JSON 里都不得以明文出现）
    for _, d, _ in rows:
        assert ID_CARD not in json.dumps(d, ensure_ascii=False)
        assert PLAIN_ID_CARD not in json.dumps(d, ensure_ascii=False)

    # 审计列保留
    from models import DynamicRecord
    with app.app_context():
        rec = DynamicRecord.query.get(rid1)
        assert rec.created_by == 'clerk01'
        assert rec.created_at.strftime('%Y-%m-%d %H:%M') == '2026-09-03 10:20'


def test_migration_defers_whole_batch_while_key_locked(isolated_app_factory):
    """5c（#21 回归）：密钥锁定态绝不写空值/明文、不标记；解锁后补迁完整。"""
    from models import db
    from utils.crypto import key_holder

    _unlock_keyholder()
    app = isolated_app_factory()
    _legacy_db_with(app, [
        {'category': 'criminal', 'name': '锁定密文人', 'id_card': None,
         'id_card_enc': key_holder.encrypt(ID_CARD), 'phone': '', 'record_date': '2026-10-01',
         'payload': '{}', 'created_by': 'admin'},
        {'category': 'road-hazard', 'name': '无身份材料行', 'phone': '', 'record_date': '2026-10-02',
         'payload': '{}', 'created_by': 'admin'},
    ])

    # 模拟进程启动期：KeyHolder 类属性 is_initialized → False（不动全局密钥，避免波及同进程其他用例）
    class _Locked:
        @property
        def is_initialized(self):
            return False

    orig = type(key_holder).is_initialized
    type(key_holder).is_initialized = _Locked.is_initialized
    try:
        locked = _run(app)
    finally:
        type(key_holder).is_initialized = orig

    assert locked['rows_migrated'] == 0, locked
    assert locked.get('deferred_locked') == 2, locked          # 整批推迟（含无身份材料的行，保持批次确定性）
    for cat in ('criminal', 'road-hazard'):
        assert _records(app, cat) == [], f'{cat} 锁定态下不得落任何迁移行'

    # 解锁后（真实密钥就绪）再跑 → 完整补迁，身份证无损
    st = _run(app)
    assert st['rows_migrated'] == 2 and not st.get('rows_failed'), st
    crim = _records(app, 'criminal')
    assert len(crim) == 1
    assert key_holder.decrypt(crim[0][1]['id_card']) == ID_CARD
    assert len(_records(app, 'road-hazard')) == 1


def test_migration_skips_undecryptable_row_without_duplicates(isolated_app_factory):
    """5d：密文解不开 → 跳过待重试（旧表不动=零丢失），其余行照迁，重跑不重复。"""
    from models import db

    _unlock_keyholder()
    app = isolated_app_factory()
    _legacy_db_with(app, [
        {'category': 'criminal', 'name': '坏密文', 'id_card': None, 'id_card_enc': 'gAAAA-bad-ciphertext',
         'phone': '', 'record_date': '2026-10-01', 'payload': '{}', 'created_by': 'admin'},
        {'category': 'criminal', 'name': '好行', 'phone': '', 'record_date': '2026-10-02',
         'payload': '{}', 'created_by': 'admin'},
    ])
    st1 = _run(app)
    assert st1['rows_migrated'] == 1 and st1['rows_failed'] == 1, st1
    rows1 = _records(app, 'criminal')
    assert [d['name'] for _, d, _ in rows1] == ['好行']

    st2 = _run(app)
    assert st2['rows_migrated'] == 0 and st2['rows_failed'] == 1, st2
    assert [d['name'] for _, d, _ in _records(app, 'criminal')] == ['好行']   # 不重复

    # 旧表原样保留（未 DROP、未 DELETE）
    from sqlalchemy import text
    with app.app_context():
        n = db.session.execute(text('SELECT COUNT(*) FROM governance_ledgers')).scalar()
        assert n == 2


def test_rerun_neither_overwrites_edited_nor_resurrects_deleted(isolated_app_factory):
    """5e：迁完后编辑过的行不被旧数据覆盖、删除过的行不被重新迁回。"""
    from models import db

    app = isolated_app_factory()
    _legacy_db_with(app, [
        # 094 起「人员状态」为刑事人员表必填项：旧库更早的行可能缺，这里补齐以便走整行保存口径
        {'category': 'criminal', 'name': '待编辑', 'phone': '', 'record_date': '2026-10-01',
         'payload': '{"personnel_status": "刑满释放人员"}', 'created_by': 'admin'},
        {'category': 'criminal', 'name': '待删除', 'phone': '', 'record_date': '2026-10-02',
         'payload': '{"personnel_status": "刑满释放人员"}', 'created_by': 'admin'},
    ])
    assert _run(app)['rows_migrated'] == 2

    client = _admin_client(app)
    before = {d['name']: (rid, d['_legacy_gov_id']) for rid, d, _ in _records(app, 'criminal')}
    # 经真实编辑链路改名
    r = client.post('/governance/roster/gov_criminal/api/record/%d' % before['待编辑'][0],
                    json={'values': {'name': '已改名'}})
    assert r.status_code == 200 and r.get_json()['success'] is True, r.get_data(as_text=True)
    # 删除另一条
    r = client.post('/dynatable/api/t/gov_criminal/record/%d/delete' % before['待删除'][0])
    assert r.status_code == 200 and r.get_json()['success'] is True, r.get_data(as_text=True)

    # 重复执行（等价于「重启」后再触发）：不覆盖、不复活
    for _ in range(2):
        st = _run(app)
        assert st['rows_migrated'] == 0, st
    after = {d['name']: d for _, d, _ in _records(app, 'criminal')}
    assert set(after) == {'已改名'}, after                 # 改名未被旧值覆盖；已删行未复活
    # 哨兵仍在编辑后的行里（幂等标记不被编辑链路抹掉）
    assert after['已改名']['_legacy_gov_id'] == before['待编辑'][1]


def test_migrated_rows_work_through_real_engine_paths(isolated_app_factory):
    """5f：迁移行经真实链路——列表打码、编辑保存保留哨兵与密文、通用搜索精确命中。"""
    from models import db
    from utils.crypto import key_holder

    _unlock_keyholder()
    app = isolated_app_factory()
    _legacy_db_with(app, [
        {'category': 'special-visit', 'name': '链路人', 'id_card': None,
         'id_card_enc': key_holder.encrypt(ID_CARD), 'phone': '13800003333', 'record_date': '2026-10-05',
         'payload': json.dumps({'village_group': '五组'}, ensure_ascii=False), 'created_by': 'admin'},
    ])
    assert _run(app)['rows_migrated'] == 1
    client = _admin_client(app)

    # 列表：identity 打码展示，不得出现明文
    r = client.get('/governance/roster/gov_special_visit/api/list')
    body = r.get_json()
    assert body['success'] and body['total'] == 1
    row = body['data'][0]
    assert row['name'] == '链路人' and row['village_group'] == '五组'
    assert ID_CARD not in json.dumps(body, ensure_ascii=False)
    assert '*' in row['id_card']

    # 单行通用搜索：完整身份证按加密指纹精确命中；姓名关键字命中
    r = client.get('/governance/roster/gov_special_visit/api/list?q=%s' % ID_CARD)
    assert r.get_json()['total'] == 1
    assert client.get('/governance/roster/gov_special_visit/api/list?q=%s' % '链路甲').get_json()['total'] == 0

    # 编辑非身份字段：哨兵与密文原样保留，身份证不被掩码覆盖
    rid = row['id']
    r = client.post('/governance/roster/gov_special_visit/api/record/%d' % rid,
                    json={'values': {'name': '链路人改', 'village_group': '六组',
                                     'id_card': row['id_card']}})
    assert r.status_code and r.get_json()['success'] is True, r.get_data(as_text=True)
    _, d, dg = _records(app, 'special-visit')[0]
    assert d['name'] == '链路人改' and d['village_group'] == '六组'
    assert d['_legacy_gov_id'] == 1
    assert key_holder.decrypt(d['id_card']) == ID_CARD

    # 导出链路通
    r = client.get('/governance/roster/gov_special_visit/api/export')
    assert r.status_code == 200


def test_fresh_database_marks_nothing_and_stays_clean(isolated_app_factory):
    """全新库（从未有过旧表）：8 张表照常初始化，迁移步骤零副作用、不报错。"""
    from sqlalchemy import text
    from models import DynamicTable, db
    from utils.governance_catalog import GOV_CATEGORY_CODES
    from utils.governance_v2_migration import migrate_legacy_if_pending

    app = isolated_app_factory()
    st = _run(app)
    assert st['rows_migrated'] == 0 and 'error' not in st, st
    with app.app_context():
        codes = {t.code for t in DynamicTable.query.filter(
            DynamicTable.code.in_(list(GOV_CATEGORY_CODES.values()))).all()}
        assert codes == set(GOV_CATEGORY_CODES.values())
        assert db.session.execute(text(
            "SELECT name FROM sqlite_master WHERE name='governance_ledgers'")).first() is None
        # 懒触发点：无旧表 → 立即返回 None
        assert migrate_legacy_if_pending() is None
