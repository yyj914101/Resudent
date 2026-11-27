# -*- coding: utf-8 -*-
"""utils/batch_center.py — 全站【导入批次】统一只读聚合服务（问题 5 收口）。

零 DB 迁移：按需聚合查询现有 5 套批次表，按 created_at 倒序归并分页，
统一输出 report_url / export_failed_url 供前端直达报告弹窗与失败行下载。
"""
from __future__ import annotations

from datetime import datetime, time

from sqlalchemy.exc import OperationalError

from models import (DomainBatchImport, ElectionBatchImport, ImportBatch,
                    SofttableBatchImport, SubsidyBatchImport, User)
from utils.async_tasks import sync_failed_pending_batches
from utils.domain_batch import DOMAIN_LABELS
from utils.dynatable_modules import label_for as dynatable_label
from utils.import_engine import batch_catalog_version

SOURCE_MODULE_OPTIONS = (
    ('resident', '居民管理'),
    ('softtable', '软表台账'),
    ('election', '选举管理'),
    ('subsidy', '补贴登记'),
    ('medical', '医保参保'),
    ('pension', '农保/养老保险'),
    ('civil', '民政对象'),
)


def _date_bounds(date_from_str: str, date_to_str: str):
    start_dt = (datetime.combine(datetime.strptime(date_from_str, '%Y-%m-%d').date(), time.min)
                if date_from_str else None)
    end_dt = (datetime.combine(datetime.strptime(date_to_str, '%Y-%m-%d').date(), time.max)
              if date_to_str else None)
    return start_dt, end_dt


def _apply_common_filters(query, model, status, start_dt, end_dt):
    if status:
        query = query.filter(model.status == status)
    if start_dt:
        query = query.filter(model.created_at >= start_dt)
    if end_dt:
        query = query.filter(model.created_at <= end_dt)
    return query


