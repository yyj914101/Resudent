"""大屏管理 —— 大厅实时数据大屏（公开投屏）+ 系统管理配置。

参考 exe V2.45.2「系统管理 → 大屏管理」移植重建（048）。
双蓝图：screen_admin（/screen-admin，admin 配置）+ screen（/screen，公开 token 投屏）。
配置存 DictCategory('系统设置')/DictItem；数据复用 dashboard_stats + 当月死亡人数 + 脱敏待办。

049-P2（每块大屏一个 token）：token 白名单存 `device_registry`（`device_type='screen'`），
一屏一行、可轮换/撤销/删除；048 的 KV 单 token 作为升级兼容兜底与首次迁移来源。
"""
import hashlib
import json
import re
import secrets
from datetime import date, datetime

from flask import Blueprint, jsonify, render_template, request, session

from models import (db, DeathReport, DeviceRegistry, DictCategory, DictItem,
                    Resident, TodoItem)
from routes.dashboard import _stats_payload
from utils.decorators import admin_required, login_required
from utils.helpers import json_response, log_operation

screen_admin_bp = Blueprint('screen_admin', __name__)
screen_bp = Blueprint('screen', __name__)

SCREEN_DEVICE_TYPE = 'screen'
KV_TOKEN_KEY = 'screen_access_token'

DEFAULTS = {
    'screen_enabled': '1',
    'screen_village_name': '',
    'screen_title': '村居智慧管理·实时数据驾驶舱',
    'screen_refresh_seconds': '60',
    'screen_work_start': '08:00',
    'screen_work_end': '18:00',
    'screen_show_death_count': '1',
}


def _setting(key, default=None):
    """从「系统设置」字典分类读取大屏配置键值。"""
    if default is None:
        default = DEFAULTS.get(key, '')
    category = DictCategory.query.filter_by(name='系统设置').first()
    if not category:
        return default
    item = DictItem.query.filter_by(category_id=category.id, label=key).first()
    if item:
        return item.value
    return default


def _save_settings(values):
    """写入「系统设置」字典分类（对齐 settings.py 的 KV 模式）。"""
    category = DictCategory.query.filter_by(name='系统设置').first()
    if not category:
        category = DictCategory(name='系统设置', description='系统配置参数', sort_order=999)
        db.session.add(category)
        db.session.flush()
    for key, value in values.items():
        item = DictItem.query.filter_by(category_id=category.id, label=key).first()
        if item:
            item.value = str(value)
        else:
            db.session.add(DictItem(category_id=category.id, label=key, value=str(value), sort_order=0))
    db.session.commit()


def _ensure_access_token():
    """048 的 KV 单 token —— 049-P2 起仅作「升级兼容兜底」与首次迁移来源。"""
    token = _setting(KV_TOKEN_KEY, '')
    if token:
        return token
    token = secrets.token_urlsafe(24)
    _save_settings({KV_TOKEN_KEY: token})
    return token


# ============ 大屏多 token 白名单（049-P2） ============

def _token_hash(token):
    """token → 16 位大写哈希，占 `device_registry.machine_code` 位（零新增字段/迁移）。"""
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()[:16].upper()


def _screen_token(row):
    try:
        return str((json.loads(row.credential or '{}') or {}).get('token') or '')
    except ValueError:
        return ''


def _screen_rows():
    return (DeviceRegistry.query
            .filter_by(device_type=SCREEN_DEVICE_TYPE)
            .order_by(DeviceRegistry.id.asc()).all())


def _store_screen(row, token):
    row.machine_code = _token_hash(token)
    row.credential = json.dumps({'token': token}, ensure_ascii=False)
    row.device_type = SCREEN_DEVICE_TYPE
    row.status = 'approved'
    row.expire = 'forever'


def _create_screen(name='', token=None):
    token = token or secrets.token_urlsafe(24)
    row = DeviceRegistry(label=(name or '')[:100], first_seen=datetime.now())
    _store_screen(row, token)
    db.session.add(row)
    db.session.commit()
    return row


def _ensure_default_screen():
    """表里没有大屏记录时，把 048 的 KV 单 token 迁成「默认大屏」——旧地址不断。"""
    rows = _screen_rows()
    if rows:
        return rows[0]
    return _create_screen('默认大屏', token=_ensure_access_token())


def _screen_addr(token):
    return f'{request.host_url.rstrip("/")}/screen/?token={token}'


def _screen_payload(row):
    token = _screen_token(row)
    status = row.status or 'approved'
    if not token:
        # R-050-01：token 被清空（如历史误操作）时不再谎报「有效」；派生态不落库
        status = 'missing'
    return {
        'id': row.id,
        'name': row.label or '未命名大屏',
        'token': token,
        'url': _screen_addr(token),
        'status': status,
        'last_seen': row.last_seen.strftime('%Y-%m-%d %H:%M:%S') if row.last_seen else '',
    }


