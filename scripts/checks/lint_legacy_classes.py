#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lint_legacy_classes.py — 禁用类 + 图标基线 门禁（对齐 handoff 补丁 §五 v1.2）

用法：
  python scripts/checks/lint_legacy_classes.py                    强门禁（违规退出码 1，全绿 0）
  python scripts/checks/lint_legacy_classes.py --list             仅列残留清单，恒退出码 0
  python scripts/checks/lint_legacy_classes.py --set-bi-baseline  把当前 bi-* 计数写为图标基线

判定规则（对齐 handoff 补丁 §一② / §五）：
  1) 结构旧类：仅在 class 属性的 token 中精确匹配，出现即判缺陷 → 退出码 1。
     精确 token 匹配天然排除 sc- 前缀（sc-pagination 的 token 是 "sc-pagination"，不等于
     "pagination"），也排除 JS 变量名 / 注释中的同名词。
  2) bi bi-* 图标：只减不增。以 bi_baseline.json 记录基线，当前计数 > 基线才 fail；
     持平或减少放行。首次无基线时仅告警（提示 --set-bi-baseline），不 fail。

白名单：mediation/list.html 的 table-responsive（crudTable 列设置菜单定位，有意保留）。
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.normpath(os.path.join(HERE, "..", "..", "templates"))
BI_BASELINE = os.path.join(HERE, "bi_baseline.json")

# 精确单 token 类（class="..." 内 token 完全相等才命中）
EXACT_DENY = ["card-body", "card-header", "table-bordered", "form-control", "form-select",
              "pagination", "badge", "btn-primary", "btn-secondary", "btn-danger"]
# 前缀类（token 以该前缀开头）
PREFIX_DENY = ["btn-outline-", "col-md-", "col-sm-", "col-lg-", "col-xl-"]
# 软告警（只提示不 fail）
SOFT_WARN = ["table-responsive"]
# 白名单：(文件路径片段, 类, 理由)
WHITELIST = [("mediation/list.html", "table-responsive", "crudTable 列设置菜单定位，有意保留")]

CLASS_ATTR = re.compile(r'class=["\']([^"\']*)["\']')
BI_RE = re.compile(r'class="[^"]*\bbi bi-[a-z0-9-]+')


def rel(p):
    return os.path.relpath(p, TEMPLATES).replace("\\", "/")


def walk():
    for dp, _dirs, fs in os.walk(TEMPLATES):
        for fn in fs:
            if fn.endswith(".html"):
                yield os.path.join(dp, fn)


def _whitelisted(r, name):
    return any(w[0] in r and w[1] == name for w in WHITELIST)


def _class_tokens(text):
    """返回 class 属性内全部 token 列表（多次 class 属性合并）"""
    toks = []
    for m in CLASS_ATTR.finditer(text):
        toks.extend(m.group(1).split())
    return toks


def scan():
    hits = {k: [] for k in EXACT_DENY + PREFIX_DENY}
    soft = {k: [] for k in SOFT_WARN}
    bi = 0
    bi_files = set()
    for p in walk():
        r = rel(p)
        text = open(p, encoding="utf-8", errors="replace").read()
        toks = _class_tokens(text)
        for name in EXACT_DENY:
            if name in toks and not _whitelisted(r, name):
                hits[name].append(r)
        for pref in PREFIX_DENY:
            if any(t.startswith(pref) for t in toks) and not _whitelisted(r, pref):
                hits[pref].append(r)
        # 组合：Bootstrap 栅格 class="row g-3"（row 后紧跟 g-N）
        if "row" in toks and any(t.startswith("g-") for t in toks):
            hits.setdefault("row.g-*", []).append(r)
        for name in SOFT_WARN:
            if name in toks and not _whitelisted(r, name):
                soft[name].append(r)
        n = len(BI_RE.findall(text))
        if n:
            bi += n
            bi_files.add(r)
    return hits, soft, bi, bi_files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--set-bi-baseline", action="store_true")
    args = ap.parse_args()

    hits, soft, bi, bi_files = scan()

    if args.set_bi_baseline:
        with open(BI_BASELINE, "w", encoding="utf-8") as f:
            json.dump({"baseline": bi}, f, ensure_ascii=False, indent=2)
        print(f"[lint] bi-* 基线已写入 {BI_BASELINE}: {bi} 处")
        return 0

    keys = EXACT_DENY + PREFIX_DENY + ["row.g-*"]
    if args.list:
        print("== 结构旧类残留 ==")
        for name in keys:
            if name in hits and hits[name]:
                print(f"  {name}: {len(hits[name])} 文件")
                for r in hits[name]:
                    print(f"      {r}")
        print("== 软告警（不 fail） ==")
        for name in SOFT_WARN:
            for r in soft[name]:
                print(f"  {name}: {r}")
        print(f"== bi bi-* 图标 ==  {bi} 处 / {len(bi_files)} 文件")
        return 0

    total = sum(len(hits.get(k, [])) for k in keys)
    if total:
        print(f"[lint] FAIL: 结构旧类残留 {total} 处（文件级）")
        for name in keys:
            for r in hits.get(name, []):
                print(f"  {name}: {r}")
        return 1

    base = None
    if os.path.exists(BI_BASELINE):
        try:
            base = json.load(open(BI_BASELINE, encoding="utf-8")).get("baseline")
        except Exception:
            base = None
    if base is None:
        print(f"[lint] WARN: 无 bi-* 基线，跳过图标判定（当前 {bi} 处；首次请 --set-bi-baseline）")
    elif bi > base:
        print(f"[lint] FAIL: bi-* 计数增加 {base} -> {bi}（只减不增）")
        return 1
    else:
        print(f"[lint] bi-* 基线 {base}，当前 {bi}（持平/减少，通过）")

    print("[lint] PASS: 结构旧类清零，图标基线通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
