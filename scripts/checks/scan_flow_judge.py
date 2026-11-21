#!/usr/bin/env python3
"""scan_flow_judge.py — A4 流动判断唯一性静态扫描（T03）

扫描 routes/ + utils/ 中散落的流动性判断，拦截「唯一实现」之外的裸比较：
- `flow_status == ...` / `flow_status != ...` 直接比较（除 utils/resident_flow.py 本身）
- `status == 'moved_out'` / `status == "moved_out"` 旧状态字面比较

命中即 FAIL（非 0 退出），并入 Sprint 子门禁与 G-5 安全门禁。

用法：
    python scripts/checks/scan_flow_judge.py [paths...]   # 默认扫描 routes/ + utils/
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 唯一实现文件（豁免）
_WHITELIST = {'resident_flow.py'}

# 裸 flow_status 比较（排除 is_outflow/flow_label 内部）
_RE_FLOW_STATUS = re.compile(r"flow_status\s*[!=]=")
# 旧状态字面 moved_out 比较
_RE_MOVED_OUT = re.compile(r"status\s*[!=]=\s*['\"]moved_out['\"]", re.IGNORECASE)


def scan_file(path: str) -> list:
    """返回 [(path, lineno, kind, snippet)]。"""
    hits = []
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return hits
    for lineno, line in enumerate(text.splitlines(), 1):
        if _RE_FLOW_STATUS.search(line):
            hits.append((path, lineno, "flow_status", line.strip()[:120]))
        if _RE_MOVED_OUT.search(line):
            hits.append((path, lineno, "moved_out", line.strip()[:120]))
    return hits


def main(argv=None) -> int:
    paths = (argv or sys.argv[1:]) or [
        os.path.join(ROOT, "routes"),
        os.path.join(ROOT, "utils"),
    ]
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, fs in os.walk(p):
                for f in fs:
                    if f.endswith(".py"):
                        files.append(os.path.join(root, f))
        elif os.path.isfile(p):
            files.append(p)

    all_hits = []
    for f in files:
        if os.path.basename(f) in _WHITELIST:
            continue  # 豁免唯一实现文件
        all_hits.extend(scan_file(f))

    fail_hits = [h for h in all_hits if h[2] == "flow_status"]   # 硬 FAIL
    warn_hits = [h for h in all_hits if h[2] == "moved_out"]     # 既有债务 warn

    for h in all_hits:
        print(f"{h[0]}:{h[1]}\t[{h[2]}]\t{h[3]}")

    print(f"\n=== 汇总 ===")
    print(f"flow_status 裸比较（硬 FAIL）：{len(fail_hits)} 处")
    print(f"status=='moved_out' 旧状态字面（warn 债务，归口 T11）：{len(warn_hits)} 处")

    if fail_hits:
        print("FAIL：存在唯一实现之外的 flow_status 流动性判断，须收敛到 utils/resident_flow.py")
        return 1
    if warn_hits:
        print("PASS（含 warn）：flow_status 唯一判断已收敛；存在 moved_out 旧状态债务，归口 T11 收敛")
        return 0
    print("PASS：无散落流动判断（唯一实现收敛于 utils/resident_flow.py）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
