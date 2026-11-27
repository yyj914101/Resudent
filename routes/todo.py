from flask import Blueprint, render_template, request, session, jsonify
from models import db, TodoItem, Resident, DictCategory, DictItem, User
from utils.decorators import login_required
from utils.helpers import json_response, paginate_query, log_operation, export_excel
from datetime import datetime, timedelta, date
from sqlalchemy import case, extract, text

todo_bp = Blueprint('todo', __name__)


AGE_REMINDER_CONFIG = [7, 18, 60, 70, 80, 90, 100]


def _get_system_setting(key, default=''):
    cat = DictCategory.query.filter_by(name='系统设置').first()
    if not cat:
        return default
    item = DictItem.query.filter_by(category_id=cat.id, label=key).first()
    return item.value if item else default


def _ensure_todo_columns():
    """P2-4 整改：复用 utils.schema_ensure 幂等补列（消除散落 PRAGMA+ALTER 样板）。"""
    from utils.schema_ensure import ensure_columns
    ensure_columns('todo_items', {
        'completed_by': 'VARCHAR(50)',
        'completed_at': 'DATETIME',
    })


def _current_operator():
    user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    return (user.real_name or user.username) if user else (session.get('username') or '')


def _todo_type_label(todo_type):
    type_map = {'birthday': '生日', 'age_reminder': '年龄到期', 'warn': '到期预警',
                'reconcile': '对账提醒', 'backup': '备份提醒',  # T20-3：备份惰性提醒
                'custom': '自定义'}
    return type_map.get(todo_type, todo_type or '')


def _is_auto_reminder(item):
    return item and item.todo_type in ('birthday', 'age_reminder', 'warn') and item.related_id


def _sync_warn_handled(item):
    """warn 类 todo 处置（done/dismiss）时同步 warn_record（T19，幂等：仅未处置生效）。"""
    if item and item.todo_type == 'warn' and item.related_id:
        from utils.warn_service import handle_warning
        handle_warning(item.related_id, result='待办区处置',
                       operator_id=session.get('user_id'))


def _dismiss_auto_reminder(item):
    item.status = 'dismissed'
    item.completed_by = _current_operator()
    item.completed_at = datetime.now()


@todo_bp.route('/')
@login_required
def list():
    _ensure_todo_columns()
    check_birthday_reminders()
    check_age_reminders()
    # 075 RC-19：副标题「共 N 条 · 待办 M 条」
    total = TodoItem.query.count()
    pending = TodoItem.query.filter(TodoItem.status == 'todo').count()
    return render_template('todo/list.html', head_total=total, head_current=pending)


@todo_bp.route('/api/list')
@login_required
def api_list():
    _ensure_todo_columns()
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    status = request.args.get('status', '').strip()
    keyword = request.args.get('keyword', '').strip()

    query = TodoItem.query
    if status:
        query = query.filter(TodoItem.status == status)
    else:
        query = query.filter(TodoItem.status != 'dismissed')
    if keyword: query = query.filter(db.or_(TodoItem.title.contains(keyword), TodoItem.content.contains(keyword)))
    # T19：warn 类 7 日未处置置顶标红（US-1 二期口径）
    from utils.warn_service import overdue_warn_todo_ids
    overdue_ids = overdue_warn_todo_ids()
    if overdue_ids:
        query = query.order_by(
            case((TodoItem.id.in_(overdue_ids), 0), else_=1),
            TodoItem.created_at.desc(),
        )
    else:
        query = query.order_by(TodoItem.created_at.desc())
    items, total, pages = paginate_query(query, page, limit)
    data = [{
        'id': i.id,
        'title': i.title,
        'content': i.content or '',
        'todo_type': i.todo_type or '',
        'todo_type_label': _todo_type_label(i.todo_type),
        'status': i.status,
        'overdue': i.id in overdue_ids,
        'remind_time': i.remind_time.strftime('%Y-%m-%d %H:%M') if i.remind_time else '',
        'related_id': i.related_id,
        'completed_by': i.completed_by or '',
        'completed_at': i.completed_at.strftime('%Y-%m-%d %H:%M') if i.completed_at else '',
        'created_at': i.created_at.strftime('%Y-%m-%d %H:%M') if i.created_at else ''
    } for i in items]
    return json_response(success=True, data={'data':data,'total':total,'pages':pages,'page':page,'limit':limit})


