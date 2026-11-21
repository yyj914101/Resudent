#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_legacy_colors.py — 008 一次性色值扫描（S6 固化前临时用）。

验证「三套蓝 → 单令牌」两条：
  1. 旧蓝 #4285f4 / #1967d2（Google 蓝，info 渐变旧值）全站 = 0。
  2. app.css 不再写裸主色值（--primary / --primary-dark 等应为 var() 转发到 tokens.css）。

退出码：0 = 通过；1 = 有残留。
"""
import pathlib
import re
import sys

LEGACY = re.compile(r"#(?:4285f4|1967d2)", re.I)

# app.css :root 里允许保留的「非主色」裸值（--lte-* 文字灰、--bs-primary-rgb 等，不在三套蓝口径内）
_ALLOWED = ("--bs-primary-rgb", "--lte-sidebar-color", "--lte-sidebar-submenu-color",
            "--lte-sidebar-header-color", "--lte-sidebar-menu-active-color",
            "--lte-sidebar-submenu-active-color", "--lte-sidebar-width")


def scan_legacy(root):
    hits = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in (".html", ".css", ".js"):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in LEGACY.finditer(text):
            hits.append("%s: %s" % (p.as_posix(), m.group(0).lower()))
    return hits


def scan_hardcoded_primary(app_css):
    """app.css :root 主色应已 var() 转发；残留裸主色（#1877C9/#115A9B 等）即未收敛。"""
    text = app_css.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r":root\s*\{[^}]*\}", text, re.S)
    if not m:
        return ["app.css 未找到 :root 块"]
    block = m.group(0)
    hardcoded = []
    for line in block.splitlines():
        # 主色类变量（--primary / --primary-dark / --sidebar-* / --bg-light）若仍写裸 #hex 即残留
        if re.search(r"--(?:primary|primary-dark|sidebar-bg|sidebar-hover|bg-light)\s*:\s*#[0-9a-fA-F]", line):
            hardcoded.append(line.strip())
    return hardcoded


def main():
    root = pathlib.Path(__file__).resolve().parent.parent.parent  # 仓库根（scripts/checks -> ../..）
    legacy = scan_legacy(root / "templates") + scan_legacy(root / "static")
    hardcoded = scan_hardcoded_primary(root / "static" / "css" / "app.css")

    ok = True
    if legacy:
        print("旧蓝残留 %d 处：" % len(legacy))
        for h in legacy:
            print("  " + h)
        ok = False
    else:
        print("旧蓝 #4285f4/#1967d2 全站 = 0 ✅")

    if hardcoded:
        print("app.css :root 主色硬编码残留 %d 处：" % len(hardcoded))
        for h in hardcoded:
            print("  " + h)
        ok = False
    else:
        print("app.css 主色已 var() 转发到 tokens.css ✅")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
