from flask import Blueprint, render_template, request, session, url_for
from models import db, DictCategory, DictItem
from models.todo import TodoItem
from utils.decorators import login_required, admin_required
from utils.helpers import json_response, log_operation, get_dict_items, get_dict_value

settings_bp = Blueprint('settings', __name__)

# Settings use DictCategory '系统设置' to store key-value config


def _get_setting(key, default=''):
    """Get a setting value from the 系统设置 category."""
    cat = DictCategory.query.filter_by(name='系统设置').first()
    if not cat:
        return default
    item = DictItem.query.filter_by(category_id=cat.id, label=key).first()
    return item.value if item else default


def _set_setting(key, value, description=''):
    """Set a setting value in the 系统设置 category."""
    cat = DictCategory.query.filter_by(name='系统设置').first()
    if not cat:
        cat = DictCategory(name='系统设置', description='系统配置参数')
        db.session.add(cat)
        db.session.flush()

    item = DictItem.query.filter_by(category_id=cat.id, label=key).first()
    if item:
        item.value = str(value)
    else:
        item = DictItem(
            category_id=cat.id,
            label=key,
            value=str(value),
            sort_order=0
        )
        db.session.add(item)
    db.session.commit()


@settings_bp.route('/branding')
@login_required
def branding():
    return render_template('settings/branding.html')


@settings_bp.route('/api/branding', methods=['POST'])
@login_required
@admin_required
def api_branding():
    system_title = request.form.get('system_title', '').strip()

    if not system_title:
        return json_response(False, '系统标题不能为空')

    _set_setting('system_title', system_title, '系统标题')

    # 075 RC-02（Q1 裁定）：村名独立设置项；可空，空时页头副标题回退不带村名口径
    village_name = request.form.get('village_name', '').strip()
    _set_setting('village_name', village_name, '村名')
    
    # Handle logo upload
    logo_file = request.files.get('logo_file')
    if logo_file and logo_file.filename:
        ext = logo_file.filename.rsplit('.', 1)[1].lower() if '.' in logo_file.filename else 'png'
        if ext not in ('png','jpg','jpeg','gif','webp'):
            return json_response(False, '仅支持 png/jpg/gif/webp 格式')
        import uuid, os
        from config import Config
        fname = f'logo_{uuid.uuid4().hex[:8]}.{ext}'
        logo_dir = os.path.join(Config.UPLOAD_FOLDER, 'logos')
        os.makedirs(logo_dir, exist_ok=True)
        logo_file.save(os.path.join(logo_dir, fname))
        logo_url = '/uploads/logos/' + fname
        _set_setting('logo_url', logo_url, 'Logo地址')
    elif request.form.get('logo_url','').strip():
        _set_setting('logo_url', request.form.get('logo_url').strip(), 'Logo地址')
    
    log_operation(session.get('username'), '系统设置', 'edit', '更新界面设置', result='success')
    return json_response(True, '保存成功')


@settings_bp.route('/api/branding/data')
@login_required
def api_branding_data():
    return json_response(True, data={
        'system_title': _get_setting('system_title', '村务管理系统'),
        'village_name': _get_setting('village_name', ''),
        'logo_url': _get_setting('logo_url', ''),
    })


@settings_bp.route('/notification')
@login_required
def notification():
    return render_template('settings/notification.html')


@settings_bp.route('/api/notification', methods=['POST'])
@login_required
@admin_required
def api_notification():
    enable_birthday_reminder = request.form.get('enable_birthday_reminder', '0').strip()
    send_time = request.form.get('send_time', '08:00').strip()
    
    _set_setting('enable_birthday_reminder', enable_birthday_reminder, '启用生日提醒')
    _set_setting('send_time', send_time, '提醒发送时间')
    if enable_birthday_reminder == '0':
        TodoItem.query.filter(
            TodoItem.todo_type == 'birthday',
            TodoItem.status == 'todo',
            TodoItem.related_id.isnot(None)
        ).delete(synchronize_session=False)
    
    # Age-based thresholds (default: enabled with 30 days notice)
    for age in [7, 18, 60, 70, 80, 90, 100]:
        enabled = request.form.get(f'enable_age_{age}', '0').strip()
        advance = request.form.get(f'age_{age}_advance', '30').strip()
        _set_setting(f'enable_age_{age}', enabled, f'{age}岁提醒启用')
        _set_setting(f'age_{age}_advance', advance, f'{age}岁提前天数')
        if enabled == '0':
            TodoItem.query.filter(
                TodoItem.todo_type == 'age_reminder',
                TodoItem.status == 'todo',
                TodoItem.related_id.isnot(None),
                TodoItem.title.contains(f'年满{age}岁')
            ).delete(synchronize_session=False)
    db.session.commit()
    
    log_operation(session.get('username'), '系统设置', 'edit', '更新通知配置', result='success')
    return json_response(True, '保存成功')


@settings_bp.route('/api/notification/data')
@login_required
def api_notification_data():
    data = {
        'enable_birthday_reminder': _get_setting('enable_birthday_reminder', '1'),
        'send_time': _get_setting('send_time', '08:00'),
    }
    age_labels = {
        7: '年满7岁(适龄入学)',
        18: '年满18岁(成年/兵役登记)',
        60: '年满60岁(办理养老退休)',
        70: '年满70岁(高龄提醒)',
        80: '年满80岁(办理高龄补贴)',
        90: '年满90岁(办理高龄补贴)',
        100: '年满100岁(办理高龄补贴)',
    }
    for age in [7, 18, 60, 70, 80, 90, 100]:
        data[f'enable_age_{age}'] = _get_setting(f'enable_age_{age}', '1')
        data[f'age_{age}_advance'] = _get_setting(f'age_{age}_advance', '30')
        data[f'age_{age}_label'] = age_labels.get(age, f'年满{age}岁')
    return json_response(True, data=data)


