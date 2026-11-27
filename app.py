import os, sys, json
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, session, send_file, send_from_directory, g)
from werkzeug.utils import secure_filename
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from config import Config
from models import db, User, Role, DictCategory, DictItem
from utils.permissions import (
    current_user_has_permission,
    required_permission_for_request,
    response_wants_json,
    user_has_special,
    user_has_permission,
)
from utils.masking import MaskFilter, register_jinja
from utils.csrf import generate_csrf_token, validate_csrf_token
from utils.dynatable_modules import label_for as module_label, is_builtin_roster_code
from utils.sync_status import current_sync_status
from utils.nav_spec import NAV_GROUPS, NAV_SETTINGS


# 侧栏菜单项级权限门（key = endpoint，value = 权限键）；未登记的项默认可见。
# 2026-11-16：「数据管理」组两项为数据维护类，恢复项级守卫——
# 无 special.import_batch 的角色看不到「导入批次」，无 dynatable.view 的看不到「台账建表」，
# 避免入口可见但点进去 403（其余业务入口仍沿用「页面级 403 + 入口常显」口径）。
_NAV_ITEM_GUARDS = {
    'resident_enhance.import_batches_page': 'special.import_batch',
    'dynatable.index': 'dynatable.view',
    # 130/131：Word 表单中心复用 notice 模块位（见 utils/permissions.BLUEPRINT_MODULES）。
    # 131 起入口降级为民政对象页「报表审批」胶囊（胶囊侧用 perm 键过滤），本门保留：
    # 若将来入口回到 nav_spec 侧栏，权限过滤即时生效，不会露出点进去 403 的死入口。
    'word_form.index': 'notice.view',
}


def _visible_nav_groups():
    """按 `_NAV_ITEM_GUARDS` 过滤侧栏项；组内 0 项则整组丢弃，返回新 dict 列表。"""
    groups = []
    for group in NAV_GROUPS:
        items = []
        for item in group['items']:
            guard = _NAV_ITEM_GUARDS.get(item['endpoint'])
            if guard is None or current_user_has_permission(guard):
                items.append(item)
        if items:
            groups.append({'key': group['key'], 'label': group['label'], 'items': items})
    return groups


# GLB-49：缺表/缺列错误 → 对应迁移脚本（按错误文本中的表名/列名关键字匹配）。
# 顺序即优先级；未命中时回落通用迁移指引，不再一律误导到 m013。
_MIGRATION_HINTS = (
    ('import_batch', 'scripts/migrations/m013_import_batch_mode.sql'),
    ('import_profiles', 'scripts/migrations/m014_import_profiles.sql'),
    ('certificate', 'scripts/migrations/m015_certificate_full.py'),
    ('notice', 'scripts/migrations/m016_notice.sql'),
    ('governance', 'scripts/migrations/m017_governance.sql'),
    ('shop', 'scripts/migrations/m018_shop.sql'),
    ('device_registry', 'scripts/migrations/m019_device_registry.sql'),
    ('medical_insurance', 'scripts/migrations/m021_medical_insurance_type.py'),
    ('last_active', 'scripts/migrations/m022_user_last_active.py'),
    ('resident_sync_log', 'scripts/migrations/m010_resident_sync_log.sql'),
    ('safety_checks', 'scripts/migrations/m028_emergency_safety_check.sql'),
    ('safety_check_hazards', 'scripts/migrations/m028_emergency_safety_check.sql'),
    ('safety_nodes', 'scripts/migrations/m012_safety_nodes.sql'),
    ('ddl_audit', 'scripts/migrations/m011_ddl_audit.sql'),
    # 080 返工 R-080-03：党员拆维度 4 列缺列错误 → m025（列错误文本含 party_members.member_identity）
    ('party_members', 'scripts/migrations/m025_party_dimensions.py'),
    ('member_identity', 'scripts/migrations/m025_party_dimensions.py'),
    # 084 就业服务：两张新表缺表错误 → m037（存量库须显式执行）
    ('welfare_employment', 'scripts/migrations/m037_employment_service.py'),
    ('welfare_flex_subsidy', 'scripts/migrations/m037_employment_service.py'),
    # 086 信访维稳：两张新表缺表错误 → m033（道交三类为配置驱动，零迁移）
    ('governance_petition', 'scripts/migrations/m033_governance_petition.py'),
    ('governance_petition_handle', 'scripts/migrations/m033_governance_petition.py'),
    # 092 模板附件区：通用附件表缺表错误 → m036（存量库须显式执行）
    ('module_attachments', 'scripts/migrations/m036_module_attachment.py'),
    # 083A 党务活动：两张新表缺表错误 → m032（存量库须显式执行）
    ('party_activity', 'scripts/migrations/m032_party_activity.py'),
    # 083B 党员发展：两张新表缺表错误 → m041（存量库须显式执行）
    ('party_development', 'scripts/migrations/m041_party_development.py'),
    # 090B 公共事务·选举：三张新表缺表错误 → m035（存量库须显式执行）
    ('election_round', 'scripts/migrations/m035_election.py'),
    ('election_voter', 'scripts/migrations/m035_election.py'),
    ('election_result', 'scripts/migrations/m035_election.py'),
    # 090A 民生诉求：两张新表缺表错误 → m034（存量库须显式执行）
    ('demand', 'scripts/migrations/m034_livelihood_demand.py'),
    ('demand_handle', 'scripts/migrations/m034_livelihood_demand.py'),
    # 089 文明实践站：公约固定表缺表错误 → m040（存量库须显式执行）
    ('civ_convention', 'scripts/migrations/m040_civilization.py'),
    # ADR-029：存量表「缺列」错误文本（SQLite: no such column: <table>.<col>）
    # 082 B 必补字段 / 093 退役补充口径 / 080 党员拆维度 / 023 低保状态 / 015 证明模板 / 022 在线状态
    ('key_group_category', 'scripts/migrations/m026_b_required_fields.py'),
    ('benefit_cert_status', 'scripts/migrations/m026_b_required_fields.py'),
    ('retire_category', 'scripts/migrations/m027_special_veteran_fields.py'),
    ('member_mobility', 'scripts/migrations/m025_party_dimensions.py'),
    ('employment_status', 'scripts/migrations/m025_party_dimensions.py'),
    ('party_status', 'scripts/migrations/m025_party_dimensions.py'),
    ('insurance_type', 'scripts/migrations/m021_medical_insurance_type.py'),
    ('ext_data', 'scripts/migrations/m021_medical_insurance_type.py'),
    ('support_status', 'scripts/migrations/m023_dibao_support_status.py'),
    ('field_schema', 'scripts/migrations/m015_certificate_full.py'),
    ('page_settings', 'scripts/migrations/m015_certificate_full.py'),
)


def _migration_script_for_error(message) -> str | None:
    """从 `no such column/table` 错误文本定位最可能的迁移脚本；未命中返回 None。"""
    low = str(message or '').lower()
    for key, script in _MIGRATION_HINTS:
        if key in low:
            return script
    return None


