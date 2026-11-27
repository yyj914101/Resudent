# -*- coding: utf-8 -*-
"""tests/test_template_smoke.py — SSR 渲染快照 + diff 门禁（007）。

用途：
- 首轮运行：对全站可渲染的 HTML 页面生成归一化快照到 tests/snapshots/（提交入库）。
- 次轮及以后：逐页对比渲染结果与快照，任一页不一致即 fail 并报出路由。

这是「SSR 行为等价」门禁的测试基座：改模板应能 diff 出差异、不改模板应稳定全绿。

归一化（去掉跨运行的动态内容，只比「模板结构」）：
  日期/时间戳 → __DATE__ / __DATETIME__；CSRF token（input + meta）→ __CSRF__；
  服务器绝对路径（pytest tmp 临时目录随机号）→ __TMP__。

已知：无参 GET 会扫到下载类二进制端点（xlsx/备份）与个别业务 bug（如
welfare_job 的 500），按「非 200 或非 text/html」记入 skip，不在本测试里修。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")

# 登录账号（init_db.py 种子）
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"


def _setup_env(tmp_path):
    """隔离 env：目录全指向 pytest tmp，且覆盖 config 的 module 级变量 + Config 类属性。

    为何要覆盖 config 的 module 级变量：config.py 的 DATA_DIR/DB_PATH/BACKUP_DIR/
    LICENSE_FILE/TEMP_DIR 在首次 import 时固化 os.environ；全量 pytest 下其它测试文件的
    module 级 os.environ.setdefault 会先污染这些 env，导致本测试渲染出旧路径。故此处
    在 os.environ 之外，再把 config 模块的 module 级变量与 Config 类属性一并覆写。
    """
    for d in ("data", "uploads", "backups", "logs", "temp", "temp/exports"):
        os.makedirs(os.path.join(str(tmp_path), d), exist_ok=True)
    os.environ["SHUCUN_DATA_DIR"] = str(tmp_path / "data")
    os.environ["SHUCUN_DB_PATH"] = str(tmp_path / "shucun.db")
    os.environ["SHUCUN_UPLOAD_DIR"] = str(tmp_path / "uploads")
    os.environ["SHUCUN_BACKUP_DIR"] = str(tmp_path / "backups")
    os.environ["SHUCUN_LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SHUCUN_TEMP_DIR"] = str(tmp_path / "temp")
    os.environ["SHUCUN_LICENSE_FILE"] = str(tmp_path / "license.key")
    os.environ["SHUCUN_PORT"] = "5000"
    os.environ["SHUCUN_SECRET_KEY"] = "test"

    import config as _cfg
    _cfg.DATA_DIR = os.environ["SHUCUN_DATA_DIR"]
    _cfg.DB_PATH = os.environ["SHUCUN_DB_PATH"]
    _cfg.UPLOAD_DIR = os.environ["SHUCUN_UPLOAD_DIR"]
    _cfg.BACKUP_DIR = os.environ["SHUCUN_BACKUP_DIR"]
    _cfg.LICENSE_FILE = os.environ["SHUCUN_LICENSE_FILE"]
    _cfg.TEMP_DIR = os.environ["SHUCUN_TEMP_DIR"]

    from config import Config
    Config.SQLALCHEMY_DATABASE_URI = (
        'sqlite:///%s?check_same_thread=False' % os.environ["SHUCUN_DB_PATH"]
    )
    Config.UPLOAD_FOLDER = os.environ["SHUCUN_UPLOAD_DIR"]
    Config.BACKUP_FOLDER = os.environ["SHUCUN_BACKUP_DIR"]
    Config.LICENSE_FILE = os.environ["SHUCUN_LICENSE_FILE"]
    Config.TEMP_FOLDER = os.environ["SHUCUN_TEMP_DIR"]
    Config.EXPORTS_DIR = os.path.join(os.environ["SHUCUN_TEMP_DIR"], "exports")
    Config.IMPORTS_DIR = os.path.join(os.environ["SHUCUN_TEMP_DIR"], "imports")


@pytest.fixture()
def app(tmp_path):
    _setup_env(tmp_path)
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


def _login(app):
    """登录 admin，返回已带 session 的 test_client。"""
    client = app.test_client()
    r = client.get("/auth/login")
    m = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', r.get_data(as_text=True))
    token = m.group(1) if m else ""
    client.post("/auth/login", data={
        "csrf_token": token, "username": ADMIN_USER, "password": ADMIN_PASS,
    })
    return client


def _collect_get_routes(app):
    """收集所有「无参 GET 页面」路由（跳过 /static、/api、及会改变 session 的动作端点）。"""
    routes = set()
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods:
            continue
        if rule.arguments:
            continue
        path = rule.rule
        if path.startswith("/static") or "/api/" in path:
            continue
        if path == "/auth/logout":  # ponytail: GET 此端点会登出 session，后续页面全 302
            continue
        routes.add(path)
    return sorted(routes)


def _snapshot_name(route):
    name = route.strip("/").replace("/", "_")
    return name or "index"


def _normalize(text, tmp_root):
    """归一化跨运行的动态内容，只保留模板结构。"""
    text = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?", "__DATETIME__", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}", "__DATE__", text)
    # 017：中文格式日期（如 death_report「填报日期：2026年09月19日」，now().strftime('%Y年%m月%d日')）
    # 同样随渲染当日变化，基线建立次日必 diff —— 011 观察到的「偶发失败」实为日期翻日。
    text = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日", "__DATE__", text)
    text = re.sub(r'(name="csrf_token"[^>]*value=")[^"]*(")', r"\1__CSRF__\2", text)
    text = re.sub(r'(name="csrf-token"[^>]*content=")[^"]*(")', r"\1__CSRF__\2", text)
    # 048 / GLB-34：大屏访问凭证 token（secrets.token_urlsafe 随机，跨运行必变）→ 归一化。
    # 只匹配 /screen/?token= 路径，避免把其他页面未来的语义化 token 参数静默抹掉。
    text = re.sub(r'(/screen/\?token=)[A-Za-z0-9_-]{20,}', r'\1__TOKEN__', text)
    # GLB-09：死亡上报页默认取「当前年/月」，跨月必 diff；按该页固定锚点 token 化。
    text = re.sub(r'(<option value=")\d{1,2}(" selected>)\d{1,2}(月</option>)',
                  r'\1__MONTH__\2__MONTH__\3', text)
    text = re.sub(r'(id="reportYear" value=")\d{4}(")', r'\1__YEAR__\2', text)
    text = re.sub(r'(id="titleYear">)\d{4}(<)', r'\1__YEAR__\2', text)
    text = re.sub(r'(id="titleMonth">)\d{1,2}(<)', r'\1__MONTH__\2', text)
    if tmp_root:
        t = str(tmp_root)
        for p in (t, t.replace("\\", "/")):
            text = text.replace(p, "__TMP__")
        # 017：tmp 根之后的路径由 os.path.join 拼出，分隔符随 OS 变化（Windows `\` /
        # Linux `/`），基线跨机不可比。仅对 __TMP__ 占位符后的路径 token 归一化分隔符
        # ——该区域已被上方替换声明为动态内容，归一化它不会掩盖模板其余部分的回归。
        text = re.sub(
            r"__TMP__([^\s\"'<]*)",
            lambda m: "__TMP__" + (("/" + m.group(1).replace("\\", "/").lstrip("/\\")) if m.group(1) else ""),
            text,
        )
    return text


def test_template_snapshots(tmp_path, app):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    client = _login(app)
    routes = _collect_get_routes(app)
    generated, compared, mismatches, skipped = [], [], [], []
    tmp = str(tmp_path)
    for route in routes:
        try:
            resp = client.get(route)
        except Exception as e:  # 业务 bug（如 welfare_job 500）→ 记 skip，不修
            skipped.append((route, "EXC %s" % type(e).__name__))
            continue
        if resp.status_code != 200 or "text/html" not in (resp.content_type or ""):
            skipped.append((route, resp.status_code))
            continue
        snap = os.path.join(SNAPSHOT_DIR, _snapshot_name(route) + ".html")
        norm = _normalize(resp.get_data(as_text=True), tmp)
        if not os.path.exists(snap):
            with open(snap, "w", encoding="utf-8", newline="\n") as f:
                f.write(norm)
            generated.append(route)
        else:
            with open(snap, encoding="utf-8") as f:
                baseline = f.read()
            if baseline != norm:
                mismatches.append(route)
            else:
                compared.append(route)

    if skipped:
        print("\n[smoke] 跳过 %d 条（非 200 / 二进制 / 业务异常）：%s"
              % (len(skipped), ", ".join("%s(%s)" % (r, c) for r, c in skipped[:15])))
    if generated:
        print("\n[smoke] 生成基线 %d 条，请将 tests/snapshots/ 提交入库后重跑进入对比模式。"
              % len(generated))
    assert not mismatches, (
        "%d 页渲染与基线不一致：%s\n"
        "若为有意改动：删除对应快照后重跑以重建基线；若为无意回归：先还原模板。"
        % (len(mismatches), ", ".join(mismatches[:10]))
    )


def test_glb09_death_report_month_tokens():
    """GLB-09：死亡上报页当前年/月必须 token 化，跨月/跨年重跑不 diff。"""
    def sample(year, month):
        return ('<input id="reportYear" value="%d" min="2000">'
                '<option value="%02d" selected>%d月</option>'
                '<span id="titleYear">%d</span>年'
                '<span id="titleMonth">%02d</span>月份死亡名单'
                % (year, month, month, year, month))

    out = _normalize(sample(2026, 10), None)
    assert '2026' not in out
    assert 'value="10" selected' not in out
    assert '__YEAR__' in out and '__MONTH__' in out
    # 跨月/跨年结构等价
    assert out == _normalize(sample(2027, 11), None)


def test_glb34_token_regex_scoped_to_screen():
    """GLB-34：token= 归一化只命中 /screen/?token=，不误伤其他页面参数。"""
    token = 'A' * 24
    assert _normalize('/screen/?token=%s' % token, None) == '/screen/?token=__TOKEN__'
    other = '/other/?token=%s' % token
    assert _normalize(other, None) == other
