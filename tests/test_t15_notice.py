# -*- coding: utf-8 -*-
"""T15-3 防误发清单 + 停发告知单单测（Sprint6 第三批）。

覆盖（对应 PRD R5 B1/D4 + 计划稿 §4.1 第 5 条）：
- 确认后同事务自动生成 cascade_checklist（affected_items 明细）+ cascade_notice（字段/编号）
- 单事务：故障注入两表 0 条；重复确认不重复生成
- 撤回后重确认：旧 checklist 作废 + 新生成；旧 notice 保留（历史单据）+ 新生成
- 重打留痕：POST print → printed_count+1 + log_operation 落库且 detail 无明文（B1）
- 脱敏：无 view_sensitive 出脱敏身份证（前6后4）、admin 明文
- 导出：temp/exports token 通道可下载、内容脱敏、权限 403
- 页面：GET checklist_view / notice-print 200，加载不计数
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

# 复用 T15-1/2 夹具与 seed 辅助（避免双份实现，对齐既有先例）
from test_t15_cascade import (
    _make_app, _admin_client, _make_resident, _make_death_report,
    _make_project, _make_subsidy_record, _make_tag, _confirm, _row, _seed_full,
    _years_ago, ID_CARD,
)


def _op_user_client(app):
    """普通操作员：仅 death_report.view（v3 权限格式，无 view_sensitive/edit/export/death_confirm）。"""
    from models import db, Role, User
    from werkzeug.security import generate_password_hash
    with app.app_context():
        perms = {'death_report.view': True, 'resident.view': True, 'schema_version': 3}
        role = Role(name='普通操作员T15-3', permissions=json.dumps(perms, ensure_ascii=False), is_system=False)
        db.session.add(role)
        db.session.commit()
        u = User(username='op_t15_3', password_hash=generate_password_hash('x'),
                 role_id=role.id, is_active=True)
        db.session.add(u)
        db.session.commit()
        uid = u.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid
    return client, uid


def _simple_seed(app, name='死亡乙', id_card='110101196201011234'):
    """轻量 seed：居民 + 死亡登记（无四表/补贴，验证空场景生成）。"""
    rid = _make_resident(app, name, _years_ago(70), id_card)
    drid = _make_death_report(app, name, id_card)
    return rid, drid


def _notice_count(app):
    from models.cascade import CascadeNotice
    with app.app_context():
        return CascadeNotice.query.count()


def _checklist_count(app):
    from models.cascade import CascadeChecklist
    with app.app_context():
        return CascadeChecklist.query.count()


# ============ T15-3 生成（checklist + notice） ============
class TestGenerate:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_confirm_generates_both_docs(self, app):
        """R5 B1/D4：确认后自动生成清单 + 告知单，字段正确。"""
        from models.cascade import CascadeChecklist, CascadeNotice
        seed = _seed_full(app)
        client = _admin_client(app)

        resp = _confirm(client, seed['drid'])
        body = resp.get_json()
        assert body['success'] is True, body
        assert body['data'].get('checklist_id')
        assert body['data'].get('notice_id')

        cl = _row(app, CascadeChecklist, body['data']['checklist_id'])
        assert cl.status == '有效'
        assert cl.death_report_id == seed['drid']
        affected = json.loads(cl.affected_items)
        assert affected['subsidies'][0]['project_name'] == '高龄津贴'
        assert affected['subsidies'][0]['amount'] == '100.00'
        assert affected['counts']['dibao'] == 1
        assert affected['counts']['disabled'] == 1
        assert affected['counts']['care_target'] == 1
        assert affected['counts']['special_support'] == 1
        assert affected['tags'][0]['tag_code'] == 'dibao'

        nt = _row(app, CascadeNotice, body['data']['notice_id'])
        assert nt.notice_no.startswith('TZ-')
        assert nt.resident_name == '死亡甲'
        assert nt.id_card_enc          # 密文原生
        assert nt.id_card_hash
        assert json.loads(nt.project_ids) == [_project_of(app, seed['r1'])]
        assert nt.stop_from  # YYYY-MM
        assert nt.reason == '死亡'
        assert nt.operator == 1
        assert nt.printed_count == 0

    def test_notice_no_unique_increment_same_day(self, app):
        """同日两条死亡确认：notice_no 递增唯一。"""
        from models.cascade import CascadeNotice
        client = _admin_client(app)

        rid1, drid1 = _simple_seed(app, '死亡乙', '110101196201011234')
        resp1 = _confirm(client, drid1)
        assert resp1.get_json()['success'] is True

        rid2, drid2 = _simple_seed(app, '死亡丙', '110101197001011235')
        resp2 = _confirm(client, drid2)
        assert resp2.get_json()['success'] is True

        n1 = _row(app, CascadeNotice, resp1.get_json()['data']['notice_id'])
        n2 = _row(app, CascadeNotice, resp2.get_json()['data']['notice_id'])
        assert n1.notice_no != n2.notice_no
        assert n1.notice_no[:9] == n2.notice_no[:9]  # 同日前缀 TZ-YYYYMMDD
        seq1 = int(n1.notice_no.split('-')[-1])
        seq2 = int(n2.notice_no.split('-')[-1])
        assert seq2 == seq1 + 1

    def test_rollback_no_docs_on_failure(self, app, monkeypatch):
        """R5-E4：级联中途失败（docs 生成前异常）→ 两表 0 条 + failed。"""
        from models.death_report import CascadeService
        from models import DeathReport
        seed = _seed_full(app)

        def _boom(dr, resident, subsidies, soft_result, now, operator_id=None):
            raise RuntimeError('注入失败')

        monkeypatch.setattr(CascadeService, '_build_cascade_docs', staticmethod(_boom))
        client = _admin_client(app)
        resp = _confirm(client, seed['drid'])
        assert resp.get_json()['success'] is False
        assert _checklist_count(app) == 0
        assert _notice_count(app) == 0
        assert _row(app, DeathReport, seed['drid']).cascade_status == 'failed'

    def test_repeat_confirm_no_dup(self, app):
        """幂等：done 后重复确认拒绝，不重复生成。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        assert _confirm(client, seed['drid']).get_json()['success'] is True
        resp2 = _confirm(client, seed['drid'])
        assert resp2.get_json()['success'] is False
        assert _checklist_count(app) == 1
        assert _notice_count(app) == 1

    def test_simple_seed_no_subsidy_still_generates(self, app):
        """空补贴场景：仍生成清单（affected 无 subsidies）与告知单（project_ids 空）。"""
        from models.cascade import CascadeNotice
        rid, drid = _simple_seed(app)
        client = _admin_client(app)
        resp = _confirm(client, drid)
        assert resp.get_json()['success'] is True
        nt = _row(app, CascadeNotice, resp.get_json()['data']['notice_id'])
        assert json.loads(nt.project_ids) == []


