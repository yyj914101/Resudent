#!/usr/bin/env python3
"""scan_child_rows.py — Z3 儿童行级收紧机械核查（T03）

扫描 care/report/resident 蓝图中「含儿童行查询」的视图是否挂 @child_rows_guard。
判定依据（§4.3-④ 机械核查约定 b）：
  含 target_type == '儿童' 或 child_type 查询的视图，必须挂 @child_rows_guard。

命中（含儿童行查询但未挂装饰器）即 FAIL（非 0 退出），并入 G-2 安全门禁。

注：care 视图的儿童行过滤实际接线在 T13/T16，本脚本在接线完成后才产生有效
FAIL 判定；T03 交付脚本 + child_rows_guard 装饰器（T06 已有）。

用法：
    python scripts/checks/scan_child_rows.py [paths...]   # 默认扫描 routes/care.py report.py resident.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 儿童行查询特征：target_type 与「儿童」比较（读儿童行），或 child_type 参与查询/过滤/比较。
# 注：仅赋值 child_type = xxx（写入）不算「查询」，避免误报写操作视图。
# 比较/过滤信号：==、!=、.in_、.like、.filter；排除单 = 赋值（child_type = ...）。
_RE_CHILD_QUERY = re.compile(
    r"target_type\s*==\s*['\"]儿童['\"]"
    r"|child_type\s*(?:==|!=|\.in_|\.like|\.filter)"
)
# 装饰器挂载特征
_RE_GUARD = re.compile(r"@\s*child_rows_guard")

_DEFAULT_FILES = [
    os.path.join(ROOT, "routes", "care.py"),
    os.path.join(ROOT, "routes", "report.py"),
    os.path.join(ROOT, "routes", "resident.py"),
]


def _find_view_decorators(lines, view_lineno):
    """从视图定义行向上找装饰器（最多回溯 5 行）。"""
    for i in range(max(0, view_lineno - 6), view_lineno):
        if _RE_GUARD.search(lines[i]):
            return True
    return False


def scan_file(path: str) -> list:
    """返回 [(path, lineno, view, msg)]（含儿童查询但未挂装饰器的视图）。"""
    hits = []
    try:
        lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
    except OSError:
        return hits

    # 找视图定义（def xxx(...) 或 @bp.route 下的 def）；跳过下划线前缀的辅助函数
    # （Flask 约定：路由视图为无前缀公开函数，_xxx 为辅助函数，非「视图」）
    view_starts = []
    for i, line in enumerate(lines):
        m = re.match(r"\s*def\s+(\w+)\s*\(", line)
        if m and not m.group(1).startswith('_'):
            view_starts.append(i)

    for lineno in view_starts:
        # 收集视图体（直到下一个 def 或空行前的缩进块）
        body = []
        j = lineno + 1
        while j < len(lines):
            l = lines[j]
            if re.match(r"\s*def\s+\w+\s*\(", l) or re.match(r"\s*@\w+", l):
                break
            body.append(l)
            j += 1
        body_text = "\n".join(body)
        if _RE_CHILD_QUERY.search(body_text):
            if not _find_view_decorators(lines, lineno):
                hits.append((path, lineno, lines[lineno].strip()[:60],
                              "含儿童行查询但未挂 @child_rows_guard"))
    return hits


def main(argv=None) -> int:
    paths = (argv or sys.argv[1:]) or _DEFAULT_FILES
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
        all_hits.extend(scan_file(f))

    for h in all_hits:
        print(f"{h[0]}:{h[1]}\t{h[2]}\t{h[3]}")

    print(f"\n=== 汇总 ===\n含儿童行查询但漏挂装饰器的视图：{len(all_hits)} 处")
    if all_hits:
        print("FAIL：存在儿童行查询视图未挂 @child_rows_guard")
        return 1
    print("PASS：儿童行查询视图均已挂 @child_rows_guard（或暂无儿童行查询）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
