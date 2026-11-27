import json

from flask import request, session

from models import Role, User


PERMISSION_SCHEMA_KEY = '__schema_version__'
PERMISSION_SCHEMA_VERSION = 3
PERMISSION_ACTIONS = ('view', 'add', 'edit', 'delete', 'export')
SPECIAL_KEYS = ('view_sensitive', 'death_confirm', 'import_batch', 'backup_restore', 'mask_config')
LEGACY_OPEN_MODULES = {'medical_insurance'}


PUBLIC_ENDPOINTS = {
    # 语义：enforce_role_permissions 不注入权限判定；豁免端点的登录/权限由端点自身
    # 装饰器或体内自校验负责（021 API-01 起本表是「enforce 豁免白名单」而非仅「免登录表」）。
    'auth.login',
    'auth.logout',
    # P1-2 整改：强制改密（登录后访问，端点内自校验 session）
    'auth.change_password',
    # B13 口令通道（T05）：启动必经，免登录但仅回环（端点内自校验）
    'auth.bootstrap_key',
    'auth.bootstrap_token_issue',
    'auth.unlock',
    'auth.key_status',
    'auth.recovery',  # 灾备恢复页：端点内回环 + 备份口令双自校验（021 API-01 归类）
    'static',
    # serve_upload 已移除（T07）：改为端点内自校验子目录权限
    'serve_upload',  # app 级端点：函数体内自校验子目录权限（021 API-01 归类）
    'root',  # app 级 '/'：redirect 目标 dashboard.index 有 @login_required 兜底
    # dashboard 聚合页：全体登录角色可见（@login_required 自校验；数据组装侧已脱敏）
    'dashboard.index',
    'dashboard.dashboard_stats',
    # 大屏展示端（048）：公开投屏免登录，端点内 token 自校验（_valid_token + 403）
    'screen.page',
    'screen.api_overview',
    # 设备准入（049）：客户端握手，免登录；端点内 Ed25519 验签 + 白名单 + 状态机自校验
    'device_handshake.handshake',
    'device_handshake.status',
    # 057b：心跳/在线人数——全体登录角色可见（@login_required 端点内自校验，
    # 同 dashboard.index 先例）；漏归 = 默认拒绝 403（ADR-016）
    'presence.heartbeat',
    'presence.online_count',
}

ENDPOINT_MODULES = {
    # ADR-023（063）：端点级归类——同一蓝图内允许不同端点归不同权限。
    # 键 = 完整 endpoint，值 = 模块键（按 action_for_request 推动作位）| None。
    # None 语义 =「登录即可」：enforce 不注入权限判定，登录由端点 @login_required 自校验，
    # 可见性/数据范围由端点内自证。None 条目**严禁**用于未挂 @login_required 的端点。
    # 判定优先级：PUBLIC_ENDPOINTS → 本表 → ADMIN_BLUEPRINTS → BLUEPRINT_MODULES → 默认拒绝。
    'settings.center': None,  # 057a hub：非 admin 可达，卡片的按项可见性由 _HUB_ITEM_PERM 过滤
    # 081：统一统计端点——端点内按指标 permission 二次校验（f'{permission}.view'）；
    # None 仅表示「登录即可」，登录已由端点 @login_required 自校验（对齐 063/ADR-023 约束）。
    'metrics.query': None,
}

ADMIN_BLUEPRINTS = {
    'backup',
    'db_admin',  # 025 数据库管理面板：admin 专属（ADR-016 归类）
    # 027：'dict_mgmt' 移至 BLUEPRINT_MODULES（'dict' 模块键）——字典 Bundle 端点按 dict.view 授权，
    # 管理端点仍由端点级 @admin_required 锁定；分类变化不放开任何既有端点。
    'license',
    'log',
    'role_mgmt',
    'settings',  # 063：仅 settings.center 由 ENDPOINT_MODULES 端点级豁免，其余端点仍 admin 专属
    'screen_admin',  # 048：大屏管理端（admin 专属配置）
    'device_mgmt',  # 049：设备准入白名单管理（admin 专属：审批即放行一台设备）
    'user_mgmt',
}

