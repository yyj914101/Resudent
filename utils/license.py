"""
授权校验核心模块（P0-2 整改：对称 HMAC 签名 → Ed25519 非对称验签）。

红线（详细设计 v1.1 §1.2 / 宪法 §1）：
- 程序内仅含「公钥」用于验签；「私钥」由作者离线持有，绝不随客户包分发。
- 公钥可在构建期/部署期经 SHUCUN_LICENSE_PUBLIC_KEY 环境变量注入覆盖。
- 机器码改用纯标准库（MAC + 稳定机器标识），去除 shell=True + wmic 弃用依赖（P3-2）。
"""
import base64
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, date

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

# 验签公钥（Ed25519，SubjectPublicKeyInfo PEM）。仅含公钥，可随包分发；
# 私钥由作者离线持有（scripts/author_license_private_key.pem，已被 .gitignore 排除）。
_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAhurTfVrmHM9FD4tYd9zydSI71lRcGPku8tc1F2nQqF4=
-----END PUBLIC KEY-----
"""


def _stable_machine_id() -> str:
    """稳定系统级机器标识（替代 wmic diskdrive serialnumber）。

    - Windows → 注册表 HKLM\\SOFTWARE\\Microsoft\\Cryptography 的 MachineGuid
                （显式 KEY_WOW64_64KEY，消除 32/64 位注册表视图差异）
    - Linux   → /etc/machine-id（或 /var/lib/dbus/machine-id）
    - 其他/失败 → 空串

    纯标准库实现，无 shell=True 注入面，不依赖已弃用的 wmic，且不依赖任何网卡 MAC。
    """
    try:
        if sys.platform == "win32":
            import winreg
            access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                access,
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                return str(value or "").strip()
        else:
            for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as f:
                        value = f.read().strip()
                    if value:
                        return value
    except Exception:
        pass
    return ""


def _stable_hardware_id() -> str:
    """稳定主板/BIOS 硬件标识（辅助锚点，跨重启与网络切换恒定，无网卡依赖）。"""
    try:
        if sys.platform == "win32":
            import winreg
            access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
            parts = []
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\BIOS",
                0,
                access,
            ) as key:
                for field in ("BaseBoardManufacturer", "BaseBoardProduct", "SystemManufacturer", "SystemProductName"):
                    try:
                        val, _ = winreg.QueryValueEx(key, field)
                        s = str(val or "").strip()
                        if s:
                            parts.append(s)
                    except OSError:
                        continue
            return "|".join(parts)
        else:
            for path in ("/sys/class/dmi/id/product_uuid", "/sys/class/dmi/id/board_name"):
                if os.path.exists(path):
                    try:
                        with open(path, encoding="utf-8") as f:
                            val = f.read().strip()
                        if val:
                            return val
                    except OSError:
                        continue
    except Exception:
        pass
    return ""


def _fallback_machine_id() -> str:
    """极端环境（无 MachineGuid 且无硬件标识）的持久化兜底标识，杜绝退回随机 MAC。"""
    lic_dir = os.path.dirname(os.path.abspath(_get_license_path()))
    id_file = os.path.join(lic_dir, ".machine_id")
    try:
        if os.path.exists(id_file):
            with open(id_file, "r", encoding="utf-8") as f:
                saved = f.read().strip()
            if saved:
                return saved
        os.makedirs(lic_dir, exist_ok=True)
        generated = uuid.uuid4().hex
        with open(id_file, "w", encoding="utf-8") as f:
            f.write(generated)
        return generated
    except OSError:
        return "STATIC-FALLBACK"


def _legacy_machine_code() -> str:
    """旧版（v1）机器码：MAC(uuid.getnode) + MachineGuid。仅用于验签向后兼容。"""
    mac = hex(uuid.getnode()).upper()
    raw = f"{mac}|{_stable_machine_id()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16].upper()


def get_machine_code() -> str:
    """生成稳定机器码（v2）：仅基于系统/硬件静态锚点，彻底移除网卡 MAC 依赖。

    解决 Windows 虚拟网卡（Hyper-V/WSL/VPN 等）或随机化 MAC 跨重启变化导致的机器码漂移。
    """
    guid = _stable_machine_id()
    hw = _stable_hardware_id()
    if guid or hw:
        raw = f"v2|{guid}|{hw}"
    else:
        raw = f"v2|fallback|{_fallback_machine_id()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16].upper()


def _payload_bytes(license_data: dict) -> bytes:
    """授权载荷规范化字节（剔除 sign 字段后 JSON 稳定序列化）。"""
    return json.dumps(
        {k: v for k, v in license_data.items() if k != "sign"},
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")


def _public_key():
    """返回验签公钥对象：优先环境变量 SHUCUN_LICENSE_PUBLIC_KEY，否则内置公钥。"""
    pem = (os.environ.get("SHUCUN_LICENSE_PUBLIC_KEY") or "").strip()
    if pem:
        key_bytes = pem.encode("utf-8") if "-----BEGIN" not in pem else pem
    else:
        key_bytes = _PUBLIC_KEY_PEM
    if isinstance(key_bytes, str):
        key_bytes = key_bytes.encode("utf-8")
    return serialization.load_pem_public_key(key_bytes)


def _load_private_key(private_key_pem=None):
    """加载作者私钥（Ed25519 PKCS8 PEM）。

    来源优先级：显式参数 > SHUCUN_LICENSE_PRIVATE_KEY 环境变量 >
    默认作者私钥文件（scripts/author_license_private_key.pem）。仅作者本地工具使用。
    """
    if private_key_pem is None:
        private_key_pem = os.environ.get("SHUCUN_LICENSE_PRIVATE_KEY") or ""
        if not private_key_pem:
            default = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts", "author_license_private_key.pem",
            )
            if os.path.exists(default):
                with open(default, "rb") as f:
                    private_key_pem = f.read()
    if isinstance(private_key_pem, str):
        private_key_pem = private_key_pem.encode("utf-8")
    if not private_key_pem:
        raise ValueError("缺少作者私钥：请提供 private_key_pem 或设置 SHUCUN_LICENSE_PRIVATE_KEY")
    return serialization.load_pem_private_key(private_key_pem, password=None)


def sign_license(payload: dict, private_key_pem=None) -> dict:
    """作者侧签发：对 payload（不含 sign）做 Ed25519 签名，返回带 sign 的 payload。

    sign 以 base64 字符串写入 payload['sign']。仅作者本地工具调用，私钥绝不随包分发。
    """
    private_key = _load_private_key(private_key_pem)
    signature = private_key.sign(_payload_bytes(payload))
    payload["sign"] = base64.b64encode(signature).decode("ascii")
    return payload


def generate_license(machine_code, expire_date, owner="", private_key_pem=None):
    """
    生成授权数据（仅作者本地调用；P0-2 整改后改为非对称签名）。

    machine_code: 客户机器码
    expire_date: 'forever' 或 '2026-12-31'
    owner: 授权对象名称
    private_key_pem: 作者私钥（缺省从环境变量/默认私钥文件读取）
    """
    payload = {
        'machine': machine_code,
        'expire': expire_date,
        'owner': owner,
        'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'version': '2.5',
    }
    return sign_license(payload, private_key_pem)


def verify_license(license_data):
    """
    校验授权文件（非对称验签）
    返回 (ok, msg, info_dict)
    """
    try:
        # 不修改原数据，先验签
        sign_b64 = license_data.get('sign', '')
        if not sign_b64:
            return False, "授权文件签名缺失", {}
        try:
            signature = base64.b64decode(sign_b64)
            _public_key().verify(signature, _payload_bytes(license_data))
        except (InvalidSignature, ValueError, TypeError):
            return False, "授权文件签名无效，已被篡改", {}

        current_code = get_machine_code()
        allowed_codes = {current_code, _legacy_machine_code()}
        if license_data.get('machine') not in allowed_codes:
            return False, f"机器码不匹配（本机: {current_code}）", {}

        expire = license_data.get('expire', '')
        if expire and expire != 'forever':
            try:
                exp_date = datetime.strptime(expire, '%Y-%m-%d').date()
                if exp_date < date.today():
                    return False, f"授权已过期（{expire}）", {}
            except ValueError:
                return False, "授权文件日期格式错误", {}

        lic_type = "局域网版"
        expire_display = '永久版' if expire == 'forever' else expire

        info = {
            'machine': license_data.get('machine', ''),
            'expire': expire_display,
            'owner': license_data.get('owner', ''),
            'type': lic_type,
            'status': '已授权'
        }
        return True, "授权校验通过", info

    except Exception as e:
        return False, f"校验异常: {str(e)}", {}


def _get_license_path():
    """获取授权文件路径"""
    try:
        from config import LICENSE_FILE
        return LICENSE_FILE
    except ImportError:
        base = os.path.dirname(os.path.abspath(__file__))
        return os.path.normpath(os.path.join(base, '..', 'license.key'))


def get_license_status():
    """获取当前授权状态（给前端用）"""
    machine_code = get_machine_code()
    lic_path = _get_license_path()

    default = {
        'status': '未授权',
        'type': '单机版',
        'machine': machine_code,
        'expire': '待接入',
        'owner': '待导入',
        'desc': '当前为单机模式，仅本机可访问。如需开通局域网访问，请将机器码或导出的配置文件发给作者获取授权',
        'license_exists': os.path.exists(lic_path)
    }

    if not os.path.exists(lic_path):
        return default

    try:
        with open(lic_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        default['desc'] = '授权文件损坏，请重新导入'
        return default

    ok, msg, info = verify_license(data)
    if ok:
        return {
            'status': '已授权',
            'type': '局域网版',
            'machine': machine_code,
            'expire': info.get('expire', '永久版'),
            'owner': f"已绑定：{info.get('owner', '')}",
            'desc': '授权正常生效，当前为局域网模式，局域网内其他电脑可正常访问',
            'license_exists': True
        }
    else:
        return {
            'status': '未授权',
            'type': '单机版',
            'machine': machine_code,
            'expire': '待接入',
            'owner': '待导入',
            'desc': f'授权校验失败：{msg}',
            'license_exists': True
        }


def check_license_on_startup():
    """启动时校验，返回 True=局域网模式 / False=仅本机"""
    status = get_license_status()
    return status['status'] == '已授权'
