# -*- coding: utf-8 -*-
"""T20-1/2 .wbak 加密备份管线（R9-E1~E4/R12-E5/E7，owner=utils/）。

容器格式：
    .wbak = 8B 魔数 b'SCWNBK01' + 分块 Fernet 密文
    密文内 zip：shucun.db（WAL checkpoint 后）+ uploads/（整体）+ meta.json
    meta.json = {format_version, created_at, db_sha256, uploads_sha256,
                 file_count, app_version}

要点：
- 密钥 = 启动口令 PBKDF2 派生（key_holder 内存密钥）；口令遗忘则备份不可解（= 设计意图，R12 态④）
- 原子写入：同目录 .tmp → os.replace；失败清理无半成品（R9-E3）
- 预检：disk_usage 余量 ≥1.5x + os.access(W_OK) + 写探针（R9-E3）
- 滚动保留：保留最近 N 份 .wbak（默认 10），删最旧；旧 .db 不参与（R1 兼容）
"""
import json
import os
import shutil
import zipfile
from datetime import datetime

MAGIC = b'SCWNBK01'
FORMAT_VERSION = 1
DEFAULT_RETENTION = 10

# 应用版本（展示用，读 app 版本锚点）
APP_VERSION = 'V2.45-K3'


def _now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ============ 分块加解密（封装 key_holder） ============

def encrypt_to_wbak(raw: bytes) -> bytes:
    """原始打包 bytes → 魔数 + 密文 bytes。"""
    from utils.crypto import key_holder
    if not key_holder.is_initialized:
        raise ValueError('密钥未解锁，无法加密备份（请先输入启动口令）')
    return MAGIC + key_holder.encrypt_bytes(raw)


def decrypt_from_wbak(data: bytes) -> bytes:
    """.wbak bytes → 原始打包 bytes。魔数不符/口令错抛异常。"""
    from utils.crypto import key_holder
    if not data[:len(MAGIC)] == MAGIC:
        raise ValueError('不是有效的 .wbak 加密备份包（魔数不符）')
    if not key_holder.is_initialized:
        raise ValueError('密钥未解锁，无法解密备份（请先输入启动口令）')
    return key_holder.decrypt_bytes(data[len(MAGIC):])


# ============ 打包 / 解包 ============

def create_wbak(db_path, uploads_dir, backup_dir, meta_extra=None):
    """创建 .wbak 加密备份包（原子落盘）。返回 (wbak_path, meta)。

    流程：WAL checkpoint(TRUNCATE) → 收集 db+uploads → 双 sha256 →
    zip 打包 → 魔数+Fernet 加密 → 临时文件 + os.replace 原子落盘。
    """
    import hashlib
    import io as _io

    if not os.path.isfile(db_path):
        raise ValueError('数据库文件不存在，无法备份')

    # ① WAL checkpoint：将 WAL 合并回主库（TRUNCATE 清理日志）
    _wal_checkpoint(db_path)

    # ② 收集 uploads 文件清单
    uploads_entries = []
    if uploads_dir and os.path.isdir(uploads_dir):
        for root, _dirs, files in os.walk(uploads_dir):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, uploads_dir)
                uploads_entries.append((rel, full))
    uploads_entries.sort()

    # ③ 双 sha256（db / uploads 整体）
    db_sha = _sha256_file(db_path)
    uploads_sha = _sha256_entries(uploads_entries)

    meta = {
        'format_version': FORMAT_VERSION,
        'created_at': _now_str(),
        'db_sha256': db_sha,
        'uploads_sha256': uploads_sha,
        'file_count': len(uploads_entries),
        'app_version': APP_VERSION,
    }
    if meta_extra:
        meta.update(meta_extra)

    # ④ zip 打包（内存 BytesIO；uploads 按相对路径归档）
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('shucun.db', open(db_path, 'rb').read())
        zf.writestr('meta.json', json.dumps(meta, ensure_ascii=False))
        for rel, full in uploads_entries:
            zf.write(full, arcname=os.path.join('uploads', rel))
    raw = buf.getvalue()

    # ⑤ 加密 + 原子落盘
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    wbak_path = os.path.join(backup_dir, f'shucun_backup_{timestamp}.wbak')
    tmp_path = wbak_path + '.tmp'
    try:
        with open(tmp_path, 'wb') as f:
            f.write(encrypt_to_wbak(raw))
        os.replace(tmp_path, wbak_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)  # 失败清理，无半成品（R9-E3）

    return wbak_path, meta


def extract_wbak(wbak_path, dest_dir):
    """解包 .wbak → 临时目录，返回 (meta, db_bytes, uploads_map)。

    校验：魔数 → 解密（口令错抛 InvalidToken）→ meta 双 sha256 核对。
    uploads_map = {相对路径: bytes}（整体替换用）。
    """
    import hashlib

    if not os.path.isfile(wbak_path):
        raise ValueError('备份文件不存在')

    with open(wbak_path, 'rb') as f:
        raw = decrypt_from_wbak(f.read())

    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, '_pkg.zip')
    try:
        with open(zip_path, 'wb') as f:
            f.write(raw)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()
            if 'shucun.db' not in names or 'meta.json' not in names:
                raise ValueError('备份包缺少关键文件（shucun.db / meta.json）')
            db_bytes = zf.read('shucun.db')
            meta = json.loads(zf.read('meta.json').decode('utf-8'))
            uploads_map = {}
            for n in names:
                if n.startswith('uploads/'):
                    uploads_map[n[len('uploads/'):]] = zf.read(n)
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)

    # 校验值核对（损坏包拒绝）
    if meta.get('db_sha256'):
        if hashlib.sha256(db_bytes).hexdigest() != meta['db_sha256']:
            raise ValueError('备份包校验失败：数据库内容与元信息不符（包可能损坏）')
    if meta.get('uploads_sha256'):
        agg = hashlib.sha256()
        for rel in sorted(uploads_map):
            agg.update(rel.encode('utf-8'))
            agg.update(uploads_map[rel])
        if agg.hexdigest() != meta['uploads_sha256']:
            raise ValueError('备份包校验失败：附件内容与元信息不符（包可能损坏）')

    return meta, db_bytes, uploads_map


