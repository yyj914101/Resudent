#!/usr/bin/env python3
"""scan_mask.py — K8 静态扫描（T06）：扫描模板/导出代码路径的敏感字段裸渲染。

用法：
    python scripts/checks/scan_mask.py [paths...]      # 默认扫描 templates/ + routes/

扫描两类命中：
1. `key`   —— 敏感字段 key 引用（id_card/phone/bank_card 及 12 变体），标记敏感数据出口点。
2. `plain` —— 字面明文敏感值（18 位身份证 / 11 位手机 / 16-19 位银行卡），硬编码明文泄漏。

K8 基线：T06 落地后，脱敏视图输出应 0 明文命中（由 test_masking 的 JSON 层测试佐证），
本脚本用于对源码的持续静态审计（漏改即破 K8=100%）。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.masking import SENSITIVE_KEY_SUFFIXES, static_scan_text  # noqa: E402

_SENSITIVE_KEY_PATTERN = re.compile(
    "(" + "|".join(sorted(SENSITIVE_KEY_SUFFIXES, key=len, reverse=True)) + ")"
)


def scan_file(path: str) -> list:
    """返回 [(path, lineno, kind, matched, snippet)]。"""
    hits = []
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return hits
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in _SENSITIVE_KEY_PATTERN.finditer(line):
            hits.append((path, lineno, "key", m.group(0), line.strip()[:120]))
        for kind, values in static_scan_text(line).items():
            for v in values:
                hits.append((path, lineno, f"plain:{kind}", v, line.strip()[:120]))
    return hits


def main(argv=None) -> int:
    paths = (argv or sys.argv[1:]) or [
        os.path.join(ROOT, "templates"),
        os.path.join(ROOT, "routes"),
    ]
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, fs in os.walk(p):
                for f in fs:
                    if f.endswith((".html", ".py", ".js")):
                        files.append(os.path.join(root, f))
        elif os.path.isfile(p):
            files.append(p)

    all_hits = []
    for f in files:
        all_hits.extend(scan_file(f))

    key_hits = [h for h in all_hits if h[2] == "key"]
    plain_hits = [h for h in all_hits if h[2].startswith("plain")]

    for h in all_hits:
        print(f"{h[0]}:{h[1]}\t[{h[2]}]\t{h[3]}")

    print("\n=== 汇总 ===")
    print(f"敏感字段 key 引用：{len(key_hits)} 处")
    print(f"字面明文敏感值：{len(plain_hits)} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
