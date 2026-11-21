from flask import Blueprint, render_template, request, session, send_file
from models import db, Resident, DeathReport
from models.death_report import CascadeService, _death_report_id_hash
from utils.decorators import login_required, permission_required
from utils.helpers import json_response, log_operation
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers
from datetime import datetime, date, timedelta
import io
import os

death_bp = Blueprint('death_report', __name__, template_folder='../templates')
DEFAULT_VILLAGE_TITLE = 'XX乡镇XX村（社区）'

# R12 字段级加密管线（复用 utils.sensitive_fields，消除死亡登记台账明文孤岛）
_DEATH_SENS_FIELDS = {'id_card': ('id_card_enc', 'id_card_hash', None)}
_decrypt, _set_enc, _clause = make_sensitive_helpers(DeathReport, _DEATH_SENS_FIELDS, key_holder)


def _resident_id_card_decrypt(r):
    """居民档案身份证明文（Resident 已字段级加密，原列 NULL，解密 _enc 供展示）。"""
    enc = getattr(r, 'id_card_enc', None)
    if enc:
        try:
            return key_holder.decrypt(enc)
        except Exception:
            return ''
    return getattr(r, 'id_card', '') or ''


def _form_int(form, key):
    """form/JSON 双载荷安全取 int（021 R-26）。

    MultiDict.get(key, type=int) 仅 form 通道可用；JSON 分支下 dict 不接受
    type= 关键字 → TypeError 500（018 D-08 同型）。统一走本 helper 消除载荷敏感性。
    """
    v = form.get(key)
    if v is None or v == '':
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@death_bp.route('/')
@login_required
def list():
    """Render death report page with on-page template."""
    return render_template('death_report/list.html')


# ─── API: Query deceased residents by year/month ───────────────────

