"""
村务管理系统 v2.2 - 后台服务
"""
import os, sys, threading, webbrowser, time, socket

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

from app import app
from utils.license import check_license_on_startup

host = '0.0.0.0' if check_license_on_startup() else '127.0.0.1'
threading.Thread(target=lambda: app.run(host=host, port=5000, debug=False, use_reloader=False), daemon=True).start()

time.sleep(2)
webbrowser.open('http://127.0.0.1:5000')

# 保持进程运行（console=False 时无窗口）
while True:
    time.sleep(1)
