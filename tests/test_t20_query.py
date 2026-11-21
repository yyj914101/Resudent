# -*- coding: utf-8 -*-
"""T20-4 自定义查询单测（Sprint6，R9-E2）。

覆盖（对应 PRD L619/L626 示例 + 计划稿 §4.2 验收第 5 条）：
- AND 组合（第3网格 + 低保 + 男 + 60岁以上 → PRD L626 示例）
- OR 组合
- 预览脱敏（无 view_sensitive 出脱敏版；admin 明文）
- 导出 Excel token 通道
- 字段白名单注入拒绝 400
- 权限 403
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from test_t15_cascade import (
    _make_app, _admin_client, _make_resident, _make_dibao, ID_CARD,
)


def _seed_grid(app):
    """造居民：第3网格 男 70岁（低保）→ 应命中；第1网格 女 30岁 → 不命中。"""
    r1 = _make_resident(app, '张三', __import__('datetime').date(1956, 1, 1), ID_CARD)  # 70岁
    r2 = _make_resident(app, '李四', __import__('datetime').date(1996, 5, 5), '110101199605051234')  # 30岁
    with app.app_context():
        from models import db, Resident
        db.session.query(Resident).filter_by(id=r1).update(
            {'village_group': '第3网格', 'gender': '男'})
        db.session.query(Resident).filter_by(id=r2).update(
            {'village_group': '第1网格', 'gender': '女'})
        db.session.commit()
    _make_dibao(app, r1)  # 张三低保
    return r1, r2


def _query(client, tree):
    return client.post('/report/api/custom-query', json=tree)


class TestCustomQuery:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_and_combination(self, app):
        """AND 组合：第3网格 + 低保 + 男 + 60岁以上（PRD L626 示例）。"""
        _seed_grid(app)
        client = _admin_client(app)
        tree = {'logic': 'AND', 'conditions': [
            {'field': 'resident.grid', 'op': 'eq', 'value': '第3网格'},
            {'field': 'dibao.status', 'op': 'eq', 'value': '有效'},
            {'field': 'resident.gender', 'op': 'eq', 'value': '男'},
            {'field': 'resident.age', 'op': 'gt', 'value': '60'},
        ]}
        resp = _query(client, tree)
        body = resp.get_json()
        assert body['success'] is True, body
        rows = body['data']
        assert len(rows) == 1
        assert rows[0]['name'] == '张三'

    def test_or_combination(self, app):
        """OR 组合：姓名含'张' 或 第1网格。"""
        _seed_grid(app)
        client = _admin_client(app)
        tree = {'logic': 'OR', 'conditions': [
            {'field': 'resident.name', 'op': 'contains', 'value': '张'},
            {'field': 'resident.grid', 'op': 'eq', 'value': '第1网格'},
        ]}
        body = _query(client, tree).get_json()
        assert body['success'] is True
        assert len(body['data']) == 2  # 张三 + 李四

    def test_preview_masked_without_sensitive(self, app):
        """无 view_sensitive：预览身份证脱敏（前6后4），不含明文。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        _seed_grid(app)
        admin = _admin_client(app)
        tree = {'logic': 'AND', 'conditions': [
            {'field': 'resident.name', 'op': 'eq', 'value': '张三'}]}
        assert _query(admin, tree).get_json()['success'] is True

        # 普通操作员：report.view 仅（v3 权限格式）
        with app.app_context():
            role = Role(name='查询操作员T20', permissions=json.dumps(
                {'report.view': True, 'schema_version': 3}, ensure_ascii=False), is_system=False)
            db.session.add(role)
            db.session.commit()
            u = User(username='op_t20q', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id
        op = app.test_client()
        with op.session_transaction() as sess:
            sess['user_id'] = uid

        body = _query(op, tree).get_json()
        assert body['success'] is True
        row = body['data'][0]
        assert ID_CARD not in row['id_card']
        assert row['id_card'].startswith('110101')  # 前6保留
        assert row['id_card'].endswith('1234')      # 后4保留

    def test_admin_sees_plaintext(self, app):
        """admin（view_sensitive）：预览含明文身份证。"""
        _seed_grid(app)
        client = _admin_client(app)
        tree = {'logic': 'AND', 'conditions': [
            {'field': 'resident.name', 'op': 'eq', 'value': '张三'}]}
        rows = _query(client, tree).get_json()['data']
        assert rows[0]['id_card'] == ID_CARD

    def test_export_token(self, app):
        """导出 Excel：token 通道下载成功且含数据行。"""
        _seed_grid(app)
        client = _admin_client(app)
        tree = {'logic': 'AND', 'conditions': [
            {'field': 'resident.grid', 'op': 'eq', 'value': '第3网格'}]}
        resp = client.post('/report/api/custom-query/export', json=tree)
        body = resp.get_json()
        assert body['success'] is True, body
        url = body['data']['download_url']
        assert url.startswith('/report/download/')
        dl = client.get(url)
        assert dl.status_code == 200
        assert 'application/vnd.openxmlformats' in dl.headers.get('Content-Type', '')

    def test_injection_field_rejected(self, app):
        """非白名单字段注入 → 400。"""
        _seed_grid(app)
        client = _admin_client(app)
        tree = {'logic': 'AND', 'conditions': [
            {'field': "resident.name' OR 1=1 --", 'op': 'eq', 'value': 'x'}]}
        resp = _query(client, tree)
        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

    def test_unknown_operator_rejected(self, app):
        """非法操作符 → 400。"""
        _seed_grid(app)
        client = _admin_client(app)
        tree = {'logic': 'AND', 'conditions': [
            {'field': 'resident.name', 'op': 'drop', 'value': 'x'}]}
        resp = _query(client, tree)
        assert resp.status_code == 400

    def test_permission_403(self, app):
        """无 report.view 用户访问 → 403。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='无权限T20Q', permissions=json.dumps(
                {'schema_version': 3}, ensure_ascii=False), is_system=False)
            db.session.add(role)
            db.session.commit()
            u = User(username='no_t20q', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        assert client.get('/report/custom-query').status_code == 403
        resp = client.post('/report/api/custom-query', json={'logic': 'AND', 'conditions': []})
        assert resp.status_code == 403