@death_bp.route('/api/deceased')
@login_required
def api_deceased():
    """Query residents with status='deceased', filtered by year/month of death."""
    year = request.args.get('year', '').strip()
    month = request.args.get('month', '').strip()

    query = Resident.query.filter(Resident.status == 'deceased')

    if year:
        try:
            y = int(year)
            if month:
                m = int(month)
                query = query.filter(
                    db.extract('year', Resident.status_date) == y,
                    db.extract('month', Resident.status_date) == m
                )
            else:
                query = query.filter(db.extract('year', Resident.status_date) == y)
        except ValueError:
            pass

    # Also query DeathReport records for this period
    dr_query = DeathReport.query
    if year:
        try:
            y = int(year)
            if month:
                m = int(month)
                dr_query = dr_query.filter(
                    db.extract('year', DeathReport.death_date) == y,
                    db.extract('month', DeathReport.death_date) == m
                )
            else:
                dr_query = dr_query.filter(db.extract('year', DeathReport.death_date) == y)
        except ValueError:
            pass

    # ── 加载所有民政对象台账，按身份证号哈希索引（R12：原列已加密置 NULL，改走 _hash 列）──
    from models import Dibao, Disabled, SpecialSupport, UnsupportedChild

    # 优先级顺序（索引越小优先级越高）：特困供养 > 事实无人抚养儿童 > 残疾人 > 低保户
    dibao_by_idcard = {}      # id_card_hash -> 低保类型
    disabled_by_idcard = {}   # id_card_hash -> 残疾详情
    special_by_idcard = {}    # household_id_card_hash -> 供养方式
    child_by_idcard = {}      # id_card_hash -> 固定标识

    for d in Dibao.query.all():
        cid = (d.id_card_hash or '').strip()
        if cid and cid not in dibao_by_idcard:
            dibao_by_idcard[cid] = d.dibao_type or ''

    for d in Disabled.query.all():
        cid = (d.id_card_hash or '').strip()
        if cid and cid not in disabled_by_idcard:
            disabled_by_idcard[cid] = d.disability_detail or ''

    for s in SpecialSupport.query.all():
        cid = (s.household_id_card_hash or '').strip()
        if cid and cid not in special_by_idcard:
            special_by_idcard[cid] = s.support_mode or ''

    for c in UnsupportedChild.query.all():
        cid = (c.id_card_hash or '').strip()
        if cid and cid not in child_by_idcard:
            child_by_idcard[cid] = '事实无人抚养儿童'

    def _map_dibao_type(raw_type):
        """dibao_type 编码转中文业务名称"""
        mapping = {
            'A类': '低保户（A类）', 'B类': '低保户（B类）', 'C类': '低保户（C类）',
            'A': '低保户（A类）', 'B': '低保户（B类）', 'C': '低保户（C类）',
        }
        return mapping.get(raw_type.strip(), raw_type) if raw_type else raw_type

    # 优先级判定：依次检查各台账，返回第一个匹配值
    PRIORITY_NAMES = [
        ('特困供养', special_by_idcard),
        ('事实无人抚养儿童', child_by_idcard),
        ('残疾人', disabled_by_idcard),
        ('低保户', dibao_by_idcard),
    ]

    def resolve_person_type(resident):
        """多台账优先查询，按优先级取第一个匹配结果，全部无匹配时取居民户属性兜底"""
        cid = (resident.id_card_hash or '').strip()
        if not cid:
            return resident.household_property or ''
        # 按优先级：特困供养 > 事实无人抚养儿童 > 残疾人 > 低保户
        for name, tbl in PRIORITY_NAMES:
            if cid in tbl:
                val = tbl[cid]
                if name == '低保户':
                    return _map_dibao_type(val)
                if name == '残疾人':
                    return val  # 残疾详情
                if name == '特困供养':
                    return val  # 供养方式
                return val
        return resident.household_property or ''

    def resolve_person_type_by_idcard_hash(id_hash, fallback=''):
        cid = (id_hash or '').strip()
        if not cid:
            return fallback
        for name, tbl in PRIORITY_NAMES:
            if cid in tbl:
                val = tbl[cid]
                if name == '低保户':
                    return _map_dibao_type(val)
                if name == '残疾人':
                    return val
                if name == '特困供养':
                    return val
                return val
        return fallback

    residents = query.order_by(Resident.village_group, Resident.name).all()
    death_reports = dr_query.order_by(DeathReport.death_date.desc()).all()

    now = datetime.now()
    data = []
    seq = 0

    # 1. From resident table (deceased status)
    for r in residents:
        seq += 1
        data.append({
            'seq': seq,
            'name': r.name or '',
            'address': r.address or '',
            'village_group': r.village_group or '',
            'gender': r.gender or '',
            'id_card': _resident_id_card_decrypt(r),
            'death_date': r.status_date.strftime('%Y-%m-%d') if r.status_date else '',
            'person_type': resolve_person_type(r),
            'remark': r.status_reason or '',
        })

    # 2. From existing death_reports (de-duplicate by name+id_card)
    existing_keys = {(d['name'], d['id_card']) for d in data}
    for dr in death_reports:
        dr_id = _decrypt(dr, 'id_card')
        key = (dr.deceased_name or '', dr_id)
        if key not in existing_keys:
            seq += 1
            data.append({
                'seq': seq,
                'id': dr.id,
                'cascade_status': dr.cascade_status or 'pending',
                'confirmed_at': (dr.confirmed_at.strftime('%Y-%m-%d %H:%M:%S')
                                 if dr.confirmed_at else ''),  # 021 R-26：前端 48h 撤回倒计时依据
                'name': dr.deceased_name or '',
                'address': dr.address or '',
                'village_group': dr.village_group or '',
                'gender': dr.gender or '',
                'id_card': dr_id,
                'death_date': dr.death_date.strftime('%Y-%m-%d') if dr.death_date else '',
                'person_type': resolve_person_type_by_idcard_hash(dr.id_card_hash, dr.person_type or ''),
                'remark': dr.remark or '',
            })

    return json_response(success=True, data=data)


