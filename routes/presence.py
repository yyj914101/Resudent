# -*- coding: utf-8 -*-
"""routes/presence.py — 用户在线状态（057b，G-02 心跳口径）。

- POST /api/heartbeat    shell.js 每 30s 调用，刷新当前用户 last_active
- GET  /api/online-count now - last_active < 60s 的用户数（心跳 30s，窗口 60s）

ADR-016 归类：全体登录角色可见（@login_required 端点内自校验，同 dashboard.index
先例）。调用契约：均走 JSON（Content-Type: application/json 或 X-Requested-With），
login_required 对 JSON/XHR 返回 401 而非 302 重定向。
"""
from datetime import datetime, timedelta

from flask import Blueprint, g, jsonify

from models import db, User
from utils.decorators import login_required

presence_bp = Blueprint('presence', __name__)

ONLINE_WINDOW_SECONDS = 60


@presence_bp.route('/heartbeat', methods=['POST'])
@login_required
def heartbeat():
    user = g.current_user
    if not user:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    user.last_active = datetime.now()
    db.session.commit()
    return jsonify({'success': True})


@presence_bp.route('/online-count')
@login_required
def online_count():
    cutoff = datetime.now() - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    online = User.query.filter(User.last_active > cutoff).count()
    return jsonify({'success': True, 'online': online})