def _touch_screen(row, min_seconds=60):
    """最近访问节流写：同一屏幕 60s 内只落库一次（治 R-048-10 的写放大）。"""
    now = datetime.now()
    if row.last_seen and (now - row.last_seen).total_seconds() < min_seconds:
        return
    row.last_seen = now
    db.session.commit()


def _config():
    try:
        refresh_seconds = int(_setting('screen_refresh_seconds') or 60)
    except (TypeError, ValueError):
        refresh_seconds = 60
    return {
        'enabled': _setting('screen_enabled') == '1',
        'village_name': _setting('screen_village_name'),
        'title': _setting('screen_title'),
        'refresh_seconds': max(30, min(3600, refresh_seconds)),
        'work_start': _setting('screen_work_start'),
        'work_end': _setting('screen_work_end'),
        'show_death_count': _setting('screen_show_death_count') != '0',
    }


def _screen_url(token=None):
    """兼容旧调用点：返回默认大屏（表内第一条）的访问地址。"""
    token = token or _screen_token(_ensure_default_screen())
    return _screen_addr(token)


def _month_bounds(today):
    start = date(today.year, today.month, 1)
    if today.month == 12:
        end = date(today.year + 1, 1, 1)
    else:
        end = date(today.year, today.month + 1, 1)
    return start, end


def _current_month_death_count():
    """当月死亡人数：居民 deceased + 死亡上报，hash 优先、姓名回退两段归并（R12 半迁移态）。"""
    today = date.today()
    start, end = _month_bounds(today)
    resident_rows = Resident.query.filter(
        Resident.status == 'deceased',
        Resident.status_date.isnot(None),
        Resident.status_date >= start,
        Resident.status_date < end,
    ).all()
    report_rows = DeathReport.query.filter(
        DeathReport.death_date >= start,
        DeathReport.death_date < end,
    ).all()
    by_hash, no_hash_names = {}, set()
    for name, id_hash in [(row.name, row.id_card_hash) for row in resident_rows] + \
                         [(row.deceased_name, row.id_card_hash) for row in report_rows]:
        name = (name or '').strip()
        id_hash = (id_hash or '').strip()
        if id_hash:
            by_hash.setdefault(id_hash, set()).add(name)
        else:
            no_hash_names.add(name)
    hashed_names = set().union(*by_hash.values()) if by_hash else set()
    return len(by_hash) + len(no_hash_names - hashed_names)


_SCREEN_ID_CARD_RE = re.compile(r'(?<!\d)\d{15}(?:\d{2}[0-9Xx])?(?!\d)')
_SCREEN_PHONE_RE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
_SCREEN_ADDRESS_RE = re.compile(r'[^\s，。；;:：]*[址号组屯村][^\s，。；;:：]*')


def _screen_safe_label(value, fallback='待办事项'):
    """公开大屏标签脱敏：剥离身份证号、手机号、地址片段，截断长文本。"""
    label = _SCREEN_ID_CARD_RE.sub('', str(value or ''))
    label = _SCREEN_PHONE_RE.sub('', label)
    label = re.sub(r'身份证(?:号)?\s*[:：]?\s*[0-9Xx\s-]{6,}', '', label, flags=re.IGNORECASE)
    label = _SCREEN_ADDRESS_RE.sub('', label)
    label = re.sub(r'\s+', ' ', label).strip(' -:：')
    return (label[:24] or fallback).strip()


# GLB-31：公开大屏只出现中文标签；未知类型统一「待办事项」，不得把英文原样上屏。
SCREEN_TODO_TYPE_LABELS = {
    'birthday': '生日提醒',
    'age_reminder': '年龄提醒',
    'warn': '补贴预警',
    'care_guardian_consent': '监护确认',
    'custom': '自定义',
}
# GLB-32：运维类待办不上村委大厅公开大屏（只保留民生类）。
SCREEN_HIDDEN_TODO_TYPES = ('reconcile', 'backup')


def _screen_todo_type_label(todo_type):
    return SCREEN_TODO_TYPE_LABELS.get(todo_type, '待办事项')


def _visible_todos():
    """大屏可见的待办：民生类，排除对账/备份等运维项。"""
    return [item for item in TodoItem.query.filter(TodoItem.status == 'todo').all()
            if (item.todo_type or '') not in SCREEN_HIDDEN_TODO_TYPES]


