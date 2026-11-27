#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_frontend.py — S6 前端扫描门禁（纯标准库，不依赖 node/npm）

三扫描：
  1) 外链（硬门禁）：templates/ 内 http(s):// 引用 = 0（离线自包含）
  2) 色值（硬门禁）：固化 scan_legacy_colors.py 的两条（旧蓝残留 + app.css 主色 var() 转发）
  3) 重复函数（报告，恒退出码 0）：跨页面文件重复定义的顶层函数清单，
     作为「应收敛单源」的输入；因同名可能不同义（如局部 showToast(type,msg)），
     不做硬失败，交由人工判断。

用法：
  python scripts/checks/scan_frontend.py            # 全部门禁 + 报告
  python scripts/checks/scan_frontend.py --report   # 仅重复函数报告（恒 0）

退出码：0 = 硬门禁通过；1 = 外链或色值违规。
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]  # 项目根
TEMPLATES = BASE / "templates"
STATIC = BASE / "static"
APP_CSS = STATIC / "css" / "app.css"

# ---- 1. 外链 ----
URL_RE = re.compile(r"https?://")

# ---- 2. 色值（与 scan_legacy_colors.py 同口径）----
LEGACY_BLUE = re.compile(r"#(?:4285f4|1967d2)", re.I)
# app.css :root 里主色变量若仍写裸 #hex 即未收敛
HARDCODED_PRIMARY = re.compile(
    r"--(?:primary|primary-dark|sidebar-bg|sidebar-hover|bg-light)\s*:\s*#[0-9a-fA-F]"
)

# ---- 3. 重复函数 ----
FUNC_DEF = re.compile(r"^function\s+([A-Za-z_$][\w$]*)\s*\(", re.M)


def scan_external_links():
    hits = []
    for p in sorted(TEMPLATES.rglob("*.html")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in URL_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append("%s:%d" % (p.relative_to(BASE).as_posix(), line))
    return hits


def scan_legacy_colors():
    hits = []
    for p in sorted(list(TEMPLATES.rglob("*.html")) + list(TEMPLATES.rglob("*.css"))
                    + list((STATIC / "css").rglob("*.css"))):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in LEGACY_BLUE.finditer(text):
            hits.append("%s: %s" % (p.relative_to(BASE).as_posix(), m.group(0).lower()))
    return hits


def scan_hardcoded_primary():
    if not APP_CSS.exists():
        return ["app.css 不存在"]
    text = APP_CSS.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r":root\s*\{[^}]*\}", text, re.S)
    if not m:
        return ["app.css 未找到 :root 块"]
    hardcoded = []
    for line in m.group(0).splitlines():
        if HARDCODED_PRIMARY.search(line):
            hardcoded.append(line.strip())
    return hardcoded


def scan_duplicate_functions():
    """跨文件重复定义的顶层函数清单（报告用，不判违规）。"""
    from collections import defaultdict
    funcs = defaultdict(list)
    for p in sorted((STATIC / "js").rglob("*.js")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in FUNC_DEF.finditer(text):
            funcs[m.group(1)].append(p.relative_to(BASE).as_posix())
    dup = {name: sorted(set(files)) for name, files in funcs.items() if len(set(files)) > 1}
    return dup


def main():
    report_only = "--report" in sys.argv

    links = scan_external_links()
    legacy = scan_legacy_colors()
    hardcoded = scan_hardcoded_primary()
    dup = scan_duplicate_functions()

    if not report_only:
        # 硬门禁 1：外链
        if links:
            print("❌ 外链违规 %d 处（离线自包含要求 = 0）：" % len(links))
            for h in links:
                print("  " + h)
        else:
            print("✅ 外链：templates/ 内 http(s):// = 0")

        # 硬门禁 2：色值
        if legacy:
            print("❌ 旧蓝 #4285f4/#1967d2 残留 %d 处：" % len(legacy))
            for h in legacy:
                print("  " + h)
        else:
            print("✅ 旧蓝 #4285f4/#1967d2 全站 = 0")

        if hardcoded:
            print("❌ app.css :root 主色硬编码残留 %d 处：" % len(hardcoded))
            for h in hardcoded:
                print("  " + h)
        else:
            print("✅ app.css 主色已 var() 转发到 tokens.css")

    # 报告（恒 0）：重复函数
    if dup:
        print("ℹ️ 跨文件重复定义 %d 个（报告，不判违规，供收敛参考）：" % len(dup))
        for name in sorted(dup):
            print("  %-24s %d 处: %s" % (name, len(dup[name]), ", ".join(dup[name])))
    else:
        print("ℹ️ 无跨文件重复函数")

    if not report_only and (links or legacy or hardcoded):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