BLUEPRINT_MODULES = {
    'care': 'care',
    'demand': 'demand',  # 090A：民生诉求（demand 新蓝图，登记→办结两跳状态机）
    'certificate': 'certificate',
    'civilization': 'civilization',  # 089：文明实践站（civilization 门面 + 软表活动记录 + 公约固定表）
    'death_report': 'death_report',
    'dibao': 'dibao',
    # 优抚对象（2026-11-16）：民政对象第 6 类台账，复用低保模块位——同一批经办人，
    # 既有角色权限设置零变化（无需补授权）；若后续需要独立授权粒度，另派单拆键。
    'youshu': 'dibao',
    'dict_mgmt': 'dict',  # 027：字典模块位——Bundle GET 归 dict.view；管理端点另挂 @admin_required
    'dynatable': 'dynatable',  # 078：软表引擎（首期统一模块键，表级权限二期）
    'disabled': 'disabled',
    'election': 'election',  # 090B：公共事务·选举（届次状态机 + 选民名册 + 结果；动作位自动映射）
    'governance': 'mediation',  # 综治管理 4 台账复用 mediation 权限位（对齐 exe 先例）
    'mediation': 'mediation',
    'medical_insurance': 'medical_insurance',
    'notice': 'notice',  # 045：补贴公示（模板 6 端点 + 记录 8 端点，动作位自动映射）
    'word_form': 'notice',  # 通用 Word (.docx) 表单中心（复用 notice 权限位，零角色迁移）
    'organization': 'organization',
    'party': 'party',
    'pension': 'pension',  # 088：养老保险（pension 门面 + 软表参保名册 + 两指标）
    'report': 'report',  # T17-3/4（详细设计 §4.1 report.* 权限键；stats_table/roster_view→view、export_*→export）
    'reconcile': 'reconcile',  # T15-4（详细设计 §4.1 L843-851：reconcile.* 新增模块位，公安/殡葬对账）
    'resident': 'resident',
    'resident_enhance': 'resident',
    'sanitation': 'sanitation',  # 090C：环卫工作（sanitation 门面 + 三软表 + 两指标 + 092 照片附件）
    'safety': 'safety',  # 030：安全工作模块（档案目录树+上传下载+搜索），GET→view / POST→edit 自动映射
    'shop': 'shop',  # 店铺信息（居民管理组；view/add/edit/delete/export 动作位自动映射）
    'subsidy': 'subsidy',
    'todo': 'todo',
    'veteran': 'veteran',
    'visit': 'visit',  # T18-1（详细设计 §1.1 L55：visit.* 新增模块位）
    'warn': 'warn',  # T19（详细设计 §1.1：warn.* 新增模块位，R8 到期预警）
    'welfare': 'welfare',
}


def normalize_role_permissions(permissions):
    """v2→v3 继承（D8）：既有 module.action 键逐位不变；special 键缺省全 false（民政主任例外由种子写入）。"""
    normalized = dict(permissions or {})
    try:
        schema_version = int(normalized.get(PERMISSION_SCHEMA_KEY, 0) or 0)
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version < PERMISSION_SCHEMA_VERSION:
        for module in LEGACY_OPEN_MODULES:
            for action in PERMISSION_ACTIONS:
                normalized.setdefault(f'{module}.{action}', True)
    # v3 新增：special 键缺省全 false
    special = normalized.setdefault('special', {})
    for key in SPECIAL_KEYS:
        special.setdefault(key, False)
    normalized[PERMISSION_SCHEMA_KEY] = PERMISSION_SCHEMA_VERSION
    return normalized