# ─── API: Save a death report (from on-page template) ──────────────

# ─── Export: Generate formatted Excel template ─────────────────────

@death_bp.route('/api/export', methods=['POST'])
@login_required
def api_export():
    """Export death report as formatted Excel (landscape A4, 9 columns)."""
    req_data = request.json or {}

    year = req_data.get('year', '').strip()
    month = req_data.get('month', '').strip()
    report_unit = req_data.get('report_unit', '').strip()
    village_name = (req_data.get('village_name') or DEFAULT_VILLAGE_TITLE).strip()
    reporter = req_data.get('reporter', '').strip()
    responsible_person = req_data.get('responsible_person', '').strip()
    report_date = req_data.get('report_date', '').strip()
    rows = req_data.get('rows', [])

    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '死亡名单'

    # ── Landscape A4 ──
    ws.page_setup.paperSize = 9  # A4
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = 0.5
    ws.page_margins.right = 0.5
    ws.page_margins.top = 0.5
    ws.page_margins.bottom = 0.5

    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    year_str = year if year else '____'
    month_str = month if month else '__'
    col_count = 9

    # ── Row 1: Title ──
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=col_count)
    cell = ws.cell(row=1, column=1)
    cell.value = f'{village_name}    {year_str}年{month_str}月份死亡名单'
    cell.font = Font(name='宋体', size=14, bold=True)
    cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 32

    # ── Row 2: Header info (填报单位, 填报人, 负责人, 填报日期) ──
    # 分4段：A-C(填报单位), D-E(填报人), F-G(负责人), H-I(填报日期)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=3)
    ws.cell(row=2, column=1, value=f'填报单位：{report_unit}（盖章）').font = Font(name='宋体', size=10)
    ws.cell(row=2, column=1).alignment = Alignment(horizontal='left', vertical='center')
    ws.merge_cells(start_row=2, start_column=4, end_row=2, end_column=5)
    ws.cell(row=2, column=4, value=f'填报人：{reporter}').font = Font(name='宋体', size=10)
    ws.cell(row=2, column=4).alignment = Alignment(horizontal='center', vertical='center')
    ws.merge_cells(start_row=2, start_column=6, end_row=2, end_column=7)
    ws.cell(row=2, column=6, value=f'负责人：{responsible_person}').font = Font(name='宋体', size=10)
    ws.cell(row=2, column=6).alignment = Alignment(horizontal='center', vertical='center')
    ws.merge_cells(start_row=2, start_column=8, end_row=2, end_column=9)
    ws.cell(row=2, column=8, value=f'填报日期：{report_date}').font = Font(name='宋体', size=10)
    ws.cell(row=2, column=8).alignment = Alignment(horizontal='right', vertical='center')
    for col in range(1, col_count + 1):
        ws.cell(row=2, column=col).font = Font(name='宋体', size=10)
        ws.cell(row=2, column=col).border = border

    # ── Row 3: Column headers (9 columns) ──
    headers = ['编号', '姓名', '户籍地址', '所在组', '性别', '身份证号码', '死亡时间', '对象类型', '备注']
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col_idx, value=h)
        cell.font = Font(name='宋体', size=10, bold=True)
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = border
    ws.row_dimensions[3].height = 25

    # ── Data rows ──
    for idx, row in enumerate(rows):
        row_idx = 4 + idx
        vals = [
            idx + 1,
            row.get('name', ''),
            row.get('address', ''),
            row.get('village_group', ''),
            row.get('gender', ''),
            row.get('id_card', ''),
            row.get('death_date', ''),
            row.get('person_type', ''),
            row.get('remark', ''),
        ]
        for col_idx, val in enumerate(vals, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = Font(name='宋体', size=10)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = border
        ws.row_dimensions[row_idx].height = 22

    # ── Column widths (A4 landscape optimized, 9 columns) ──
    col_widths = [5.5, 10, 24, 7, 6, 22, 12, 10, 14]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ── Print settings ──
    ws.print_title_rows = '1:3'

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    log_operation(session.get('username', ''), '死亡上报', 'export',
                  f'导出死亡名单 {len(rows)}条 (年月:{year_str}-{month_str})')

    return send_file(output,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True,
                     download_name=f'{village_name}_{year_str}年{month_str}月份死亡名单.xlsx')


# ─── API: Confirm a death report (R5 级联，T15-1) ──────────────────

@death_bp.route('/api/confirm', methods=['POST'])
@permission_required('special.death_confirm')
def api_confirm():
    """R5 死亡登记确认：单事务级联 6 项注销（T15-1 CascadeService）。

    - 幂等：cascade_status='done' 拒绝重复确认；'failed' 可重跑
    - 事务：run_cascade 成功后统一 commit；失败整体回滚并置 failed（观测标记）
    - 权限：special.death_confirm（民政主任种子，app.py）
    """
    form = request.form if request.form else (request.json or {})
    rid = _form_int(form, 'id')
    if not rid:
        return json_response(False, '参数错误')

    dr = DeathReport.query.get(rid)
    if not dr:
        return json_response(False, '记录不存在')
    if dr.cascade_status == 'done':
        return json_response(False, '该登记已确认，请勿重复操作')

    try:
        result = CascadeService.run_cascade(dr, operator_id=session.get('user_id'))
        db.session.commit()
        log_operation(session.get('username', ''), '死亡上报', 'confirm',
                      f'确认死亡登记并级联注销: {dr.deceased_name}')
        return json_response(True, '确认成功', data=result)
    except Exception as e:
        db.session.rollback()
        try:
            # 回滚后独立小事务标记 failed（供重跑），不掩盖原始错误
            dr.cascade_status = 'failed'
            db.session.commit()
        except Exception:
            db.session.rollback()
        return json_response(False, f'级联失败: {str(e)}')


# ─── API: Revoke a confirmed death report (R5/B2 48h 撤回，T15-2) ──

REVOKE_WINDOW_HOURS = 48  # B2 撤回窗口（与 48h 未确认标红待办机制各自独立）


@death_bp.route('/api/revoke', methods=['POST'])
@permission_required('special.death_confirm')
def api_revoke():
    """R5-E6/E7 + B2：确认后 48h 内撤回，单事务回滚 6 表级联；超时/E22 冲突禁止。

    - 窗口：confirmed_at + 48h，超过拒绝（走人工纠错，标注遗留限制）
    - E22：期间已新补贴发放（amount>0）或对账引用（reconcile_result 已处置）→ 禁止撤回
    - 撤回成功后 cascade_status 重置 pending，可重新确认（log_operation 留痕追溯）
    """
    form = request.form if request.form else (request.json or {})
    rid = _form_int(form, 'id')
    if not rid:
        return json_response(False, '参数错误')

    dr = DeathReport.query.get(rid)
    if not dr:
        return json_response(False, '记录不存在')
    if dr.cascade_status != 'done':
        return json_response(False, '仅已确认的死亡登记可撤回')

    # 48h 窗口校验（B2：两个 48h 独立——撤回窗口作用于 done，未确认标红作用于 pending）
    if not dr.confirmed_at:
        return json_response(False, '确认时间缺失，无法校验撤回窗口')
    deadline = dr.confirmed_at + timedelta(hours=REVOKE_WINDOW_HOURS)
    if datetime.now() > deadline:
        return json_response(False, '超过 48 小时撤回窗口，撤回入口已关闭（遗留限制：走人工纠错）')

    try:
        # E22 冲突检测：期间新发放（本次停发记录为'停发'天然排除；amount>0 排除冲正负额）
        from models.subsidy_project import SubsidyRecord
        resident = Resident.query.filter_by(id_card_hash=_death_report_id_hash(dr)).first()
        if resident:
            new_subsidies = SubsidyRecord.query.filter(
                SubsidyRecord.resident_id == resident.id,
                SubsidyRecord.status == '正常',
                SubsidyRecord.create_time > dr.confirmed_at,
                SubsidyRecord.amount > 0,
            ).count()
            if new_subsidies:
                return json_response(False, f'撤回冲突（E22）：确认后已发生 {new_subsidies} 笔新补贴发放，禁止撤回')

            # E22 冲突检测：对账引用（reconcile_result 已处置，按 id_card_hash 匹配）
            from models.reconcile import ReconcileResult
            ref = ReconcileResult.query.filter(
                ReconcileResult.id_card_hash == _death_report_id_hash(dr),
                ReconcileResult.handle_status == '已处置',
                ReconcileResult.created_at > dr.confirmed_at,
            ).first()
            if ref:
                return json_response(False, '撤回冲突（E22）：确认后该对象已被对账引用，禁止撤回')

        result = CascadeService.revoke_cascade(dr, operator_id=session.get('user_id'))
        db.session.commit()
        log_operation(session.get('username', ''), '死亡上报', 'revoke',
                      f'撤回死亡登记并回滚级联: {dr.deceased_name}')
        return json_response(True, '撤回成功（6 表级联已回滚）', data=result)
    except Exception as e:
        db.session.rollback()
        # 撤回失败不置 failed：保持 done 状态可重试
        return json_response(False, f'撤回失败: {str(e)}')


# ─── T15-3：防误发清单 + 停发告知单（生成/回溯/打印/导出） ──────────

def _can_view_sensitive():
    """当前用户是否可查看明文（view_sensitive 位或 admin）。"""
    from models import User
    from utils.permissions import user_has_special
    user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    if not user:
        return False
    return bool(user.role_id == 1 or user_has_special(user, 'view_sensitive'))


def _resolve_id_card_display(dr, resident_id_card_enc=None):
    """身份证展示：view_sensitive 解密明文；否则脱敏（前6后4，mask_id_card）。

    告知单存 resident 档案密文 id_card_enc；无密文时兜底 dr.id_card 明文。
    """
    from utils.crypto import key_holder
    from utils.masking import mask_id_card
    raw = ''
    if resident_id_card_enc:
        try:
            raw = key_holder.decrypt(resident_id_card_enc)
        except Exception:
            raw = ''
    if not raw:
        raw = _decrypt(dr, 'id_card')
    if _can_view_sensitive():
        return raw
    return mask_id_card(raw) if raw else ''


def _load_notice(rid):
    """按 death_report_id 取当前有效告知单（最新一条）。"""
    from models.cascade import CascadeNotice
    return (CascadeNotice.query
            .filter_by(death_report_id=rid)
            .order_by(CascadeNotice.id.desc()).first())


def _load_checklist(rid):
    """按 death_report_id 取最新防误发清单。"""
    from models.cascade import CascadeChecklist
    return (CascadeChecklist.query
            .filter_by(death_report_id=rid)
            .order_by(CascadeChecklist.id.desc()).first())


@death_bp.route('/<int:rid>/checklist_view')
@login_required
@permission_required('death_report.view')
def checklist_view(rid):
    """T15-3：防误发清单回溯页（可打印/导出留档，PRD 可回溯）。"""
    import json
    dr = DeathReport.query.get(rid)
    if not dr:
        return json_response(False, '记录不存在'), 404
    checklist = _load_checklist(rid)
    affected = {}
    if checklist and checklist.affected_items:
        try:
            affected = json.loads(checklist.affected_items)
        except (ValueError, TypeError):
            affected = {}
    return render_template('death_report/checklist.html',
                           dr=dr, checklist=checklist, affected=affected,
                           village_title=DEFAULT_VILLAGE_TITLE,
                           id_card_display=_resolve_id_card_display(dr))


@death_bp.route('/<int:rid>/notice-print')
@login_required
@permission_required('death_report.view')
def notice_print(rid):
    """T15-3：停发告知单打印模板页（D4，A4 纵向分页）。

    仅渲染不计数；点击「打印」按钮时调 POST /api/notice/print 留痕（B1）。
    """
    import json
    dr = DeathReport.query.get(rid)
    if not dr:
        return json_response(False, '记录不存在'), 404
    notice = _load_notice(rid)
    if not notice:
        return json_response(False, '该死亡登记尚未生成停发告知单'), 404
    project_ids = []
    if notice.project_ids:
        try:
            project_ids = json.loads(notice.project_ids)
        except (ValueError, TypeError):
            project_ids = []
    # 项目名列表 + 操作人姓名（模板展示）
    from models.subsidy_project import SubsidyProject
    project_names = []
    for pid in project_ids:
        proj = SubsidyProject.query.get(pid)
        project_names.append(proj.name if proj else f'项目#{pid}')
    from models import User
    op = User.query.get(notice.operator) if notice.operator else None
    operator_name = op.username if op else ''
    return render_template('death_report/notice_print.html',
                           dr=dr, notice=notice, project_ids=project_ids,
                           project_names=project_names, operator_name=operator_name,
                           village_title=DEFAULT_VILLAGE_TITLE,
                           id_card_display=_resolve_id_card_display(dr, notice.id_card_enc))


@death_bp.route('/api/notice/print', methods=['POST'])
@login_required
@permission_required('death_report.edit')
def api_notice_print():
    """T15-3：停发告知单重打留痕（B1：printed_count+1 + log_operation，detail 无明文）。"""
    form = request.form if request.form else (request.json or {})
    nid = form.get('id', type=int)
    if not nid:
        return json_response(False, '参数错误')
    from models.cascade import CascadeNotice
    notice = CascadeNotice.query.get(nid)
    if not notice:
        return json_response(False, '告知单不存在')
    notice.printed_count = (notice.printed_count or 0) + 1
    db.session.commit()
    log_operation(session.get('username', ''), '死亡上报', 'print_notice',
                  f'重新打印停发告知单 {notice.notice_no}（第{notice.printed_count}次）')
    return json_response(True, '已记录打印留痕', data={'printed_count': notice.printed_count})


@death_bp.route('/api/checklist/print', methods=['POST'])
@login_required
@permission_required('death_report.edit')
def api_checklist_print():
    """T15-3：防误发清单打印留痕（printed+1 + log_operation）。"""
    form = request.form if request.form else (request.json or {})
    cid = form.get('id', type=int)
    if not cid:
        return json_response(False, '参数错误')
    from models.cascade import CascadeChecklist
    checklist = CascadeChecklist.query.get(cid)
    if not checklist:
        return json_response(False, '清单不存在')
    checklist.printed = (checklist.printed or 0) + 1
    db.session.commit()
    log_operation(session.get('username', ''), '死亡上报', 'print_checklist',
                  f'打印防误发清单（死亡登记 #{checklist.death_report_id}，第{checklist.printed}次）')
    return json_response(True, '已记录打印留痕', data={'printed': checklist.printed})


def _write_export_xlsx(headers, rows, module='death_report', filename='export.xlsx'):
    """生成 xlsx 落盘 temp/exports/<uuid>.xlsx，返回 (abs_path, download_url)。

    T15-3 新导出点直接走 temp/exports token 通道（T07 规范，禁 serve_upload）：
    脱敏在组装 rows 时由调用方按 mask_export_rows 处理；此处只落盘 + 发 token。
    （通用实现上提 utils/helpers.write_export_xlsx，此处保留薄封装避免破坏既有调用。）
    """
    from utils.helpers import write_export_xlsx
    return write_export_xlsx(headers, rows, module=module, filename=filename)


@death_bp.route('/api/notice/export', methods=['POST'])
@login_required
@permission_required('death_report.export')
def api_notice_export():
    """T15-3：停发告知单 Excel 导出（token 通道；无 view_sensitive 出脱敏版）。"""
    import json
    from utils.masking import mask_export_rows
    form = request.form if request.form else (request.json or {})
    rid = form.get('id', type=int)
    if not rid:
        return json_response(False, '参数错误')
    dr = DeathReport.query.get(rid)
    if not dr:
        return json_response(False, '记录不存在')
    notice = _load_notice(rid)
    if not notice:
        return json_response(False, '该死亡登记尚未生成停发告知单')

    project_ids = []
    if notice.project_ids:
        try:
            project_ids = json.loads(notice.project_ids)
        except (ValueError, TypeError):
            project_ids = []
    from models.subsidy_project import SubsidyProject
    project_names = []
    for pid in project_ids:
        proj = SubsidyProject.query.get(pid)
        project_names.append(proj.name if proj else f'项目#{pid}')

    headers = ['告知单编号', '姓名', '身份证', '停发项目', '停发起始期', '停发原因', '生成时间', '操作人']
    rows = [[notice.notice_no, notice.resident_name,
             _resolve_id_card_display(dr, notice.id_card_enc),
             '、'.join(project_names), notice.stop_from, notice.reason,
             notice.generated_at.strftime('%Y-%m-%d %H:%M') if notice.generated_at else '',
             str(notice.operator or '')]]
    rows = mask_export_rows(headers, rows, _can_view_sensitive())

    abs_path, token_url = _write_export_xlsx(
        headers, rows, module='death_report',
        filename=f'停发告知单_{notice.notice_no}.xlsx')
    if not token_url:
        return json_response(False, '导出通道未就绪')
    return json_response(True, '导出成功', data={'download_url': f'/report/download/{token_url}',
                                                'file': os.path.basename(abs_path)})


@death_bp.route('/api/checklist/export', methods=['POST'])
@login_required
@permission_required('death_report.export')
def api_checklist_export():
    """T15-3：防误发清单 Excel 导出（token 通道；无 view_sensitive 出脱敏版）。"""
    import json
    from utils.masking import mask_export_rows
    form = request.form if request.form else (request.json or {})
    rid = form.get('id', type=int)
    if not rid:
        return json_response(False, '参数错误')
    dr = DeathReport.query.get(rid)
    if not dr:
        return json_response(False, '记录不存在')
    checklist = _load_checklist(rid)
    if not checklist:
        return json_response(False, '该死亡登记尚未生成防误发清单')
    affected = {}
    if checklist.affected_items:
        try:
            affected = json.loads(checklist.affected_items)
        except (ValueError, TypeError):
            affected = {}

    headers = ['项目名称', '最近发放日期', '金额', '适用标签', '对象姓名', '身份证']
    rows = []
    for s in affected.get('subsidies', []):
        rows.append([s.get('project_name', ''), s.get('issue_date', ''), s.get('amount', ''),
                     s.get('target_tags') or '', dr.deceased_name or '',
                     _resolve_id_card_display(dr)])
    if not rows:
        # 无补贴停发时仍导出台账影响摘要
        counts = affected.get('counts', {})
        rows.append(['（无在发补贴，台账软失效）', '', '', '',
                     dr.deceased_name or '', _resolve_id_card_display(dr)])
        rows.append(['台账影响', '', '', '',
                     '低保/残疾/特困/其他', json.dumps(counts, ensure_ascii=False)])
    rows = mask_export_rows(headers, rows, _can_view_sensitive())

    abs_path, token_url = _write_export_xlsx(
        headers, rows, module='death_report',
        filename=f'防误发清单_{dr.deceased_name}.xlsx')
    if not token_url:
        return json_response(False, '导出通道未就绪')
    return json_response(True, '导出成功', data={'download_url': f'/report/download/{token_url}',
                                                'file': os.path.basename(abs_path)})
