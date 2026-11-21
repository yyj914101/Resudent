"""同步状态 provider（064）：顶栏「同步状态」的唯一真源。

产品事实：当前为单机 SQLite，无同步服务 → local「本机数据」。
LAN 部署时把 `_provider` 换成读取真实同步状态的函数（返回同一结构），
模板与样式零改动；任何异常统一降级 unknown，不显示假时间。
"""


def _local_status():
    """单机模式：陈述事实，不编造同步时间。"""
    return {
        'mode': 'local',
        'state': 'local',
        'tone': 'muted',
        'text': '本机数据',
        'title': '单机模式：数据保存在本机，未启用同步',
    }


def _unknown_status():
    """provider 异常 / 无数据：灰点 + 未知，不隐藏控件。"""
    return {
        'mode': 'unknown',
        'state': 'unknown',
        'tone': 'muted',
        'text': '同步状态未知',
        'title': '同步状态暂不可用，请稍后刷新',
    }


# LAN 升级 seam：仅替换此 provider，模板仍消费 {mode,state,tone,text,title}。
_provider = _local_status


def current_sync_status():
    """返回模板可直接消费的状态结构；异常不拖垮整页渲染。"""
    try:
        return _provider()
    except Exception:
        return _unknown_status()
