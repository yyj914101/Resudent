# -*- coding: utf-8 -*-
"""utils/election_service.py — 090B 公共事务·选举业务语义收口（单一真源）。

状态机迁移表、守卫、唯一性与对账口径全部收在本模块（AGENTS.md §11：routes/ 只做
HTTP 接线，不拥有业务语义）；routes/election.py 只传参调用，禁复制实现。

依据：`docs/design/090B-公共事务选举-一页纸字段清单-模板-20261106.md` v1.2
- §6 状态机 S-01~S-04；注：「非法迁移一律 409 且不落库；仅『选民登记』状态可新增/修改选民」
- §7 对账口径 M-01/M-02/M-03
- §10 校验 V-01~V-05

口径说明（S-04 前置「公示期结束」的落地）：表结构无公示期字段，以「全部结果行的
公示日期（E-006 notice_date）均已填写且至少一条结果」作为公示信息完备的判据，
供 review 确认；如需显式公示截止日应走缺口流程加列。
"""
from collections import Counter
from functools import wraps

from models import (db, ElectionRound, ElectionVoter, ElectionResult,
                    ElectionBatchImport, ElectionBatchError)
from utils import election_field_catalog as catalog
from utils.crypto import key_holder, sha256_hash
from utils.dict_enum import get_enum
from utils.helpers import json_response
from utils.masking import mask_id_card
from utils.sensitive_fields import make_sensitive_helpers

# §6 状态机（唯一真源；非法迁移 409 且不落库，V-01）
ELECTION_TRANSITIONS = {
    ('筹备', '选民登记'),     # S-01 进入登记（前置：登记期起止已填）
    ('选民登记', '投票'),     # S-02 结束登记（前置：登记期已结束）
    ('投票', '结果公示'),     # S-03 完成投票（前置：已录入结果）
    ('结果公示', '已完成'),   # S-04 归档（前置：公示期结束，见模块 docstring 口径）
}

# §6 注：仅「选民登记」状态可新增/修改选民（V-02 登记期守卫，按状态口径落地；
# 删除同锁：登记期外名册冻结，防止投票后误删——任务书未单列，RESULT 留痕）
VOTER_EDITABLE_STATUSES = ('选民登记',)
# 结果录入窗口：投票（录入 S-03 前置「已录入结果」）+ 结果公示（修正/补公示日期）
RESULT_EDITABLE_STATUSES = ('投票', '结果公示')

# R12 字段级加密管线（身份证 PII-identity / 联系方式 PII-contact）
# 105：由 routes/election.py 下沉到本模块——单条登记与批量导入共用同一写入管线（禁止两套）。
_ELECTION_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}
voter_decrypt, voter_set_enc, voter_clause = make_sensitive_helpers(
    ElectionVoter, _ELECTION_SENS_FIELDS, key_holder)


def _text(v):
    return (v or '').strip()


def validate_voter_fields(form):
    """一页纸 §3/§10 选民校验（单一真源；单条登记与批量导入共用）。

    `form` 可为 request.form 或普通 dict（只需 .get）：批量导入不得另写一套校验。
    """
    name = _text(form.get('name'))
    if not name:
        raise ValueError('选民姓名必填')
    id_card = _text(form.get('id_card')).upper()
    if not id_card:
        raise ValueError('身份证号必填')
    if len(id_card) != 18:
        raise ValueError('身份证号须为 18 位（V-002）')
    register_status = _text(form.get('register_status')) or '未登记'
    if register_status not in get_enum('选民登记状态'):
        raise ValueError('登记状态取值不合法（字典 D-02）')
    proxy_name = _text(form.get('proxy_name'))
    if register_status == '委托投票' and not proxy_name:
        raise ValueError('委托投票须填写委托受托人（V-04）')
    return {'name': name, 'id_card': id_card, 'group_name': _text(form.get('group_name')),
            'register_status': register_status, 'is_out': _parse_bool(form, 'is_out'),
            'proxy_name': proxy_name, 'mobile_box': _parse_bool(form, 'mobile_box'),
            'phone': _text(form.get('phone')), 'remark': _text(form.get('remark'))}


def _parse_bool(form, key):
    return _text(form.get(key)) in ('1', 'on', 'true', '是')


