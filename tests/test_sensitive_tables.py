# -*- coding: utf-8 -*-
"""14 台账模型字段级加密回归测试（R12 复用重构收官）。

覆盖：本轮将 Disabled/CareTarget/SpecialSupport/Veteran/DeathReport/
MedicalInsurance/PartyMember/OrganizationMember/
Mediation/UnsupportedChild/WelfareJob 12 个台账接入
utils.sensitive_fields 通用管线后，逐表验证：
  ① 写入 → 原列置 NULL
  ② 密文落库（Fernet gAAAA 前缀）
  ③ sha256 等值哈希正确
  ④ 解密还原正确（含末位 X 大小写归一）
  ⑤ sha256_hash 等值检索命中
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from models import (
    Disabled, CareTarget, SpecialSupport, Veteran, DeathReport,
    MedicalInsurance, PartyMember, OrganizationMember,
    Mediation, UnsupportedChild, WelfareJob,
)


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    monkeypatch.setenv("SHUCUN_DB_PATH", db_path)
    monkeypatch.setenv("SHUCUN_TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("SHUCUN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SHUCUN_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("SHUCUN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHUCUN_SECRET_KEY", "test")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


def _unlock_keyholder():
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())


# 14 表：模型、敏感字段映射（field → (enc, hash, prefix)）、样例明文、构造器
_CASES = [
    ("Disabled", "disabled_persons", Disabled,
     {"id_card": ("id_card_enc", "id_card_hash", None),
      "phone": ("phone_enc", "phone_hash", None),
      "guardian_phone": ("guardian_phone_enc", "guardian_phone_hash", None)},
     {"id_card": "11010119900101123x", "phone": "13812345678", "guardian_phone": "13912345678"},
     lambda: Disabled()),
    ("CareTarget", "care_targets", CareTarget,
     {"guardian_phone": ("guardian_phone_enc", "guardian_phone_hash", None)},
     {"guardian_phone": "13712345678"},
     lambda: CareTarget()),
    ("SpecialSupport", "special_support", SpecialSupport,
     {"household_id_card": ("household_id_card_enc", "household_id_card_hash", None),
      "bank_card": ("bank_card_enc", "bank_card_hash", None),
      "account_holder_id_card": ("account_holder_id_card_enc", "account_holder_id_card_hash", None),
      "phone": ("phone_enc", "phone_hash", None)},
     {"household_id_card": "11010119900101123x", "bank_card": "6221881234567890",
      "account_holder_id_card": "110101199001011234", "phone": "13812345678"},
     lambda: SpecialSupport()),
    ("Veteran", "veteran", Veteran,
     {"id_card": ("id_card_enc", "id_card_hash", None),
      "phone": ("phone_enc", "phone_hash", None),
      "relative_phone": ("relative_phone_enc", "relative_phone_hash", None)},
     {"id_card": "11010119900101123x", "phone": "13812345678", "relative_phone": "13912345678"},
     lambda: Veteran(name='测试')),
    ("DeathReport", "death_reports", DeathReport,
     {"id_card": ("id_card_enc", "id_card_hash", None)},
     {"id_card": "11010119900101123x"},
     lambda: DeathReport(deceased_name='测试', death_date=date(2026, 1, 1))),
    ("MedicalInsurance", "medical_insurance_payments", MedicalInsurance,
     {"id_card": ("id_card_enc", "id_card_hash", None),
      "contact_phone": ("contact_phone_enc", "contact_phone_hash", None),
      "mobile_phone": ("mobile_phone_enc", "mobile_phone_hash", None)},
     {"id_card": "11010119900101123x", "contact_phone": "13812345678", "mobile_phone": "13912345678"},
     lambda: MedicalInsurance()),
    ("PartyMember", "party_members", PartyMember,
     {"id_card": ("id_card_enc", "id_card_hash", None),
      "phone": ("phone_enc", "phone_hash", None)},
     {"id_card": "11010119900101123x", "phone": "13812345678"},
     lambda: PartyMember(name='测试')),
    ("OrganizationMember", "organization_members", OrganizationMember,
     {"id_card": ("id_card_enc", "id_card_hash", None),
      "phone": ("phone_enc", "phone_hash", None)},
     {"id_card": "11010119900101123x", "phone": "13812345678"},
     lambda: OrganizationMember(org_type='机构', name='测试')),
    ("Mediation", "mediations", Mediation,
     {"applicant_id_card": ("applicant_id_card_enc", "applicant_id_card_hash", None),
      "respondent_id_card": ("respondent_id_card_enc", "respondent_id_card_hash", None)},
     {"applicant_id_card": "11010119900101123x", "respondent_id_card": "110101199001011234"},
     lambda: Mediation()),
    ("UnsupportedChild", "unsupported_children", UnsupportedChild,
     {"id_card": ("id_card_enc", "id_card_hash", None),
      "phone": ("phone_enc", "phone_hash", None)},
     {"id_card": "11010119900101123x", "phone": "13812345678"},
     lambda: UnsupportedChild()),
    ("WelfareJob", "welfare_jobs", WelfareJob,
     {"id_card": ("id_card_enc", "id_card_hash", None),
      "phone": ("phone_enc", "phone_hash", None),
      "bank_info": ("bank_info_enc", "bank_info_hash", None)},
     {"id_card": "11010119900101123x", "phone": "13812345678", "bank_info": "6221881234567890"},
     lambda: WelfareJob(name='测试')),
]


@pytest.mark.parametrize("model_name,table,model,field_map,samples,factory", _CASES,
                         ids=[c[1] for c in _CASES])
def test_sensitive_roundtrip(tmp_path, monkeypatch, model_name, table, model, field_map, samples, factory):
    """① 原列 NULL ② 密文落库 ③ hash 正确 ④ 解密还原 ⑤ 等值检索命中。"""
    app = _make_app(tmp_path, monkeypatch)
    _unlock_keyholder()

    from utils.crypto import key_holder, sha256_hash
    from utils.sensitive_fields import make_sensitive_helpers
    from models import db

    decrypt, set_enc, _clause = make_sensitive_helpers(model, field_map, key_holder)

    with app.app_context():
        db.create_all()
        rec = factory()
        for field, raw in samples.items():
            set_enc(rec, field, raw)
        db.session.add(rec)
        db.session.commit()
        rid = rec.id

    with app.app_context():
        row = model.query.get(rid)
        for field, raw in samples.items():
            enc_col, hash_col, prefix_col = field_map[field]
            # ① 原列置 NULL
            assert getattr(row, field) is None, f"{table}.{field} 原列应为 NULL"
            # ② 密文落库（Fernet gAAAA 前缀）
            enc = getattr(row, enc_col)
            assert enc is not None and enc.startswith("gAAAA"), f"{table}.{field}_enc 应为 Fernet 密文"
            # ④ 解密还原（id_card 类统一大写）
            expected = raw.upper() if field.endswith("id_card") else raw
            assert decrypt(row, field) == expected, f"{table}.{field} 解密还原失败"
            # ③ sha256 等值哈希正确
            assert getattr(row, hash_col) == sha256_hash(expected), f"{table}.{field}_hash 错误"
            # ⑤ 等值检索命中
            found = model.query.filter(getattr(model, hash_col) == sha256_hash(expected)).first()
            assert found is not None and found.id == rid, f"{table}.{field} 哈希检索未命中"


def test_sensitive_columns_created_by_migration(tmp_path, monkeypatch):
    """m003 迁移幂等补列覆盖全部 16 表敏感字段（含本轮 14 表）。"""
    app = _make_app(tmp_path, monkeypatch)
    _unlock_keyholder()

    from utils.crypto import key_holder
    from scripts.migrations.run_migration import ensure_sensitive_columns, load_inventory
    from models import db

    inventory = load_inventory()
    with app.app_context():
        db.create_all()
        # 首跑：db.create_all 已按模型建好 _enc/_hash 列，ensure_sensitive_columns 幂等（不再新增）
        stats = ensure_sensitive_columns(db)
        # 重跑幂等：第二次不再新增列
        stats2 = ensure_sensitive_columns(db)
        assert stats2['added_columns'] == 0

        from sqlalchemy import text
        for t in inventory['tables']:
            for field in t['fields']:
                for suffix in ('_enc', '_hash'):
                    col = f'{field}{suffix}'
                    rows = db.session.execute(text(f"PRAGMA table_info({t['table']})")).fetchall()
                    names = {r[1] for r in rows}
                    assert col in names, f"缺少列 {t['table']}.{col}"