def _ensure_party_dimensions() -> dict:
    """080 返工 R-080-03/R-080-06/M2/M3：party_members 四维列自愈 + 未迁移行保守回填。

    - ensure_columns 幂等补 4 列；列就绪后按 m025 同一条 UPDATE 回填未迁移行；
    - M2：整段包 try/except——ensure_columns/回填任一异常仅 WARNING + rollback，
      不得抛穿 create_app（ALTER 撞锁/duplicate column 曾抛穿启动）；
    - M3：回填前探测「部分维度有值但 party_status 空」的风险行，有则整段跳过并告警，
      严禁用宽 WHERE 覆盖/清空已有维度（N-080b-02 实测曾 3/3 行被改写）；
    - 回填 SQL 单一真源在 `scripts/migrations/m025_party_dimensions.py`，importlib 复用；
    - 幂等：已迁移库二跑 0 行；stats['backfilled'] 记实际更新 rowcount。
    """
    stats = {'backfilled': 0}
    try:
        from utils.schema_ensure import ensure_columns
        ensure_columns('party_members', {
            'member_identity': 'VARCHAR(20)',
            'member_mobility': 'VARCHAR(20)',
            'employment_status': 'VARCHAR(20)',
            'party_status': 'VARCHAR(20)',   # 刻意无 DEFAULT：NULL 作为「未迁移」标记（m025 口径）
        })
        unmigrated = db.session.execute(text(
            'SELECT COUNT(*) FROM party_members WHERE party_status IS NULL')).scalar() or 0
        if unmigrated:
            # M3：m025 的 BACKFILL_SQL 隐含「party_status IS NULL ⇒ 四维全空」前提；
            # 若存在「部分维度有值但状态空」的行，直接套宽 WHERE 会覆盖/清空已有维度
            # （N-080b-02 实测 3/3 行被改写）。故先探测风险行：有则整段跳过回填并告警，
            # 留待显式执行 m025 并人工核对；无风险才按 m025 同源保守回填。
            risky = db.session.execute(text(
                'SELECT COUNT(*) FROM party_members WHERE party_status IS NULL '
                'AND (member_identity IS NOT NULL OR member_mobility IS NOT NULL '
                'OR employment_status IS NOT NULL)')).scalar() or 0
            if risky:
                stats['skipped_risky'] = risky
                logging.getLogger('shucun').warning(
                    'party_members 存在 %d 行「部分维度有值但状态空」，跳过启动回填以免覆盖'
                    '已有维度；请显式执行 scripts/migrations/m025_party_dimensions.py 并人工核对',
                    risky)
            else:
                import importlib.util
                _m025 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     'scripts', 'migrations', 'm025_party_dimensions.py')
                spec = importlib.util.spec_from_file_location('m025_party_dimensions', _m025)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                result = db.session.execute(text(mod.BACKFILL_SQL))
                stats['backfilled'] = max(int(result.rowcount or 0), 0)
                db.session.commit()
            logging.getLogger('shucun').info(
                'party_members 启动自愈：party_status IS NULL 残留 %d 行（自愈前计数）', unmigrated)
    except Exception as e:   # noqa: BLE001 —— 自愈失败不阻断启动（列/数据面仍可用）
        try:
            db.session.rollback()
        except Exception:
            pass
        logging.getLogger('shucun').warning(
            'party_members 拆维度自愈跳过（可手工执行 m025）： %s: %s', type(e).__name__, e)
    return stats


def _get_logo_url():
    logo = DictItem.query.join(DictCategory).filter(
        DictCategory.name == '系统设置',
        DictItem.label == 'logo_url'
    ).first()
    return logo.value if logo else None


def _get_system_title():
    title = DictItem.query.join(DictCategory).filter(
        DictCategory.name == '系统设置',
        DictItem.label == 'system_title'
    ).first()
    return title.value if title else '村级村务综合管理系统'


def _get_village_name():
    """075 RC-02（Q1 裁定）：村名设置项；未配置返回空串（页面回退不带村名口径）。"""
    village = DictItem.query.join(DictCategory).filter(
        DictCategory.name == '系统设置',
        DictItem.label == 'village_name'
    ).first()
    return (village.value or '') if village else ''


def _is_local_request():
    remote = request.remote_addr or ''
    return remote in ('127.0.0.1', '::1', 'localhost') or remote.startswith('127.')


# 启动期只读体检的预期结构（R-037-02：单表元组 → 表→列清单，可扩展）。
# import_batch 四列来自 m013（GLB-03-③）；import_profiles 来自 m014（只查存在性/行数）。
# ADR-029：补齐 082/093/080 等「存量表加列」迁移，旧项目库启动即告警，不再等到 /dashboard/ 500。
_STARTUP_TABLE_COLUMNS = {
    'import_batch': ('mode', 'profile_key', 'policy', 'updated_rows'),
    'import_profiles': (),
    'party_members': ('member_identity', 'member_mobility',
                      'employment_status', 'party_status'),
    'medical_insurance_payments': ('insurance_type', 'ext_data', 'benefit_cert_status'),
    'veteran': ('key_group_category', 'retire_category'),
    'dibao': ('support_status',),
    'certificate_templates': ('field_schema', 'page_settings', 'is_active', 'version'),
    'users': ('last_active',),
}

# 表级兜底脚本（列名未命中 _MIGRATION_HINTS 时使用；列级映射以 _MIGRATION_HINTS 为准）
_STARTUP_TABLE_HINT = {
    'import_batch': 'scripts/migrations/m013_import_batch_mode.sql',
    'import_profiles': 'scripts/migrations/m014_import_profiles.sql',
    'party_members': 'scripts/migrations/m025_party_dimensions.py',
    'medical_insurance_payments': 'scripts/migrations/m021_medical_insurance_type.py',
    'veteran': 'scripts/migrations/m026_b_required_fields.py',
    'dibao': 'scripts/migrations/m023_dibao_support_status.py',
    'certificate_templates': 'scripts/migrations/m015_certificate_full.py',
    'users': 'scripts/migrations/m022_user_last_active.py',
}


def _script_for_column(column):
    """按列名反查迁移脚本（列级提示优先；未命中返回 None）。"""
    low = str(column or '').lower()
    for key, script in _MIGRATION_HINTS:
        if key in low:
            return script
    return None


