#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ShuCun Management System V2.45.1 desktop launcher.

T05 增量：启动口令采集 + 回环一次性令牌通道（B13）。
- launcher 在启动服务前弹出口令输入框
- 通过 POST /auth/bootstrap-key?token=...&password=... 提交（仅本机回环）
- 口令不落盘、不写日志；密钥由服务端 KeyHolder 内存持有
"""
import configparser
import json
import os
import socket
import subprocess
import sys
import time
import tkinter as tk
import urllib.parse
import urllib.request
import webbrowser
from tkinter import messagebox

VERSION = "V2.45.1"
APP_NAME = "村务综合管理系统"

# B13：主通道端点（仅本机回环）
BOOTSTRAP_PATH = "/auth/bootstrap-key"


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.dirname(__file__))


def load_server_config():
    app_dir = get_app_dir()
    config_path = os.path.join(app_dir, "config.ini")
    config = configparser.ConfigParser()
    config.optionxform = str
    if os.path.exists(config_path):
        config.read(config_path, encoding="utf-8-sig")
    section = "server" if config.has_section("server") else "Server"
    host = config.get(section, "host", fallback="0.0.0.0") if config.has_section(section) else "0.0.0.0"
    port = config.get(section, "port", fallback="5000") if config.has_section(section) else "5000"
    return host, int(port)


def is_port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
            return True
    except OSError:
        return False


def start_server_hidden(app_dir):
    server_candidates = [
        os.path.join(app_dir, "村务综合管理系统后台服务.exe"),
        os.path.join(app_dir, "ShuCunServer.exe"),
        os.path.join(app_dir, "run_server.py"),
    ]
    server_exe = next((path for path in server_candidates if os.path.exists(path)), "")
    if not os.path.exists(server_exe):
        raise FileNotFoundError("未找到后台服务程序")

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0

    if server_exe.lower().endswith(".py"):
        cmd = [sys.executable, server_exe]
    else:
        cmd = [server_exe]

    subprocess.Popen(
        cmd,
        cwd=app_dir,
        creationflags=subprocess.CREATE_NO_WINDOW,
        startupinfo=startupinfo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=False,
    )


def request_bootstrap_key(port, token, password):
    """向本机服务提交启动口令（B13 主通道）。返回 (ok, msg)。"""
    url = f"http://127.0.0.1:{port}{BOOTSTRAP_PATH}"
    query = urllib.parse.urlencode({"token": token, "password": password})
    req = urllib.request.Request(f"{url}?{query}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("success")), data.get("msg", "")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            return False, body.get("msg", f"HTTP {e.code}")
        except Exception:
            return False, f"HTTP {e.code}"
    except Exception as exc:
        return False, str(exc)


def request_bootstrap_token(port):
    """从本机服务取回一次性启动令牌（P1-3 整改，仅回环）。失败返回 None。"""
    url = f"http://127.0.0.1:{port}/auth/bootstrap-token"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("token") if data.get("success") else None
    except Exception:
        return None


class Launcher:
    def __init__(self):
        self.app_dir = get_app_dir()
        self.host, self.port = load_server_config()
        self.cancelled = False
        self.bootstrap_token = ""  # 由服务端 /auth/bootstrap-token 下发（预留），此处用本机通道

        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {VERSION}")
        self.root.geometry("400x300")
        self.root.resizable(False, False)
        self.root.configure(bg="#f3f6fb")
        self.root.protocol("WM_DELETE_WINDOW", self.close_launcher_only)
        self.center_window()
        self.build_ui()
        self.root.after(200, self.start_flow)

    def center_window(self):
        self.root.update_idletasks()
        w, h = 400, 300
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def build_ui(self):
        tk.Label(self.root, text=APP_NAME, font=("Microsoft YaHei", 15, "bold"), bg="#f3f6fb", fg="#165DFF").pack(pady=(18, 2))
        tk.Label(self.root, text=VERSION, font=("Microsoft YaHei", 9), bg="#f3f6fb", fg="#64748b").pack()
        tk.Label(self.root, text="请输入启动口令以解锁数据加密密钥", font=("Microsoft YaHei", 9),
                 bg="#f3f6fb", fg="#334155").pack(pady=(14, 6))
        self.pwd_var = tk.StringVar()
        self.pwd_entry = tk.Entry(self.root, textvariable=self.pwd_var, show="●",
                                  font=("Microsoft YaHei", 12), width=26, justify="center")
        self.pwd_entry.pack(pady=(2, 10))
        self.pwd_entry.bind("<Return>", lambda e: self.on_submit_password())
        tk.Button(self.root, text="确认并启动", command=self.on_submit_password,
                  bg="#165DFF", fg="#ffffff", relief="flat", width=18,
                  font=("Microsoft YaHei", 10)).pack(pady=(2, 8))
        self.status_var = tk.StringVar(value="正在检测后台服务...")
        tk.Label(self.root, textvariable=self.status_var, font=("Microsoft YaHei", 9),
                 bg="#f3f6fb", fg="#94a3b8").pack(pady=(8, 4))
        self.progress = tk.Canvas(self.root, width=270, height=7, bg="#dbe3ef", highlightthickness=0)
        self.progress.pack()
        self.bar = self.progress.create_rectangle(0, 0, 0, 7, fill="#165DFF", outline="")

    def set_progress(self, percent, text):
        self.progress.coords(self.bar, 0, 0, int(270 * percent / 100), 7)
        self.status_var.set(text)
        self.root.update()

    def on_submit_password(self):
        pwd = self.pwd_var.get()
        if not pwd:
            messagebox.showwarning("提示", "请输入启动口令")
            return
        self.set_progress(15, "正在启动服务端...")
        if not is_port_open(self.port):
            try:
                start_server_hidden(self.app_dir)
            except Exception as exc:
                messagebox.showerror("启动失败", f"服务端启动失败：{exc}")
                return
        self.start_time = time.time()
        self.wait_and_submit(pwd)

    def wait_and_submit(self, password):
        if not is_port_open(self.port):
            if int(time.time() - self.start_time) >= 30:
                messagebox.showwarning("启动超时", "服务启动超时，请检查端口占用或防火墙。")
                return
            self.set_progress(min(30 + (int(time.time() - self.start_time)) * 2, 60), "等待服务就绪...")
            self.root.after(500, lambda: self.wait_and_submit(password))
            return
        # 服务已就绪 → 取一次性令牌（P1-3 整改，修复恒 401）
        self.set_progress(65, "正在获取启动令牌...")
        token = request_bootstrap_token(self.port)
        if not token:
            self.set_progress(0, "获取启动令牌失败")
            messagebox.showerror("解锁失败", "无法获取启动令牌，请稍后重试。")
            return
        # 主通道提交口令（本机回环）
        self.set_progress(70, "正在提交启动口令...")
        ok, msg = request_bootstrap_key(self.port, token, password)
        if ok:
            self.set_progress(100, "密钥已解锁，正在打开浏览器...")
            self.root.after(400, self.open_browser)
        else:
            self.set_progress(0, msg or "口令提交失败")
            messagebox.showerror("解锁失败", f"{msg}\n请确认口令后重试（不锁定）。")

    def open_browser(self):
        webbrowser.open(f"http://127.0.0.1:{self.port}")
        self.root.destroy()

    def close_launcher_only(self):
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    Launcher().run()


if __name__ == "__main__":
    main()