def _screen_todos(limit=12):
    """隐私安全、紧凑的公开大屏待办列表。"""
    now = datetime.now()
    today = now.date()
    items = _visible_todos()

    def sort_key(item):
        if item.remind_time is None:
            return 2, datetime.max, -(item.created_at.timestamp() if item.created_at else 0)
        if item.remind_time < now:
            priority = 0
        elif item.remind_time.date() == today:
            priority = 1
        else:
            priority = 2
        return priority, item.remind_time, -(item.created_at.timestamp() if item.created_at else 0)

    result = []
    for item in sorted(items, key=sort_key)[:limit]:
        resident = db.session.get(Resident, item.related_id) if item.related_id else None
        # R-048-01：公开端点不返回姓名/电话/住址；scope 仅取村组（聚合级标识）
        scope = str(resident.village_group or '').strip()[:24] if resident else ''
        reminder_status = 'normal'
        if item.remind_time:
            if item.remind_time < now:
                reminder_status = 'overdue'
            elif item.remind_time.date() == today:
                reminder_status = 'today'
        result.append({
            'scope': scope,
            'category': _screen_todo_type_label(item.todo_type),
            'reminder_status': reminder_status,
        })
    return result


def _screen_data():
    # R-048-06：直接调用 dashboard 的纯统计函数（不再绕过登录装饰器取数）
    data = dict(_stats_payload())
    data.pop('veteran_count', None)
    data['current_month_death_count'] = _current_month_death_count()
    data['screen_todos'] = _screen_todos()
    # 与公开列表同口径：运维类待办不计入大屏「待办提醒」数
    data['todo_count'] = len(_visible_todos())
    return data


def _valid_token():
    """校验大屏 token，返回 (ok, row)。

    先查 `device_registry`（一屏一行）；**表内还没有任何大屏记录**时才回退 048 的 KV
    单 token（升级兼容：管理员还没打开过管理页时老地址仍能投屏）。一旦建过行就只认表——
    否则轮换/撤销后被 KV 里的旧值兜住，旧地址会永远有效。
    回退路径只读，不生成新 token。
    """
    supplied = (request.args.get('token') or request.headers.get('X-Screen-Token') or '').strip()
    if not supplied:
        return False, None
    row = DeviceRegistry.query.filter_by(device_type=SCREEN_DEVICE_TYPE,
                                         machine_code=_token_hash(supplied)).first()
    if row is not None:
        if row.status != 'approved':
            return False, None
        ok = secrets.compare_digest(supplied, _screen_token(row))
        return ok, (row if ok else None)
    if DeviceRegistry.query.filter_by(device_type=SCREEN_DEVICE_TYPE).first() is not None:
        return False, None
    kv = _setting(KV_TOKEN_KEY, '')
    return (bool(kv) and secrets.compare_digest(supplied, kv)), None


# ============ 管理端（/screen-admin，admin 专属） ============

@screen_admin_bp.route('/')
@login_required
@admin_required
def manage():
    _ensure_default_screen()
    return render_template('screen/manage.html', config=_config(), screen_url=_screen_url())


@screen_admin_bp.route('/api/config')
@login_required
@admin_required
def api_config():
    _ensure_default_screen()          # R-050-04：仅取「KV→默认大屏」迁移副作用，无返回值引用
    screens = [_screen_payload(row) for row in _screen_rows()]
    return json_response(True, data={
        'config': _config(),
        'screens': screens,
        # 兼容 048 前端字段（默认大屏）
        'screen_url': screens[0]['url'] if screens else _screen_url(),
        'token': screens[0]['token'] if screens else '',
        'last_seen_at': screens[0]['last_seen'] if screens else '',
    })


@screen_admin_bp.route('/api/screens')
@login_required
@admin_required
def api_screens():
    return json_response(True, data={'items': [_screen_payload(row) for row in _screen_rows()]})


@screen_admin_bp.route('/api/screens', methods=['POST'])
@login_required
@admin_required
def api_screen_create():
    payload = request.form if request.form else (request.json or {})
    name = str(payload.get('name') or '').strip()[:100] or '新增大屏'
    row = _create_screen(name)
    log_operation(session.get('username', ''), '大屏管理', 'add',
                  f'新增大屏「{name}」', result='success')
    return json_response(True, '已新增一块大屏', data={'item': _screen_payload(row)})


def _screen_or_404(screen_id):
    row = DeviceRegistry.query.get(screen_id)
    if not row or row.device_type != SCREEN_DEVICE_TYPE:
        return None
    return row


