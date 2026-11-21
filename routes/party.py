from flask import Blueprint, render_template, request, session, jsonify, send_file
from models import db, PartyMember, PartyFee, PartyTransfer, PartyPosition, Resident, OrganizationMember
from models import PartyActivity, PartyActivityReview, PartyDevelopment, PartyDevelopmentLog
from utils.decorators import login_required
from utils.helpers import paginate_query, json_response, log_operation, export_excel, get_dict_items
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers
from datetime import datetime, date

party_bp = Blueprint('party', __name__)

# R12 字段级加密管线（复用 utils.sensitive_fields，消除党员台账明文孤岛）
_PARTY_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}
_decrypt, _set_enc, _clause = make_sensitive_helpers(PartyMember, _PARTY_SENS_FIELDS, key_holder)


def _resident_plain(resident, field):
    """居民档案敏感字段明文（Resident 已字段级加密，原列 NULL，解密 _enc）。"""
    enc = getattr(resident, field + '_enc', None)
    if enc:
        try:
            return key_holder.decrypt(enc)
        except Exception:
            return ''
    return getattr(resident, field, '') or ''


def calc_dues(income):
    """党费分段计费（税后月收入 → 月应缴）。"""
    if not income or income <= 0:
        return 0
    if income <= 3000:
        return round(income * 0.005, 2)
    elif income <= 5000:
        return round(income * 0.01, 2)
    elif income <= 10000:
        return round(income * 0.015, 2)
    else:
        return min(round(income * 0.02, 2), 1000)


def _member_type_display(identity, mobility, employment_status, party_status, member_type=''):
    """080 A-1：列表「人员类别」列的组合展示（身份 · 流动 · 在职；已转出优先）。"""
    if (party_status or '').strip() == '已转出':
        return '已转出'
    parts = [v for v in ((identity or '').strip(), (mobility or '').strip(),
                         (employment_status or '').strip()) if v]
    return ' · '.join(parts) if parts else (member_type or '')


def _member_type_export(identity, mobility, employment_status, party_status):
    """080 返工 M4：导出「人员类别」串——身份·流动·在职·在册 非空段拼接。

    与列表展示 `_member_type_display` 解耦：列表仍以「已转出」折叠展示（UI 不变），
    导出独立成四段，避免「已转出」吃掉身份/流动/在职（R-080-05 往返失真）。
    """
    parts = [v for v in ((identity or '').strip(), (mobility or '').strip(),
                         (employment_status or '').strip(), (party_status or '').strip()) if v]
    return ' · '.join(parts)


def _parse_member_type(mt):
    """080 返工 M1/M4：解析导入「人员类别」——组合串/旧单值双向兼容，往返不丢维度。

    取值全部由字典派生（M1：本函数不得出现这四个枚举值字面量）：
    - 「已转出」= 在册状态 ∩ 人员类别旧值；「在家」= 流动状态中不出现在旧值里的那个；
    - 四段组合串按「·」切分，逐段对位 身份/流动/在职/在册；
    - 旧单值（整串无分隔符）折算与 m025 回填一致：正式/预备 → 流动=在家；
      流动党员 → 流动；已转出 → 在册=已转出；其他 → 仅 在册；
    - 返回 (identity, mobility, employment, party_status)。
    """
    from utils.dict_enum import get_enum
    raw = (mt or '').strip()
    if not raw:
        return '', '', '', ''
    ids = set(get_enum('党员身份'))
    mobs = set(get_enum('党员流动状态'))
    emps = set(get_enum('党员在职状态'))
    roll = set(get_enum('党员在册状态'))
    legacy = set(get_enum('人员类别'))
    transferred_v = next(iter(roll & legacy), '')
    in_roll_v = next(iter(roll - {transferred_v}), '')
    home_v = next(iter([m for m in mobs if not any(m in l for l in legacy)]), '')
    flowing_v = next(iter([m for m in mobs if any(m in l for l in legacy)]), '')
    identity = mobility = employment = party_status = ''
    parts = [p.strip() for p in raw.split('·') if p.strip()]
    for v in parts:
        if v == transferred_v:
            party_status = transferred_v
        elif v in ids:
            identity = v
            if len(parts) == 1:
                mobility = home_v            # 旧单值：正式/预备 → 在家（m025 口径）
        elif v in mobs:
            mobility = v
        elif v in emps:
            employment = v
        elif v in legacy:
            mobility = flowing_v             # 旧单值：流动党员 → 不猜身份
    return identity, mobility, employment, (party_status or in_roll_v)


def _resident_politics(identity, mobility, party_status):
    """080 A-1：按「在册状态 × 党员身份 × 流动状态」判定居民政治面貌。

    旧「人员类别」为混合单字段，拆维度后等价映射：
    已转出→群众；预备党员→中共预备党员；正式党员→中共党员；
    身份空且流动→中共党员（等价旧「流动党员」）；其余 → ''。
    取值合法性以字典为真源（get_enum 校验，空值允许；非法值 → ''）。
    """
    from utils.dict_enum import get_enum
    identity = (identity or '').strip()
    mobility = (mobility or '').strip()
    party_status = (party_status or '').strip()
    if identity and identity not in get_enum('党员身份'):
        return ''
    if mobility and mobility not in get_enum('党员流动状态'):
        return ''
    if party_status and party_status not in get_enum('党员在册状态'):
        return ''
    if party_status == '已转出':
        return '群众'
    if identity == '预备党员':
        return '中共预备党员'
    if identity == '正式党员':
        return '中共党员'
    if not identity and mobility == '流动':
        return '中共党员'
    return ''


def sync_resident_politics(id_card, identity='', mobility='', party_status='在册'):
    if not id_card: return
    res = Resident.query.filter_by(id_card_hash=sha256_hash(str(id_card).strip().upper())).first()
    if not res: return
    politics = _resident_politics(identity, mobility, party_status)
    if politics:
        res.politics_status = politics
    try: db.session.commit()
    except Exception: db.session.rollback()


def sync_party_members_to_residents():
    """054-A#2：批量同步党员 → 居民政治面貌，返回更新条数。

    直接按 id_card_hash 关联（R12 等值检索口径），不解密明文——即使某行
    id_card_enc 缺失/损坏也不影响关联。
    """
    updated = 0
    for pm in PartyMember.query.all():
        if not pm.id_card_hash:
            continue
        res = Resident.query.filter_by(id_card_hash=pm.id_card_hash).first()
        if not res:
            continue
        politics = _resident_politics(pm.member_identity, pm.member_mobility, pm.party_status)
        if politics and res.politics_status != politics:
            res.politics_status = politics
            updated += 1
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return updated


def organization_positions_for_id_card_hash(id_card_hash):
    """054-A#5：按 id_card_hash 关联组织架构任职（跨模块只读，不解密明文）。"""
    if not id_card_hash:
        return []
    rows = OrganizationMember.query.filter_by(id_card_hash=id_card_hash)\
        .order_by(OrganizationMember.start_date.desc()).all()
    return [{
        'org_type': m.org_type or '', 'name': m.name or '', 'position': m.position or '',
        'start_date': m.start_date.strftime('%Y-%m-%d') if m.start_date else '',
        'end_date': m.end_date.strftime('%Y-%m-%d') if m.end_date else '',
        'is_current': bool(m.is_current),
    } for m in rows]

@party_bp.route('/')
@login_required
def party_list():
    # 080 A-1：人员类别拆维度——四类下拉 options 均由字典渲染
    from utils.helpers import get_dict_items
    from utils.dict_enum import get_enum
    party_dicts = {
        name: ([i.label for i in get_dict_items(name)] or list(get_enum(name)))
        for name in ('党员身份', '党员流动状态', '党员在职状态', '党员在册状态')
    }
    return render_template('party/list.html', party_dicts=party_dicts)

