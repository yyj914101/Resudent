#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""013 内联 JS 外移：把模板 `{% block extra_js %}` 内的 `<script>` 体抽到
`static/js/pages/<name>.js`，原位替换为 `<script src>`。

字节级搬运（不改脚本体一个字符），与 CSS 外移（S1~S3）同一方法论。
处理两类形态：
  · 默认（脚本已在 `{% block extra_js %}` 内）→ 原位换 `<script src>`。
  · `--from-content`（脚本在 `{% block content %}` 内末尾）→ 抽出脚本体、
    从 content 移除，新建 `extra_js` 块放 `<script src>`（沿用 base 的 seam）。

用法:
    python3 scripts/extract_inline_js.py templates/<路径>.html <js_name>.js [--from-content]

幂等：目标块内已无内联 `<script>`（只剩 `<script src>`）时打印 SKIP 不改动。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TAG = '<script src="{{ url_for(\'static\', filename=\'js/pages/%s\') }}"></script>'

SCRIPT = r'<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>'


def _block(block_name):
    return re.compile(r'\{%\s*block\s+' + block_name + r'\s*%\}(.*?)\{%\s*endblock\s*%\}', re.S)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    from_content = "--from-content" in sys.argv
    if len(args) != 2:
        print("用法: extract_inline_js.py <模板相对路径> <js_name.js> [--from-content]")
        sys.exit(2)
    tpl_rel, jsname = args[0], args[1]
    tpl_path = os.path.join(ROOT, tpl_rel)
    js_path = os.path.join(ROOT, "static", "js", "pages", jsname)

    with open(tpl_path, encoding="utf-8") as f:
        s = f.read()

    block_name = "content" if from_content else "extra_js"
    m = _block(block_name).search(s)
    if not m:
        print("SKIP: 无 %s 块: %s" % (block_name, tpl_rel))
        return
    region = m.group(1)

    sm = re.search(SCRIPT, region, re.S)
    if not sm:
        print("SKIP: %s 内无内联 <script>: %s" % (block_name, tpl_rel))
        return
    body = sm.group(1).lstrip("\n").rstrip("\n")
    if not body.strip():
        print("SKIP: 空脚本体:", tpl_rel)
        return

    os.makedirs(os.path.dirname(js_path), exist_ok=True)
    with open(js_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body + "\n")

    if from_content:
        # 从 content 移除脚本（连同其前后空白行），再在文件末尾追加 extra_js 块
        new_region = region[:sm.start()] + region[sm.end():]
        s = s[:m.start(1)] + new_region.rstrip("\n") + "\n" + s[m.end(1):]
        block = ('\n{% block extra_js %}\n' + (TAG % jsname) + '\n{% endblock %}\n')
        s = s.rstrip("\n") + block
    else:
        new_region = region[:sm.start()] + (TAG % jsname) + region[sm.end():]
        s = s[:m.start(1)] + new_region + s[m.end(1):]

    with open(tpl_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)

    print("OK  %s -> static/js/pages/%s (%d 行)%s" % (
        tpl_rel, jsname, body.count("\n") + 1, " [from-content]" if from_content else ""))


if __name__ == "__main__":
    main()
