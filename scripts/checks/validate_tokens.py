#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_tokens.py — 三层令牌门禁（任务 003，纯标准库，不依赖 node/npm）

用法：
  python scripts/checks/validate_tokens.py --schema     强门禁（违规退出码 1，全绿 0）
  python scripts/checks/validate_tokens.py --residue    旧主色残留清单（恒退出码 0）
  python scripts/checks/validate_tokens.py              打印本用法

--schema 校验项：
  1) tokens.css 三层标记齐备（PRIMITIVE / SEMANTIC / COMPONENT）
  2) 必需语义令牌存在（--color-primary / --color-bg / ... / --color-info）
  3) --color-primary 经 var() 链解析后 == #171717（ADR-022 中性黑主操作色）
  4) semantic / component 层不得出现裸色值（应 var() 引用）

--residue 基线报告：
  扫描 static/css/app.css 与 templates/** 中旧主色 #1a73e8 / #165DFF 残留，
  输出「文件:行号」清单（作 M0-② / S1 套令牌的输入）；退出码恒 0。
"""
import argparse
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]  # 项目根（cunwuguanli-main）
TOKENS_CSS = BASE / "static" / "css" / "tokens.css"
APP_CSS = BASE / "static" / "css" / "app.css"
TEMPLATES_DIR = BASE / "templates"

PRIMARY_TARGET = "#171717"              # ADR-022 主操作色（--c-ink-2）；ADR-009 #1877C9 已作废
RESIDUE_COLORS = ("#1a73e8", "#165DFF")  # 旧主色（M0 前遗留）
REQUIRED_SEMANTIC = (
    "--color-primary", "--color-bg", "--color-surface", "--color-text",
    "--color-text-muted", "--color-border",
    "--color-success", "--color-warning", "--color-danger", "--color-info",
)
SECTION_MARKERS = ("PRIMITIVE", "SEMANTIC", "COMPONENT")

TOKEN_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def parse_tokens(css_text):
    """提取 --name: value 对（value 剥离行内注释）。"""
    tokens = {}
    for m in TOKEN_RE.finditer(css_text):
        name, value = m.group(1), m.group(2)
        value = re.sub(r"/\*.*?\*/", "", value).strip()
        tokens[name] = value
    return tokens


def resolve_var(tokens, name, depth=0):
    """沿 var(--x) 链递归解析到裸值；失败返回 None。"""
    if depth > 8 or name not in tokens:
        return None
    value = tokens[name]
    m = re.fullmatch(r"var\((--[\w-]+)\)", value)
    if m:
        return resolve_var(tokens, m.group(1), depth + 1)
    return value


def cmd_schema():
    if not TOKENS_CSS.exists():
        print(f"✗ 缺少 tokens.css：{TOKENS_CSS}")
        return 1
    text = TOKENS_CSS.read_text(encoding="utf-8")
    errors = []

    for marker in SECTION_MARKERS:
        if marker not in text:
            errors.append(f"缺少层标记：{marker}")

    tokens = parse_tokens(text)

    for name in REQUIRED_SEMANTIC:
        if name not in tokens:
            errors.append(f"缺少必需语义令牌：{name}")

    resolved = resolve_var(tokens, "--color-primary") if "--color-primary" in tokens else None
    if resolved is None:
        errors.append("--color-primary 无法解析")
    elif resolved.lower() != PRIMARY_TARGET.lower():
        errors.append(f"--color-primary 解析值为 {resolved}，应为 {PRIMARY_TARGET}")

    # semantic 段（含 component）不得写裸色值
    if "SEMANTIC" in text and HEX_RE.search(text.split("SEMANTIC", 1)[1]):
        errors.append("semantic / component 层出现裸色值（应 var() 引用）")

    if errors:
        for e in errors:
            print(f"✗ {e}")
        return 1
    print(f"✓ tokens.css 三层令牌合规：三层齐备、必需语义令牌完整、--color-primary == {PRIMARY_TARGET}")
    return 0


def cmd_residue():
    targets = []
    if APP_CSS.exists():
        targets.append(APP_CSS)
    if TEMPLATES_DIR.exists():
        targets.extend(TEMPLATES_DIR.rglob("*.html"))

    hits = []
    for p in targets:
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            low = line.lower()
            for color in RESIDUE_COLORS:
                if color.lower() in low:
                    hits.append((p.relative_to(BASE), i, color))

    if not hits:
        print("无旧主色残留（#1a73e8 / #165DFF）。")
    else:
        print(f"旧主色残留 {len(hits)} 处（供 M0-② / S1 套令牌时清除；本命令恒退出码 0）：")
        for rel, i, color in hits:
            print(f"  {rel}:{i}  {color}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="三层令牌门禁（任务 003）")
    parser.add_argument("--schema", action="store_true",
                        help="强门禁：三层齐备 + 必需语义令牌 + --color-primary 一致")
    parser.add_argument("--residue", action="store_true",
                        help="旧主色残留基线报告（恒退出码 0）")
    args = parser.parse_args(argv)

    if args.schema:
        return cmd_schema()
    if args.residue:
        return cmd_residue()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
