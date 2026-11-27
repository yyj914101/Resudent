#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机 GUI 测试用服务器启动包装（环境准备用，不属于业务代码）。

run_server.py 会无条件把 SHUCUN_* 环境变量重定向到用户数据目录
（~/ShucunManagement），测试需要固定在项目目录内的 shucun.db /
key_meta.json，故用本包装：强制设定环境变量后直接 waitress 起服务。
仅监听 127.0.0.1 回环（未授权局域网时 app 层也会拦截非回环请求）。

用法：.venv/bin/python scripts/dev_server.py [port]
"""
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
os.environ['SHUCUN_DATA_DIR'] = BASE_DIR
os.environ['SHUCUN_DB_PATH'] = os.path.join(BASE_DIR, 'shucun.db')
os.environ['SHUCUN_UPLOAD_DIR'] = os.path.join(BASE_DIR, 'uploads')
os.environ['SHUCUN_BACKUP_DIR'] = os.path.join(BASE_DIR, 'backups')
os.environ['SHUCUN_TEMP_DIR'] = os.path.join(BASE_DIR, 'temp')
os.environ.setdefault('SHUCUN_LAN_AUTHORIZED', '0')
sys.path.insert(0, BASE_DIR)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000


def main():
    from waitress import serve
    from app import create_app
    app = create_app()
    # 开发测试通道：静态资源禁用强缓存（app.py 默认 604800s=7 天，
    # 会导致 JS/CSS 修复在浏览器 7 天内不可见——2026-11-11 编辑入口死链排查实证）。
    # max-age=0 后浏览器每次仍带 ETag revalidate，未变更返回 304，开销可忽略。
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    print(f'[dev_server] 数据目录: {BASE_DIR}')
    print(f'[dev_server] 数据库:   {os.environ["SHUCUN_DB_PATH"]}')
    print(f'[dev_server] 静态缓存: 0（每次 revalidate） | 监听:     http://127.0.0.1:{PORT}')
    serve(app, host='127.0.0.1', port=PORT, _quiet=False)


if __name__ == '__main__':
    main()