@screen_admin_bp.route('/api/screens/<int:screen_id>/rotate', methods=['POST'])
@login_required
@admin_required
def api_screen_rotate(screen_id):
    row = _screen_or_404(screen_id)
    if not row:
        return json_response(False, '大屏不存在')
    token = secrets.token_urlsafe(24)
    _store_screen(row, token)
    db.session.commit()
    log_operation(session.get('username', ''), '大屏管理', 'edit',
                  f'轮换大屏「{row.label or row.id}」凭证', result='success')
    return json_response(True, '已轮换，旧地址立即失效', data={'item': _screen_payload(row)})


@screen_admin_bp.route('/api/screens/<int:screen_id>/revoke', methods=['POST'])
@login_required
@admin_required
def api_screen_revoke(screen_id):
    row = _screen_or_404(screen_id)
    if not row:
        return json_response(False, '大屏不存在')
    row.status = 'revoked'
    db.session.commit()
    log_operation(session.get('username', ''), '大屏管理', 'edit',
                  f'撤销大屏「{row.label or row.id}」', result='success')
    return json_response(True, '已撤销，该地址立即失效', data={'item': _screen_payload(row)})


@screen_admin_bp.route('/api/screens/<int:screen_id>', methods=['DELETE'])
@login_required
@admin_required
def api_screen_delete(screen_id):
    row = _screen_or_404(screen_id)
    if not row:
        return json_response(False, '大屏不存在')
    name = row.label or str(row.id)
    db.session.delete(row)
    db.session.commit()
    log_operation(session.get('username', ''), '大屏管理', 'delete',
                  f'删除大屏「{name}」', result='success')
    return json_response(True, '已删除')


@screen_admin_bp.route('/api/config/save', methods=['POST'])
@login_required
@admin_required
def api_config_save():
    form = request.form if request.form else (request.json or {})
    try:
        refresh_seconds = int(form.get('refresh_seconds', 60))
    except (TypeError, ValueError):
        return json_response(False, '刷新间隔必须是数字')
    if refresh_seconds < 30 or refresh_seconds > 3600:
        return json_response(False, '刷新间隔范围为30-3600秒')
    work_start = str(form.get('work_start', '08:00')).strip()
    work_end = str(form.get('work_end', '18:00')).strip()
    # R-048-03：服务端强校验 HH:MM，非法值不落库（start==end 按 24 小时营业放行）
    if not all(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) for value in (work_start, work_end)):
        return json_response(False, '工作时间为 HH:MM 格式')
    values = {
        'screen_enabled': '1' if str(form.get('enabled', '0')) in ('1', 'true', 'on') else '0',
        'screen_village_name': str(form.get('village_name', '')).strip()[:100],
        'screen_title': str(form.get('title', DEFAULTS['screen_title'])).strip()[:100] or DEFAULTS['screen_title'],
        'screen_refresh_seconds': refresh_seconds,
        'screen_work_start': work_start,
        'screen_work_end': work_end,
        'screen_show_death_count': '1' if str(form.get('show_death_count', '0')) in ('1', 'true', 'on') else '0',
    }
    _save_settings(values)
    log_operation(session.get('username', ''), '大屏管理', 'edit', '更新大屏展示配置', result='success')
    return json_response(True, '保存成功', data={'config': _config()})


@screen_admin_bp.route('/api/token/rotate', methods=['POST'])
@login_required
@admin_required
def api_token_rotate():
    """048 旧端点：轮换「默认大屏」，等价于 /api/screens/<id>/rotate（保持前端兼容）。"""
    row = _ensure_default_screen()
    token = secrets.token_urlsafe(24)
    _store_screen(row, token)
    db.session.commit()
    log_operation(session.get('username', ''), '大屏管理', 'edit',
                  '重新生成默认大屏凭证', result='success')
    return json_response(True, '凭证已重新生成', data={'token': token,
                                                  'screen_url': _screen_addr(token),
                                                  'item': _screen_payload(row)})


# ============ 展示端（/screen，公开 token 投屏） ============

@screen_bp.route('/')
def page():
    ok, row = _valid_token()
    if not ok or not _config()['enabled']:
        return render_template('screen/unavailable.html'), 403
    if row:
        _touch_screen(row)
    config = _config()
    return render_template('screen/screen.html', config=config, token=request.args.get('token', ''))


@screen_bp.route('/api/overview')
def api_overview():
    ok, row = _valid_token()
    if not ok or not _config()['enabled']:
        return jsonify({'success': False, 'msg': '大屏访问未启用或凭证无效'}), 403
    if row:
        _touch_screen(row)
    config = _config()
    data = _screen_data()
    if not config['show_death_count']:
        data['current_month_death_count'] = None
    response = jsonify({
        'success': True,
        'data': data,
        'config': config,
        'updated_at': datetime.now().isoformat(timespec='seconds'),
    })
    response.headers['Cache-Control'] = 'no-store'
    return response
