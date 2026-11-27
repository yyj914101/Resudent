# -*- coding: utf-8 -*-
"""tests/test_123_license_machine_code_stability.py — 机器码防虚拟网卡 MAC 漂移与旧码兼容回归测试。

注：外发补丁原文件名 test_117_*.py，套打时按主仓任务号重命名为 test_123（任务 123 机器码根治），
消除与 BOARD 任务 117（真实数据走查）的编号巧合重叠（110/111/113/114/115/116 同款先例）。
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from utils import license as lic


def _ephemeral_keypair():
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem.decode("utf-8")


def test_machine_code_immune_to_virtual_nic_mac_drift(monkeypatch):
    """虚拟网卡 MAC 跨重启变化（含本地管理随机 MAC FA:95:CA:10:3D:FF）时，机器码必须保持恒定。"""
    monkeypatch.setattr(lic, "_stable_machine_id", lambda: "11111111-2222-3333-4444-555555555555")
    monkeypatch.setattr(lic, "_stable_hardware_id", lambda: "ASUSTeK|PRIME-B550M")

    macs = [0xFA95CA103DFF, 0x021122334455, 0x00155D010203, 0xDEADBEEF1234]
    codes = []
    for m in macs:
        monkeypatch.setattr(uuid, "getnode", lambda m=m: m)
        c = lic.get_machine_code()
        assert len(c) == 16 and c == c.upper()
        codes.append(c)

    assert len(set(codes)) == 1, f"虚拟网卡 MAC 变化导致机器码漂移：{codes}"


def test_v2_license_survives_virtual_mac_change_across_reboots(monkeypatch):
    """按 v2 机器码签发的授权，在虚拟网卡 MAC 变更后仍应验签通过。"""
    priv_pem, pub_pem = _ephemeral_keypair()
    monkeypatch.setenv("SHUCUN_LICENSE_PUBLIC_KEY", pub_pem)
    monkeypatch.setattr(lic, "_stable_machine_id", lambda: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    monkeypatch.setattr(lic, "_stable_hardware_id", lambda: "LENOVO|ThinkCentre")

    monkeypatch.setattr(uuid, "getnode", lambda: 0xFA95CA103DFF)
    code_v2 = lic.get_machine_code()
    payload = lic.generate_license(code_v2, "forever", owner="测试村委", private_key_pem=priv_pem)

    monkeypatch.setattr(uuid, "getnode", lambda: 0x02AA11BB22CC)
    ok, msg, info = lic.verify_license(payload)
    assert ok, msg
    assert info["status"] == "已授权"
    assert info["machine"] == code_v2


def test_legacy_license_backward_compatible_when_mac_unchanged(monkeypatch):
    """未漂移的存量旧版（v1 MAC+MachineGuid）授权在升级后仍应兼容通过。"""
    priv_pem, pub_pem = _ephemeral_keypair()
    monkeypatch.setenv("SHUCUN_LICENSE_PUBLIC_KEY", pub_pem)
    monkeypatch.setattr(lic, "_stable_machine_id", lambda: "99999999-8888-7777-6666-555555555555")
    monkeypatch.setattr(lic, "_stable_hardware_id", lambda: "DELL|OptiPlex")
    monkeypatch.setattr(uuid, "getnode", lambda: 0x001A2B3C4D5E)

    legacy_code = lic._legacy_machine_code()
    assert legacy_code != lic.get_machine_code()
    payload = lic.generate_license(legacy_code, "forever", owner="老客户村委", private_key_pem=priv_pem)

    ok, msg, _info = lic.verify_license(payload)
    assert ok, msg


def test_mismatched_machine_code_still_rejected(monkeypatch):
    """非本机签发的授权文件必须仍被拒绝。"""
    priv_pem, pub_pem = _ephemeral_keypair()
    monkeypatch.setenv("SHUCUN_LICENSE_PUBLIC_KEY", pub_pem)
    monkeypatch.setattr(lic, "_stable_machine_id", lambda: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    monkeypatch.setattr(lic, "_stable_hardware_id", lambda: "HP|ProDesk")
    monkeypatch.setattr(uuid, "getnode", lambda: 0xFA95CA103DFF)

    payload = lic.generate_license("DEADCAFEBABE0001", "forever", owner="他机", private_key_pem=priv_pem)
    ok, msg, _info = lic.verify_license(payload)
    assert not ok
    assert "机器码不匹配" in msg


def test_fallback_machine_id_persists_when_os_ids_unavailable(monkeypatch, tmp_path):
    """当系统 MachineGuid 与硬件标识均不可用时，应落盘持久化 .machine_id 而非退回随机 MAC。"""
    monkeypatch.setattr(lic, "_stable_machine_id", lambda: "")
    monkeypatch.setattr(lic, "_stable_hardware_id", lambda: "")
    monkeypatch.setattr(lic, "_get_license_path", lambda: str(tmp_path / "license.key"))

    monkeypatch.setattr(uuid, "getnode", lambda: 0xFA95CA103DFF)
    c1 = lic.get_machine_code()
    assert (tmp_path / ".machine_id").exists()

    monkeypatch.setattr(uuid, "getnode", lambda: 0x029988776655)
    c2 = lic.get_machine_code()
    assert c1 == c2