def _fmt_dt(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S') if dt else ''


def _resolve_user_names(op_ids):
    ids = {i for i in op_ids if isinstance(i, int)}
    if not ids:
        return {}
    return {u.id: u.username for u in User.query.filter(User.id.in_(ids)).all()}


def query_unified_batches(*, source_module='', status='', mode='',
                          date_from='', date_to='', page=1, limit=20):
    """聚合全站导入批次，返回 {'data': [...], 'total': N, 'pages': P}。"""
    start_dt, end_dt = _date_bounds(date_from, date_to)
    top_k = max(page * limit, limit)
    wanted = {source_module} if source_module else {
        'resident', 'softtable', 'election', 'subsidy', 'medical', 'pension', 'civil'
    }

    entries = []
    total = 0

    # 1. 居民管理 ImportBatch
    if 'resident' in wanted:
        try:
            q = _apply_common_filters(ImportBatch.query, ImportBatch, status, start_dt, end_dt)
            if mode:
                q = q.filter(ImportBatch.mode == mode)
            total += q.count()
            rows = (q.order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
                    .limit(top_k).all())
            names = _resolve_user_names([r.operator for r in rows])
            for r in rows:
                entries.append((r.created_at or datetime.min, {
                    'id': r.id, 'source_module': 'resident', 'source_label': '居民管理',
                    'batch_id': r.batch_id, 'source_file': r.source_file or '',
                    'mode': r.mode or 'custom',
                    'mode_label': '居民 · ' + ('模板' if r.mode == 'template' else '自定义'),
                    'profile_key': r.profile_key,
                    'catalog_version': batch_catalog_version(r),
                    'status': r.status, 'total_rows': r.total_rows or 0,
                    'inserted_rows': r.success_rows or 0,
                    'updated_rows': r.updated_rows or 0,
                    'skip_rows': r.skip_rows or 0,
                    'fail_rows': r.fail_rows or 0,
                    'operator': names.get(r.operator) or (str(r.operator) if r.operator else ''),
                    'created_at': _fmt_dt(r.created_at),
                    'report_url': f'/import/{r.batch_id}/report',
                    'export_failed_url': f'/import/{r.batch_id}/export-failed',
                }))
        except OperationalError:
            pass

    # 若筛选了居民专属 mode（template/custom），非居民批次不匹配
    allow_other = not mode

    # 2. 软表台账 SofttableBatchImport
    if 'softtable' in wanted and allow_other:
        try:
            sync_failed_pending_batches(SofttableBatchImport.query.filter_by(status='pending').all())
            q = _apply_common_filters(SofttableBatchImport.query, SofttableBatchImport, status, start_dt, end_dt)
            total += q.count()
            rows = (q.order_by(SofttableBatchImport.created_at.desc(), SofttableBatchImport.id.desc())
                    .limit(top_k).all())
            for r in rows:
                entries.append((r.created_at or datetime.min, {
                    'id': r.id, 'source_module': 'softtable', 'source_label': '软表台账',
                    'batch_id': r.batch_id, 'source_file': r.source_file or '',
                    'mode': 'softtable',
                    'mode_label': f'软表 · {dynatable_label(r.module_key)}',
                    'catalog_version': r.table_code or '—',
                    'status': r.status, 'total_rows': r.total_rows or 0,
                    'inserted_rows': r.success_rows or 0,
                    'updated_rows': 0, 'skip_rows': 0,
                    'fail_rows': r.fail_rows or 0,
                    'operator': r.operator or '',
                    'created_at': _fmt_dt(r.created_at),
                    'report_url': f'/dynatable/api/import-report/{r.batch_id}',
                    'export_failed_url': f'/dynatable/api/import-export-failed/{r.batch_id}',
                }))
        except OperationalError:
            pass

    # 3. 选举管理 ElectionBatchImport
    if 'election' in wanted and allow_other:
        try:
            q = _apply_common_filters(ElectionBatchImport.query, ElectionBatchImport, status, start_dt, end_dt)
            total += q.count()
            rows = (q.order_by(ElectionBatchImport.created_at.desc(), ElectionBatchImport.id.desc())
                    .limit(top_k).all())
            names = _resolve_user_names([r.operator for r in rows])
            for r in rows:
                entries.append((r.created_at or datetime.min, {
                    'id': r.id, 'source_module': 'election', 'source_label': '选举管理',
                    'batch_id': r.batch_id, 'source_file': r.source_file or '',
                    'mode': 'election',
                    'mode_label': f'选民 · 届次#{r.round_id}',
                    'catalog_version': '—',
                    'status': r.status, 'total_rows': r.total_rows or 0,
                    'inserted_rows': r.success_rows or 0,
                    'updated_rows': 0, 'skip_rows': 0,
                    'fail_rows': r.fail_rows or 0,
                    'operator': names.get(r.operator) or (str(r.operator) if r.operator else ''),
                    'created_at': _fmt_dt(r.created_at),
                    'report_url': f'/election/api/rounds/{r.round_id}/voters/import-report/{r.batch_id}',
                    'export_failed_url': f'/election/api/rounds/{r.round_id}/voters/import-export-failed/{r.batch_id}',
                }))
        except OperationalError:
            pass

    # 4. 补贴登记 SubsidyBatchImport
    if 'subsidy' in wanted and allow_other:
        try:
            q = _apply_common_filters(SubsidyBatchImport.query, SubsidyBatchImport, status, start_dt, end_dt)
            total += q.count()
            rows = (q.order_by(SubsidyBatchImport.created_at.desc(), SubsidyBatchImport.id.desc())
                    .limit(top_k).all())
            names = _resolve_user_names([r.operator for r in rows])
            for r in rows:
                entries.append((r.created_at or datetime.min, {
                    'id': r.id, 'source_module': 'subsidy', 'source_label': '补贴登记',
                    'batch_id': r.batch_id, 'source_file': r.source_file or '',
                    'mode': 'subsidy',
                    'mode_label': f'补贴 · 项目#{r.project_id}',
                    'catalog_version': '—',
                    'status': r.status, 'total_rows': r.total_rows or 0,
                    'inserted_rows': r.success_rows or 0,
                    'updated_rows': 0, 'skip_rows': 0,
                    'fail_rows': r.fail_rows or 0,
                    'operator': names.get(r.operator) or (str(r.operator) if r.operator else ''),
                    'created_at': _fmt_dt(r.created_at),
                    'report_url': f'/subsidy/api/record/import-report/{r.batch_id}',
                    'export_failed_url': f'/subsidy/api/record/import-export-failed/{r.batch_id}',
                }))
        except OperationalError:
            pass

    # 5. 通用域 DomainBatchImport（medical / civil）
    want_domains = []
    if 'medical' in wanted:
        want_domains.append('medical')
    if 'pension' in wanted:
        want_domains.append('pension')
    if 'civil' in wanted:
        want_domains.extend(['dibao', 'disabled', 'care_special', 'care_child', 'care_target', 'welfare_job'])
    if want_domains and allow_other:
        try:
            sync_failed_pending_batches(
                DomainBatchImport.query.filter(
                    DomainBatchImport.domain.in_(want_domains),
                    DomainBatchImport.status == 'pending',
                ).all()
            )
            q = DomainBatchImport.query.filter(DomainBatchImport.domain.in_(want_domains))
            q = _apply_common_filters(q, DomainBatchImport, status, start_dt, end_dt)
            total += q.count()
            rows = (q.order_by(DomainBatchImport.created_at.desc(), DomainBatchImport.id.desc())
                    .limit(top_k).all())
            for r in rows:
                dlabel, dprefix = DOMAIN_LABELS.get(r.domain, (r.domain, ''))
                if r.domain == 'medical':
                    src_mod, src_lbl = 'medical', '医保参保'
                elif r.domain == 'pension':
                    src_mod, src_lbl = 'pension', '农保/养老保险'
                else:
                    src_mod, src_lbl = 'civil', '民政对象'
                entries.append((r.created_at or datetime.min, {
                    'id': r.id, 'source_module': src_mod,
                    'source_label': src_lbl,
                    'batch_id': r.batch_id, 'source_file': r.source_file or '',
                    'mode': r.mode or 'append',
                    'mode_label': f'{dlabel} · {r.scope_key}' if r.scope_key else dlabel,
                    'catalog_version': '—',
                    'status': r.status, 'total_rows': r.total_rows or 0,
                    'inserted_rows': r.inserted_rows or 0,
                    'updated_rows': r.updated_rows or 0,
                    'skip_rows': r.skip_rows or 0,
                    'fail_rows': r.fail_rows or 0,
                    'operator': r.operator or '',
                    'created_at': _fmt_dt(r.created_at),
                    'report_url': f'{dprefix}/api/import-report/{r.batch_id}' if dprefix else '',
                    'export_failed_url': f'{dprefix}/api/import-export-failed/{r.batch_id}' if dprefix else '',
                }))
        except OperationalError:
            pass

    entries.sort(key=lambda pair: pair[0], reverse=True)
    start = (page - 1) * limit
    page_data = [item for _, item in entries[start:start + limit]]
    pages = max(1, (total + limit - 1) // limit) if total else 0
    return {'data': page_data, 'total': total, 'pages': pages}