# ─── 设置中心 hub（057a · A 方案 §四 S1 入口页） ─────────────────────────
# 定位：底部「设置」的落点，4 组 × 3 项入口卡，点卡跳原页面（不做无刷新容器）。
# 权限：本蓝图在 ADMIN_BLUEPRINTS（ADMIN 专属），当前产品侧栏设置入口也仅 role_id==1 渲染；
#       分组按项过滤 + 空组整组隐藏（A 方案 §4.3 推论 2/3），为将来放开非 admin 留好结构。
_SETTINGS_HUB = (
    ('账号与权限', (
        ('用户管理', '账号、所属角色与状态', 'user_mgmt.list', 'users'),
        ('角色管理', '角色与权限位分配', 'role_mgmt.list', 'shield-check'),
        ('授权管理', '授权码与到期时间', 'license.index', 'key'),
    )),
    ('数据与规则', (
        ('字典管理', '字典分类与字典项', 'dict_mgmt.list', 'book-open'),
        ('字段注册审批', '导入扩展字段的准入', 'resident_enhance.import_registry', 'list-checks'),
        ('数据联动与补贴口径', '可视化配置主档联动与政策性补贴', 'settings.linkage_config', 'settings'),
        ('数据库安全', '备份、恢复与校验', 'backup.index', 'database'),
    )),
    ('界面与展示', (
        ('界面设置', '系统名称与 Logo', 'settings.branding', 'palette'),
        ('通知配置', '提醒渠道与开关', 'settings.notification', 'bell'),
        ('大屏管理', '投屏页面与访问凭证', 'screen_admin.manage', 'monitor'),
    )),
    ('系统与审计', (
        ('设备管理', '机器码准入与解绑', 'device_mgmt.index', 'hard-drive'),
        ('操作日志', '全站操作留痕', 'log.list', 'scroll-text'),
        ('数据库管理面板', '在线 DDL 与体检', 'db_admin.index', 'database-zap'),
    )),
)

# 非 admin 也可进入的项：按模块权限位判定（对应 BLUEPRINT_MODULES 的 dict / resident）
_HUB_ITEM_PERM = {
    'dict_mgmt.list': 'dict.view',
    'resident_enhance.import_registry': 'resident.view',
}


def _visible_hub_groups(is_admin, has_perm):
    """按权限过滤 hub 入口项；可见项为 0 的分组整组丢弃（A 方案 §4.3 推论 2/3）。

    抽成纯函数（060）：路由只取 g.current_user 与权限判定，过滤规则可单测，
    不依赖请求上下文（url_for 除外，单测在 app context 内调用）。
    """
    groups = []
    for label, items in _SETTINGS_HUB:
        visible = []
        for title, desc, endpoint, icon_name in items:
            perm = _HUB_ITEM_PERM.get(endpoint)
            if not is_admin and not (perm and has_perm(perm)):
                continue
            visible.append({
                'title': title,
                'desc': desc,
                'icon': icon_name,
                'href': url_for(endpoint),
            })
        if visible:
            groups.append({'label': label, 'items': visible})
    return groups


@settings_bp.route('/center')
@login_required
def center():
    """设置中心入口页（hub）。"""
    from flask import g
    from utils.permissions import current_user_has_permission as _has_perm

    user = getattr(g, 'current_user', None)
    is_admin = bool(user and getattr(user, 'role_id', None) == 1)
    return render_template('settings/index.html', groups=_visible_hub_groups(is_admin, _has_perm))


# ─── 数据联动与补贴口径可视化配置（零迁移，存系统设置 JSON） ──────────────
@settings_bp.route('/linkage')
@login_required
@admin_required
def linkage_config():
    """设置中心 → 数据联动与补贴口径可视化配置页。"""
    return render_template('settings/linkage.html')


@settings_bp.route('/api/linkage/data')
@login_required
@admin_required
def api_linkage_data():
    from utils.linkage_config import build_config_view_data
    return json_response(True, data=build_config_view_data())


@settings_bp.route('/api/linkage/policy', methods=['POST'])
@login_required
@admin_required
def api_linkage_save_policy():
    from utils.linkage_config import save_policy_config
    payload = request.get_json(silent=True) or {}
    try:
        save_policy_config(payload)
    except ValueError as e:
        return json_response(False, str(e), code=400)
    log_operation(session.get('username', ''), 'settings', 'update', '更新政策性补贴聚合口径配置')
    return json_response(True, '政策性补贴口径已保存并实时生效')


@settings_bp.route('/api/linkage/sync', methods=['POST'])
@login_required
@admin_required
def api_linkage_save_sync():
    from utils.linkage_config import save_sync_config
    payload = request.get_json(silent=True) or {}
    try:
        save_sync_config(payload)
    except ValueError as e:
        return json_response(False, str(e), code=400)
    log_operation(session.get('username', ''), 'settings', 'update', '更新居民主档跨模块联动字段配置')
    return json_response(True, '居民主档联动配置已保存并实时生效')


@settings_bp.route('/api/linkage/reset', methods=['POST'])
@login_required
@admin_required
def api_linkage_reset():
    from utils.linkage_config import reset_linkage_config
    payload = request.get_json(silent=True) or {}
    scope = (payload.get('scope') or 'all').strip()
    if scope not in ('policy', 'sync', 'all'):
        return json_response(False, '无效的重置范围', code=400)
    reset_linkage_config(scope)
    log_operation(session.get('username', ''), 'settings', 'reset', f'恢复数据联动与补贴口径出厂默认（{scope}）')
    return json_response(True, '已恢复出厂默认配置')
