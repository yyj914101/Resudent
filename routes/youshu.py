# -*- coding: utf-8 -*-
"""routes/youshu.py — 优抚对象台账（2026-11-16，民政对象第 6 类）。

字段口径（主理人派单）：姓名 / 身份证 / 电话 / 救助类别（字典「优抚对象救助类别」，
可在字典管理修改）/ 优抚金额 / 是否死亡 / 保障状态。数据表参考低保查询（同一套
civil 页面壳 + 加密管线），不做批量导入/模板（未派单，需要时另开）。

权限（ADR-016）：`BLUEPRINT_MODULES['youshu'] = 'dibao'` —— 优抚与低保同属民政对象
台账、使用同一批经办人，**复用低保模块位**，既有角色权限设置零变化（无需补授权）。
"""
from flask import Blueprint, render_template, request, session, send_file

from models import db, Youshu
from utils.decorators import login_required
from utils.helpers import paginate_query, json_response, log_operation, export_excel
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers

youshu_bp = Blueprint('youshu', __name__)

# R12 字段级加密管线（身份证/电话；与低保同一套 make_sensitive_helpers 工厂）
_YOUSHU_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}
_youshu_decrypt, _youshu_set_enc, _youshu_clause = make_sensitive_helpers(
    Youshu, _YOUSHU_SENS_FIELDS, key_holder)

# 保障状态与低保同口径（076 ADR-024②：已认定/复审中/待核实）
SUPPORT_STATUSES = ('已认定', '复审中', '待核实')
# 是否死亡：与低保 is_deceased 同口径（是/否）
DECEASED_VALUES = ('是', '否')


def _filtered_query(args):
    """列表筛选：姓名模糊；身份证/电话走 sha256 等值（消除明文 LIKE，P1-1 同类整改）。"""
    name = args.get('name', '').strip()
    id_card = args.get('id_card', '').strip()
    care_category = args.get('care_category', '').strip()
    support_status = args.get('support_status', '').strip()
    is_deceased = args.get('is_deceased', '').strip()

    query = Youshu.query.filter(Youshu.status == '有效').order_by(Youshu.id.desc())
    if name:
        query = query.filter(Youshu.name.like(f'%{name}%'))
    if id_card:
        query = query.filter(Youshu.id_card_hash == sha256_hash(id_card.upper()))
    if care_category:
        query = query.filter(Youshu.care_category == care_category)
    if support_status:
        query = query.filter(Youshu.support_status == support_status)
    if is_deceased:
        query = query.filter(Youshu.is_deceased == is_deceased)
    return query


def _serialize(r):
    """序列化（敏感字段解密；api_list 复用，api_detail 未单设——行点编辑直接取行数据）。"""
    return {
        'id': r.id,
        'name': r.name or '',
        'id_card': _youshu_decrypt(r, 'id_card'),
        'phone': _youshu_decrypt(r, 'phone'),
        'care_category': r.care_category or '',
        'amount': r.amount or 0,
        'is_deceased': r.is_deceased or '否',
        'support_status': r.support_status or '已认定',
        'remark': r.remark or '',
    }


@youshu_bp.route('/')
@login_required
def list_page():
    return render_template('youshu/list.html',
                           youshu_count=Youshu.query.filter(Youshu.status == '有效').count(),
                           support_statuses=SUPPORT_STATUSES)


@youshu_bp.route('/api/list')
@login_required
def api_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    query = _filtered_query(request.args)
    items, total, pages = paginate_query(query, page, limit)
    return json_response(success=True, data={
        'data': [_serialize(r) for r in items],
        'total': total, 'pages': pages, 'page': page, 'limit': limit,
    })


@youshu_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    form = request.form if request.form else (request.json or {})
    rid = form.get('id', type=int)
    if rid:
        r = Youshu.query.get(rid)
        if not r:
            return json_response(False, '记录不存在')
    else:
        r = Youshu()

    name = (form.get('name') or '').strip()
    id_card = (form.get('id_card') or '').strip()
    if not name:
        return json_response(False, '请填写姓名')
    if not id_card:
        return json_response(False, '请填写身份证号')

    r.name = name
    # 敏感字段走字段级加密管线（明文列置 NULL）
    _youshu_set_enc(r, 'id_card', id_card)
    _youshu_set_enc(r, 'phone', (form.get('phone') or '').strip())
    r.care_category = (form.get('care_category') or '').strip()
    r.amount = form.get('amount', 0, type=float)
    deceased = (form.get('is_deceased') or '否').strip()
    r.is_deceased = deceased if deceased in DECEASED_VALUES else '否'
    status = (form.get('support_status') or '已认定').strip()
    r.support_status = status if status in SUPPORT_STATUSES else '已认定'
    r.remark = (form.get('remark') or '').strip()

    if not rid:
        db.session.add(r)
    try:
        db.session.commit()
        log_operation(session.get('username', ''), '优抚对象',
                      'edit' if rid else 'add', f'保存优抚对象: {r.name}')
        return json_response(True, '保存成功', data={'id': r.id})
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')


@youshu_bp.route('/api/delete/<int:rid>', methods=['POST'])
@login_required
def api_delete(rid):
    r = Youshu.query.get(rid)
    if not r:
        return json_response(False, '记录不存在')
    try:
        db.session.delete(r)
        db.session.commit()
        log_operation(session.get('username', ''), '优抚对象', 'delete', f'删除优抚对象 ID:{rid}')
        return json_response(True, '删除成功')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'删除失败: {str(e)}')


@youshu_bp.route('/api/export')
@login_required
def api_export():
    """按当前筛选导出；身份证号列经 export_excel 的 T06 脱敏管线（无 view_sensitive 自动打码）。"""
    headers = ['序号', '姓名', '身份证号', '电话', '救助类别', '优抚金额（元）', '是否死亡', '保障状态', '备注']
    rows = []
    for idx, r in enumerate(_filtered_query(request.args).all(), 1):
        d = _serialize(r)
        rows.append([idx, d['name'], d['id_card'], d['phone'], d['care_category'],
                     d['amount'], d['is_deceased'], d['support_status'], d['remark']])
    return export_excel(headers, rows, '优抚对象名单.xlsx')
