# -*- coding: utf-8 -*-
"""tests/conftest.py — 全局测试夹具。

测试环境隔离（2026-09-07 夹具基线修复）：
1) SHUCUN_DISABLE_PERIODIC：禁用 lazy_check 周期巡检线程（T19 原有，防后台异步写库干扰）。
2) SHUCUN_DISABLE_PASSWORD_CHANGE：P1-2 强制改密拦截会让「登录 admin」的业务请求全被 302/403，
   多数测试夹具未处理 must_change_password 标记，故默认禁用；test_password_change_enforce.py
   为改密拦截专项测试，须保留真实行为，故排除。

实现要点：直接写 os.environ + teardown 恢复，不用 monkeypatch.setenv —— 测试方法内
monkeypatch.undo() 会连带撤销 autouse 经同一 monkeypatch 实例注入的 env，导致 undo() 之后
的请求恢复改密拦截而 403（已实测 test_t21_pdf::test_pdf_font_missing_error 与
test_t15_cascade::test_failed_retry 因此失败）。

注：曾尝试在此全局锁定 SHUCUN_* 到统一隔离根以消除「config.py 首 import 固化污染 env」
问题（test_backup_restore_roundtrip 全量顺序偶发失败），实测会引入 WinError 5 恢复失败
回归（普通单跑即红），已回退。该遗留改走专项：将测试文件模块级 os.environ.setdefault
迁移至文件级 fixture（monkeypatch.setenv 随文件结束自动恢复），不在 conftest 做全局干预。
"""
import os

import pytest


@pytest.fixture()
def isolated_app_factory(tmp_path, monkeypatch):
    """GLB-17：统一测试隔离夹具工厂（返回 `make()`）。

    背景：各测试文件曾各自手抄「SHUCUN_* env + `Config.SQLALCHEMY_DATABASE_URI` 覆写」。
    全量运行下 `config.py` 首次 import 即固化路径，后跑文件只改 env 不生效 —— 这是
    GLB-05 A 类 4 ERROR 的结构性成因。本工厂把「建目录 → 设 env → 覆写 config 模块变量
    与 Config 类属性 → create_app」收敛到一处，调用方只拿 app。

    用法：
        def test_x(isolated_app_factory):
            app = isolated_app_factory()
    """
    counter = {'n': 0}

    def _make(extra_env=None):
        counter['n'] += 1
        root = tmp_path / ('iso%d' % counter['n'])
        dirs = {
            'data': root / 'data',
            'uploads': root / 'uploads',
            'backups': root / 'backups',
            'logs': root / 'logs',
            'temp': root / 'temp',
        }
        for path in dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        (dirs['temp'] / 'exports').mkdir(exist_ok=True)
        (dirs['temp'] / 'imports').mkdir(exist_ok=True)
        db_path = root / 'shucun.db'
        env = {
            'SHUCUN_DATA_DIR': str(dirs['data']),
            'SHUCUN_DB_PATH': str(db_path),
            'SHUCUN_UPLOAD_DIR': str(dirs['uploads']),
            'SHUCUN_BACKUP_DIR': str(dirs['backups']),
            'SHUCUN_LOG_DIR': str(dirs['logs']),
            'SHUCUN_TEMP_DIR': str(dirs['temp']),
            'SHUCUN_LICENSE_FILE': str(dirs['data'] / 'license.key'),
            'SHUCUN_PORT': '5000',
            'SHUCUN_SECRET_KEY': 'test',
        }
        env.update(extra_env or {})
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))

        import config as cfg
        from config import Config
        for name, value in (
            ('DATA_DIR', dirs['data']),
            ('DB_PATH', db_path),
            ('UPLOAD_DIR', dirs['uploads']),
            ('BACKUP_DIR', dirs['backups']),
            ('LICENSE_FILE', dirs['data'] / 'license.key'),
            ('TEMP_DIR', dirs['temp']),
        ):
            monkeypatch.setattr(cfg, name, str(value))
        for name, value in (
            ('SECRET_KEY', 'test'),
            ('SQLALCHEMY_DATABASE_URI', 'sqlite:///%s?check_same_thread=False' % db_path),
            ('UPLOAD_FOLDER', str(dirs['uploads'])),
            ('BACKUP_FOLDER', str(dirs['backups'])),
            ('LICENSE_FILE', str(dirs['data'] / 'license.key')),
            ('TEMP_FOLDER', str(dirs['temp'])),
            ('EXPORTS_DIR', str(dirs['temp'] / 'exports')),
            ('IMPORTS_DIR', str(dirs['temp'] / 'imports')),
        ):
            monkeypatch.setattr(Config, name, value)

        from app import create_app
        application = create_app()
        application.config['TESTING'] = True
        return application

    return _make


@pytest.fixture(autouse=True)
def _test_isolated_env(request):
    keys = ["SHUCUN_DISABLE_PERIODIC", "SHUCUN_DISABLE_PASSWORD_CHANGE"]
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["SHUCUN_DISABLE_PERIODIC"] = "1"
    _p = (request.node.fspath.strpath if hasattr(request.node, "fspath") and request.node.fspath
          else str(getattr(request.node, "path", "")))
    if "password_change_enforce" not in _p:
        os.environ["SHUCUN_DISABLE_PASSWORD_CHANGE"] = "1"
    yield
    for k in keys:
        if saved[k] is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = saved[k]