def apply_voter_fields(voter, fields):
    """写入选民字段（单一真源）：明文列 + R12 加密列 + V-011 派生 hash。"""
    voter.name = fields['name']
    voter.group_name = fields['group_name']
    voter.register_status = fields['register_status']
    voter.is_out = fields['is_out']
    voter.proxy_name = fields['proxy_name']
    voter.mobile_box = fields['mobile_box']
    voter.remark = fields['remark']
    voter_set_enc(voter, 'id_card', fields['id_card'])
    voter_set_enc(voter, 'phone', fields['phone'])
    voter.resident_hash = sha256_hash(fields['id_card'])   # V-011：由 V-002 派生（软关联居民卡）


class TransitionDenied(Exception):
    """状态机/守卫拒绝（路由转 409 JSON，不落库）。"""


def election_api(fn):
    """090B 路由共用错误转译（attachment_api 同款形态）：
    ValueError（入参不合法）→ 400 JSON；TransitionDenied（状态机/守卫）→ 409 JSON。
    路由保持零 try/except 的薄形态。"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            resp = json_response(False, str(e))
            resp.status_code = 400
            return resp
        except TransitionDenied as e:
            resp = json_response(False, str(e))
            resp.status_code = 409
            return resp
    return wrapper


def _fmt_date(d):
    return d.strftime('%Y-%m-%d') if d else '未填'


def check_transition(round_obj, to_status):
    """S-01~S-04 逐边守卫；通过返回 None，拒绝抛 TransitionDenied（409）。"""
    current = round_obj.status or '筹备'
    if (current, to_status) not in ELECTION_TRANSITIONS:
        raise TransitionDenied('非法状态迁移: %s→%s' % (current, to_status))

    if (current, to_status) == ('筹备', '选民登记'):
        # S-01 前置：登记期起止已填
        if not round_obj.register_start or not round_obj.register_end:
            raise TransitionDenied('进入登记前须先填写登记期起止（当前: %s ~ %s）' % (
                _fmt_date(round_obj.register_start), _fmt_date(round_obj.register_end)))
    elif (current, to_status) == ('选民登记', '投票'):
        # S-02 前置：登记期已结束（今天 > 登记期止）
        from datetime import date
        today = date.today()
        if not round_obj.register_end or today <= round_obj.register_end:
            raise TransitionDenied('登记期尚未结束（%s），不能结束登记进入投票' % _fmt_date(round_obj.register_end))
    elif (current, to_status) == ('投票', '结果公示'):
        # S-03 前置：已录入结果
        has_result = db.session.query(ElectionResult.id).filter_by(round_id=round_obj.id).first()
        if not has_result:
            raise TransitionDenied('尚未录入任何当选结果，不能完成投票进入结果公示')
    elif (current, to_status) == ('结果公示', '已完成'):
        # S-04 前置：公示期结束（落地口径：公示信息完备——见模块 docstring）
        results = ElectionResult.query.filter_by(round_id=round_obj.id).all()
        if not results:
            raise TransitionDenied('尚未录入任何当选结果，不能归档')
        missing = [r.winner_name for r in results if not r.notice_date]
        if missing:
            raise TransitionDenied('公示期结束前须补齐全部结果的公示日期，缺: %s' % '、'.join(missing[:3]))
    return None


def check_voter_editable(round_obj):
    """V-02：仅「选民登记」状态可新增/修改（及删除，见常量注释）选民。"""
    if (round_obj.status or '') not in VOTER_EDITABLE_STATUSES:
        raise TransitionDenied('当前届次状态为「%s」，仅「选民登记」状态可增删改选民'
                               % (round_obj.status or '筹备'))


def check_result_editable(round_obj):
    """结果录入窗口：投票（录入）/ 结果公示（修正）。"""
    if (round_obj.status or '') not in RESULT_EDITABLE_STATUSES:
        raise TransitionDenied('当前届次状态为「%s」，仅「投票」或「结果公示」状态可录入结果'
                               % (round_obj.status or '筹备'))


def check_voter_unique(round_id, id_card, exclude_id=None):
    """V-03：同届次同人一条（round_id + id_card_hash）；重复抛 TransitionDenied（409）。"""
    h = sha256_hash((id_card or '').strip().upper())
    query = ElectionVoter.query.filter_by(round_id=round_id, id_card_hash=h)
    if exclude_id:
        query = query.filter(ElectionVoter.id != exclude_id)
    if query.first():
        raise TransitionDenied('该选民已在本届名册中（同届次同人唯一）')


def reconcile_round(round_id):
    """§7 对账与统计（M-01/M-02/M-03）。

    M-01 选民登记数 = 名册「已登记」行数；
    M-02 得票合计 = Σ 得票；
    M-03 四值互斥对账：已登记 + 未登记 + 委托投票 + 外出 = 名册总数。
    """
    voters = ElectionVoter.query.filter_by(round_id=round_id).all()
    results = ElectionResult.query.filter_by(round_id=round_id).all()
    by_status = Counter((v.register_status or '') for v in voters)
    registered = by_status.get('已登记', 0)
    votes_total = sum(int(r.votes or 0) for r in results)
    return {
        'total': len(voters),
        'registered': registered,                      # M-01
        'by_status': dict(by_status),                  # M-03 四值计数
        'four_way_sum': sum(by_status.get(k, 0) for k in ('已登记', '未登记', '委托投票', '外出')),
        'results_count': len(results),
        'votes_total': votes_total,                    # M-02
    }


# ═══════════════════ 105 选民批量导入（ADR-030） ═══════════════════

def normalize_import_values(row, mapping):
    """单行原始值 → 目录 key 值（空单元格保留语义：文本 ''、布尔 None）。

    校验口径与 `validate_voter_fields` 同源（必填/18 位/字典/委托规则），只是把
    「空值 → 不覆盖」与「建行默认值」的差异留在写入侧（见 apply_voter_row）。
    """
    values = {}
    for key in catalog.FIELD_KEYS:
        raw = row.get(mapping[key]) if key in mapping else None
        values[key] = catalog.parse_bool(raw) if key in catalog.BOOL_KEYS else catalog.cell_text(raw)

    values['id_card'] = values['id_card'].upper()
    if not values['name']:
        raise ValueError('选民姓名必填')
    if not values['id_card']:
        raise ValueError('身份证号必填')
    if len(values['id_card']) != 18:
        raise ValueError('身份证号须为 18 位（V-002）')
    status = values['register_status']
    if status and status not in get_enum('选民登记状态'):
        raise ValueError('登记状态取值不合法（字典 D-02）')
    if status == '委托投票' and not values['proxy_name']:
        raise ValueError('委托投票须填写委托受托人（V-04）')
    return values


def apply_voter_row(round_id, values):
    """overwrite 唯一写入 seam（ADR-030 决策 3）：命中即覆盖、未命中即新建。

    命中（round_id + id_card_hash）时**仅用上传行非空字段整体更新**，空单元格保留
    库中原值（裁定 1）；这不是「跳过记录」，对外只有 overwrite 一种策略。
    返回 'created' / 'updated'。
    """
    id_card = values['id_card']
    voter = ElectionVoter.query.filter_by(round_id=round_id,
                                          id_card_hash=sha256_hash(id_card)).first()
    if voter is None:
        fields = {
            'name': values['name'],
            'id_card': id_card,
            'group_name': values['group_name'],
            'register_status': values['register_status'] or '未登记',
            'is_out': bool(values['is_out']),
            'proxy_name': values['proxy_name'],
            'mobile_box': bool(values['mobile_box']),
            'phone': values['phone'],
            'remark': values['remark'],
        }
        voter = ElectionVoter(round_id=round_id)
        apply_voter_fields(voter, fields)
        db.session.add(voter)
        return 'created'

    if values['name']:
        voter.name = values['name']
    for key in ('group_name', 'proxy_name', 'remark'):
        if values[key]:
            setattr(voter, key, values[key])
    if values['register_status']:
        voter.register_status = values['register_status']
    if values['is_out'] is not None:
        voter.is_out = values['is_out']
    if values['mobile_box'] is not None:
        voter.mobile_box = values['mobile_box']
    if values['phone']:                      # 空电话不覆盖既有密文
        voter_set_enc(voter, 'phone', values['phone'])
    voter.resident_hash = sha256_hash(id_card)
    return 'updated'


def add_batch_error(batch_id, row_no, id_card_masked, reason):
    """失败行进暂存表（身份证已打码，绝不落明文）。"""
    db.session.add(ElectionBatchError(
        batch_id=batch_id, row_no=row_no,
        id_card_masked=(id_card_masked or '')[:30],
        reason=str(reason)[:300]))


def mark_aborted_stale():
    """进程重启后，残留 running 的选民导入批次标 aborted（幂等三件套之一）。"""
    stale = ElectionBatchImport.query.filter_by(status='running').all()
    for batch in stale:
        batch.status = 'aborted'
    if stale:
        db.session.commit()
    return len(stale)


def run_election_batch(task_id, batch_id):
    """105 批量导入 A1 处理函数（run_async 约定：fn(task_id, **kwargs)，已处 app context 内）。

    逐行：目录映射 → 校验 → overwrite 写入；失败行只进 election_batch_error（打码），
    成功行直接落 election_voter。每行独立提交，失败回滚不影响已成功行（计数与库一致）。
    """
    from datetime import datetime

    from utils.async_tasks import _update
    from utils.import_engine import read_import_file   # 2026-11-19 契约扩展：.xlsx/.xls/.csv 统一分发

    batch = ElectionBatchImport.query.filter_by(batch_id=batch_id).first()
    if not batch:
        raise RuntimeError('选民导入批次不存在：%s' % batch_id)
    if batch.status == 'done':
        return                                     # 幂等：已完成批次不重跑

    round_obj = db.session.get(ElectionRound, batch.round_id)
    if round_obj is None:
        batch.status = 'failed'
        db.session.commit()
        _update(task_id, status='failed', result_summary='届次不存在')
        return

    # 执行时二次校验（验收 1）：排队期间届次可能已离开「选民登记」状态
    try:
        check_voter_editable(round_obj)
    except TransitionDenied as exc:
        batch.status = 'failed'
        db.session.commit()
        _update(task_id, status='failed', result_summary=str(exc))
        return

    batch.status = 'running'
    db.session.commit()

    try:
        headers, rows = read_import_file(batch.source_path)
    except Exception as exc:                       # 文件损坏/被清理
        batch.status = 'failed'
        db.session.commit()
        _update(task_id, status='failed', result_summary='文件解析失败：%s' % exc)
        return

    mapping, missing = catalog.map_headers(headers)
    total = len(rows)
    batch.total_rows = total
    if missing:
        batch.status = 'failed'
        db.session.commit()
        _update(task_id, status='failed',
                result_summary='模板缺少列：%s' % '、'.join(missing))
        return
    db.session.commit()
    _update(task_id, progress=0, result_summary='开始导入 %d 行' % total)

    success = 0
    fail = 0
    for offset, row in enumerate(rows, start=2):   # Excel 行号从 2 起（首行表头）
        raw_id = catalog.cell_text(row.get(mapping['id_card'])).upper()
        try:
            values = normalize_import_values(row, mapping)
            apply_voter_row(batch.round_id, values)
            db.session.commit()
            success += 1
        except (ValueError, TransitionDenied) as exc:
            db.session.rollback()
            add_batch_error(batch_id, offset, mask_id_card(raw_id) if raw_id else '',
                            str(exc))
            db.session.commit()
            fail += 1
        except Exception as exc:                   # 行级 DB 异常不打断整批
            db.session.rollback()
            add_batch_error(batch_id, offset, mask_id_card(raw_id) if raw_id else '',
                            '行处理失败：%s: %s' % (type(exc).__name__, exc))
            db.session.commit()
            fail += 1
        if total and (offset - 1) % 20 == 0:
            _update(task_id, progress=min(99, int((offset - 1) * 100 / total)),
                    result_summary='已处理 %d/%d 行' % (offset - 1, total))

    batch = ElectionBatchImport.query.filter_by(batch_id=batch_id).first()
    batch.status = 'done'
    batch.success_rows = success
    batch.fail_rows = fail
    batch.finished_at = datetime.now()
    db.session.commit()
    _update(task_id, status='done', progress=100,
            result_summary='导入完成：成功 %d 行，失败 %d 行' % (success, fail),
            finished_at=datetime.now())