def _warn_missing_import_tables():
    """GLB-03-③ + R-037-02/03：启动期只读体检（PRAGMA table_info），只 warning 不 ALTER。

    - import_batch：缺列 → warning（列名 + m013）；表不存在（建表前）静默跳过。
    - import_profiles：表缺失或行数为 0 → warning 提示执行 m014（R-037-03：
      create_all 会建空表，内置 standard_v1 种子必须执行 m014 才有，否则
      「方案下拉为空」无任何告警）。
    - ADR-029 扩展：party_members / medical_insurance_payments / veteran / dibao /
      certificate_templates / users 的加列迁移缺列同样告警（按列名给脚本）。
    """
    log = logging.getLogger('shucun')
    try:
        for table, expect_cols in _STARTUP_TABLE_COLUMNS.items():
            rows = db.session.execute(text(f'PRAGMA table_info({table})')).fetchall()
            have = {r[1] for r in rows}
            if not have:
                if table == 'import_profiles':
                    log.warning('启动体检：%s 表不存在，请执行 '
                                'scripts/migrations/m014_import_profiles.sql', table)
                continue                          # import_batch：空库/建表前静默跳过
            missing = [c for c in expect_cols if c not in have]
            for column in missing:
                log.warning('启动体检：%s 缺列 %s，请执行 %s',
                            table, column,
                            _script_for_column(column) or _STARTUP_TABLE_HINT.get(table, ''))
            if table == 'import_profiles':
                count = db.session.execute(
                    text('SELECT COUNT(*) FROM import_profiles')).scalar()
                if not count:
                    log.warning('启动体检：import_profiles 行数为 0，内置方案未初始化，'
                                '请执行 scripts/migrations/m014_import_profiles.sql')
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        log.warning('启动体检 schema 检查跳过（不影响启动）：%s: %s', type(e).__name__, e)


def _current_user_can_view_sensitive():
    """当前登录用户是否有明文查看权限（view_sensitive 位；admin 天然全通）。

    P3-8 整改：优先复用 g.current_user（load_current_user 装载），避免每请求重复查 User。
    """
    user = g.get('current_user')
    if user is None:
        user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    return bool(user and (user.role_id == 1 or user_has_special(user, 'view_sensitive')))


def _build_shell_banner():
    """壳层提示条（071 改：7 天内到期口径，对齐样图 §1.3）。

    只统计 remind_time 落在 [now, now+7d] 且未办结（非 done/dismissed）的待办；
    无数据 / 未登录 / 表未建 → None（整条不渲染）。
    文案：`N 条待办将在 7 天内到期` + 有数据时追最近一条 ` · <title> <MM-DD> 截止`。
    """
    if g.get('current_user') is None:
        return None
    try:
        from models.todo import TodoItem
        from datetime import datetime, timedelta
        now = datetime.now()
        window_end = now + timedelta(days=7)
        items = (
            TodoItem.query
            .filter(
                TodoItem.status.notin_(['done', 'dismissed']),
                TodoItem.remind_time.isnot(None),
                TodoItem.remind_time >= now,
                TodoItem.remind_time <= window_end,
            )
            .order_by(TodoItem.remind_time.asc())
            .all()
        )
        if not items:
            return None
        text = '%d 条待办将在 7 天内到期' % len(items)
        first = items[0]
        if first.title and first.remind_time:
            text += ' · %s %s 截止' % (first.title, first.remind_time.strftime('%m-%d'))
        return {
            'count': len(items),
            'text': text,
            'href': url_for('todo.list'),
            'link_text': '查看待办',
        }
    except Exception:
        return None