@todo_bp.route('/api/types')
@login_required
def api_types():
    cat = DictCategory.query.filter_by(name='待办事项类型').first()
    values = []
    if cat:
        values = [item.value or item.label for item in cat.items.filter_by(is_active=True).all()]
    defaults = ['日常工作', '群众诉求', '资料办理', '走访核查', '会议安排', '其他']
    merged = []
    for val in values + defaults:
        if val and val not in merged:
            merged.append(val)
    return json_response(True, data=merged)


@todo_bp.route('/api/export')
@login_required
def api_export():
    """Export todo items to Excel."""
    _ensure_todo_columns()
    status = request.args.get('status', '').strip()
    keyword = request.args.get('keyword', '').strip()

    query = TodoItem.query
    if status:
        query = query.filter(TodoItem.status == status)
    else:
        query = query.filter(TodoItem.status != 'dismissed')
    if keyword: query = query.filter(db.or_(TodoItem.title.contains(keyword), TodoItem.content.contains(keyword)))
    query = query.order_by(TodoItem.created_at.desc())
    items = query.all()

    status_map = {'todo': '待办', 'done': '已完成'}

    headers = ['序号', '标题', '内容', '类型', '状态', '完成人', '完成时间', '创建时间']
    rows = []
    for i, item in enumerate(items, 1):
        rows.append([
            i,
            item.title or '',
            item.content or '',
            _todo_type_label(item.todo_type),
            status_map.get(item.status, item.status or ''),
            item.completed_by or '',
            item.completed_at.strftime('%Y-%m-%d %H:%M') if item.completed_at else '',
            item.created_at.strftime('%Y-%m-%d %H:%M') if item.created_at else '',
        ])

    return export_excel(headers, rows, '待办事项.xlsx')


@todo_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    _ensure_todo_columns()
    f=request.form; title=f.get('title','').strip()
    if not title: return json_response(False,'标题不能为空')
    todo_type = f.get('todo_type', '').strip() or '自定义'
    t=TodoItem(title=title,content=f.get('content','').strip(),todo_type=todo_type,status='todo')
    try:
        if f.get('remind_time'):
            from datetime import datetime as dt
            t.remind_time = dt.strptime(f.get('remind_time').strip(), '%Y-%m-%dT%H:%M')
    except Exception: pass
    db.session.add(t)
    try: db.session.commit();log_operation(session.get('username',''),'待办','add',f'新增待办: {title}');return json_response(True,'保存成功',data={'id':t.id})
    except Exception: db.session.rollback();return json_response(False,'保存失败')


@todo_bp.route('/api/done/<int:tid>', methods=['POST'])
@login_required
def api_done(tid):
    _ensure_todo_columns()
    t=TodoItem.query.get(tid)
    if not t: return json_response(False,'记录不存在')
    if t.status == 'todo':
        t.status = 'done'
        t.completed_by = _current_operator()
        t.completed_at = datetime.now()
        _sync_warn_handled(t)
    else:
        t.status = 'todo'
        t.completed_by = ''
        t.completed_at = None
    try: db.session.commit();return json_response(True,'操作成功')
    except Exception: db.session.rollback();return json_response(False,'操作失败')


@todo_bp.route('/api/batch-done', methods=['POST'])
@login_required
def api_batch_done():
    _ensure_todo_columns()
    ids=request.form.getlist('ids[]') or (request.json or {}).get('ids',[])
    count=0
    operator = _current_operator()
    for tid in ids:
        t=TodoItem.query.get(int(tid))
        if t:
            t.status='done'
            t.completed_by = operator
            t.completed_at = datetime.now()
            _sync_warn_handled(t)
            count+=1
    try: db.session.commit();return json_response(True,f'已完成{count}条')
    except Exception: db.session.rollback();return json_response(False,'操作失败')