def _load_permissions(user):
    if not user:
        return {}
    if user.role_id == 1:
        return {'*': True}
    role = Role.query.get(user.role_id)
    if not role:
        return {}
    try:
        return normalize_role_permissions(json.loads(role.permissions or '{}'))
    except (TypeError, ValueError):
        return {}


def user_has_permission(user, perm_key):
    if not user:
        return False
    if user.role_id == 1:
        return True
    perms = _load_permissions(user)
    if perm_key.startswith('special.'):
        special = perms.get('special') or {}
        return bool(perms.get('*') or special.get(perm_key.split('.', 1)[1]))
    return bool(perms.get('*') or perms.get(perm_key))


def user_has_special(user, special_key):
    """special 位查询（显式装饰器用）：@permission_required('special.death_confirm')。"""
    return user_has_permission(user, f'special.{special_key}')


def current_user_has_permission(perm_key):
    user_id = session.get('user_id')
    if not user_id:
        return False
    return user_has_permission(User.query.get(user_id), perm_key)


def response_wants_json():
    return (
        request.is_json
        or request.path.startswith('/api/')
        or '/api/' in request.path
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


def module_for_endpoint(endpoint):
    if not endpoint or '.' not in endpoint:
        return None
    blueprint = endpoint.rsplit('.', 1)[0]
    return BLUEPRINT_MODULES.get(blueprint)


def action_for_request(endpoint):
    view_name = endpoint.rsplit('.', 1)[-1] if endpoint else ''
    path = request.path.lower()
    method = request.method.upper()

    if 'export' in view_name or '/export' in path or 'word' in view_name:
        return 'export'
    if 'delete' in view_name or '/delete' in path or method == 'DELETE':
        return 'delete'
    if method == 'POST':
        if any(token in view_name for token in ('save', 'add', 'import', 'upload', 'create', 'auto_create')):
            return 'add'
        if any(token in view_name for token in ('edit', 'update', 'status', 'done', 'approve', 'reject', 'cancel', 'close', 'return', 'execute', 'restore', 'clear')):
            return 'edit'
        # T20-4：查询/预览类 POST 归 view（自定义查询构建器，非写操作）。
        # 021 API-02：移到写判定之后——写 token 优先，避免未来名字含
        # view/preview/query 的写 POST 被降级为 view（现命中 4 处均为纯只读，行为不变）。
        if 'query' in view_name or 'preview' in view_name or 'view' in view_name:
            return 'view'
        return 'edit'
    return 'view'


def required_permission_for_request():
    """端点级权限判定的单一出口（ADR-016 + ADR-023）。

    优先级固定（063 验收 1）：
    1. PUBLIC_ENDPOINTS   → None（登录+权限双豁免，端点内自证）
    2. ENDPOINT_MODULES   → 模块键.动作位；值为 None 时返回 None（登录即可，@login_required 自校验）
    3. ADMIN_BLUEPRINTS   → '__admin__'
    4. BLUEPRINT_MODULES  → 模块键.动作位
    5. 默认拒绝           → '__admin__'（漏归 = 403，红线不放松）
    """
    endpoint = request.endpoint
    if endpoint in PUBLIC_ENDPOINTS:
        return None

    if endpoint in ENDPOINT_MODULES:
        module = ENDPOINT_MODULES[endpoint]
        return None if module is None else f'{module}.{action_for_request(endpoint)}'

    blueprint = endpoint.rsplit('.', 1)[0] if endpoint and '.' in endpoint else None
    if blueprint in ADMIN_BLUEPRINTS:
        return '__admin__'

    module = module_for_endpoint(endpoint)
    if not module:
        # 021 API-01：默认拒绝。未登记进 BLUEPRINT_MODULES/ADMIN_BLUEPRINTS、
        # 也未显式列入 PUBLIC_ENDPOINTS 的端点一律按 __admin__ 处理——
        # 新蓝图忘归类即被锁死（暴露于测试），而非静默绕过权限强制。
        return '__admin__'
    return f'{module}.{action_for_request(endpoint)}'
