import hashlib
import json as _json

from flask import Blueprint, render_template, request, session
from models import db, DictCategory, DictItem
from utils.decorators import login_required, admin_required
from utils.helpers import json_response, log_operation
from datetime import datetime

dict_bp = Blueprint('dict_mgmt', __name__)


@dict_bp.route('/')
@login_required
def list():
    """字典管理页：登录 + dict.view 可见（063-FIX，A 方案 §4.3）。

    登录由 @login_required 兜底；模块位由 BLUEPRINT_MODULES['dict_mgmt'] = 'dict' 判定。
    admin 专属已下沉到写操作端点（分类/项增删改仍 @admin_required），本页不放开任何写权限。
    """
    return render_template('dict_mgmt/list.html')


# ---- Dictionary Bundle（027 D3）----

def _build_dict_bundle():
    """全量字典：返回 (bundle, order)。

    - bundle = {分类名: {value: label}}（027 契约，原样保留——label() 与老消费方零改动）；
    - order  = {分类名: [{value, label}, ...]}（033 有序结构：按 sort_order 排序，
      下拉顺序唯一真源；jsonify 的 sort_keys 只重排对象 key，数组保持写入顺序，
      数字串 value 也不会被 JS 对象整形键重排）。
    仅启用项；分类不筛 is_active（与 get_dict_items 同口径）。
    """
    bundle = {}
    order = {}
    # R-048-02：`系统设置` 是 KV 配置分类（含大屏 token 等凭证），不得作为字典下发
    cats = (DictCategory.query.filter(DictCategory.name != '系统设置')
            .order_by(DictCategory.sort_order.asc(), DictCategory.id.asc()).all())
    for cat in cats:
        pairs = {}
        ordered = []
        items = (DictItem.query.filter_by(category_id=cat.id, is_active=True)
                 .order_by(DictItem.sort_order.asc(), DictItem.id.asc()).all())
        for it in items:
            pairs[str(it.value)] = it.label
            ordered.append({'value': str(it.value), 'label': it.label})
        bundle[cat.name] = pairs
        order[cat.name] = ordered
    return bundle, order


def _dict_bundle_version(bundle, order):
    """内容哈希当版本号：任何保存/编辑/删除都会自发 bump（R4 版本失效，无需新表）。

    033：哈希输入含 bundle + order——order 形状/顺序一变版本同样失效，
    前端旧缓存（无 order 字段）不会与新形状混用。
    """
    payload = _json.dumps({'bundle': bundle, 'order': order}, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(payload.encode('utf-8')).hexdigest()


@dict_bp.route('/api/bundle')
@login_required
def api_bundle():
    bundle, order = _build_dict_bundle()
    version = _dict_bundle_version(bundle, order)
    if request.args.get('v') == version:
        return json_response(True, data={'unchanged': True, 'v': version})
    return json_response(True, data={'unchanged': False, 'v': version,
                                     'bundle': bundle, 'order': order})


# ---- Categories ----

@dict_bp.route('/api/categories')
@login_required
@admin_required
def api_categories():
    categories = DictCategory.query.order_by(DictCategory.sort_order.asc(), DictCategory.id.asc()).all()
    data = []
    for cat in categories:
        data.append({
            'id': cat.id,
            'name': cat.name,
            'description': cat.description or '',
            'is_active': cat.is_active,
            'sort_order': cat.sort_order or 0,
            'item_count': cat.items.count() if cat.items else 0,
            'created_at': cat.created_at.strftime('%Y-%m-%d %H:%M') if cat.created_at else '',
        })
    return json_response(True, data=data)


@dict_bp.route('/api/category/save', methods=['POST'])
@login_required
@admin_required
def api_category_save():
    item_id = request.form.get('id', type=int)
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    sort_order = request.form.get('sort_order', 0, type=int)

    if not name:
        return json_response(False, '分类名称不能为空')

    if item_id:
        cat = DictCategory.query.get(item_id)
        if not cat:
            return json_response(False, '分类不存在')
        existing = DictCategory.query.filter(DictCategory.name == name, DictCategory.id != item_id).first()
        if existing:
            return json_response(False, '分类名称已存在')
        action = 'edit'
    else:
        existing = DictCategory.query.filter_by(name=name).first()
        if existing:
            return json_response(False, '分类名称已存在')
        cat = DictCategory()
        action = 'add'

    cat.name = name
    cat.description = description
    cat.sort_order = sort_order

    db.session.add(cat)
    db.session.commit()
    log_operation(session.get('username'), '字典管理', action, f'{action}字典分类: {name}', result='success')
    return json_response(True, '保存成功')


@dict_bp.route('/api/category/delete/<int:cat_id>', methods=['POST'])
@login_required
@admin_required
def api_category_delete(cat_id):
    cat = DictCategory.query.get(cat_id)
    if not cat:
        return json_response(False, '分类不存在')
    name = cat.name
    # Items will be cascade-deleted (defined in model)
    db.session.delete(cat)
    db.session.commit()
    log_operation(session.get('username'), '字典管理', 'delete', f'删除字典分类: {name}', result='success')
    return json_response(True, '删除成功')


# ---- Items ----

@dict_bp.route('/api/items/<int:category_id>')
@login_required
@admin_required
def api_items(category_id):
    items = DictItem.query.filter_by(category_id=category_id)\
        .order_by(DictItem.sort_order.asc(), DictItem.id.asc()).all()
    data = []
    for item in items:
        data.append({
            'id': item.id,
            'category_id': item.category_id,
            'label': item.label,
            'value': item.value,
            'sort_order': item.sort_order or 0,
            'is_active': item.is_active,
            'created_at': item.created_at.strftime('%Y-%m-%d %H:%M') if item.created_at else '',
        })
    return json_response(True, data=data)


@dict_bp.route('/api/item/save', methods=['POST'])
@login_required
@admin_required
def api_item_save():
    item_id = request.form.get('id', type=int)
    category_id = request.form.get('category_id', type=int)
    label = request.form.get('label', '').strip()
    value = request.form.get('value', '').strip()
    sort_order = request.form.get('sort_order', 0, type=int)

    if not category_id:
        return json_response(False, '请选择分类')
    if not label:
        return json_response(False, '字典项名称不能为空')
    if not value:
        value = label

    if item_id:
        item = DictItem.query.get(item_id)
        if not item:
            return json_response(False, '字典项不存在')
        action = 'edit'
    else:
        item = DictItem()
        item.category_id = category_id
        action = 'add'

    item.label = label
    item.value = value
    item.sort_order = sort_order

    db.session.add(item)
    db.session.commit()
    log_operation(session.get('username'), '字典管理', action, f'{action}字典项: {label}', result='success')
    return json_response(True, '保存成功')


@dict_bp.route('/api/item/toggle/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def api_item_toggle(item_id):
    item = DictItem.query.get(item_id)
    if not item:
        return json_response(False, '字典项不存在')
    item.is_active = not item.is_active
    db.session.commit()
    status = '启用' if item.is_active else '停用'
    log_operation(session.get('username'), '字典管理', 'toggle', f'{status}字典项: {item.label}', result='success')
    return json_response(True, f'已{status}')


@dict_bp.route('/api/item/delete/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def api_item_delete(item_id):
    item = DictItem.query.get(item_id)
    if not item:
        return json_response(False, '字典项不存在')
    label = item.label
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username'), '字典管理', 'delete', f'删除字典项: {label}', result='success')
    return json_response(True, '删除成功')