def _project_of(app, subsidy_record_id):
    from models.subsidy_project import SubsidyRecord
    with app.app_context():
        return SubsidyRecord.query.get(subsidy_record_id).project_id


# ============ T15-3 撤回后重确认 ============
class TestRevokeReconfirm:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_revoke_voids_checklist_keeps_notice(self, app):
        """B2：撤回后清单置作废；告知单为历史单据保留（不设作废态）。"""
        from models.cascade import CascadeChecklist, CascadeNotice
        seed = _seed_full(app)
        client = _admin_client(app)
        c_resp = _confirm(client, seed['drid'])
        cid = c_resp.get_json()['data']['checklist_id']
        nid = c_resp.get_json()['data']['notice_id']

        resp = client.post('/death-report/api/revoke', data={'id': seed['drid']})
        assert resp.get_json()['success'] is True

        assert _row(app, CascadeChecklist, cid).status == '作废'
        assert _row(app, CascadeNotice, nid) is not None  # 保留

    def test_reconfirm_generates_new_docs(self, app):
        """撤回后重新确认：新清单有效（旧作废）、新告知单生成（旧保留），编号递增。"""
        from models.cascade import CascadeChecklist, CascadeNotice
        seed = _seed_full(app)
        client = _admin_client(app)
        c1 = _confirm(client, seed['drid']).get_json()['data']
        n1 = _row(app, CascadeNotice, c1['notice_id'])

        assert client.post('/death-report/api/revoke', data={'id': seed['drid']}).get_json()['success'] is True
        c2 = _confirm(client, seed['drid']).get_json()['data']
        assert c2['checklist_id'] != c1['checklist_id']
        assert c2['notice_id'] != c1['notice_id']

        old_cl = _row(app, CascadeChecklist, c1['checklist_id'])
        new_cl = _row(app, CascadeChecklist, c2['checklist_id'])
        assert old_cl.status == '作废'
        assert new_cl.status == '有效'

        n2 = _row(app, CascadeNotice, c2['notice_id'])
        assert n2.notice_no != n1.notice_no
        assert _notice_count(app) == 2


