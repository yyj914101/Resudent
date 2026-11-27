"""居民流动性唯一判断（A4 红线，T03 落地 + T11 扩展）

全工程唯一实现：is_outflow / flow_label / is_inflow，以及
查询过滤辅助 filter_by_flow_status / exclude_outflow（T11-5 流动标记筛选）。
禁止在其他位置散落 `status == 'moved_out'` 或 `flow_status` 裸比较，
违者由 scripts/checks/scan_flow_judge.py 静态扫描拦截。

注：居民状态字段为 residents.status（normal/deceased/moved_out），
T08 扩展 flow_status（常住/流入/流出）作为流动性唯一判定来源。

079 A0：三个状态值改从「流动状态」字典读（utils/dict_enum.get_enum，进程级缓存，
首次访问定值；改字典需重启——080 改值走迁移+重启）。模块经 PEP 562 ``__getattr__``
暴露 FLOW_RESIDENT / FLOW_IN / FLOW_OUT 三个常量名：既有的
``from utils.resident_flow import FLOW_OUT`` 等导入写法与比较语义完全不变，
字典缺项/无上下文（模块导入期）由 dict_enum 回退种子值（与旧字面量逐条相等）。
"""
from __future__ import annotations

# 流动性状态名 → dict_enum 取值槽（label 等值绑定；种子回退见 dict_enum 契约）
_FLOW_SLOTS = ('FLOW_RESIDENT', 'FLOW_IN', 'FLOW_OUT')
_FLOW_LABEL_OF_SLOT = {'FLOW_RESIDENT': '常住', 'FLOW_IN': '流入', 'FLOW_OUT': '流出'}

_flow_values_cache = None


def _flow_values() -> dict:
    """槽名 → 字典取值（进程缓存一次）。"""
    global _flow_values_cache
    if _flow_values_cache is None:
        from utils.dict_enum import get_enum_value
        _flow_values_cache = {slot: get_enum_value('流动状态', label)
                              for slot, label in _FLOW_LABEL_OF_SLOT.items()}
    return _flow_values_cache


def __getattr__(name):  # PEP 562：FLOW_RESIDENT / FLOW_IN / FLOW_OUT 惰性常量
    if name in _FLOW_SLOTS:
        return _flow_values()[name]
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def _flow_labels() -> dict:
    """flow_status 值 → 展示标签（值与标签在种子语义下相同；字典化后随值走）。"""
    vals = _flow_values()
    return {vals[slot]: label for slot, label in _FLOW_LABEL_OF_SLOT.items()}


def is_outflow(resident) -> bool:
    """唯一流动性判断：resident.flow_status == '流出'。

    全工程禁止其他组合判断（如 status=='moved_out' 等）。
    """
    if resident is None:
        return False
    # 模块内取值走显式访问器（LOAD_GLOBAL 不经 PEP 562 __getattr__）
    return getattr(resident, 'flow_status', None) == _flow_values()['FLOW_OUT']


def is_inflow(resident) -> bool:
    """T11-5：是否流入居民（flow_status == '流入'）。"""
    if resident is None:
        return False
    return getattr(resident, 'flow_status', None) == _flow_values()['FLOW_IN']


def flow_label(resident) -> str:
    """返回流动性展示标签（常住/流入/流出；未知值回退原文）。"""
    if resident is None:
        return ''
    status = getattr(resident, 'flow_status', None)
    return _flow_labels().get(status, status or '')


def filter_by_flow_status(query, value):
    """按流动状态筛选（列表/导出过滤用，非单条判断；T11-5）。"""
    if value:
        from models.resident import Resident
        query = query.filter(Resident.flow_status == value)
    return query


def exclude_outflow(query):
    """排除流出居民（候选列表口径，A4：流出不进候选人）。"""
    from sqlalchemy import or_
    from models.resident import Resident
    return query.filter(or_(
        Resident.flow_status.is_(None),
        Resident.flow_status != _flow_values()['FLOW_OUT'],
    ))
