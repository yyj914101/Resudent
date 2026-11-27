#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示数据逐页核验（环境准备用）：登录 → 解锁 → 逐 API 抓取，核对种子关键字。

JSON 响应先解码再查（Flask 默认 ensure_ascii=True 会把中文转义成 \\uXXXX）。
用法：python scripts/verify_demo_data.py [port]
"""
import json
import re
import sys
import urllib.request
import urllib.parse
import http.cookiejar

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5012
BASE = f'http://127.0.0.1:{PORT}'

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(path):
    req = urllib.request.Request(BASE + path)
    try:
        with opener.open(req, timeout=15) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')


def post(path, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + path, data=body, headers={
        'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with opener.open(req, timeout=15) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')


def searchable(body):
    """JSON 响应解码成不转义文本；非 JSON 原样返回。"""
    try:
        return json.dumps(json.loads(body), ensure_ascii=False)
    except ValueError:
        return body


def main():
    st, html = get('/auth/login')
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    st, body = post('/auth/login', {'username': 'admin', 'password': 'admin123',
                                    'csrf_token': m.group(1) if m else ''})
    st2, dash = get('/dashboard/')
    print(f'[登录] login={st} dashboard={st2}')
    st3, ub = post('/auth/unlock', {'password': 'Shucun2026'})
    print(f'[解锁] unlock={st3}')

    checks = [
        ('选举-届次', '/election/api/rounds', '三合村第十二届'),
        ('选举-选民', '/election/api/rounds/1/voters', '韦建国'),
        ('选举-结果', '/election/api/rounds/1/results', '村委会主任'),
        ('文明-公约', '/civilization/api/conventions', '村规民约'),
        ('文明-活动', '/civilization/roster/civilization_activity/api/list', '重阳节'),
        ('养老-名册', '/pension/roster/pension_roster/api/list', '已缴费'),
        ('安全-检查', '/safety/api/checks', '安全生产综合检查'),
        ('安全-应急名册', '/safety/api/emergency/summary', '应急工作队伍'),
        ('环卫-队伍', '/sanitation/roster/sanitation_team/api/list', '保洁队'),
        ('环卫-清运', '/sanitation/roster/sanitation_cleanup/api/list', '桂B51888'),
        ('诉求-列表', '/demand/api/list', '鸡圈'),
        ('诉求-办理', '/demand/api/detail/2', '钉桩'),
        ('公示-记录', '/notice/api/records', '特困供养'),
        ('证明-开具', '/certificate/api/issues', 'ZM202611050001'),
        ('店铺-列表', '/shop/api/list', '便民小卖部'),
        ('店铺-设施', '/shop/roster/shop_facility/api/list', '太阳能路灯'),
        ('店铺-缴费', '/shop/roster/shop_annual_fee/api/list', '垃圾处理费'),
        ('信访-列表', '/governance/petition/api/list', 'XF2026-0002'),
        ('综治-特困走访', '/governance/special-visit/api/list', '蓝海生'),
        ('综治-道路隐患', '/governance/road-hazard/api/list', '连村道'),
        ('综治-治保会', '/governance/security-committee/api/list', '主任'),
        ('党务-活动', '/party/activity/api/list', '四中全会精神'),
        ('党务-评议', '/party/activity/api/reviews?year=2026', '优秀'),
        ('党务-发展', '/party/development/api/list', '蓝志强'),
        ('就业-登记', '/welfare/employment/api/list', '灵活就业'),
        ('就业-补贴', '/welfare/employment/api/list?tab=subsidy', '社保补贴'),
    ]
    ok = bad = 0
    for name, path, kw in checks:
        st, body = get(path)
        hit = kw in searchable(body)
        ok += hit
        bad += (not hit)
        print(f"{'✓' if hit else '✗'} [{st}] {name} <- {path}"
              + ('' if hit else f'（未找到「{kw}」）'))
    print(f'\n[结果] 命中 {ok}/{len(checks)}，未命中 {bad}')


if __name__ == '__main__':
    main()
