# -*- coding: utf-8 -*-
"""079 A0：业务枚举真源读取（dict_items 单一真源，整合方案 D4/D8 选项①）。

接口（深模块，调用方只需认识两个函数）：
    get_enum(category)          -> tuple[str, ...]  # 字典取值（sort_order 序）
    get_enum_value(cat, label)  -> str              # label 等值绑定的字典 value

行为契约（零行为变化，079 验收 1/2）：
- 字典分类存在且非空 → 按字典返回；分类缺失/为空 → 回退 ``dict_seed.DICT_SEEDS`` 种子值
  （种子与收敛前硬编码逐条相等，故任何状态下取值与收敛前一致）；
- 无 app 上下文（模块导入期）→ 直接回退种子**且不落缓存**，避免导入期值冻结进程缓存；
- 进程级缓存：首次带上下文访问定值，此后改字典需重启生效
  （080 改值走迁移 + 重启，与既有部署习惯一致；079 RESULT 已登记）。
字面量唯一真源在 ``utils/dict_seed.DICT_SEEDS``；本文件不得出现枚举字面量。
"""
import threading

from utils.dict_seed import DICT_SEEDS, seed_label

_lock = threading.Lock()
_cache = {}  # category -> tuple[str, ...]（进程级；reset_enum_cache 仅测试用）


def get_enum(category):
    """按字典读枚举取值；缺分类/为空/无上下文 → 种子回退（见模块 docstring 契约）。"""
    cached = _cache.get(category)
    if cached is not None:
        return cached
    try:
        from utils.helpers import get_dict_items  # 延迟导入：避免 models 包导入环
        items = get_dict_items(category)
    except RuntimeError:
        # 模块导入期等无 app 上下文场景：回退种子且不缓存（首个带上下文的访问再定值）
        return tuple(seed_label(i) for i in DICT_SEEDS.get(category, ()))
    vals = tuple(i.label for i in items) or tuple(
        seed_label(i) for i in DICT_SEEDS.get(category, ()))
    with _lock:
        _cache.setdefault(category, vals)
    return _cache[category]


def get_enum_value(category, label):
    """label 等值绑定：返回字典中该 label 的 value；分类/项缺失或无上下文 → 回退原 label。"""
    try:
        from utils.helpers import get_dict_value  # 延迟导入：避免 models 包导入环
        v = get_dict_value(category, label)
    except RuntimeError:
        return label
    return v if v else label


def reset_enum_cache():
    """测试钩子：清空进程缓存（仅测试用；生产改字典走重启）。"""
    with _lock:
        _cache.clear()
