# -*- coding: utf-8 -*-
"""022 居民主档数据同步测试（ResidentSyncService + api_save 钩子）。

覆盖（对应 022 验收标准）：
- 强/中耦合表按 resident_id 回写（name/village_group；dibao.grid 拍板 #5）
- medical 仅已关联行回写，未关联下发原始行不变；双电话列同写主档 phone
- 松散耦合按 id_card_hash 定位（special_support household_hash / veteran）
- 剩余 4 表抽样核验：unsupported_children / welfare_job / party_member /
  organization_member（审阅修复 P3-8，11 表全覆盖）
- death_reports 已确认行（cascade_status='done'）不回写，拍板 #2 历史快照（审阅修复）
- 加密字段（phone/bank_card）复制主档密文/哈希列，等值检索仍命中
- id_card 变更拒绝闸（拍板 #4/#6）：hash 等值对比，密文不可读仍 fail-closed（审阅修复 P3-6）
- 失败整体回滚 + failed 日志独立事务补写（故障注入）+ 失败原因落 error_message（审阅修复）
- sync_log 留痕（成功/失败、敏感字段脱敏）；affected_tables 按字段实际写入计入（审阅修复 P3-3）
- resident_sync_log.resident_id 无重复自动索引（审阅修复 P3-4）
- 无白名单变更不触发同步
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    for k, v in {
        "SHUCUN_DB_PATH": db_path,
        "SHUCUN_TEMP_DIR": str(tmp_path / "temp"),
        "SHUCUN_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SHUCUN_BACKUP_DIR": str(tmp_path / "backups"),
        "SHUCUN_DATA_DIR": str(tmp_path / "data"),
        "SHUCUN_LAN_AUTHORIZED": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    monkeypatch.setattr(config.Config, "IMPORTS_DIR", str(tmp_path / "temp" / "imports"))
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


def _unlock_keyholder():
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # admin 全权限
    return client


ID_CARD = '110101196001011234'


def _make_resident(app, name='张三', id_card=ID_CARD, phone='13812345678',
                   village_group='一组', **extra):
    client = _admin_client(app)
    payload = {'name': name, 'id_card': id_card, 'phone': phone,
               'village_group': village_group}
    payload.update(extra)
    r = client.post('/resident/api/save', data=payload)
    j = r.get_json()
    assert j['success'], j
    return j['data']['id']


def _edit(client, rid, **fields):
    data = {'id': rid}
    data.update(fields)
    return client.post('/resident/api/save', data=data)


def _row(app, model_cls, pk):
    with app.app_context():
        return model_cls.query.get(pk)


def _seed_strong(app, rid):
    """造强/中耦合副本：dibao/disabled/care_target + medical(已关联+未关联)。"""
    from models import db, Dibao, Disabled, CareTarget, MedicalInsurance
    with app.app_context():
        for cls, kw in [
            (Dibao, dict(resident_id=rid, name='旧名')),
            (Disabled, dict(resident_id=rid, name='旧名')),
            (CareTarget, dict(resident_id=rid, name='旧名', target_type='老人',
                             child_type='', source='手工建档')),
        ]:
            db.session.add(cls(**kw))
        # medical：一条已关联、一条未关联（下发原始数据，不得被回写）
        db.session.add(MedicalInsurance(resident_id=rid, name='旧名',
                                        link_status='已关联居民'))
        db.session.add(MedicalInsurance(resident_id=rid, name='下发原始',
                                        link_status='未关联居民'))
        db.session.commit()


def _seed_loose(app):
    """造松散耦合副本：special_support(household_hash) + veteran(id_card_hash)。"""
    from models import db, SpecialSupport, Veteran
    from utils.crypto import sha256_hash
    with app.app_context():
        db.session.add(SpecialSupport(household_name='旧户主', household_id_card=ID_CARD,
                                      household_id_card_hash=sha256_hash(ID_CARD),
                                      phone='13800000000', village_group='一组',
                                      status='有效'))
        db.session.add(Veteran(name='旧名', id_card=ID_CARD,
                               id_card_hash=sha256_hash(ID_CARD),
                               phone='13800000000'))
        db.session.commit()


def _seed_rest(app):
    """造剩余 4 表松散耦合副本（审阅修复 P3-8：11 表全覆盖抽样核验）。"""
    from models import (db, UnsupportedChild, WelfareJob, PartyMember,
                        OrganizationMember)
    from utils.crypto import sha256_hash
    h = sha256_hash(ID_CARD)
    with app.app_context():
        db.session.add(UnsupportedChild(name='旧名', id_card=ID_CARD, id_card_hash=h,
                                        gender='男', phone='13800000000',
                                        address='旧址', village_group='一组'))
        db.session.add(WelfareJob(name='旧名', id_card=ID_CARD, id_card_hash=h,
                                  phone='13800000000', village_group='一组'))
        db.session.add(PartyMember(name='旧名', id_card=ID_CARD, id_card_hash=h,
                                    gender='男', nation='汉族', phone='13800000000'))
        db.session.add(OrganizationMember(org_type='妇联', name='旧名',
                                          id_card=ID_CARD, id_card_hash=h,
                                          phone='13800000000'))
        db.session.commit()


# ═══════════════ 022 同步测试 ═══════════════

class TestResidentSync:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_strong_coupled_sync(self, app):
        """强/中耦合：改 name/village_group → 副本更新；dibao.grid 拍板 #5。"""
        _unlock_keyholder()
        from models import Dibao, Disabled, CareTarget
        rid = _make_resident(app)
        _seed_strong(app, rid)
        client = _admin_client(app)

        j = _edit(client, rid, name='李四', village_group='二组').get_json()
        assert j['success'], j

        assert _row(app, Dibao, 1).name == '李四'
        assert _row(app, Dibao, 1).grid == '二组'          # 拍板 #5：所在组→所在网格
        assert _row(app, Disabled, 1).name == '李四'
        assert _row(app, Disabled, 1).village_group == '二组'
        assert _row(app, CareTarget, 1).name == '李四'

    def test_medical_only_linked(self, app):
        """medical：已关联行回写，未关联下发原始行不变；双电话列同写主档 phone。"""
        _unlock_keyholder()
        from models import db, MedicalInsurance
        rid = _make_resident(app)
        _seed_strong(app, rid)
        client = _admin_client(app)

        j = _edit(client, rid, name='王五', phone='13911112222').get_json()
        assert j['success'], j

        with app.app_context():
            linked = MedicalInsurance.query.filter_by(link_status='已关联居民').first()
            unlinked = MedicalInsurance.query.filter_by(link_status='未关联居民').first()
            r = db.session.query(MedicalInsurance).filter_by(resident_id=rid).first()
        assert linked.name == '王五'
        # 双电话列 = 主档 phone 密文/哈希
        assert linked.contact_phone_enc and linked.mobile_phone_enc
        assert linked.contact_phone_hash == linked.mobile_phone_hash
        assert unlinked.name == '下发原始'           # 未关联不动

    def test_loose_by_hash(self, app):
        """松散耦合：special_support(household_hash) / veteran 按 id_card_hash 定位回写。"""
        _unlock_keyholder()
        from models import SpecialSupport, Veteran
        rid = _make_resident(app)
        _seed_loose(app)
        client = _admin_client(app)

        j = _edit(client, rid, name='赵六', phone='13911112222',
                  village_group='三组').get_json()
        assert j['success'], j

        sp = _row(app, SpecialSupport, 1)
        assert sp.village_group == '三组'
        assert sp.phone_enc is not None               # phone 已重建密文
        v = _row(app, Veteran, 1)
        assert v.name == '赵六'

    def test_phone_encrypted_rebuild(self, app):
        """加密字段：phone 变更 → 副本 phone_enc/phone_hash 与主档一致，等值检索命中。"""
        _unlock_keyholder()
        from models import db, Resident, Disabled
        rid = _make_resident(app)
        _seed_strong(app, rid)
        client = _admin_client(app)

        j = _edit(client, rid, phone='13700001111').get_json()
        assert j['success'], j

        with app.app_context():
            r = Resident.query.get(rid)
            d = Disabled.query.get(1)
            assert d.phone_enc == r.phone_enc
            assert d.phone_hash == r.phone_hash
            assert d.phone is None                   # 副本明文列置 NULL

    def test_id_card_change_rejected(self, app):
        """拍板 #4/#6：编辑改 id_card → 拒绝，主档 id_card 不变。"""
        _unlock_keyholder()
        from models import db, Resident
        rid = _make_resident(app)
        client = _admin_client(app)

        j = _edit(client, rid, name='新名', id_card='110101197001019999').get_json()
        assert j['success'] is False
        assert '不可修改' in j['msg']

        with app.app_context():
            r = Resident.query.get(rid)
            assert r.name == '张三'                  # 整体未提交
            assert r.id_card_hash

        # 回传相同 id_card → 放行
        j2 = _edit(client, rid, name='新名', id_card=ID_CARD).get_json()
        assert j2['success'], j2

    def test_rollback_on_failure(self, app, monkeypatch):
        """失败整体回滚 + failed 日志独立事务补写（故障注入）。"""
        _unlock_keyholder()
        from models import db, Resident, Dibao, ResidentSyncLog
        rid = _make_resident(app)
        _seed_strong(app, rid)
        client = _admin_client(app)

        import routes.resident as rmod

        def boom(resident, changes, operator=None):
            raise RuntimeError('模拟副本回写失败')

        monkeypatch.setattr(rmod.ResidentSyncService, 'propagate', boom)

        j = _edit(client, rid, name='会失败').get_json()
        assert j['success'] is False

        with app.app_context():
            assert Resident.query.get(rid).name == '张三'     # 主档回滚
            assert Dibao.query.get(1).name == '旧名'           # 副本未变
            log = ResidentSyncLog.query.filter_by(status='failed').first()
            assert log is not None                            # failed 独立事务留痕
            assert log.field == 'name'

    def test_sync_log_success_masked(self, app):
        """成功后 sync_log 留痕；敏感字段（phone）脱敏不落明文。"""
        _unlock_keyholder()
        from models import ResidentSyncLog
        rid = _make_resident(app)
        _seed_strong(app, rid)
        client = _admin_client(app)

        j = _edit(client, rid, name='改名人', phone='13600009999').get_json()
        assert j['success'], j

        with app.app_context():
            logs = ResidentSyncLog.query.filter_by(status='success').all()
            assert logs
            by_field = {l.field: l for l in logs}
            assert 'name' in by_field and 'phone' in by_field
            phone_log = by_field['phone']
            assert '13600009999' not in (phone_log.new_value or '')  # 脱敏
            assert phone_log.affected_tables                      # 影响表留痕

    def test_no_change_no_sync(self, app):
        """无白名单字段变更（只改 remark）→ 不触发同步、无 sync_log。"""
        _unlock_keyholder()
        from models import ResidentSyncLog
        rid = _make_resident(app)
        _seed_strong(app, rid)
        client = _admin_client(app)

        j = _edit(client, rid, remark='备注而已').get_json()
        assert j['success'], j

        with app.app_context():
            assert ResidentSyncLog.query.count() == 0

    def test_death_report_done_excluded(self, app):
        """拍板 #2（审阅修复）：已确认（done）死亡登记保持历史快照不回写；pending 行照常回写。"""
        _unlock_keyholder()
        from models import db, DeathReport
        from utils.crypto import sha256_hash
        from datetime import date
        rid = _make_resident(app)
        with app.app_context():
            db.session.add(DeathReport(
                deceased_name='旧名-已确认', death_date=date(2020, 1, 1),
                id_card=ID_CARD, id_card_hash=sha256_hash(ID_CARD),
                cascade_status='done'))
            db.session.add(DeathReport(
                deceased_name='旧名-待确认', death_date=date(2021, 1, 1),
                id_card=ID_CARD, id_card_hash=sha256_hash(ID_CARD),
                cascade_status='pending'))
            db.session.commit()
        client = _admin_client(app)

        j = _edit(client, rid, name='历史不改名').get_json()
        assert j['success'], j

        with app.app_context():
            done = DeathReport.query.filter_by(cascade_status='done').first()
            pending = DeathReport.query.filter_by(cascade_status='pending').first()
            assert done.deceased_name == '旧名-已确认'      # 拍板 #2：已确认保持快照
            assert pending.deceased_name == '历史不改名'    # 未确认照常同步

    def test_failed_log_error_message(self, app, monkeypatch):
        """审阅修复：failed 日志落 error_message 失败原因，供排查。"""
        _unlock_keyholder()
        from models import db, ResidentSyncLog
        rid = _make_resident(app)
        _seed_strong(app, rid)
        client = _admin_client(app)

        import routes.resident as rmod

        def boom(resident, changes, operator=None):
            raise RuntimeError('模拟副本回写失败-审阅修复')

        monkeypatch.setattr(rmod.ResidentSyncService, 'propagate', boom)

        j = _edit(client, rid, name='会失败').get_json()
        assert j['success'] is False

        with app.app_context():
            log = ResidentSyncLog.query.filter_by(status='failed').first()
            assert log is not None
            assert '模拟副本回写失败' in (log.error_message or '')   # 失败原因留痕

    def test_rest_tables_sync(self, app):
        """审阅修复 P3-8：剩余 4 表（unsupported_children/welfare_job/party_member/
        organization_member）按 FIELD_MAP 回写，11 表全覆盖。"""
        _unlock_keyholder()
        from models import (db, Resident, UnsupportedChild, WelfareJob, PartyMember,
                            OrganizationMember)
        rid = _make_resident(app)
        _seed_rest(app)
        client = _admin_client(app)

        j = _edit(client, rid, name='新名', phone='13911112222',
                  village_group='五组', bank_card='6222020000009999').get_json()
        assert j['success'], j

        with app.app_context():
            r = Resident.query.get(rid)
            uc = UnsupportedChild.query.first()
            assert uc.name == '新名'
            assert uc.phone_enc == r.phone_enc and uc.phone is None   # 敏感列复制密文
            wj = WelfareJob.query.first()
            assert wj.name == '新名' and wj.village_group == '五组'
            assert wj.phone_enc == r.phone_enc
            pm = PartyMember.query.first()
            assert pm.name == '新名'
            assert pm.phone_enc == r.phone_enc
            om = OrganizationMember.query.first()
            assert om.name == '新名'
            assert om.phone_enc == r.phone_enc

    def test_affected_tables_per_field(self, app):
        """审阅修复 P3-3：affected_tables 按字段实际写入的表计入。
        只改 bank_card → 仅 dibao 计入（care_targets 无 bank_card 映射不得计入）。"""
        _unlock_keyholder()
        from models import ResidentSyncLog
        import json as _json
        rid = _make_resident(app)
        _seed_strong(app, rid)
        client = _admin_client(app)

        j = _edit(client, rid, bank_card='6222020000009999').get_json()
        assert j['success'], j

        with app.app_context():
            logs = ResidentSyncLog.query.filter_by(status='success').all()
            assert len(logs) == 1 and logs[0].field == 'bank_card'
            affected = _json.loads(logs[0].affected_tables)
            assert affected == {'dibao': 1}                    # 仅实际写入的表
            assert 'care_targets' not in affected              # 无映射字段不计入

    def test_id_card_gate_fail_closed(self, app):
        """审阅修复 P3-6：密文不可读（解密失败）时 id_card 变更闸仍生效（hash 等值）。"""
        _unlock_keyholder()
        from models import db, Resident
        rid = _make_resident(app)
        with app.app_context():
            r = Resident.query.get(rid)
            r.id_card_enc = 'GARBAGE-CIPHER-INVALID'           # 模拟密文损坏/密钥失配
            db.session.commit()
        client = _admin_client(app)

        # 不同新证号 → 拒绝（旧实现解密失败返回空串时闸门放行）
        j = _edit(client, rid, name='新名', id_card='110101197001019999').get_json()
        assert j['success'] is False
        assert '不可修改' in j['msg']

        # 回传相同 id_card → hash 等值放行
        j2 = _edit(client, rid, name='新名', id_card=ID_CARD).get_json()
        assert j2['success'], j2

    def test_no_duplicate_resident_id_index(self, app):
        """审阅修复 P3-4：resident_sync_log.resident_id 仅保留命名索引。"""
        from models import db
        from sqlalchemy import text
        with app.app_context():
            rows = db.session.execute(text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='resident_sync_log'")).fetchall()
            names = {row[0] for row in rows}
        assert 'idx_resident_sync_log_rid' in names             # 命名索引在
        assert 'ix_resident_sync_log_resident_id' not in names  # 自动重复索引不在
