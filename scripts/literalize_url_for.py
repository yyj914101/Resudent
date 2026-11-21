#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""013 内联 JS 外移（含 Jinja 字面量化）：把脚本体内的 `{{ url_for(...) }}`
用 Flask 实际求值替换成字面量 URL，再抽出到 static/js/pages/<name>.js。

仅处理「脚本内的 url_for 全是无参或带占位符参数（0/999/"" 等）+ 外部 .replace()
消费」的页面。脚本内若有 url_for 以外的 Jinja（如 {% for %} 数据循环、{{ 变量 }}），
本脚本拒绝处理并报错，交给「数据注入」批次。

用法:
    python3 scripts/literalize_url_for.py <模板相对路径> <js_name.js>

幂等：已无内联 <script>（只剩 src）时打印 SKIP。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TAG = '<script src="{{ url_for(\'static\', filename=\'js/pages/%s\') }}"></script>'
SCRIPT = r'<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>'
URFOR = re.compile(r"url_for\((?:[^()]|\([^()]*\))*\)")


def _block(block_name):
    return re.compile(r'\{%\s*block\s+' + block_name + r'\s*%\}(.*?)\{%\s*endblock\s*%\}', re.S)


def resolve_url_for(app):
    """返回一个函数，把 url_for(...) 调用字符串求值为字面量 URL。"""
    from flask import url_for

    def resolve(call):
        # call 已是完整 "url_for(...)" 调用，eval 直接得 URL
        with app.test_request_context('/'):
            return eval(call, {"url_for": url_for})
    return resolve


def main():
    if len(sys.argv) != 3:
        print("用法: literalize_url_for.py <模板相对路径> <js_name.js>")
        sys.exit(2)
    tpl_rel, jsname = sys.argv[1], sys.argv[2]
    tpl_path = os.path.join(ROOT, tpl_rel)
    js_path = os.path.join(ROOT, "static", "js", "pages", jsname)

    # 隔离 env（同 smoke fixture），再建 app 求值 url_for
    import tempfile
    tmp = tempfile.mkdtemp()
    for d in ("data", "uploads", "backups", "logs", "temp", "temp/exports"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    for k, v in {"SHUCUN_DATA_DIR": tmp + "/data", "SHUCUN_DB_PATH": tmp + "/shucun.db",
                 "SHUCUN_UPLOAD_DIR": tmp + "/uploads", "SHUCUN_BACKUP_DIR": tmp + "/backups",
                 "SHUCUN_LOG_DIR": tmp + "/logs", "SHUCUN_TEMP_DIR": tmp + "/temp",
                 "SHUCUN_LICENSE_FILE": tmp + "/license.key", "SHUCUN_PORT": "5000",
                 "SHUCUN_SECRET_KEY": "test", "SHUCUN_DISABLE_PERIODIC": "1",
                 "SHUCUN_DISABLE_PASSWORD_CHANGE": "1"}.items():
        os.environ[k] = v
    import config as cfg
    cfg.DATA_DIR = os.environ["SHUCUN_DATA_DIR"]; cfg.DB_PATH = os.environ["SHUCUN_DB_PATH"]
    cfg.UPLOAD_DIR = os.environ["SHUCUN_UPLOAD_DIR"]; cfg.BACKUP_DIR = os.environ["SHUCUN_BACKUP_DIR"]
    cfg.LICENSE_FILE = os.environ["SHUCUN_LICENSE_FILE"]; cfg.TEMP_DIR = os.environ["SHUCUN_TEMP_DIR"]
    from config import Config
    Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///%s?check_same_thread=False' % os.environ["SHUCUN_DB_PATH"]
    Config.UPLOAD_FOLDER = os.environ["SHUCUN_UPLOAD_DIR"]; Config.BACKUP_FOLDER = os.environ["SHUCUN_BACKUP_DIR"]
    Config.LICENSE_FILE = os.environ["SHUCUN_LICENSE_FILE"]; Config.TEMP_FOLDER = os.environ["SHUCUN_TEMP_DIR"]
    Config.EXPORTS_DIR = os.path.join(os.environ["SHUCUN_TEMP_DIR"], "exports")
    Config.IMPORTS_DIR = os.path.join(os.environ["SHUCUN_TEMP_DIR"], "imports")
    from app import create_app
    app = create_app()
    resolve = resolve_url_for(app)

    with open(tpl_path, encoding="utf-8") as f:
        s = f.read()

    m = _block("extra_js").search(s)
    if not m:
        print("SKIP: 无 extra_js 块:", tpl_rel)
        return
    region = m.group(1)

    sm = re.search(SCRIPT, region, re.S)
    if not sm:
        print("SKIP: extra_js 内无内联 <script>:", tpl_rel)
        return
    body = sm.group(1).lstrip("\n").rstrip("\n")
    if not body.strip():
        print("SKIP: 空脚本体:", tpl_rel)
        return

    # 检查是否有 url_for 以外的 Jinja（数据注入场景 → 拒绝，交给专门批次）
    non_urlfor = re.findall(r'(\{\{.*?\}\}|\{%.*?%\})', body, re.S)
    non_urlfor = [x for x in non_urlfor if 'url_for' not in x]
    if non_urlfor:
        print("REFUSE: 脚本含非 url_for Jinja（需数据注入批次）:", tpl_rel)
        for x in non_urlfor[:5]:
            print("   ", x.strip()[:80])
        return

    # 字面量化：{{ url_for(...) }} → 求值字面量
    def repl(mo):
        inner = mo.group(1).strip()
        # inner 是 "url_for(...)" 或带引号的完整调用
        # 提取 url_for(...) 调用
        uf = URFOR.search(inner)
        if not uf:
            return mo.group(0)  # 非 url_for，保留
        call = uf.group(0)
        val = resolve(call)
        return val

    literal_body = re.sub(r'\{\{(.*?)\}\}', repl, body, flags=re.S)

    # 安全闸：量化后不得残留任何 Jinja
    residual = re.findall(r'(\{\{.*?\}\}|\{%.*?%\})', literal_body, re.S)
    if residual:
        print("REFUSE: 量化后仍残留 Jinja，未改动:", tpl_rel)
        for x in residual[:5]:
            print("   ", x.strip()[:80])
        return

    # 写 .js
    os.makedirs(os.path.dirname(js_path), exist_ok=True)
    with open(js_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(literal_body + "\n")

    # 模板换 <script src>
    new_region = region[:sm.start()] + (TAG % jsname) + region[sm.end():]
    s = s[:m.start(1)] + new_region + s[m.end(1):]
    with open(tpl_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)

    n_uf = len(re.findall(r'url_for\(', body))
    print("OK  %s -> static/js/pages/%s (%d 行, 字面量化 %d 个 url_for)" % (
        tpl_rel, jsname, literal_body.count("\n") + 1, n_uf))


if __name__ == "__main__":
    main()