@todo_bp.route('/api/delete/<int:tid>', methods=['POST'])
@login_required
def api_delete(tid):
    _ensure_todo_columns()
    t=TodoItem.query.get(tid)
    if not t: return json_response(False,'记录不存在')
    try:
        if _is_auto_reminder(t):
            _dismiss_auto_reminder(t)
            _sync_warn_handled(t)
        else:
            db.session.delete(t)
        db.session.commit()
        return json_response(True,'删除成功')
    except Exception: db.session.rollback();return json_response(False,'删除失败')


@todo_bp.route('/api/batch-delete', methods=['POST'])
@login_required
def api_batch_delete():
    _ensure_todo_columns()
    ids=request.form.getlist('ids[]') or (request.json or {}).get('ids',[])
    count=0
    for tid in ids:
        t=TodoItem.query.get(int(tid))
        if t:
            if _is_auto_reminder(t):
                _dismiss_auto_reminder(t)
                _sync_warn_handled(t)
            else:
                db.session.delete(t)
            count+=1
    try: db.session.commit();return json_response(True,f'已删除{count}条')
    except Exception: db.session.rollback();return json_response(False,'操作失败')


def check_birthday_reminders():
    """Check birthdays and auto-create TODO items."""
    try:
        today = date.today()
        # 检查生日提醒开关
        if _get_system_setting('enable_birthday_reminder', '1') == '0':
            return
        advance_days = 7
        try:
            advance_days = int(_get_system_setting('birthday_advance_days', '7'))
        except Exception:
            pass
        end_date = today + timedelta(days=advance_days)
        residents = Resident.query.filter(Resident.status=='normal', Resident.birth_date.isnot(None)).all()
        for res in residents:
            if not res.birth_date: continue
            try: bd_this = date(today.year, res.birth_date.month, res.birth_date.day)
            except ValueError: bd_this = date(today.year, 2, 28)
            if today <= bd_this <= end_date:
                existing = TodoItem.query.filter_by(todo_type='birthday',related_id=res.id).filter(extract('year',TodoItem.created_at)==today.year).first()
                if not existing:
                    age = today.year - res.birth_date.year
                    db.session.add(TodoItem(title=f'{res.name} 生日({age}岁)',content=f'{res.name}(身份证:{res.id_card})将于{today.year}年{res.birth_date.month}月{res.birth_date.day}日过{age}岁生日',todo_type='birthday',status='todo',related_id=res.id))
        db.session.commit()
    except Exception as e:
        print(f'[Todo] birthday error: {e}')


def check_age_reminders():
    """Check residents approaching milestone ages (60/80/90/100)."""
    try:
        today = date.today()
        for target_age in AGE_REMINDER_CONFIG:
            if _get_system_setting(f'enable_age_{target_age}', '1') == '0':
                continue
            try:
                advance_days = int(_get_system_setting(f'age_{target_age}_advance', '30'))
            except Exception:
                advance_days = 30
            end_date = today + timedelta(days=advance_days)
            residents = Resident.query.filter(Resident.status=='normal', Resident.birth_date.isnot(None)).all()
            for res in residents:
                if not res.birth_date: continue
                age_at_end = end_date.year - res.birth_date.year
                if age_at_end != target_age: continue
                try: bd_check = date(end_date.year, res.birth_date.month, res.birth_date.day)
                except ValueError: bd_check = date(end_date.year, 2, 28)
                if today <= bd_check <= end_date:
                    existing = TodoItem.query.filter_by(todo_type='age_reminder',related_id=res.id).filter(extract('year',TodoItem.created_at)==today.year).first()
                    if existing: continue
                    label = f'年满{target_age}岁'
                    if target_age == 60: label += '(办理养老退休)'
                    elif target_age == 70: label += '(高龄提醒)'
                    elif target_age >= 80: label += '(办理高龄补贴)'
                    db.session.add(TodoItem(
                        title=f'{res.name} {label}',
                        content=f'{res.name}(身份证:{res.id_card})将于{end_date.year}年{res.birth_date.month}月{res.birth_date.day}日{label}，请提前办理相关手续。',
                        todo_type='age_reminder', status='todo', related_id=res.id))
        db.session.commit()
    except Exception as e:
        print(f'[Todo] age error: {e}')
