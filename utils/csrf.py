# -*- coding: utf-8 -*-
"""utils.csrf — 全局 CSRF 防护（P2-1 整改收尾）。

背景：既往整改仅完成 `SESSION_COOKIE_SAMESITE='Lax'` Cookie 加固（主要缓解），
全局 CSRF token 校验未实施（需前端配合）。本模块补齐服务端 token 生成/校验，
配合 base.html 的自动携带 JS 与各表单的 hidden input，闭环"上传/导入类接口
可被跨站触发"的 CSRF 攻击面。

口径：
- token 按会话生成（secrets.token_urlsafe(32)），存 session['_csrf_token']。
- 校验用 hmac.compare_digest 常量时间比较，防时序侧信道。
- JSON 接口（Content-Type: application/json）由浏览器 CORS 预检保护（服务端
  无 Access-Control-Allow-Origin），故豁免；本机回环通道（launcher/unlock/
  recovery）豁免。
"""
import secrets
import hmac

from flask import session

_SESSION_KEY = '_csrf_token'


def generate_csrf_token() -> str:
    """生成（或复用）当前会话的 CSRF token 并返回。渲染页面时经 context_processor 调用。"""
    token = session.get(_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_SESSION_KEY] = token
    return token


def get_csrf_token() -> str:
    """读取当前会话 CSRF token（不存在返回空串，无副作用）。"""
    return session.get(_SESSION_KEY, '') or ''


def validate_csrf_token(submitted: str) -> bool:
    """校验提交 token 与会话 token 是否一致（常量时间比较）。"""
    expected = get_csrf_token()
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)