def create_app(skip_startup_hooks=False):
    """建应用。skip_startup_hooks=True 供 run_async 的 worker 线程使用——
    那只是「借一个 app context」，不是进程启动，不得触发启动期清理（R-035-03）。"""
    app = Flask(__name__)
    app.config.from_object(Config)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)

    # T03 B1/B5：SQLite 连接 PRAGMA 注入（WAL / busy_timeout=30s / foreign_keys）
    from sqlalchemy import event
    with app.app_context():
        @event.listens_for(db.engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _connection_record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    from routes.auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.resident import resident_bp
    from routes.medical_insurance import medical_bp
    from routes.dibao import dibao_bp
    from routes.disabled import disabled_bp
    from routes.care import care_bp
    from routes.organization import org_bp
    from routes.party import party_bp
    from routes.mediation import mediation_bp
    from routes.subsidy import subsidy_bp
    from routes.welfare_job import welfare_bp
    from routes.todo import todo_bp
    from routes.backup import backup_bp
    from routes.certificate import cert_bp as certificate_bp
    from routes.notice import notice_bp
    from routes.word_form import word_form_bp
    from routes.settings import settings_bp
    from routes.user_mgmt import user_mgmt_bp
    from routes.role_mgmt import role_mgmt_bp
    from routes.dict_mgmt import dict_bp as dict_mgmt_bp
    from routes.log import log_bp
    from routes.death_report import death_bp
    from routes.report import report_bp
    from routes.metrics import metrics_bp
    from routes.veteran import veteran_bp
    from routes.youshu import youshu_bp
    from routes.license_mgmt import license_bp
    from routes.resident_enhance import resident_enhance_bp
    from routes.visit import visit_bp
    from routes.warn import warn_bp
    from routes.reconcile import reconcile_bp
    from routes.db_admin import db_admin_bp
    from routes.safety import safety_bp
    from routes import safety_emergency  # 087：应急+综合检查路由挂同一 safety_bp（register 前导入生效）
    from routes.governance import governance_bp
    from routes.shop import shop_bp
    from routes.pension import pension_bp
    from routes.election import election_bp
    from routes.demand import demand_bp
    from routes.sanitation import sanitation_bp  # 090C：环卫工作（三软表 + 两指标 + 092 照片附件）
    from routes.civilization import civilization_bp
    from routes.screen import screen_admin_bp, screen_bp
    from routes.device_handshake import device_handshake_bp
    from routes.device_mgmt import device_mgmt_bp
    from routes.dynatable import dynatable_bp
    from routes.presence import presence_bp
    from utils.device_auth import guard as device_guard, apply_cookie as device_apply_cookie

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(dashboard_bp, url_prefix='/dashboard')
    app.register_blueprint(resident_bp, url_prefix='/resident')
    app.register_blueprint(medical_bp, url_prefix='/medical-insurance')
    app.register_blueprint(dibao_bp, url_prefix='/dibao')
    app.register_blueprint(disabled_bp, url_prefix='/disabled')
    app.register_blueprint(care_bp, url_prefix='/care')
    app.register_blueprint(org_bp, url_prefix='/organization')
    app.register_blueprint(party_bp, url_prefix='/party')
    app.register_blueprint(mediation_bp, url_prefix='/mediation')
    app.register_blueprint(subsidy_bp, url_prefix='/subsidy')
    app.register_blueprint(welfare_bp, url_prefix='/welfare')
    app.register_blueprint(todo_bp, url_prefix='/todo')
    app.register_blueprint(backup_bp, url_prefix='/backup')
    app.register_blueprint(certificate_bp, url_prefix='/certificate')
    app.register_blueprint(notice_bp, url_prefix='/notice')
    app.register_blueprint(word_form_bp, url_prefix='/word-form')
    app.register_blueprint(settings_bp, url_prefix='/settings')
    app.register_blueprint(user_mgmt_bp, url_prefix='/user-mgmt')
    app.register_blueprint(role_mgmt_bp, url_prefix='/role-mgmt')
    app.register_blueprint(dict_mgmt_bp, url_prefix='/dict')
    app.register_blueprint(log_bp, url_prefix='/log')
    app.register_blueprint(death_bp, url_prefix='/death-report')
    app.register_blueprint(report_bp, url_prefix='/report')
    # 081：统一统计取数入口 GET /api/stats/query（指标注册表见 utils/metrics.py）
    app.register_blueprint(metrics_bp, url_prefix='/api/stats')
    app.register_blueprint(veteran_bp, url_prefix='/veteran')
    app.register_blueprint(youshu_bp, url_prefix='/youshu')
    app.register_blueprint(license_bp, url_prefix='/license')
    app.register_blueprint(resident_enhance_bp, url_prefix='/import')
    app.register_blueprint(visit_bp, url_prefix='/visit')
    app.register_blueprint(warn_bp, url_prefix='/warn')
    app.register_blueprint(reconcile_bp, url_prefix='/reconcile')
    app.register_blueprint(db_admin_bp, url_prefix='/db-admin')
    # 030：安全工作模块（档案目录树+上传下载+搜索）；uploads/safety 不加入
    # UPLOAD_SUBDIR_PERM_MAP（serve_upload 默认 403），文件访问一律走节点 id 端点。
    app.register_blueprint(safety_bp, url_prefix='/safety')
    # 综治管理（4 台账，配置驱动；权限复用 mediation 位）+ 店铺信息（居民管理组）
    app.register_blueprint(governance_bp, url_prefix='/governance')
    app.register_blueprint(shop_bp, url_prefix='/shop')
    # 088：养老保险（pension 门面页 + roster_facade 软表参保名册 + 两指标代理）
    app.register_blueprint(pension_bp, url_prefix='/pension')
    # 090B：公共事务·选举（届次状态机 + 选民名册 + 结果；m035 三固定表）
    app.register_blueprint(election_bp, url_prefix='/election')
    # 090A：民生诉求（demand 门面 + 两跳状态机 + 处理记录子表 + 三指标）
    app.register_blueprint(demand_bp, url_prefix='/demand')
    # 090C：环卫工作（sanitation 门面页 + 三软表挂载 + 两指标代理 + 092 照片附件区）
    app.register_blueprint(sanitation_bp, url_prefix='/sanitation')
    # 089：文明实践站（civilization 门面页 + 公约固定表 + roster_facade 软表活动记录 + 两指标代理）
    app.register_blueprint(civilization_bp, url_prefix='/civilization')
    # 048：大屏管理（screen_admin=/screen-admin 配置端，screen=/screen 公开 token 投屏）
    app.register_blueprint(screen_admin_bp, url_prefix='/screen-admin')
    # 057b：在线状态（心跳 + 在线人数），全体登录角色可见
    app.register_blueprint(presence_bp, url_prefix='/api')
    app.register_blueprint(screen_bp, url_prefix='/screen')
    # 049：局域网设备准入（device_handshake=/device 客户端握手，device_mgmt=/device-mgmt 管理后台）
    app.register_blueprint(device_handshake_bp, url_prefix='/device')
    app.register_blueprint(device_mgmt_bp, url_prefix='/device-mgmt')
    # 078：软表引擎（动态台账）——上传建表/数据列表/行内编辑/结构修改
    app.register_blueprint(dynatable_bp, url_prefix='/dynatable')

    @app.route('/')
    def root():
        return redirect(url_for('auth.login'))

    # T07：uploads 子目录权限映射（B8/B10 闭环）
    # serve_upload 从 PUBLIC_ENDPOINTS 移除，端点内自校验
    UPLOAD_SUBDIR_PERM_MAP = {
        'mediation': 'mediation.view',
        'visit': 'visit.view',
        'logos': '__admin__',
    }
    _SVG_PLACEHOLDER = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
        b'<rect width="1" height="1" fill="#f0f0f0"/></svg>'
    )

    @app.route('/uploads/<path:filename>')
    def serve_upload(filename):
        # 1. 登录校验
        if 'user_id' not in session:
            return _upload_forbidden()
        user = g.get('current_user')
        if not user or not user.is_active:
            session.clear()
            return _upload_forbidden()

        # 2. 子目录权限映射
        subdir = filename.split('/')[0] if '/' in filename else ''
        perm_key = UPLOAD_SUBDIR_PERM_MAP.get(subdir)
        if perm_key is None:
            # 未映射的子目录（含 export/misc/根）一律 403
            return _upload_forbidden()
        if perm_key == '__admin__':
            if user.role_id != 1:
                return _upload_forbidden()
        else:
            if not user_has_permission(user, perm_key):
                return _upload_forbidden()

        return send_from_directory(Config.UPLOAD_FOLDER, filename)

    def _upload_forbidden():
        """uploads 403 响应：<img> 场景返回 SVG 占位符防破图，其他返回 403 页。"""
        accept = request.headers.get('Accept', '')
        if 'image/' in accept:
            from flask import Response
            return Response(_SVG_PLACEHOLDER, status=200,
                            mimetype='image/svg+xml')
        if response_wants_json():
            return jsonify({'success': False, 'msg': '权限不足'}), 403
        return render_template('errors/403.html'), 403

    @app.before_request
    def load_current_user():
        """P3-8 整改：每请求仅查一次 User 存入 g.current_user，供权限/脱敏/模板复用。"""
        g.current_user = User.query.get(session.get('user_id')) if session.get('user_id') else None

    @app.before_request
    def csrf_protect():
        """P2-1 整改收尾：全局 CSRF token 校验（仅状态变更方法、非 JSON、非回环）。

        - JSON 接口（Content-Type: application/json）由浏览器 CORS 预检保护（服务端
          无 Access-Control-Allow-Origin），跨站无法直发，豁免。
        - 本机回环通道（launcher/unlock/recovery 等）仅 127.0.0.1 可达，豁免。
        - 表单提交读 form['csrf_token']，AJAX 读 X-CSRF-Token 头（base.html 自动注入）。
        - SHUCUN_DISABLE_CSRF=1 可临时关闭（运维逃生通道，默认开启）。
        """
        if os.environ.get('SHUCUN_DISABLE_CSRF') == '1':
            return None
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return None
        if request.is_json:
            return None
        if _is_local_request():
            return None
        submitted = (request.headers.get('X-CSRF-Token')
                     or request.form.get('csrf_token')
                     or request.form.get('_csrf_token'))
        if not validate_csrf_token(submitted or ''):
            if response_wants_json():
                return jsonify({'success': False, 'msg': 'CSRF 校验失败，请刷新页面后重试'}), 400
            return render_template('errors/403.html'), 403
        return None

    @app.before_request
    def enforce_lan_license():
        if os.environ.get('SHUCUN_LAN_AUTHORIZED') == '1':
            return None
        if _is_local_request():
            return None
        if response_wants_json():
            return jsonify({'success': False, 'msg': '未授权局域网访问，请先在本机导入授权文件'}), 403
        return '未授权局域网访问，请先在服务端电脑导入授权文件。', 403

    @app.before_request
    def enforce_device_admission():
        """049：局域网设备准入 —— 白名单设备才可访问（单机版不启用）。

        位置：局域网授权之后、维护模式之前。设备级先于用户级鉴权。
        """
        return device_guard()

    @app.after_request
    def issue_device_cookie(response):
        """049：客户端首次带 ?device_token= 打开页面时，把会话 token 落成 cookie。"""
        return device_apply_cookie(response)

    # P1-2 补强：must_change_password 用户改密前全局锁死业务端点
    # 既往仅登录瞬间跳转改密页一次，用户可直访业务 URL 绕过；此处改为全局拦截
    _PASSWORD_CHANGE_EXEMPT = {'auth', 'static'}
    _PASSWORD_CHANGE_EXEMPT_ENDPOINTS = {'serve_upload'}

    @app.before_request
    def enforce_password_change():
        # 测试隔离开关（默认关闭，仅自动化/CI 设置；仿 SHUCUN_DISABLE_CSRF / SHUCUN_DISABLE_PERIODIC 先例）
        # 2026-09-07 夹具基线修复：业务测试夹具未处理 P1-2 强制改密拦截导致 302 全挂，conftest 对非专项测试置 1
        if os.environ.get('SHUCUN_DISABLE_PASSWORD_CHANGE') == '1':
            return None
        user = g.get('current_user')
        if not user or not user.is_active:
            return None
        if not getattr(user, 'must_change_password', False):
            return None
        endpoint = request.endpoint or ''
        blueprint = endpoint.rsplit('.', 1)[0] if '.' in endpoint else endpoint
        if blueprint in _PASSWORD_CHANGE_EXEMPT or endpoint in _PASSWORD_CHANGE_EXEMPT_ENDPOINTS:
            return None
        if response_wants_json():
            return jsonify({'success': False,
                            'msg': '首次登录须先修改密码', 'must_change_password': True}), 403
        return redirect(url_for('auth.change_password'))

    @app.before_request
    def enforce_role_permissions():
        perm_key = required_permission_for_request()
        if not perm_key:
            return None

        if 'user_id' not in session:
            if response_wants_json():
                return jsonify({'success': False, 'msg': '未登录'}), 401
            return redirect(url_for('auth.login'))

        user = g.get('current_user')
        if not user or not user.is_active:
            session.clear()
            if response_wants_json():
                return jsonify({'success': False, 'msg': '未登录'}), 401
            return redirect(url_for('auth.login'))

        if perm_key == '__admin__':
            if user.role_id != 1:
                if response_wants_json():
                    return jsonify({'success': False, 'msg': '权限不足'}), 403
                return render_template('errors/403.html'), 403
            return None

        if not current_user_has_permission(perm_key):
            if response_wants_json():
                return jsonify({'success': False, 'msg': '权限不足'}), 403
            return render_template('errors/403.html'), 403

        return None

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return render_template('errors/500.html'), 500

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    # 035 R-034-04：全局 413 统一 JSON 友好文案。MAX_CONTENT_LENGTH 与内核 MAX_IMPORT_SIZE
    # 同为 50MiB，multipart 信封开销使请求体先超限——不注册时用户拿到 413 白页；
    # 注册后返回与内核 start_batch 一致的文案，「≤50MB」对外口径不变。
    from werkzeug.exceptions import RequestEntityTooLarge

    @app.errorhandler(RequestEntityTooLarge)
    def request_entity_too_large(e):
        return jsonify({'success': False, 'msg': '文件超过 50MB 上限，请分批导入'}), 413

    @app.errorhandler(OperationalError)
    def database_busy(e):
        """B5：SQLITE_BUSY 并发写锁 → 409 友好提示（非 500 崩溃）。"""
        db.session.rollback()
        msg = str(e)
        is_busy = ('database is locked' in msg.lower()
                   or 'database table is locked' in msg.lower())
        if is_busy:
            if response_wants_json():
                return jsonify({'success': False, 'msg': '数据库繁忙，请稍后重试（并发写锁）'}), 409
            return render_template('errors/500.html'), 409
        # GLB-03-② / GLB-49：缺列/缺表不伪装成通用 500，且按缺失表/列映射到对应迁移脚本。
        low = msg.lower()
        if 'no such column' in low or 'no such table' in low:
            script = _migration_script_for_error(msg)
            if script:
                detail = ('数据库结构与程序版本不一致（缺少表或列）。请执行迁移脚本 '
                          f'{script} 后重启；原始错误：' + msg)
            else:
                script = 'scripts/migrations/'
                detail = ('数据库结构与程序版本不一致（缺少表或列）。请对照 '
                          'scripts/migrations/ 执行对应迁移脚本后重启；原始错误：' + msg)
            if response_wants_json():
                return jsonify({'success': False, 'msg': detail,
                                'migration': script}), 500
            return render_template('errors/500.html', detail=detail), 500
        return render_template('errors/500.html'), 500

    register_jinja(app)
    # 065：has_perm 同时注册为 Jinja 全局。原因：`{% from '...' import macro %}` 默认不带
    # context，context processor 注入的变量在宏内部不可见 → 宏里的权限过滤会 Undefined。
    # 注册为 globals 后，模板与宏都可用；context processor 的同名键仍优先，行为不变。
    app.jinja_env.globals['has_perm'] = current_user_has_permission
    # 2026-11-16：软表「菜单归属」键 → 中文标签（单一真源 utils/dynatable_modules.py）。
    # 共享名册模板（roster_page / bootstrap / upload）由 shop / pension / civilization /
    # sanitation 多处渲染，用全局函数一处接入即可，避免每个渲染点传一遍。
    app.jinja_env.globals['module_label'] = module_label
    # C-1：内置预置台账 code 判定（隐藏内置表删除按钮，防止误删 m031 指标联动真源）。
    app.jinja_env.globals['is_builtin_roster_code'] = is_builtin_roster_code
    # 第一批软表闭环：按模块归属自动追加页内胶囊（请求级缓存，无动态表时返空）。
    # 065 同款原因：module_tabs 是 {% from %} 导入宏，context processor 变量宏内不可见，
    # 必须注册为 Jinja 全局（has_perm / module_label 同款姿势）。
    app.jinja_env.globals['dynamic_module_tabs'] = (
        __import__('utils.roster_facade', fromlist=['list_dynamic_tabs']).list_dynamic_tabs)

    @app.after_request
    def mask_json_response(response):
        """T06 JSON 层统一脱敏：无 view_sensitive 权限时递归脱敏敏感字段。

        g.skip_masking 豁免（明文查看专用端点置位后自行返回明文 + 记日志）。
        """
        if response.mimetype == 'application/json' and not g.get('skip_masking'):
            try:
                data = response.get_json()
            except Exception:
                return response
            if data is not None and not _current_user_can_view_sensitive():
                masked = MaskFilter().apply(data, can_view_sensitive=False)
                response.set_data(json.dumps(masked, ensure_ascii=False))
        return response

    @app.after_request
    def apply_security_headers(response):
        """P0-3 加固：全局 HTTP 安全响应头（防点击劫持、MIME 嗅探与 Referer 泄露）。"""
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "frame-ancestors 'none';"
        )
        return response

    @app.context_processor
    def inject_globals():
        return {
            'current_user': g.get('current_user'),
            'has_perm': current_user_has_permission,
            # 侧栏单一真源（v4 六组 14 入口 + 底部设置；权限过滤在 _visible_nav_groups）
            'nav_groups': _visible_nav_groups(),
            'nav_settings': NAV_SETTINGS,
            'logo_url': _get_logo_url(),
            'system_title': _get_system_title(),
            # 075 RC-02：村名（Q1 裁定），页头副标题统一口径用
            'village_name': _get_village_name(),
            'now': datetime.now,
            # P2-1 整改：CSRF token 注入所有模板（base.html meta + 各表单 hidden input）
            'csrf_token': generate_csrf_token,
            # 057a 壳层：在线人数（057b 接入 /api/online-count 后由前端覆盖该初值）+ 提示条
            'online_count': 0,
            # 064：顶栏同步状态（单机「本机数据」；LAN 由 provider 升级，模板零改动）
            'sync_status': current_sync_status(),
            'shell_banner': _build_shell_banner(),
        }

    with app.app_context():
        db.create_all()
        # P1-2 整改：users 表 must_change_password 列幂等补齐（存量库 create_all 不追加列）
        from sqlalchemy import text as _text
        _users_cols = {row[1] for row in db.session.execute(_text("PRAGMA table_info(users)")).fetchall()}
        if 'must_change_password' not in _users_cols:
            db.session.execute(_text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT 0"))
            db.session.commit()
        # m046：safety_nodes 补 table_code 列及索引（支持 folder + file + table 混合目录树）
        _sn_cols = {row[1] for row in db.session.execute(_text("PRAGMA table_info(safety_nodes)")).fetchall()}
        if _sn_cols and 'table_code' not in _sn_cols:
            db.session.execute(_text("ALTER TABLE safety_nodes ADD COLUMN table_code VARCHAR(60)"))
            db.session.execute(_text("CREATE INDEX IF NOT EXISTS ix_safety_nodes_table_code ON safety_nodes(table_code)"))
            db.session.commit()

        # 022 审阅修复：resident_sync_log 补 error_message 列（存量库 create_all 不追加列）
        from utils.schema_ensure import ensure_columns
        ensure_columns('resident_sync_log', {'error_message': 'VARCHAR(200)'})
        # 036 F4（R-036-02 修复）：import_batch 补 catalog_version 列（版本快照独立列，
        # 幂等补列；新库由 create_all 建齐，存量库在此补齐）
        ensure_columns('import_batch', {'catalog_version': 'VARCHAR(20)'})
        # 057b：users 补 last_active 列（在线心跳，轨 A 脚本 m022）
        ensure_columns('users', {'last_active': 'DATETIME'})
        # 067 ADR-024②：dibao 补 support_status 列（保障状态，轨 A 脚本 m023；
        # DEFAULT '已认定' 由 SQLite 对存量行自动回填）
        ensure_columns('dibao', {'support_status': "VARCHAR(10) DEFAULT '已认定'"})
        # 080 返工 M2：党员四维列自愈已移入 `if not skip_startup_hooks:` 启动守卫
        # （create_app 亦被后台任务线程调用，逐线程自愈会越界改写数据）。

        # 022 审阅修复 P3-4：清理 resident_id 同列重复自动索引（index=True 遗留，幂等）
        try:
            db.session.execute(_text(
                "DROP INDEX IF EXISTS ix_resident_sync_log_resident_id"))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logging.getLogger('shucun').warning(
                '清理 resident_sync_log 重复索引失败: %s', e)

        # 首次安装创建默认管理员（默认口令首次登录强制改密，P1-2 整改）
        from werkzeug.security import generate_password_hash
        if not User.query.filter_by(username='admin').first():
            role = Role.query.filter_by(id=1).first()
            if not role:
                role = Role(name='超级管理员', permissions='{"*": true}')
                db.session.add(role)
                db.session.flush()
            admin = User(username='admin',
                        password_hash=generate_password_hash('admin123'),
                        role_id=role.id, is_active=True,
                        must_change_password=True)
            db.session.add(admin)
            db.session.commit()

        # T10：预置「民政主任」角色种子（special: view_sensitive + death_confirm + report.view/export）
        # M4.4 整改：report 蓝图权限补入；已存在角色幂等补齐（US-5 民政主任为报表导出人）
        mz_role = Role.query.filter_by(name='民政主任').first()
        if not mz_role:
            civil_perms = {'__schema_version__': 3, 'special': {
                'view_sensitive': True, 'death_confirm': True}}
            for _m in ('resident', 'care', 'dibao', 'disabled', 'death_report',
                       'medical_insurance', 'veteran', 'subsidy', 'welfare',
                       'mediation', 'visit', 'warn', 'reconcile',
                       'certificate', 'notice', 'shop'):
                for _a in ('view', 'add', 'edit', 'delete', 'export'):
                    civil_perms[f'{_m}.{_a}'] = True
            civil_perms['report.view'] = True
            civil_perms['report.export'] = True
            mz_role = Role(name='民政主任', description='民政业务台账主管（一老一小/低保/残疾/死亡/报表）',
                           permissions=json.dumps(civil_perms, ensure_ascii=False),
                           is_system=True)
            db.session.add(mz_role)
        else:
            # 幂等补齐：既有库角色缺 report 权限时补入（不覆盖其他权限）
            try:
                _perms = json.loads(mz_role.permissions or '{}')
            except (ValueError, TypeError):
                _perms = {}
            if not _perms.get('report.view') or not _perms.get('report.export') \
                    or not _perms.get('visit.view') or not _perms.get('warn.view') \
                    or not _perms.get('reconcile.view') or not _perms.get('certificate.view') \
                    or not _perms.get('notice.view') or not _perms.get('shop.view'):
                _perms['report.view'] = True
                _perms['report.export'] = True
                for _a in ('view', 'add', 'edit', 'delete'):
                    _perms[f'visit.{_a}'] = True
                _perms['warn.view'] = True
                _perms['warn.edit'] = True
                # T15-4：对账模块幂等补齐（无 delete 端点）
                _perms['reconcile.view'] = True
                _perms['reconcile.add'] = True
                _perms['reconcile.edit'] = True
                _perms['reconcile.export'] = True
                # 042：证明开具五动作位（edit=停用/作废，export=Word 导出）
                for _a in ('view', 'add', 'edit', 'delete', 'export'):
                    _perms[f'certificate.{_a}'] = True
                # 045：补贴公示五动作位（模板 CRUD / 记录生成 / 导出）
                for _a in ('view', 'add', 'edit', 'delete', 'export'):
                    _perms[f'notice.{_a}'] = True
                # 047：店铺信息五动作位（R-047-01：seed 必须落在有调用方的本文件）
                for _a in ('view', 'add', 'edit', 'delete', 'export'):
                    _perms[f'shop.{_a}'] = True
                mz_role.permissions = json.dumps(_perms, ensure_ascii=False)
        db.session.commit()

        # 首次安装初始化字典数据
        if DictCategory.query.count() == 0:
            dict_data = {
                '村组': ['思榜','旧寨'],
                '与户主关系': ['户主本人','配偶','子女','父母','儿媳','女婿','孙子','孙女','外孙子','外孙女','曾孙子','曾孙女','兄弟姐妹','岳父母','公婆','祖父','祖母','外祖父','外祖母','其他亲属','非亲属'],
                '政治面貌': ['中共党员','中共预备党员','共青团员','群众','民主党派','无党派人士'],
                '学历': ['研究生','本科','大专','高中/中专','初中','小学','文盲'],
                '健康状态': ['健康','一般','长期慢性病','重大疾病','残疾'],
                '户属性': ['一般农户','脱贫户','监测户','五保户'],
                # T11-3：政策性居民标签（resident_tag.tag_code 来源）
                '居民标签': ['低保','残疾','困难老人','留守儿童','困境儿童','独居老人','高龄老人','脱贫户','监测户','五保户'],
                '户口类型': ['农业户口','非农业户口','统一居民户口'],
                '残疾类型': ['视力','听力','言语','肢体','智力','精神','多重'],
                '残疾等级': ['一级','二级','三级','四级'],
                '低保类型': ['A类','B类','C类'],
                '岗位类型（公益岗）': ['护林员','保洁员','护路员','巡河员','其他'],
                '纠纷类型': ['邻里纠纷','土地纠纷','婚姻家庭纠纷','债务纠纷','其他'],
                '风险等级': ['低风险','中风险','高风险'],
                '婚姻状况': ['未婚','已婚','离异','丧偶'],
                '人员状态': ['正常','死亡','迁出'],
                '务工状态': ['本地务工','外出务工','返乡','无务工'],
                '民族': ['汉','壮','苗','瑶','侗','仫佬','毛南','回','彝','水','仡佬','京','土家','满'],
                '党支部': ['中国共产党融水苗族自治县融水镇三合村小源屯支部委员会','中国共产党融水苗族自治县融水镇三合村思榜屯支部委员会','中国共产党融水苗族自治县融水镇三合村黎邓屯支部委员会','中国共产党融水苗族自治县融水镇三合村古营屯支部委员会','中国共产党融水苗族自治县融水镇三合村黄洞屯支部委员会','中国共产党融水苗族自治县融水镇三合村旧寨屯支部委员会'],
                '开户行': ['中国工商银行','中国农业银行','中国银行','中国建设银行','交通银行','中国邮政储蓄银行','招商银行','中信银行','中国光大银行','华夏银行','中国民生银行','广发银行','兴业银行','平安银行','上海浦东发展银行','广西农村商业银行','农村信用合作社','北部湾银行','桂林银行','柳州银行'],
            }
            # 080 R-1：与 utils/dict_seed.DICT_SEEDS 重复的分类以种子真源为准，避免字面量三源并存
            from utils.dict_seed import DICT_SEEDS, seed_pair
            dict_data.update({k: list(v) for k, v in DICT_SEEDS.items()})
            sort = 0
            for cat_name, items in dict_data.items():
                sort += 10
                cat = DictCategory(name=cat_name, description=cat_name, sort_order=sort)
                db.session.add(cat)
                db.session.flush()
                for idx, item in enumerate(items):
                    # 090A：种子条目支持 (label, value) 元组形态（「诉求来源」value 编码）
                    label, value = seed_pair(item)
                    di = DictItem(category_id=cat.id, label=label, value=value, sort_order=idx)
                    db.session.add(di)
            # 关爱对象分类
            care_cat = DictCategory(name='关爱对象分类', description='关爱对象分类标签', sort_order=200)
            db.session.add(care_cat)
            db.session.flush()
            for idx, label in enumerate(['留守儿童','独居老人','高龄老人']):
                di = DictItem(category_id=care_cat.id, label=label, value=label, sort_order=idx)
                db.session.add(di)
            # 系统设置字典（logo、标题等）
            sys_cat = DictCategory(name='系统设置', description='系统设置参数', sort_order=999)
            db.session.add(sys_cat)
            db.session.flush()
            for idx, (k,v) in enumerate([('logo_url',''),('system_title','村级村务综合管理系统')]):
                di = DictItem(category_id=sys_cat.id, label=k, value=v, sort_order=idx)
                db.session.add(di)
            db.session.commit()

        # H-2 / H-5 整改：m012 / m028 幂等播种迁至应用启动期，消除请求生命周期内 DDL 与每请求查询。
        # 必须位于「首次安装字典种子」之后：m028 会补插 dict_categories 枚举行，
        # 若先于首装种子执行会使 DictCategory.query.count()==0 守卫失效、跳过全部基础字典。
        from routes.safety import _ensure_safety_seed
        from routes.safety_emergency import _ensure_emergency_seed
        _ensure_safety_seed()
        _ensure_emergency_seed()

        # 035 R-034-03①：进程重启后残留 running 批次标记 aborted（断点续跑接线）。
        # 启动即跑一次：aborted 批次可经向导重进（execute 端点本就放行 aborted），
        # run_import 按身份证 hash 命中库内已成功行 → 跳过不重复写入。
        # 防御化：存量库未执行 m013（import_batch 尚无 mode/policy/updated_rows 列）时，
        # 该查询会抛 OperationalError —— 启动钩子不得成为应用启动的单点故障（035 实测：
        # 未迁移库上曾致 create_app 整体失败）。按 AGENTS §11 数据库纪律，这四列走
        # scripts/migrations/m013_import_batch_mode.sql 迁移，不在启动期做临时 ALTER。
        # 035 复核 R-035-03：本钩子语义是「进程启动时清残留」，而 create_app 在本仓
        # 还被 run_async 的每个后台任务线程调用一次（async_tasks._runner）——若每次都扫，
        # 会把正在执行的导入批次误标 aborted（破坏 execute/resumable 依赖的
        # 「aborted ⇒ 无人执行」不变量）。故由调用方显式声明是否属于进程启动。
        if not skip_startup_hooks:
            # 080 返工 M2：党员四维列自愈属进程启动语义，必须在守卫内，避免后台线程越界
            _party_heal = _ensure_party_dimensions()
            if _party_heal['backfilled']:
                logging.getLogger('shucun').warning(
                    'party_members 拆维度自愈：回填未迁移行 %d 行（对应 scripts/migrations/'
                    'm025_party_dimensions.py，发布仍须显式执行 m025 并核对）',
                    _party_heal['backfilled'])

            # 043 R-042-02：种子属进程启动语义（create_app 也被后台任务线程调用），
            # 必须在守卫内；只 SELECT/INSERT/UPDATE，未迁移库静默跳过。
            from utils.certificate_catalog import ensure_certificate_seed
            try:
                ensure_certificate_seed()
            except SQLAlchemyError as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                logging.getLogger('shucun').warning(
                    '证明开具种子跳过（不影响启动）：%s: %s', type(e).__name__, e)

            # 045 R-045-03：补贴公示内置模板种子移入进程启动守卫（对齐 042/043 纪律）；
            # 后台任务线程建 app（skip_startup_hooks=True）不写库。
            from models.notice import ensure_notice_seed
            try:
                ensure_notice_seed()
            except SQLAlchemyError as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                logging.getLogger('shucun').warning(
                    '补贴公示种子跳过（不影响启动）：%s: %s', type(e).__name__, e)

            # GLB-44：特困供养表单字典补齐（存量库幂等；新库由 init_db.py 完整播种）
            from utils.dict_seed import ensure_care_support_dict
            try:
                ensure_care_support_dict()
            except SQLAlchemyError as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                logging.getLogger('shucun').warning(
                    '特困供养字典种子跳过（不影响启动）：%s: %s', type(e).__name__, e)

            # 141（补丁 C-2）：8 张综治固定表幂等初始化 + 一代综治台账历史数据按需补迁。
            # 进程启动期 KeyHolder 为锁定态：含身份材料的待迁行会被整批推迟（不写空值/明文），
            # 由工作台页/旧入口 302 的懒触发点在解锁后补迁；任何异常都不阻断启动。
            from utils.governance_v2_migration import ensure_governance_v2_tables
            try:
                ensure_governance_v2_tables(migrate_legacy=True)
            except Exception:
                pass

            # 079 A0 / 080 A / 082 B / 094 B-9：全部字典种子幂等补录
            # （种子真源 utils/dict_seed.DICT_SEEDS，双真源已收敛）
            from utils.dict_seed import ensure_enum_dicts
            try:
                ensure_enum_dicts()
            except SQLAlchemyError as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                logging.getLogger('shucun').warning(
                    '字典种子跳过（不影响启动）：%s: %s', type(e).__name__, e)

            from utils.import_engine import mark_aborted_stale
            _warn_missing_import_tables()
            try:
                mark_aborted_stale()
            except SQLAlchemyError as e:
                # R-035-04：钩子语义是「尽力清理」，任何 DB 异常都不得阻断启动；
                # rollback 自身失败也不得二次抛出。
                try:
                    db.session.rollback()
                except Exception:
                    pass
                _msg = str(e).lower()
                if 'no such column' in _msg or 'no such table' in _msg:
                    logging.getLogger('shucun').warning(
                        '启动钩子 mark_aborted_stale 跳过（存量库 schema 未迁移 m013，请执行 '
                        'scripts/migrations/m013_import_batch_mode.sql）：%s', e)
                else:
                    logging.getLogger('shucun').warning(
                        '启动钩子 mark_aborted_stale 跳过（非缺列异常，不影响应用启动）：%s: %s',
                        type(e).__name__, e)

            # 105：选民批量导入批次同理——进程重启后残留 running 标 aborted（幂等三件套之一）
            from utils.election_service import mark_aborted_stale as mark_election_batches_stale
            try:
                mark_election_batches_stale()
            except SQLAlchemyError as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                logging.getLogger('shucun').warning(
                    '启动钩子 election 批次清理跳过（不影响启动）：%s: %s',
                    type(e).__name__, e)

            # 107：软表批量导入批次同理——进程重启后残留 running 标 aborted（幂等三件套之一）
            from utils.domain_batch import mark_aborted_stale as mark_domain_batches_stale
            try:
                mark_domain_batches_stale()
            except Exception:
                pass
            from utils.softtable_batch import mark_aborted_stale as mark_softtable_batches_stale
            try:
                mark_softtable_batches_stale()
            except SQLAlchemyError as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                logging.getLogger('shucun').warning(
                    '启动钩子 softtable 批次清理跳过（不影响启动）：%s: %s',
                    type(e).__name__, e)

            # 108：补贴批量登记批次同理——进程重启后残留 running 标 aborted（幂等三件套之一）
            from utils.subsidy_service import mark_aborted_stale as mark_subsidy_batches_stale
            try:
                mark_subsidy_batches_stale()
            except SQLAlchemyError as e:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                logging.getLogger('shucun').warning(
                    '启动钩子 subsidy 批次清理跳过（不影响启动）：%s: %s',
                    type(e).__name__, e)

    # T07：注册 temp/exports 周期清理任务（B11 闭环，每日执行）
    from utils.async_tasks import (register_periodic, cleanup_temp_exports,
                                   cleanup_temp_imports)
    register_periodic("cleanup_exports", cleanup_temp_exports, 86400)
    # T02：注册 temp/imports 明文临时文件清理（§2.5 第7步，每日执行）
    register_periodic("cleanup_imports", cleanup_temp_imports, 86400)
    # T19：注册到期预警惰性巡检（R8-E0 lazy_check，每日执行；启动即跑一次补检）
    # 注：lazy_check 需 db（create_task），周期线程须在 app context 内执行；
    # SHUCUN_DISABLE_PERIODIC=1 时跳过（测试隔离，避免周期巡检与手动巡检抢锁）
    import os as _os
    if not _os.environ.get('SHUCUN_DISABLE_PERIODIC'):
        from utils.async_tasks import lazy_check_periodic

        def _lazy_check_periodic():
            with app.app_context():
                lazy_check_periodic()

        register_periodic("lazy_check", _lazy_check_periodic, 86400)

        # T15-4：季度对账计划首启检查（B4 双触发之一）+ 源名单 ≤30 天清理（U5）
        from utils.reconcile_service import ensure_quarter_task, cleanup_expired_sources

        def _reconcile_quarter_check():
            with app.app_context():
                ensure_quarter_task()
                cleanup_expired_sources()

        register_periodic("reconcile_quarter", _reconcile_quarter_check, 86400)

        # T20-3：备份惰性提醒（R9-E4，每日首启补检，默认 7 天）
        from utils.async_tasks import backup_reminder_check

        def _backup_reminder():
            with app.app_context():
                backup_reminder_check()

        register_periodic("backup_reminder", _backup_reminder, 86400)

    return app


app = create_app()
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 604800

if __name__ == '__main__':
    from utils.license import check_license_on_startup
    if check_license_on_startup():
        print('[授权] 授权有效，监听 0.0.0.0:5000（局域网可访问）')
        app.run(host='0.0.0.0', port=5000, debug=False)
    else:
        print('[授权] 未授权或授权无效，仅监听 127.0.0.1:5000（本机可访问）')
        app.run(host='127.0.0.1', port=5000, debug=False)
