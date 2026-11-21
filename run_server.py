#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ShuCun Management System V2.45.1 server entry."""
import os
import shutil
import sys
from configparser import ConfigParser
from datetime import datetime

VERSION = "V2.45.1"
APP_FOLDER = "ShucunManagement"


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.dirname(__file__))


APP_DIR = get_app_dir()
CONFIG_FILE = os.path.join(APP_DIR, "config.ini")


def get_user_data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_FOLDER)


def load_config():
    config = ConfigParser()
    config.optionxform = str
    if not config.has_section("server"):
        config.add_section("server")
    config.set("server", "host", "0.0.0.0")
    config.set("server", "port", "5000")
    config.set("server", "debug", "false")

    if os.path.exists(CONFIG_FILE):
        config.read(CONFIG_FILE, encoding="utf-8-sig")

    # Keep config.ini intentionally small for V2.45.1.
    compact = ConfigParser()
    compact.optionxform = str
    compact.add_section("server")
    compact.set("server", "host", config.get("server", "host", fallback=config.get("Server", "host", fallback="0.0.0.0")))
    compact.set("server", "port", config.get("server", "port", fallback=config.get("Server", "port", fallback="5000")))
    compact.set("server", "debug", config.get("server", "debug", fallback=config.get("Server", "debug", fallback="false")))
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        compact.write(f)
    return compact


def ensure_user_dirs():
    data_dir = get_user_data_dir()
    paths = {
        "data_dir": data_dir,
        "db_dir": os.path.join(data_dir, "data"),
        "uploads": os.path.join(data_dir, "uploads"),
        "backups": os.path.join(data_dir, "backups"),
        "logs": os.path.join(data_dir, "logs"),
        "temp": os.path.join(data_dir, "temp"),
        "temp_exports": os.path.join(data_dir, "temp", "exports"),
        "temp_imports": os.path.join(data_dir, "temp", "imports"),
    }
    for path in paths.values():
        os.makedirs(path, exist_ok=True)
    return paths


def init_database(paths):
    db_path = os.path.join(paths["db_dir"], "shucun.db")
    if os.path.exists(db_path):
        return db_path

    seed_candidates = [
        os.path.join(APP_DIR, "seed", "shucun_seed.db"),
        os.path.join(APP_DIR, "shucun_seed.db"),
        os.path.join(APP_DIR, "shucun.db"),
    ]
    for src in seed_candidates:
        if os.path.exists(src):
            shutil.copy2(src, db_path)
            return db_path
    return db_path


def setup_environment(config):
    paths = ensure_user_dirs()
    db_path = init_database(paths)
    os.environ["SHUCUN_DATA_DIR"] = paths["data_dir"]
    os.environ["SHUCUN_DB_PATH"] = db_path
    os.environ["SHUCUN_UPLOAD_DIR"] = paths["uploads"]
    os.environ["SHUCUN_BACKUP_DIR"] = paths["backups"]
    os.environ["SHUCUN_TEMP_DIR"] = paths["temp"]
    os.environ["SHUCUN_LICENSE_FILE"] = os.path.join(paths["data_dir"], "license.key")
    os.environ["SHUCUN_PORT"] = config.get("server", "port", fallback="5000")
    return paths, db_path


def parse_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def print_startup_info(host, port, paths, db_path):
    print("=" * 58)
    print(f"  ShuCun Management System {VERSION}")
    print(f"  Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Config: {CONFIG_FILE}")
    print(f"  Data dir: {paths['data_dir']}")
    print(f"  Database: {db_path}")
    print(f"  Local URL: http://127.0.0.1:{port}")
    print(f"  LAN host: {host}:{port}")
    print("=" * 58)


def main():
    config = load_config()
    paths, db_path = setup_environment(config)
    host = config.get("server", "host", fallback="0.0.0.0")
    port = int(config.get("server", "port", fallback="5000"))
    debug = parse_bool(config.get("server", "debug", fallback="false"))

    sys.path.insert(0, APP_DIR)
    from app import create_app
    from utils.license import check_license_on_startup

    lan_authorized = check_license_on_startup()
    os.environ["SHUCUN_LAN_AUTHORIZED"] = "1" if lan_authorized else "0"
    if not lan_authorized and host not in ("127.0.0.1", "localhost", "::1"):
        host = "127.0.0.1"

    app = create_app()
    print_startup_info(host, port, paths, db_path)

    if getattr(sys, 'frozen', False):
        for _env_key in ('SHUCUN_DISABLE_CSRF', 'SHUCUN_DISABLE_PASSWORD_CHANGE'):
            if os.environ.get(_env_key) == '1':
                print(f'[安全] 打包生产环境已忽略危险开关 {_env_key}=1', file=sys.stderr)
                os.environ.pop(_env_key, None)

    if debug and not getattr(sys, 'frozen', False):
        # 仅非打包开发态且限定回环地址允许开启 Werkzeug Debugger
        app.run(host='127.0.0.1', port=port, debug=True)
    else:
        if debug:
            print('[安全] 打包运行环境已强制禁用 debug 模式，改用 waitress 启动', file=sys.stderr)
        from waitress import serve
        serve(app, host=host, port=port, _quiet=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log_targets = [
            os.path.join(APP_DIR, "server_startup_error.log"),
            os.path.join(get_user_data_dir(), "logs", "server_startup_error.log"),
        ]
        for log_path in log_targets:
            try:
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("\n" + "=" * 80 + "\n")
                    f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
                    traceback.print_exc(file=f)
            except Exception:
                pass
        raise