# ============ 预检 / 保留 / 恢复 ============

def preflight_dir(dest_dir, estimated_size):
    """目标目录可写 + 空间预检（R9-E3：不可写/空间不足提示并中止）。

    estimated_size 为预估备份包大小；余量需 ≥1.5 倍。
    """
    os.makedirs(dest_dir, exist_ok=True)
    if not os.access(dest_dir, os.W_OK):
        raise ValueError(f'目标目录不可写：{dest_dir}（请检查 U 盘/目录权限）')

    # 写探针：建删 .probe
    probe = os.path.join(dest_dir, '.wbak_probe')
    try:
        with open(probe, 'wb') as f:
            f.write(b'ok')
        os.remove(probe)
    except OSError:
        raise ValueError(f'目标目录不可写（写探针失败）：{dest_dir}')

    try:
        usage = shutil.disk_usage(dest_dir)
    except OSError:
        raise ValueError('无法读取目标目录空间信息')
    if usage.free < estimated_size * 1.5:
        raise ValueError(f'目标目录空间不足（需要约 {estimated_size // (1024*1024)}MB，'
                         f'可用 {usage.free // (1024*1024)}MB），已中止，无半成品产生')


def enforce_retention(backup_dir, count=None):
    """滚动保留：保留最近 N 份 .wbak，删除最旧（.db 不参与，R1 兼容）。返回删除数。"""
    if count is None:
        count = DEFAULT_RETENTION
    if count <= 0:
        return 0
    os.makedirs(backup_dir, exist_ok=True)
    wbaks = sorted(
        (os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.wbak')),
        key=os.path.getmtime, reverse=True,
    )
    removed = 0
    for old in wbaks[count:]:
        try:
            os.remove(old)
            removed += 1
        except OSError:
            pass
    return removed


def latest_wbak(backup_dir):
    """最近一份 .wbak 的 (path, mtime)；无则 None。"""
    os.makedirs(backup_dir, exist_ok=True)
    wbaks = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.wbak')]
    if not wbaks:
        return None
    wbaks.sort(key=os.path.getmtime, reverse=True)
    p = wbaks[0]
    return p, os.path.getmtime(p)


def restore_from_wbak(wbak_path, db_path, uploads_dir, backup_dir):
    """从 .wbak 完整恢复：解密校验 → pre_restore 快照 → 原子替换 db + uploads。

    调用方需先 db.session.remove() + db.engine.dispose()（恢复纪律）。
    返回 meta（供提示）。
    """
    import tempfile

    dest = tempfile.mkdtemp(prefix='wbak_restore_')
    try:
        meta, db_bytes, uploads_map = extract_wbak(wbak_path, dest)

        # SQLite header 核对（恢复前校验包完整性）
        if db_bytes[:16] != b'SQLite format 3\x00':
            raise ValueError('备份包中的数据库文件不是有效的 SQLite 数据库（包可能损坏）')

        # pre_restore 快照（可回退）
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        if os.path.exists(db_path):
            shutil.copy2(db_path, os.path.join(backup_dir, f'pre_restore_{ts}.db'))

        # 原子替换 db
        db_tmp = db_path + '.restore.tmp'
        with open(db_tmp, 'wb') as f:
            f.write(db_bytes)
        os.replace(db_tmp, db_path)

        # 清除旧 WAL/SHM 残留（防旧日志覆盖恢复的主库，恢复纪律）
        for _suffix in ('-wal', '-shm'):
            _p = db_path + _suffix
            if os.path.exists(_p):
                os.remove(_p)

        # uploads 整体替换（旧目录改名保留可回退）
        if uploads_map:
            os.makedirs(uploads_dir, exist_ok=True)
            old_dir = f'{uploads_dir}.pre_restore_{ts}'
            if os.path.isdir(uploads_dir) and not os.path.isdir(old_dir):
                os.rename(uploads_dir, old_dir)
            os.makedirs(uploads_dir, exist_ok=True)
            for rel, content in uploads_map.items():
                full = os.path.join(uploads_dir, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'wb') as f:
                    f.write(content)
        return meta
    finally:
        shutil.rmtree(dest, ignore_errors=True)


# ============ 内部工具 ============

def _wal_checkpoint(db_path):
    """WAL checkpoint(TRUNCATE)：将 WAL 合并回主库并截断日志。"""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            conn.close()
    except sqlite3.Error:
        pass  # 非 WAL 模式或无 WAL 文件时忽略


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _sha256_entries(entries):
    import hashlib
    h = hashlib.sha256()
    for rel, full in entries:
        h.update(rel.encode('utf-8'))
        with open(full, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
    return h.hexdigest()
