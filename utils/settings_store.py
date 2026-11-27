# -*- coding: utf-8 -*-
"""系统设置字典统一读取入口（P1-1：消除 utils -> routes.backup 反向依赖）。"""
import logging

logger = logging.getLogger(__name__)


def get_settings_value(key: str, default: str = '') -> str:
    """读取字典类别「系统设置」下的键值，异常时记录日志并返回默认值。"""
    try:
        from models import DictCategory, DictItem
        cat = DictCategory.query.filter_by(name='系统设置').first()
        if cat:
            item = DictItem.query.filter_by(category_id=cat.id, label=key).first()
            if item and item.value:
                return item.value
    except Exception:
        logger.debug('读取系统设置 %s 失败，回退默认值', key, exc_info=True)
    return default