@party_bp.route('/api/list')
@login_required
def api_list():
    p=request.args; page=p.get('page',1,type=int); limit=p.get('limit',20,type=int)
    name=p.get('name','').strip(); id_card=p.get('id_card','').strip()
    mtype=p.get('member_identity','').strip(); branch=p.get('branch','').strip()
    now=datetime.now()

    # Party members
    pq = PartyMember.query.order_by(PartyMember.id.desc())
    if name: pq=pq.filter(PartyMember.name.like(f'%{name}%'))
    if id_card: pq=pq.filter(PartyMember.id_card_hash == sha256_hash(id_card.strip().upper()))
    if mtype: pq=pq.filter(PartyMember.member_identity==mtype)
    if branch: pq=pq.filter(PartyMember.branch.like(f'%{branch}%'))

    # Auto-detect from residents（R12：身份证按 hash 列比对，消除明文 LIKE）
    subq = db.session.query(PartyMember.id_card_hash).filter(PartyMember.id_card_hash.isnot(None)).subquery()
    rq = Resident.query.filter(
        Resident.politics_status.in_(['中共党员','中共预备党员']),
        ~Resident.id_card_hash.in_(db.select(subq.c.id_card_hash))  # AUD-01：避免 Subquery 强制转换告警
    )
    if name: rq=rq.filter(Resident.name.like(f'%{name}%'))
    if id_card: rq=rq.filter(Resident.id_card_hash == sha256_hash(id_card.strip().upper()))

    total = pq.count() + rq.count()
    pages = max(1, (total + limit - 1) // limit)
    party_items, _, _ = paginate_query(pq, page, limit)
    used = len(party_items)
    d=[]
    for r in party_items:
        age=now.year-r.birth_date.year-((now.month,now.day)<(r.birth_date.month,r.birth_date.day)) if r.birth_date else 0
        pa=now.year-r.join_date.year if r.join_date else 0
        uf=PartyFee.query.filter_by(member_id=r.id,status='unpaid').count()
        d.append({'id':r.id,'name':r.name or '','id_card':_decrypt(r,'id_card'),'gender':r.gender or '',
            'birth_date':r.birth_date.strftime('%Y-%m-%d') if r.birth_date else '','age':age,'party_age':pa,
            'nation':r.nation or '','education':r.education or '',
            'join_date':r.join_date.strftime('%Y-%m-%d') if r.join_date else '',
            'confirm_date':r.confirm_date.strftime('%Y-%m-%d') if r.confirm_date else '',
            'member_type':r.member_type or '','phone':_decrypt(r,'phone'),'branch':r.branch or '',
            'member_identity':r.member_identity or '','member_mobility':r.member_mobility or '',
            'employment_status':r.employment_status or '','party_status':r.party_status or '',
            'member_type_display':_member_type_display(r.member_identity, r.member_mobility,
                r.employment_status, r.party_status, r.member_type),
            'current_address':r.current_address or '','unpaid_fees':uf,'source':'manual'})
    if used < limit:
        resid_items, _, _ = paginate_query(rq, 1, limit - used)
        for r in resid_items:
            age=now.year-r.birth_date.year-((now.month,now.day)<(r.birth_date.month,r.birth_date.day)) if r.birth_date else 0
            d.append({'id':0,'name':r.name or '','id_card':_resident_plain(r,'id_card'),'gender':r.gender or '',
                'birth_date':r.birth_date.strftime('%Y-%m-%d') if r.birth_date else '','age':age,
                'party_age':0,'nation':r.nation or '','education':r.education or '',
                'join_date':'','confirm_date':'','member_type':'自动识别','member_type_display':'自动识别',
                'member_identity':'','member_mobility':'','employment_status':'','party_status':'',
                'phone':_resident_plain(r,'phone'),
                'branch':'','current_address':r.address or '','unpaid_fees':0,'source':'auto','resident_id':r.id})
    return json_response(success=True,data={'data':d,'total':total,'pages':pages,'page':page,'limit':limit})

@party_bp.route('/api/summary')
@login_required
def api_summary():
    """080 A-1：党员情况汇总面板（在册/正式/预备/流动/退休/已转出/自动关联/建档）。

    081：薄转发统一走 ``MetricsEngine('party_summary')``——口径与缓存 key 与
    ``/api/stats/query?metric_key=party_summary`` 同源；响应键集合与数值保持不变。
    """
    from utils.metrics import MetricsEngine
    data = MetricsEngine.query('party_summary')['data']
    return json_response(True, data=data)

@party_bp.route('/api/sync-residents', methods=['POST'])
@login_required
def api_sync_residents():
    """054-A#3：手动触发批量同步党员 → 居民政治面貌。"""
    updated = sync_party_members_to_residents()
    log_operation(session.get('username', ''), '党员', 'sync', f'同步居民政治面貌 {updated} 条')
    return json_response(True, f'同步完成，更新 {updated} 条', data={'updated': updated})

@party_bp.route('/api/auto-create', methods=['POST'])
@login_required
def api_auto_create():
    f=request.form; rid=f.get('resident_id',type=int)
    res=Resident.query.get(rid)
    if not res: return json_response(False,'居民不存在')
    if res.id_card_hash and PartyMember.query.filter_by(id_card_hash=res.id_card_hash).first():
        return json_response(False,'已是党员')
    pm=PartyMember(name=res.name,gender=res.gender,
        birth_date=res.birth_date,nation=res.nation,education=res.education,
        current_address=res.address,
        member_identity='正式党员',member_mobility='在家',
        employment_status='',party_status='在册',
        join_date=datetime.now().date())
    _set_enc(pm, 'id_card', _resident_plain(res, 'id_card'))
    _set_enc(pm, 'phone', _resident_plain(res, 'phone'))
    db.session.add(pm)
    try: db.session.commit();sync_resident_politics(_resident_plain(res, 'id_card'),pm.member_identity,pm.member_mobility,pm.party_status);return json_response(True,'已转为正式党员',data={'id':pm.id})
    except Exception: db.session.rollback();return json_response(False,'创建失败')

@party_bp.route('/api/detail/<int:pid>')
@login_required
def api_detail(pid):
    r=PartyMember.query.get(pid)
    if not r: return json_response(False,'记录不存在')
    now=datetime.now()
    age=now.year-r.birth_date.year-((now.month,now.day)<(r.birth_date.month,r.birth_date.day)) if r.birth_date else 0
    pa=now.year-r.join_date.year if r.join_date else 0
    family=[]
    res_linked=None
    if r.id_card_hash:
        res=Resident.query.filter_by(id_card_hash=r.id_card_hash).first()
        if res:
            res_linked=res
        if res and res.household_code:
            for f in Resident.query.filter(Resident.household_code==res.household_code).order_by(Resident.relationship).all():
                fa=now.year-f.birth_date.year-((now.month,now.day)<(f.birth_date.month,f.birth_date.day)) if f.birth_date else 0
                family.append({'resident_id': f.id, 'name':f.name or '','id_card':_resident_plain(f,'id_card'),'gender':f.gender or '',
                    'nation':f.nation or '','age':fa,'relationship':f.relationship or '','phone':_resident_plain(f,'phone'),
                    'family_situation': f.remark or ''})
    transfers=[{'id':t.id,'seq_no':t.seq_no,'transfer_type':t.transfer_type,
        'transfer_date':t.transfer_date.strftime('%Y-%m-%d') if t.transfer_date else '',
        'from_branch':t.from_branch or '','to_branch':t.to_branch or '',
        'reason':t.reason or '','remark':t.remark or ''} for t in r.transfers]
    positions=[{'id':p.id,'position_name':p.position_name or '',
        'start_date':p.start_date.strftime('%Y-%m-%d') if p.start_date else '',
        'end_date':p.end_date.strftime('%Y-%m-%d') if p.end_date else '','remark':p.remark or ''} for p in r.positions]
    return json_response(success=True,data={
        'id':r.id,'name':r.name or '','id_card':_decrypt(r,'id_card'),'gender':r.gender or '',
        'birth_date':r.birth_date.strftime('%Y-%m-%d') if r.birth_date else '','age':age,
        'nation':r.nation or '','education':r.education or '','phone':_decrypt(r,'phone'),
        'current_address':r.current_address or '','party_age':pa,
        'join_date':r.join_date.strftime('%Y-%m-%d') if r.join_date else '',
        'confirm_date':r.confirm_date.strftime('%Y-%m-%d') if r.confirm_date else '',
        'member_type':r.member_type or '','branch':r.branch or '',
        'member_identity':r.member_identity or '','member_mobility':r.member_mobility or '',
        'employment_status':r.employment_status or '','party_status':r.party_status or '',
        'family':family,'transfers':transfers,'positions':positions,
        'organization_positions': organization_positions_for_id_card_hash(r.id_card_hash),
        # 详情 v2（ADR-022 设计稿）：户籍性质读取关联居民户口类型；最近更新为档案真实时间戳
        'household_type': (res_linked.household_type or '') if res_linked else '',
        'updated_at': r.updated_at.strftime('%Y-%m-%d %H:%M') if r.updated_at else ''})

@party_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    f=request.form; did=f.get('id',type=int)
    r=PartyMember.query.get(did) if did else PartyMember()
    if not did: db.session.add(r)
    for k in ['name','gender','nation','education','current_address',
              'member_identity','member_mobility','employment_status','party_status','branch']:
        setattr(r,k,f.get(k,'').strip())
    id_card_raw = f.get('id_card','').strip()
    _set_enc(r, 'id_card', id_card_raw)
    _set_enc(r, 'phone', f.get('phone',''))
    def pd(v):
        if not v: return None
        try: return datetime.strptime(v.strip(),'%Y-%m-%d').date()
        except Exception: return None
    r.birth_date=pd(f.get('birth_date'));r.join_date=pd(f.get('join_date'));r.confirm_date=pd(f.get('confirm_date'))
    try: db.session.commit();sync_resident_politics(id_card_raw,r.member_identity,r.member_mobility,r.party_status);log_operation(session.get('username',''),'党员','save',f'保存: {r.name}');return json_response(True,'保存成功',data={'id':r.id})
    except Exception as e: db.session.rollback();return json_response(False,f'保存失败: {str(e)}')

@party_bp.route('/api/family-situation/save', methods=['POST'])
@login_required
def api_family_situation_save():
    rid = request.form.get('resident_id', type=int)
    situation = request.form.get('family_situation', '').strip()
    if not rid:
        return json_response(False, '参数不全')
    resident = Resident.query.get(rid)
    if not resident:
        return json_response(False, '家庭成员不存在')
    resident.remark = situation
    try:
        db.session.commit()
        log_operation(session.get('username', ''), '党员家庭情况', 'save',
                      f'保存家庭情况: {resident.name}')
        return json_response(True, '保存成功')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')

@party_bp.route('/api/delete/<int:pid>', methods=['POST'])
@login_required
def api_delete(pid):
    r=PartyMember.query.get(pid)
    if not r: return json_response(False,'记录不存在')
    id_card=_decrypt(r,'id_card')
    try: db.session.delete(r);db.session.commit();sync_resident_politics(id_card,'','','已转出');return json_response(True,'删除成功')
    except Exception: db.session.rollback();return json_response(False,'删除失败')

@party_bp.route('/api/transfer/save', methods=['POST'])
@login_required
def api_transfer_save():
    f=request.form; tid=f.get('id',type=int);mid=f.get('member_id',type=int)
    t=PartyTransfer.query.get(tid) if tid else PartyTransfer()
    if not tid: db.session.add(t)
    t.member_id=mid;t.seq_no=f.get('seq_no',1,type=int);t.transfer_type=f.get('transfer_type','').strip()
    try: t.transfer_date=datetime.strptime(f.get('transfer_date').strip(),'%Y-%m-%d').date() if f.get('transfer_date') else None
    except Exception: t.transfer_date=None
    t.from_branch=f.get('from_branch','').strip();t.to_branch=f.get('to_branch','').strip()
    t.reason=f.get('reason','').strip();t.remark=f.get('remark','').strip()
    # 054-A#6：流转党支部须在「党支部」字典内（value 或 label 均可）
    branch_items = get_dict_items('党支部')
    branch_vals = {i.value for i in branch_items} | {i.label for i in branch_items}
    for field_name, label in (('from_branch', '原党支部'), ('to_branch', '目标党支部')):
        v = f.get(field_name, '').strip()
        if v and branch_vals and v not in branch_vals:
            return json_response(False, f'{label}「{v}」不在党支部字典内，请从下拉选择')
    try: db.session.commit();return json_response(True,'保存成功')
    except Exception: db.session.rollback();return json_response(False,'保存失败')

@party_bp.route('/api/transfer/delete/<int:tid>', methods=['POST'])
@login_required
def api_transfer_delete(tid):
    t=PartyTransfer.query.get(tid)
    if not t: return json_response(False,'记录不存在')
    try: db.session.delete(t);db.session.commit();return json_response(True,'删除成功')
    except Exception: db.session.rollback();return json_response(False,'删除失败')

@party_bp.route('/api/position/save', methods=['POST'])
@login_required
def api_position_save():
    f=request.form; pid=f.get('id',type=int);mid=f.get('member_id',type=int)
    p=PartyPosition.query.get(pid) if pid else PartyPosition()
    if not pid: db.session.add(p)
    p.member_id=mid;p.position_name=f.get('position_name','').strip()
    try: p.start_date=datetime.strptime(f.get('start_date').strip(),'%Y-%m-%d').date() if f.get('start_date') else None
    except Exception: p.start_date=None
    try: p.end_date=datetime.strptime(f.get('end_date').strip(),'%Y-%m-%d').date() if f.get('end_date') else None
    except Exception: p.end_date=None
    p.remark=f.get('remark','').strip()
    try: db.session.commit();return json_response(True,'保存成功')
    except Exception: db.session.rollback();return json_response(False,'保存失败')

@party_bp.route('/api/position/delete/<int:pid>', methods=['POST'])
@login_required
def api_position_delete(pid):
    p=PartyPosition.query.get(pid)
    if not p: return json_response(False,'记录不存在')
    try: db.session.delete(p);db.session.commit();return json_response(True,'删除成功')
    except Exception: db.session.rollback();return json_response(False,'删除失败')

@party_bp.route('/api/template')
@login_required
def api_template():
    import openpyxl;from openpyxl.styles import Font, PatternFill;import io
    wb=openpyxl.Workbook();ws=wb.active;ws.title='党员导入模板'
    h=['姓名','身份证号','性别','出生日期','民族','学历','入党日期','转正日期','人员类别','手机号码','所在党支部','现居住址']
    ws.append(h)
    for ci in range(1,len(h)+1):
        cell=ws.cell(row=1,column=ci);cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill(start_color='dc2626',end_color='dc2626',fill_type='solid')
        ws.column_dimensions[chr(64+ci)].width=max(14,len(h[ci-1])*2+2)
    out=io.BytesIO();wb.save(out);out.seek(0)
    return send_file(out,mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',as_attachment=True,download_name='党员导入模板.xlsx')

@party_bp.route('/api/import', methods=['POST'])
@login_required
def api_import():
    file=request.files.get('file');mode=request.form.get('mode','append')
    try: import openpyxl;buf=__import__('io').BytesIO(file.read());wb=openpyxl.load_workbook(buf);ws=wb.active;rows=[r for r in ws.iter_rows(min_row=2,values_only=True)]
    except Exception: return json_response(False,'文件解析失败')
    col={};[col.update({h.strip():i}) for i,h in enumerate([c.value for c in ws[1]]) if h]
    def gv(cn):
        # 080 返工 R-080-19：表头缺失兜底（导出「手机号」vs 模板「手机号码」此前直接
        # rows[ri][None] 抛 TypeError → 用导出文件回导 500）；列超界同样按空值处理
        idx=col.get(cn)
        if idx is None: return ''
        row=rows[ri] if ri < len(rows) else ()
        v=row[idx] if idx < len(row) else None
        return str(v).strip() if v is not None else ''
    def pd(v):
        if not v: return None
        from datetime import date as dt_date
        if isinstance(v, datetime): return v.date()
        if isinstance(v, dt_date): return v
        s=str(v).strip()
        for fmt in ('%Y-%m-%d','%Y/%m/%d','%Y.%m.%d','%Y年%m月%d日'):
            try: return datetime.strptime(s,fmt).date()
            except Exception: pass
        return None
    imp=0;upd=0;skp=0;errs=[]
    for ri,row in enumerate(rows):
        if not row or all(c is None for c in row): continue
        nm=gv('姓名')
        if not nm: errs.append(f'第{ri+2}行: 姓名空');continue
        ic=gv('身份证号')
        ex=PartyMember.query.filter_by(id_card_hash=sha256_hash(ic.upper())).first() if ic else None
        if ex and mode=='append': skp+=1;continue
        r=ex if ex and mode=='overwrite' else PartyMember()
        if not ex or mode!='overwrite': db.session.add(r)
        r.name=nm;_set_enc(r,'id_card',ic);r.gender=gv('性别');r.nation=gv('民族');r.education=gv('学历')
        _set_enc(r,'phone',gv('手机号码'));r.current_address=gv('现居住址');r.branch=gv('所在党支部')
        r.birth_date=pd(rows[ri][col.get('出生日期')] if col.get('出生日期') is not None else None)
        r.join_date=pd(rows[ri][col.get('入党日期')] if col.get('入党日期') is not None else None)
        r.confirm_date=pd(rows[ri][col.get('转正日期')] if col.get('转正日期') is not None else None)
        # 导入模板仍以「人员类别」历史列录入：折算四维（旧值流动党员不猜身份；不回写历史列）
        # 080 返工 R-080-05：改走 _parse_member_type——兼容导出组合串「身份 · 流动 · 在职」，
        # 导出→导入往返四维相等（此前组合串落 else 分支静默清零身份/流动）
        mt = gv('人员类别')
        if mt:
            identity, mobility, employment, status = _parse_member_type(mt)
            r.member_identity = identity
            r.member_mobility = mobility
            r.employment_status = employment or (r.employment_status or '')
            r.party_status = status
        try: db.session.flush()
        except Exception as e: db.session.rollback();errs.append(f'第{ri+2}行: {str(e)}');continue
        # Sync resident politics
        sync_resident_politics(ic, r.member_identity, r.member_mobility, r.party_status)
        if ex and mode=='overwrite': upd+=1
        else: imp+=1
    try: db.session.commit()
    except Exception: db.session.rollback();return json_response(False,'导入失败')
    msg=f'新增{imp}条'; 
    if upd: msg+=f',更新{upd}条'
    if skp: msg+=f',跳过{skp}条'
    if errs: msg+='<br>失败'+str(len(errs))+'条'
    log_operation(session.get('username',''),'党员','import',msg)
    return json_response(success=len(errs)==0,msg=msg)

@party_bp.route('/api/export')
@login_required
def api_export():
    items=PartyMember.query.all();now=datetime.now()
    h=['姓名','身份证号','性别','出生日期','年龄','党龄','民族','学历','入党日期','转正日期','人员类别','手机号','所在党支部','现居住址']
    rows=[]
    for r in items:
        age=now.year-r.birth_date.year-((now.month,now.day)<(r.birth_date.month,r.birth_date.day)) if r.birth_date else ''
        pa=now.year-r.join_date.year if r.join_date else ''
        rows.append([r.name or '',_decrypt(r,'id_card'),r.gender or '',(r.birth_date.strftime('%Y-%m-%d') if r.birth_date else ''),age,pa,r.nation or '',r.education or '',(r.join_date.strftime('%Y-%m-%d') if r.join_date else ''),(r.confirm_date.strftime('%Y-%m-%d') if r.confirm_date else ''),_member_type_export(r.member_identity,r.member_mobility,r.employment_status,r.party_status),_decrypt(r,'phone'),r.branch or '',r.current_address or ''])
    return export_excel(h,rows,'党员数据.xlsx')


# ═══════════════════════════════════════════════════════════════
# 党费缴纳
# ═══════════════════════════════════════════════════════════════

@party_bp.route('/dues')
@login_required
def dues_list():
    return render_template('party/dues.html')


@party_bp.route('/api/dues/list')
@login_required
def api_dues_list():
    """党费缴纳年度台账 API"""
    p = request.args
    page = p.get('page', 1, type=int)
    limit = p.get('limit', 20, type=int)
    name = p.get('name', '').strip()
    id_card = p.get('id_card', '').strip()
    status_filter = p.get('status', '').strip()
    now = datetime.now()
    year = p.get('year', type=int) or now.year

    # Get all party members (filtered by name/id_card)
    base = PartyMember.query.order_by(PartyMember.name.asc())
    if name:
        base = base.filter(PartyMember.name.like(f'%{name}%'))
    if id_card:
        base = base.filter(PartyMember.id_card_hash == sha256_hash(id_card.strip().upper()))

    all_members = base.all()

    # Build rows with all 12 months data
    raw_rows = []
    for member in all_members:
        months_data = []
        yearly_income_total = 0
        year_total_calc = 0
        year_total_paid = 0
        year_arrears = 0
        has_any_fee = False

        for m in range(1, 13):
            fee = PartyFee.query.filter_by(
                member_id=member.id,
                fee_year=year,
                fee_month=m
            ).first()

            if fee:
                mi = fee.monthly_income or 0
                calc = calc_dues(mi)
                paid = fee.paid_amount if fee.paid_amount else 0
                arrears = round(calc - paid, 2)
                if arrears < 0:
                    arrears = 0
                status = 'paid' if paid >= calc else 'unpaid'
                has_any_fee = True
            else:
                mi = 0
                calc = 0
                paid = 0
                arrears = calc
                status = 'none'

            months_data.append({
                'month': m,
                'monthly_income': mi,
                'calc': calc,
                'paid': paid,
                'arrears': arrears,
                'status': status,
            })
            yearly_income_total += mi
            year_total_calc += calc
            year_total_paid += paid
            year_arrears += arrears

        year_arrears = round(year_arrears, 2)
        average_monthly_income = round(yearly_income_total / 12, 2)

        # 054-A#9：超期三态 —— 存在「当前月之前」的欠缴即 overdue，否则 arrears（修正原 dead branch）
        # 054-R：过去月判定须含年份 —— 只比 month 会让「查上一年度」的 10~12 月误判为 arrears
        has_arrears = year_arrears > 0
        if year < now.year:
            past_month_upto = 13          # 上一年度：整年皆过去
        elif year == now.year:
            past_month_upto = now.month   # 当年：仅当前月之前
        else:
            past_month_upto = 1           # 未来年度：无过去月
        past_arrears = any(md['arrears'] > 0 and md['month'] < past_month_upto for md in months_data)
        if not has_arrears:
            overall_status = 'normal'
        elif past_arrears:
            overall_status = 'overdue'
        else:
            overall_status = 'arrears'

        # 054-A#8：收入分段（连续同额月合并）
        income_segments = []
        for md in months_data:
            inc = md['monthly_income']
            if income_segments and income_segments[-1]['income'] == inc:
                income_segments[-1]['to_month'] = md['month']
            else:
                income_segments.append({'income': inc, 'from_month': md['month'], 'to_month': md['month']})

        raw_rows.append({
            'id': member.id,
            'name': member.name or '',
            'id_card': _decrypt(member, 'id_card'),
            'branch': member.branch or '',
            'monthly_income': average_monthly_income,
            'months': months_data,
            'income_segments': income_segments,
            'year_total_calc': round(year_total_calc, 2),
            'year_total_paid': round(year_total_paid, 2),
            'year_arrears': year_arrears,
            'overall_status': overall_status,
        })

    # Apply status filter on computed status
    if status_filter == 'normal':
        raw_rows = [r for r in raw_rows if r['overall_status'] == 'normal']
    elif status_filter == 'arrears':
        raw_rows = [r for r in raw_rows if r['overall_status'] == 'arrears']
    elif status_filter == 'overdue':
        raw_rows = [r for r in raw_rows if r['overall_status'] == 'overdue']

    total = len(raw_rows)
    total_pages = max(1, (total + limit - 1) // limit)
    start = (page - 1) * limit
    page_items = raw_rows[start:start + limit]

    # Annual stats
    annual_calc = sum(r['year_total_calc'] for r in raw_rows)
    annual_paid = sum(r['year_total_paid'] for r in raw_rows)
    annual_arrears = sum(r['year_arrears'] for r in raw_rows)

    return json_response(success=True, data={
        'data': page_items,
        'total': total,
        'pages': total_pages,
        'page': page,
        'limit': limit,
        'stats': {
            'annual_calculated': round(annual_calc, 2),
            'annual_paid': round(annual_paid, 2),
            'annual_arrears': round(annual_arrears, 2),
        },
        'year': year,
    })


@party_bp.route('/api/dues/save', methods=['POST'])
@login_required
def api_dues_save():
    """保存党费缴纳记录"""
    f = request.form
    member_id = f.get('member_id', type=int)
    year = f.get('fee_year', type=int) or datetime.now().year
    month = f.get('fee_month', type=int) or datetime.now().month
    monthly_income = f.get('monthly_income', type=float) or 0

    if not member_id:
        return json_response(False, '参数不全')

    calculated = calc_dues(monthly_income)
    paid_amount = f.get('paid_amount', type=float) or 0
    arrears = round(calculated - paid_amount, 2)

    if arrears <= 0:
        status = 'normal'
    else:
        status = 'arrears'

    # Upsert
    fee = PartyFee.query.filter_by(
        member_id=member_id,
        fee_year=year,
        fee_month=month
    ).first()

    if fee:
        fee.monthly_income = monthly_income
        fee.amount = calculated
        fee.paid_amount = paid_amount
        fee.status = status
    else:
        fee = PartyFee(
            member_id=member_id,
            fee_year=year,
            fee_month=month,
            monthly_income=monthly_income,
            amount=calculated,
            paid_amount=paid_amount,
            status=status
        )
        db.session.add(fee)

    try:
        db.session.commit()
        log_operation(session.get('username', ''), '党费缴纳', 'save',
                      f'保存党费: {PartyMember.query.get(member_id).name} ({year}年{month}月)')
        return json_response(True, '保存成功', data={
            'id': fee.id,
            'calculated_amount': calculated,
            'paid_amount': paid_amount,
            'arrears': arrears,
            'status': status,
        })
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')


@party_bp.route('/api/dues/income-standard', methods=['POST'])
@login_required
def api_dues_income_standard():
    """054-A#7：从某月起批量设置 12 个月收入标准 + 自动计算应缴。"""
    f = request.form
    member_id = f.get('member_id', type=int)
    income = f.get('monthly_income', type=float)
    start_month = f.get('start_month', type=int) or 1
    year = f.get('fee_year', type=int) or datetime.now().year
    if not member_id or income is None:
        return json_response(False, '参数不全')
    if not 1 <= start_month <= 12:
        return json_response(False, '起始月份须在 1-12 之间')
    n = 0
    for m in range(start_month, 13):
        fee = PartyFee.query.filter_by(member_id=member_id, fee_year=year, fee_month=m).first()
        calc = calc_dues(income)
        if fee:
            fee.monthly_income = income
            fee.amount = calc
        else:
            db.session.add(PartyFee(member_id=member_id, fee_year=year, fee_month=m,
                                    monthly_income=income, amount=calc, paid_amount=0, status='unpaid'))
        n += 1
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return json_response(False, '设置失败')
    log_operation(session.get('username', ''), '党费缴纳', 'save',
                  f'批量设置收入标准: {PartyMember.query.get(member_id).name} ({year}年{start_month}-12月)')
    return json_response(True, f'已设置 {year} 年 {start_month}-12 月收入标准（共 {n} 个月）', data={'count': n})


@party_bp.route('/api/dues/export')
@login_required
def api_dues_export():
    """导出年度党费台账汇总表"""
    name = request.args.get('name', '').strip()
    id_card = request.args.get('id_card', '').strip()
    status_filter = request.args.get('status', '').strip()
    now = datetime.now()
    year = request.args.get('year', type=int) or now.year

    base = PartyMember.query.order_by(PartyMember.name.asc())
    if name:
        base = base.filter(PartyMember.name.like(f'%{name}%'))
    if id_card:
        base = base.filter(PartyMember.id_card_hash == sha256_hash(id_card.strip().upper()))

    members = base.all()

    def calc_dues(income):
        if not income or income <= 0:
            return 0
        if income <= 3000:
            return round(income * 0.005, 2)
        elif income <= 5000:
            return round(income * 0.01, 2)
        elif income <= 10000:
            return round(income * 0.015, 2)
        else:
            return min(round(income * 0.02, 2), 1000)

    # 年度台账表头：序号、姓名、身份证号、平均月收入、1月~12月实缴、年应缴、年实缴、年欠缴、状态
    headers = ['序号', '党员姓名', '身份证号', '平均月收入']
    for m in range(1, 13):
        headers.append(f'{m}月实缴')
    headers += ['年应缴', '年实缴', '年欠缴', '状态']

    rows = []
    for idx, m in enumerate(members, 1):
        month_paids = []
        yearly_income_total = 0
        year_calc = 0
        year_paid = 0

        for mon in range(1, 13):
            fee = PartyFee.query.filter_by(
                member_id=m.id, fee_year=year, fee_month=mon
            ).first()
            if fee:
                mi = fee.monthly_income or 0
                calc = calc_dues(mi)
                paid = fee.paid_amount if fee.paid_amount else 0
                month_paids.append(paid)
                yearly_income_total += mi
                year_calc += calc
                year_paid += paid
            else:
                month_paids.append(0)

        year_arrears = round(year_calc - year_paid, 2)
        if year_arrears < 0:
            year_arrears = 0

        if year_arrears <= 0 and any(p > 0 for p in month_paids):
            status_label = '正常'
        elif year_arrears > 0 and any(p > 0 for p in month_paids):
            status_label = '欠缴'
        else:
            status_label = '超期'
        status_value = 'normal' if status_label == '正常' else 'arrears' if status_label == '欠缴' else 'overdue'
        if status_filter and status_filter != status_value:
            continue

        average_monthly_income = round(yearly_income_total / 12, 2)
        row = [idx, m.name or '', _decrypt(m, 'id_card'), average_monthly_income]
        row += month_paids
        row += [round(year_calc, 2), round(year_paid, 2), year_arrears, status_label]
        rows.append(row)

    return export_excel(headers, rows, f'{year}年度党费台账.xlsx')


# ═══════════════════════════════════════════════════════════════
# 党务活动台账（083A：三类活动固定单表多类型 + 民主评议逐人）
#
# 结构真源：models/party_activity.py（m032 建表）；类型/等次取值只认 dict_items
# （utils/dict_seed.DICT_SEEDS「党务活动类型」「民主评议等次」，非法值语义失败不落库）。
# 三类活动 = 党员教育 / 组织生活 / 党风廉政（立项规划 §三.083A「三合一」）；
# 民主评议不占类型枚举，逐人等次落 party_activity_review（年度+党员唯一，upsert）。
# 统计走 metric_key（party_activity / party_review_grade），不新写 /api/stats。
# ═══════════════════════════════════════════════════════════════

ACTIVITY_DICT_CATEGORIES = ('党务活动类型', '民主评议等次')


def _activity_query():
    """列表/导出共用筛选：年度 × 支部 × 类型（+ 主题关键字）。"""
    query = PartyActivity.query
    p = request.args
    year = p.get('year', type=int)
    if year:
        query = query.filter(PartyActivity.activity_date >= date(year, 1, 1),
                             PartyActivity.activity_date <= date(year, 12, 31))
    branch = p.get('branch', '').strip()
    if branch:
        query = query.filter(PartyActivity.branch == branch)
    atype = p.get('activity_type', '').strip()
    if atype:
        query = query.filter(PartyActivity.activity_type == atype)
    keyword = p.get('keyword', '').strip()
    if keyword:
        query = query.filter(PartyActivity.title.like(f'%{keyword}%'))
    return query


def _activity_to_dict(item):
    return {
        'id': item.id,
        'activity_type': item.activity_type or '',
        'title': item.title or '',
        'activity_date': item.activity_date.strftime('%Y-%m-%d') if item.activity_date else '',
        'host': item.host or '',
        'expected_count': item.expected_count if item.expected_count is not None else 0,
        'actual_count': item.actual_count if item.actual_count is not None else 0,
        'branch': item.branch or '',
        'content': item.content or '',
        'attachment_path': item.attachment_path or '',
        'remark': item.remark or '',
        'created_by': item.created_by or '',
    }


def _review_to_dict(review, member_name=''):
    return {
        'id': review.id,
        'year': review.year,
        'party_member_id': review.party_member_id,
        'member_name': member_name,
        'grade': review.grade or '',
        'review_date': review.review_date.strftime('%Y-%m-%d') if review.review_date else '',
        'remark': review.remark or '',
    }


@party_bp.route('/activity')
@login_required
def activity_page():
    return render_template('party/activity.html')


@party_bp.route('/activity/api/options')
@login_required
def activity_api_options():
    """下拉数据源：类型/等次字典 + 支部去重列表 + 在册党员名单（评议录入用）。"""
    from utils.dict_enum import get_enum
    branches = [r[0] for r in db.session.query(PartyMember.branch)
                .filter(PartyMember.branch.isnot(None), PartyMember.branch != '')
                .distinct().order_by(PartyMember.branch).all()]
    # 在册名单：按 079 枚举单一真源派生——在册 = 「党员在册状态」去掉与旧「人员类别」
    # 交集（已转出，m025 同法）后的取值集合；party_members 零改动（复用 080 四维）
    from utils.dict_enum import get_enum
    roll = set(get_enum('党员在册状态'))
    in_roll_values = roll - (roll & set(get_enum('人员类别')))
    members = [{'id': m.id, 'name': m.name or '', 'branch': m.branch or ''}
               for m in PartyMember.query.filter(PartyMember.party_status.in_(in_roll_values))
               .order_by(PartyMember.name).all()]
    return json_response(success=True, data={
        'dicts': {name: list(get_enum(name)) for name in ACTIVITY_DICT_CATEGORIES},
        'branches': branches,
        'members': members,
    })


@party_bp.route('/activity/api/list')
@login_required
def activity_api_list():
    page = max(request.args.get('page', 1, type=int), 1)
    limit = min(max(request.args.get('limit', 20, type=int), 1), 100)
    query = _activity_query().order_by(PartyActivity.activity_date.desc(),
                                       PartyActivity.id.desc())
    items, total, _pages = paginate_query(query, page, limit)
    return json_response(success=True,
                         data=[_activity_to_dict(item) for item in items], total=total)


@party_bp.route('/activity/api/detail/<int:aid>')
@login_required
def activity_api_detail(aid):
    item = PartyActivity.query.get(aid)
    if not item:
        return json_response(False, '记录不存在')
    return json_response(success=True, data=_activity_to_dict(item))


@party_bp.route('/activity/api/save', methods=['POST'])
@login_required
def activity_api_save():
    """活动保存：必填 → 枚举（只认字典）→ 日期 → 应到实到非负整数。

    枚举非法 = 语义失败（success=False，不落库，084 先例）；实到 ≤ 应到不强制
    （缺席合法，列席超应到亦允许）。附件路径本任务为占位列，不开放录入（092 承载）。
    """
    from utils.dict_enum import get_enum
    f = request.form
    aid = f.get('id', type=int)
    item = PartyActivity.query.get(aid) if aid else None
    if aid and not item:
        return json_response(False, '记录不存在')

    activity_type = f.get('activity_type', '').strip()
    title = f.get('title', '').strip()
    raw_date = f.get('activity_date', '').strip()
    if not activity_type:
        return json_response(False, '活动类型不能为空')
    if activity_type not in get_enum('党务活动类型'):
        return json_response(False, '活动类型取值不合法')
    if not title:
        return json_response(False, '活动主题不能为空')
    if not raw_date:
        return json_response(False, '活动日期不能为空')
    try:
        activity_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
    except ValueError:
        return json_response(False, '活动日期格式应为 YYYY-MM-DD')

    def _nonneg_int(name):
        raw = f.get(name, '').strip()
        if not raw:
            return 0
        try:
            return int(raw)
        except ValueError:
            return -1

    expected = _nonneg_int('expected_count')
    actual = _nonneg_int('actual_count')
    if expected < 0 or actual < 0:
        return json_response(False, '应到/实到须为非负整数')

    if not item:
        item = PartyActivity(created_by=session.get('username', '') or None)
        db.session.add(item)
    item.activity_type = activity_type
    item.title = title
    item.activity_date = activity_date
    item.host = f.get('host', '').strip()
    item.expected_count = expected
    item.actual_count = actual
    item.branch = f.get('branch', '').strip()
    item.content = f.get('content', '').strip()
    item.remark = f.get('remark', '').strip()
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return json_response(False, f'保存失败: {exc}')

    # R-103-05：活动增改后失效聚合缓存（metric_key=party_activity cache_ttl=45s，
    # 不失效则页面统计行最长滞后 45s——走查实证「录 3 条仍显 1 场」根因之一）
    from utils.agg_cache import invalidate
    invalidate()
    log_operation(session.get('username', ''), '党员',
                  'edit' if aid else 'add',
                  f'党务活动保存: {title}(id={item.id})')
    return json_response(True, '保存成功', data={'id': item.id})


@party_bp.route('/activity/api/delete/<int:aid>', methods=['POST'])
@login_required
def activity_api_delete(aid):
    """删除活动：评议子表按年度挂靠（年度+党员唯一），不随单场活动级联删除。"""
    item = PartyActivity.query.get(aid)
    if not item:
        return json_response(False, '记录不存在')
    title = item.title or str(item.id)
    db.session.delete(item)
    db.session.commit()
    from utils.agg_cache import invalidate   # R-103-05：删除同口径失效聚合缓存
    invalidate()
    log_operation(session.get('username', ''), '党员', 'delete', f'删除党务活动: {title}')
    return json_response(True, '已删除')


@party_bp.route('/activity/api/export')
@login_required
def activity_api_export():
    """导出台账（与列表同筛选；无敏感列，走通用脱敏管道）。"""
    query = _activity_query().order_by(PartyActivity.activity_date.desc(),
                                       PartyActivity.id.desc())
    h = ['活动类型', '活动主题', '活动日期', '主持人', '应到人数', '实到人数', '所在党支部', '活动内容', '备注']
    rows = [[item.activity_type or '', item.title or '',
             item.activity_date.strftime('%Y-%m-%d') if item.activity_date else '',
             item.host or '',
             item.expected_count if item.expected_count is not None else 0,
             item.actual_count if item.actual_count is not None else 0,
             item.branch or '', item.content or '', item.remark or '']
            for item in query.all()]
    return export_excel(h, rows, '党务活动台账.xlsx')


@party_bp.route('/activity/api/reviews')
@login_required
def activity_api_reviews():
    """民主评议逐人记录（按年度）；等次分布由前端对返回行就地计数（= 逐人求和）。"""
    year = request.args.get('year', type=int)
    if not year:
        return json_response(False, '请选择评议年度')
    rows = (PartyActivityReview.query.filter_by(year=year)
            .order_by(PartyActivityReview.id).all())
    names = {m.id: (m.name or '') for m in PartyMember.query.filter(
        PartyMember.id.in_([r.party_member_id for r in rows])).all()} if rows else {}
    return json_response(success=True,
                         data=[_review_to_dict(r, names.get(r.party_member_id, ''))
                               for r in rows])


@party_bp.route('/activity/api/review-save', methods=['POST'])
@login_required
def activity_api_review_save():
    """民主评议批量逐人录入（upsert）：year + member_ids/grades 平行数组。

    全量前置校验后才写库（任一非法整批失败不落库）；(year, party_member_id) 唯一，
    重复提交更新等次/日期，不产生重复行。等次只认 dict_items「民主评议等次」。
    """
    from utils.dict_enum import get_enum
    f = request.form
    year = f.get('year', type=int)
    if not year:
        return json_response(False, '请选择评议年度')
    member_ids = [v for v in f.getlist('member_ids') if str(v).strip()]
    grades = [str(v).strip() for v in f.getlist('grades')]
    if not member_ids:
        return json_response(False, '请至少选择一名党员')
    if len(member_ids) != len(grades):
        return json_response(False, '党员与等次数量不一致')

    valid_grades = set(get_enum('民主评议等次'))
    for mid, grade in zip(member_ids, grades):
        if not grade:
            return json_response(False, '等次不能为空')
        if grade not in valid_grades:
            return json_response(False, f'评议等次「{grade}」取值不合法')
        if not PartyMember.query.get(mid):
            return json_response(False, f'党员不存在(id={mid})')

    raw_review_date = f.get('review_date', '').strip()
    review_date = None
    if raw_review_date:
        try:
            review_date = datetime.strptime(raw_review_date, '%Y-%m-%d').date()
        except ValueError:
            return json_response(False, '评议日期格式应为 YYYY-MM-DD')

    saved = 0
    for mid, grade in zip(member_ids, grades):
        row = PartyActivityReview.query.filter_by(year=year, party_member_id=mid).first()
        if row:
            row.grade = grade
            if review_date:
                row.review_date = review_date
        else:
            db.session.add(PartyActivityReview(
                year=year, party_member_id=mid, grade=grade,
                review_date=review_date,
                created_by=session.get('username', '') or None))
        saved += 1
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return json_response(False, f'保存失败: {exc}')
    log_operation(session.get('username', ''), '党员', 'edit',
                  f'民主评议录入 {year} 年度 {saved} 人')
    return json_response(True, f'已保存 {saved} 条评议记录')


# ═══════════════════════════════════════════════════════════════
# 党员发展（083B：五阶段名册 + 阶段迁移状态机）
#
# 结构真源：models/party_development.py（m041 建表）；阶段取值只认 dict_items
# 「党员发展阶段」（utils/dict_seed.DICT_SEEDS）。状态机范式照 086 信访：
# 候选值 → 校验 → 原子落库，非法迁移（跳级/回退/同态）HTTP 409；
# 每次合法迁移写日志（from/to/节点日期/操作人）。
# 转正回写：迁移到「正式党员」且显式确认时，仅允许改 party_members 的
# party_status 与 member_identity 两列（080 四维保护），改前快照留日志。
# 身份证/联系电话接 R12 字段级加密；列表/导出恒打码，详情仅 view_sensitive 明文。
# ═══════════════════════════════════════════════════════════════

DEVELOPMENT_DICT_CATEGORIES = ('党员发展阶段',)

# 状态机（唯一真源）：阶段序从字典派生（R2：routes 内零枚举字面量），仅前滚一格；
# 跳级/回退/同态重复均为非法迁移。节点日期列按字典序一一对应（列名是结构标识）。
_DEV_STAGE_DATE_ATTRS = ('apply_date', 'activist_date', 'develop_date',
                         'probate_date', 'full_member_date')


def _dev_stages():
    from utils.dict_enum import get_enum
    return tuple(get_enum('党员发展阶段'))


def _dev_transitions():
    """合法迁移集合：字典相邻两项，仅前滚一格。"""
    stages = _dev_stages()
    return set(zip(stages, stages[1:]))


def _dev_stage_date_column(stage):
    """目标阶段 → 节点日期列；按字典序映射，避免阶段字面量散落。"""
    stages = _dev_stages()
    if stage not in stages:
        return None
    index = stages.index(stage)
    return _DEV_STAGE_DATE_ATTRS[index] if index < len(_DEV_STAGE_DATE_ATTRS) else None


def _dev_final_stage():
    """终态阶段（字典末项）：转正回写判定用。"""
    stages = _dev_stages()
    return stages[-1] if stages else ''

# R12：身份证 / 联系电话（两套列各一组，禁跨模型复用——086 先例）
_DEV_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}
_dev_decrypt, _dev_set_enc, _dev_clause = make_sensitive_helpers(
    PartyDevelopment, _DEV_SENS_FIELDS, key_holder)


def _mask_dev_detail():
    """详情是否打码：列表/导出恒打码，详情仅 view_sensitive 用户取明文（086 同法）。"""
    from flask import g
    from utils.masking import user_can_view_sensitive
    return not user_can_view_sensitive(g.get('current_user'))


def _dev_in_roll_status():
    """回写目标在册状态：党员在册状态 −（∩人员类别）派生（079/m025 同法，零字面量）。"""
    from utils.dict_enum import get_enum
    roll = set(get_enum('党员在册状态'))
    in_roll = roll - (roll & set(get_enum('人员类别')))
    return next(iter(in_roll), '')


def _dev_operator():
    """操作人：优先 g.current_user（测试夹具只写 user_id，session['username'] 缺失），回退 session。"""
    from flask import g
    user = g.get('current_user')
    return (getattr(user, 'username', None) or session.get('username', '') or '')


def _dev_to_dict(item, mask=True, with_logs=False):
    from utils.masking import mask_id_card, mask_phone
    id_card = _dev_decrypt(item, 'id_card')
    phone = _dev_decrypt(item, 'phone')
    if mask:
        id_card, phone = mask_id_card(id_card), mask_phone(phone)

    def _iso(col):
        value = getattr(item, col)
        return value.strftime('%Y-%m-%d') if value else ''

    data = {
        'id': item.id,
        'name': item.name or '',
        'id_card': id_card,
        'gender': item.gender or '',
        'phone': phone,
        'branch': item.branch or '',
        'stage': item.stage or '',
        'apply_date': _iso('apply_date'),
        'activist_date': _iso('activist_date'),
        'develop_date': _iso('develop_date'),
        'probate_date': _iso('probate_date'),
        'full_member_date': _iso('full_member_date'),
        'introducer': item.introducer or '',
        'remark': item.remark or '',
        'created_by': item.created_by or '',
    }
    if with_logs:
        data['logs'] = [{
            'id': log.id,
            'from_stage': log.from_stage or '',
            'to_stage': log.to_stage or '',
            'stage_date': log.stage_date.strftime('%Y-%m-%d') if log.stage_date else '',
            'operator': log.operator or '',
            'writeback_before': log.writeback_before or '',
            'created_at': log.created_at.strftime('%Y-%m-%d %H:%M') if log.created_at else '',
        } for log in item.logs.all()]
    return data


def _dev_normalize_id_card(raw, existing_hash=None):
    """身份证 15/18 位校验 + 等值 hash（R-084-01 同因：hash 为 NULL 时不参与唯一约束）。

    R4：18 位末位允许 X（与 import_engine / R12 hash 大写口径一致），不再一律 isdigit。
    """
    value = (raw or '').strip().upper()
    if not value:
        return '', None
    if len(value) == 15:
        valid = value.isdigit()
    elif len(value) == 18:
        valid = value[:17].isdigit() and (value[17].isdigit() or value[17] == 'X')
    else:
        valid = False
    if not valid:
        return '', 'invalid'
    return value, sha256_hash(value)


@party_bp.route('/development')
@login_required
def development_page():
    return render_template('party/development.html')


@party_bp.route('/development/api/options')
@login_required
def development_api_options():
    """下拉数据源：阶段字典（含推进顺序）+ 支部去重列表。"""
    from utils.dict_enum import get_enum
    branches = [r[0] for r in db.session.query(PartyMember.branch)
                .filter(PartyMember.branch.isnot(None), PartyMember.branch != '')
                .distinct().order_by(PartyMember.branch).all()]
    return json_response(success=True, data={
        'dicts': {name: list(get_enum(name)) for name in DEVELOPMENT_DICT_CATEGORIES},
        'branches': branches,
    })


@party_bp.route('/development/api/list')
@login_required
def development_api_list():
    page = max(request.args.get('page', 1, type=int), 1)
    limit = min(max(request.args.get('limit', 20, type=int), 1), 100)
    query = PartyDevelopment.query
    stage = request.args.get('stage', '').strip()
    if stage:
        query = query.filter(PartyDevelopment.stage == stage)
    branch = request.args.get('branch', '').strip()
    if branch:
        query = query.filter(PartyDevelopment.branch == branch)
    keyword = request.args.get('keyword', '').strip()
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(db.or_(PartyDevelopment.name.like(like),
                                    PartyDevelopment.introducer.like(like),
                                    PartyDevelopment.id_card_hash == sha256_hash(
                                        keyword.strip().upper())))
    query = query.order_by(PartyDevelopment.updated_at.desc(), PartyDevelopment.id.desc())
    items, total, _pages = paginate_query(query, page, limit)
    return json_response(success=True,
                         data=[_dev_to_dict(item) for item in items], total=total)


@party_bp.route('/development/api/detail/<int:dev_id>')
@login_required
def development_api_detail(dev_id):
    item = PartyDevelopment.query.get(dev_id)
    if not item:
        return json_response(False, '记录不存在')
    return json_response(success=True, data=_dev_to_dict(
        item, mask=_mask_dev_detail(), with_logs=True))


@party_bp.route('/development/api/save', methods=['POST'])
@login_required
def development_api_save():
    """发展记录保存（新增/编辑基本信息）：id_card_hash 唯一（一人一条）。

    阶段不在此接口修改：新增固定「申请人」（阶段只经 /transition 前滚），
    编辑仅基本信息（姓名/身份证/电话/性别/支部/介绍人/申请日期/备注）。
    """
    from utils.dict_enum import get_enum
    from utils.masking import mask_id_card
    from sqlalchemy.exc import IntegrityError
    f = request.form
    dev_id = f.get('id', type=int)
    item = PartyDevelopment.query.get(dev_id) if dev_id else None
    if dev_id and not item:
        return json_response(False, '记录不存在')

    name = f.get('name', '').strip()
    if not name:
        return json_response(False, '姓名不能为空')
    # R5：无权用户详情回显打码值（含 '*'）→ 该列跳过写入，避免打码串覆盖明文/密文
    id_card_input = f.get('id_card', '').strip()
    id_card_masked = '*' in id_card_input
    if id_card_masked:
        id_card_raw, id_card_hash = '', None
    else:
        id_card_raw, id_card_hash = _dev_normalize_id_card(id_card_input)
    if id_card_hash == 'invalid':
        return json_response(False, '身份证号格式不正确（15 位数字或 18 位，末位可为 X）')

    if id_card_hash:
        dup = PartyDevelopment.query.filter(
            PartyDevelopment.id_card_hash == id_card_hash,
            PartyDevelopment.id != (item.id if item else 0)).first()
        if dup:
            return json_response(False, '该身份证已存在发展记录，请改为编辑')

    raw_apply = f.get('apply_date', '').strip()
    apply_date = None
    if raw_apply:
        try:
            apply_date = datetime.strptime(raw_apply, '%Y-%m-%d').date()
        except ValueError:
            return json_response(False, '申请入党日期格式应为 YYYY-MM-DD')

    if not item:
        initial_stage = list(get_enum('党员发展阶段'))[0]  # 「申请人」（字典首项，非字面量）
        item = PartyDevelopment(stage=initial_stage,
                                created_by=session.get('username', '') or None)
        db.session.add(item)
    item.name = name
    if not id_card_masked:
        _dev_set_enc(item, 'id_card', id_card_raw)
    phone_input = f.get('phone', '').strip()
    if '*' not in phone_input:
        _dev_set_enc(item, 'phone', phone_input)
    item.gender = f.get('gender', '').strip()
    item.branch = f.get('branch', '').strip()
    item.introducer = f.get('introducer', '').strip()
    item.remark = f.get('remark', '').strip()
    if apply_date:
        item.apply_date = apply_date
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return json_response(False, '该身份证已存在发展记录，请改为编辑')
    except Exception as exc:
        db.session.rollback()
        return json_response(False, f'保存失败: {exc}')

    log_operation(session.get('username', ''), '党员', 'edit' if dev_id else 'add',
                  f'党员发展保存: {name}(id={item.id})')
    return json_response(True, '保存成功', data={'id': item.id})


@party_bp.route('/development/api/transition', methods=['POST'])
@login_required
def development_api_transition():
    """阶段迁移（唯一入口）：仅前滚一格，非法迁移 HTTP 409。

    - to_stage 须在字典「党员发展阶段」内（否则 400 语义失败）；
    - (current, to_stage) 不在状态机 → 409（跳级/回退/同态重复）；
    - 目标阶段节点日期必填（缺则 400）；
    - 迁移到「正式党员」且 confirm_writeback 显式确认 → 回写 party_members 仅两列
      （party_status/member_identity），改前快照留日志；无确认不回写；
    - 校验全部前置完成后原子落库（stage + 日期列 + 日志 + 回写一次 commit）。
    """
    from utils.dict_enum import get_enum
    f = request.form
    dev_id = f.get('id', type=int)
    to_stage = f.get('to_stage', '').strip()
    item = PartyDevelopment.query.get(dev_id) if dev_id else None
    if not item:
        return json_response(False, '记录不存在')
    if not to_stage or to_stage not in get_enum('党员发展阶段'):
        return json_response(False, '目标阶段取值不合法')

    current = item.stage or list(get_enum('党员发展阶段'))[0]
    if (current, to_stage) not in _dev_transitions():
        return jsonify({'success': False,
                        'msg': f'非法阶段迁移: {current}→{to_stage}（仅允许前滚一格）'}), 409

    raw_stage_date = f.get('stage_date', '').strip()
    if not raw_stage_date:
        return json_response(False, '请填写该阶段节点日期')
    try:
        stage_date = datetime.strptime(raw_stage_date, '%Y-%m-%d').date()
    except ValueError:
        return json_response(False, '节点日期格式应为 YYYY-MM-DD')

    want_writeback = f.get('confirm_writeback', '').strip() in ('1', 'on', 'true')
    writeback_done = False
    writeback_before = None
    member = None
    final_stage = _dev_final_stage()
    if to_stage == final_stage and item.id_card_hash:
        member = PartyMember.query.filter_by(id_card_hash=item.id_card_hash).first()
        if member and want_writeback:
            writeback_before = (f'party_status={member.party_status or ""};'
                                f'member_identity={member.member_identity or ""}')

    # ── 校验全部通过，原子落库 ──
    stage_column = _dev_stage_date_column(to_stage)
    if not stage_column:
        return json_response(False, '目标阶段未配置节点日期列')
    item.stage = to_stage
    setattr(item, stage_column, stage_date)
    if member and want_writeback:
        # 仅两列（080 四维保护；member_identity=to_stage 即字典值，party_status=在册派生值）
        member.member_identity = to_stage
        member.party_status = _dev_in_roll_status()
        writeback_done = True
    db.session.add(PartyDevelopmentLog(
        dev_id=item.id, from_stage=current, to_stage=to_stage,
        stage_date=stage_date, operator=_dev_operator() or None,
        writeback_before=writeback_before))
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return json_response(False, f'迁移失败: {exc}')

    log_operation(session.get('username', ''), '党员', 'transition',
                  f'党员发展阶段迁移: {item.name} {current}→{to_stage}'
                  + ('（含转正回写）' if writeback_done else ''))
    return json_response(True, f'阶段已推进至「{to_stage}」', data={
        'id': item.id,
        'stage': item.stage,
        'stage_date': stage_date.strftime('%Y-%m-%d'),
        'writeback': 'done' if writeback_done else (
            'skipped' if to_stage == final_stage else 'not_applicable'),
    })


@party_bp.route('/development/api/export')
@login_required
def development_api_export():
    """导出名册：一人一行含五阶段日期（未到阶段留空）；敏感列走通用脱敏管道。"""
    query = PartyDevelopment.query
    stage = request.args.get('stage', '').strip()
    if stage:
        query = query.filter(PartyDevelopment.stage == stage)
    branch = request.args.get('branch', '').strip()
    if branch:
        query = query.filter(PartyDevelopment.branch == branch)
    query = query.order_by(PartyDevelopment.updated_at.desc(), PartyDevelopment.id.desc())

    h = ['姓名', '身份证号', '性别', '联系电话', '所属党支部', '当前阶段',
         '申请入党日期', '积极分子确定日期', '发展对象确定日期', '预备党员日期', '转正日期',
         '培养联系人/入党介绍人', '备注']

    def _iso(col):
        return col.strftime('%Y-%m-%d') if col else ''

    rows = [[item.name or '', _dev_decrypt(item, 'id_card'), item.gender or '',
             _dev_decrypt(item, 'phone'), item.branch or '', item.stage or '',
             _iso(item.apply_date), _iso(item.activist_date), _iso(item.develop_date),
             _iso(item.probate_date), _iso(item.full_member_date),
             item.introducer or '', item.remark or '']
            for item in query.all()]
    return export_excel(h, rows, '党员发展名册.xlsx')