# ============ T15-3 重打留痕（B1） ============
class TestPrintTrace:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_notice_print_increments_and_logs(self, app):
        """B1：POST print → printed_count+1 + log_operation 落库，detail 无明文身份证。"""
        from models.cascade import CascadeNotice
        from models.log import OperationLog
        seed = _seed_full(app)
        client = _admin_client(app)
        nid = _confirm(client, seed['drid']).get_json()['data']['notice_id']

        resp = client.post('/death-report/api/notice/print', data={'id': nid})
        assert resp.get_json()['success'] is True
        assert _row(app, CascadeNotice, nid).printed_count == 1

        with app.app_context():
            log = OperationLog.query.filter_by(module='死亡上报', action='print_notice') \
                .order_by(OperationLog.id.desc()).first()
        assert log is not None
        assert nid and log.summary  # 摘要含编号/姓名
        assert ID_CARD not in (log.summary or '')
        assert ID_CARD not in (log.detail or '')

    def test_checklist_print_increments(self, app):
        """清单打印留痕：printed+1。"""
        from models.cascade import CascadeChecklist
        seed = _seed_full(app)
        client = _admin_client(app)
        cid = _confirm(client, seed['drid']).get_json()['data']['checklist_id']

        resp = client.post('/death-report/api/checklist/print', data={'id': cid})
        assert resp.get_json()['success'] is True
        assert _row(app, CascadeChecklist, cid).printed == 1

    def test_page_load_not_count(self, app):
        """加载打印页不计数（点击打印按钮才留痕）。"""
        from models.cascade import CascadeNotice
        seed = _seed_full(app)
        client = _admin_client(app)
        nid = _confirm(client, seed['drid']).get_json()['data']['notice_id']

        resp = client.get(f'/death-report/{seed["drid"]}/notice-print')
        assert resp.status_code == 200
        assert _row(app, CascadeNotice, nid).printed_count == 0

    def test_print_403_without_edit(self, app):
        """权限：无 death_report.edit 用户打印 → 403。"""
        seed = _seed_full(app)
        admin = _admin_client(app)
        nid = _confirm(admin, seed['drid']).get_json()['data']['notice_id']

        client, _ = _op_user_client(app)
        resp = client.post('/death-report/api/notice/print', data={'id': nid})
        assert resp.status_code == 403


# ============ T15-3 脱敏（出口默认脱敏，明文仅 view_sensitive） ============
class TestMasking:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_notice_page_masked_without_sensitive(self, app):
        """无 view_sensitive：告知单页身份证脱敏（前6后4），不含明文。"""
        seed = _seed_full(app)
        admin = _admin_client(app)
        _confirm(admin, seed['drid'])

        client, _ = _op_user_client(app)
        resp = client.get(f'/death-report/{seed["drid"]}/notice-print')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert ID_CARD not in html            # 无明文
        assert '110101' in html               # 前6保留
        assert '1234' in html                 # 后4保留

    def test_notice_page_plaintext_for_admin(self, app):
        """admin（view_sensitive）：告知单页显示明文身份证。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])

        resp = client.get(f'/death-report/{seed["drid"]}/notice-print')
        assert resp.status_code == 200
        assert ID_CARD in resp.get_data(as_text=True)

    def test_checklist_view_200(self, app):
        """清单回溯页 200（含受影响明细）。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        resp = client.get(f'/death-report/{seed["drid"]}/checklist_view')
        assert resp.status_code == 200
        assert '防误发' in resp.get_data(as_text=True)

    def test_pages_403_without_view(self, app):
        """权限：无 death_report.view → 页面 403。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        seed = _seed_full(app)
        admin = _admin_client(app)
        _confirm(admin, seed['drid'])

        with app.app_context():
            role = Role(name='无viewT15-3', permissions=json.dumps({'resident.view': True, 'schema_version': 3}, ensure_ascii=False), is_system=False)
            db.session.add(role)
            db.session.commit()
            u = User(username='no_view_t15_3', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid

        assert client.get(f'/death-report/{seed["drid"]}/checklist_view').status_code == 403
        assert client.get(f'/death-report/{seed["drid"]}/notice-print').status_code == 403


# ============ T15-3 导出（temp/exports token 通道） ============
class TestExport:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _confirm_seed(self, app):
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        return seed, client

    def test_notice_export_token_download_admin(self, app):
        """告知单导出：token 通道下载成功，admin 含明文身份证。"""
        seed, client = self._confirm_seed(app)
        resp = client.post('/death-report/api/notice/export', data={'id': seed['drid']})
        body = resp.get_json()
        assert body['success'] is True, body
        url = body['data']['download_url']
        assert url.startswith('/report/download/')

        dl = client.get(url)
        assert dl.status_code == 200
        assert 'application/vnd.openxmlformats' in dl.headers.get('Content-Type', '')

    def test_notice_export_masked_without_sensitive(self, app):
        """告知单导出：无 view_sensitive 用户 → token 下载 403（导出需 death_report.export 权限）。"""
        seed, admin = self._confirm_seed(app)
        client, _ = _op_user_client(app)
        resp = client.post('/death-report/api/notice/export', data={'id': seed['drid']})
        assert resp.status_code == 403

    def test_checklist_export_token(self, app):
        """清单导出：token 通道下载成功。"""
        seed, client = self._confirm_seed(app)
        resp = client.post('/death-report/api/checklist/export', data={'id': seed['drid']})
        body = resp.get_json()
        assert body['success'] is True, body
        dl = client.get(body['data']['download_url'])
        assert dl.status_code == 200

    def test_export_missing_doc_404(self, app):
        """边界：未确认（无告知单）导出 → 提示未生成。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        resp = client.post('/death-report/api/notice/export', data={'id': seed['drid']})
        assert resp.get_json()['success'] is False
