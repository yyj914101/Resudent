#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ShuCun Management System V2.45.1 LAN client.

V2.45.2（049）：加设备准入握手。
    连接 = 探测端口 → 握手（机器码 + 凭证）→ 按状态分支：
      disabled  单机版，无需准入 → 直接打开（旧行为）
      pending   首次登记，已保存凭证 → 提示等待管理员审批
      approved  拿到会话 token → 打开 /auth/login?device_token=…（服务端种 cookie）
      revoked   已被撤销 → 提示联系管理员
"""
import configparser
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from tkinter import messagebox

VERSION = "V2.45.2"
APP_NAME = "村务综合管理系统"
CLIENT_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ShucunManagementClient")
CONFIG_FILE = os.path.join(CLIENT_DIR, "client_config.ini")
CREDENTIAL_FILE = os.path.join(CLIENT_DIR, "device_credential.json")
WINDOW_WIDTH = 430
WINDOW_HEIGHT = 560
HANDSHAKE_TIMEOUT = 6


def cleanup_pyinstaller_temp():
    temp_root = tempfile.gettempdir()
    current = getattr(sys, "_MEIPASS", "")
    for name in os.listdir(temp_root):
        if not name.startswith("_MEI"):
            continue
        path = os.path.join(temp_root, name)
        if current and os.path.abspath(path) == os.path.abspath(current):
            continue
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass


def load_config():
    os.makedirs(CLIENT_DIR, exist_ok=True)
    config = configparser.ConfigParser()
    config.optionxform = str
    if not config.has_section("client"):
        config.add_section("client")
    if os.path.exists(CONFIG_FILE):
        try:
            config.read(CONFIG_FILE, encoding="utf-8-sig")
        except Exception:
            pass
    return config


def save_config(host, port):
    config = load_config()
    config.set("client", "host", host)
    config.set("client", "port", str(port))
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        config.write(f)


def check_server(host, port):
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- 设备准入

def _stable_machine_id():
    """稳定机器标识（**必须与 utils/license.py 同源同算法**，否则准入机器码对不上）。

    客户端不 import utils.license：那会连带 cryptography 依赖，客户端只需机器码。
    """
    import sys as _sys
    try:
        if _sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                return str(value or "")
        else:
            for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as f:
                        value = f.read().strip()
                    if value:
                        return value
    except Exception:
        pass
    return ""


def get_machine_code():
    """机器码：MAC + 稳定机器标识 → SHA256 前 16 位大写（与 utils/license.get_machine_code 等价）。"""
    import hashlib
    import uuid
    raw = f"{hex(uuid.getnode()).upper()}|{_stable_machine_id()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16].upper()


def load_credential():
    if not os.path.exists(CREDENTIAL_FILE):
        return None
    try:
        with open(CREDENTIAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("sign") else None
    except Exception:
        return None


def save_credential(credential):
    if not isinstance(credential, dict):
        return
    os.makedirs(CLIENT_DIR, exist_ok=True)
    with open(CREDENTIAL_FILE, "w", encoding="utf-8") as f:
        json.dump(credential, f, ensure_ascii=False, indent=2)


def post_json(url, payload):
    """POST JSON（Content-Type: application/json —— 服务端 CSRF 对 JSON 豁免）。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=HANDSHAKE_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def handshake(host, port, machine_code, credential, label=""):
    """返回 (status, data, error)。status ∈ disabled/pending/approved/revoked/invalid。"""
    base = f"http://{host}:{int(port)}"
    try:
        payload = {"machine_code": machine_code, "label": label}
        if credential:
            payload["credential"] = credential
        body = post_json(f"{base}/device/handshake", payload)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            return "error", {}, f"服务端返回 HTTP {exc.code}"
    except Exception as exc:
        return "error", {}, f"握手失败：{type(exc).__name__}"

    data = body.get("data") or {}
    if not body.get("success"):
        return data.get("status") or "error", data, body.get("msg") or "握手被拒绝"
    return data.get("status") or "error", data, ""


# ---------------------------------------------------------------- UI

class ClientApp:
    def __init__(self):
        cleanup_pyinstaller_temp()
        self.machine_code = get_machine_code()
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} 局域网客户端 {VERSION}")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.root.resizable(False, False)
        self.root.configure(bg="#f3f6fb")
        self.center_window()
        self.build_ui()
        self.load_saved()
        self.host_entry.focus_set()
        self.root.mainloop()

    def center_window(self):
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - WINDOW_WIDTH) // 2
        y = (self.root.winfo_screenheight() - WINDOW_HEIGHT) // 2
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

    def build_ui(self):
        main = tk.Frame(self.root, bg="#f3f6fb", padx=28, pady=18)
        main.pack(fill=tk.BOTH, expand=True)

        tk.Label(main, text=APP_NAME, font=("Microsoft YaHei", 16, "bold"), bg="#f3f6fb", fg="#165DFF").pack()
        tk.Label(main, text=f"局域网客户端 {VERSION}", font=("Microsoft YaHei", 10), bg="#f3f6fb", fg="#64748b").pack(pady=(4, 12))

        tip = tk.Label(
            main,
            text="输入服务器电脑的 IP 和端口后点击连接。首次连接会自动登记，需管理员在「系统管理 → 设备管理」批准。",
            font=("Microsoft YaHei", 9),
            bg="#eaf2ff",
            fg="#1d4ed8",
            wraplength=340,
            justify=tk.LEFT,
            padx=12,
            pady=8,
        )
        tip.pack(fill=tk.X, pady=(0, 12))

        tk.Label(main, text="本机机器码（发给管理员核对）", font=("Microsoft YaHei", 9, "bold"), bg="#f3f6fb", fg="#334155").pack(anchor=tk.W)
        machine_row = tk.Frame(main, bg="#f3f6fb")
        machine_row.pack(fill=tk.X, pady=(4, 10))
        self.machine_var = tk.StringVar(value=self.machine_code)
        tk.Entry(machine_row, textvariable=self.machine_var, font=("Consolas", 12), relief=tk.SOLID, bd=1,
                 state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        tk.Button(machine_row, text="复制", font=("Microsoft YaHei", 9), relief=tk.FLAT, bg="#e2e8f0",
                  cursor="hand2", command=self.copy_machine).pack(side=tk.LEFT, padx=(6, 0), ipady=3)

        tk.Label(main, text="服务器地址", font=("Microsoft YaHei", 9, "bold"), bg="#f3f6fb", fg="#334155").pack(anchor=tk.W)
        self.host_var = tk.StringVar()
        self.host_entry = tk.Entry(main, textvariable=self.host_var, font=("Consolas", 12), relief=tk.SOLID, bd=1)
        self.host_entry.pack(fill=tk.X, ipady=4, pady=(4, 8))

        tk.Label(main, text="端口", font=("Microsoft YaHei", 9, "bold"), bg="#f3f6fb", fg="#334155").pack(anchor=tk.W)
        self.port_var = tk.StringVar(value="5000")
        self.port_entry = tk.Entry(main, textvariable=self.port_var, font=("Consolas", 12), relief=tk.SOLID, bd=1)
        self.port_entry.pack(fill=tk.X, ipady=4, pady=(4, 8))

        tk.Label(main, text="设备凭证（管理员重新签发时粘贴到这里）", font=("Microsoft YaHei", 9, "bold"),
                 bg="#f3f6fb", fg="#334155").pack(anchor=tk.W)
        self.cred_text = tk.Text(main, height=3, font=("Consolas", 8), relief=tk.SOLID, bd=1, wrap=tk.WORD)
        self.cred_text.pack(fill=tk.X, pady=(4, 10))

        bottom = tk.Frame(main, bg="#f3f6fb")
        bottom.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_var = tk.StringVar(value="示例：192.168.1.100 / 5000")
        self.status_label = tk.Label(
            bottom,
            textvariable=self.status_var,
            font=("Microsoft YaHei", 8),
            bg="#e2e8f0",
            fg="#64748b",
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=360,
            padx=10,
        )
        self.status_label.pack(fill=tk.X, ipady=5, pady=(0, 10))

        self.connect_btn = tk.Button(
            bottom,
            text="连接并打开登录页面",
            font=("Microsoft YaHei", 11, "bold"),
            bg="#165DFF",
            fg="white",
            activebackground="#0f3f9f",
            activeforeground="white",
            relief=tk.FLAT,
            cursor="hand2",
            pady=8,
            command=self.connect,
        )
        self.connect_btn.pack(fill=tk.X)

        self.host_entry.bind("<Return>", lambda e: self.connect())
        self.port_entry.bind("<Return>", lambda e: self.connect())

    def copy_machine(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.machine_code)
        self.status_var.set(f"机器码已复制：{self.machine_code}")

    def load_saved(self):
        config = load_config()
        self.host_var.set(config.get("client", "host", fallback=""))
        self.port_var.set(config.get("client", "port", fallback="5000"))
        cred = load_credential()
        if cred:
            self.cred_text.insert("1.0", json.dumps(cred, ensure_ascii=False))

    def read_credential(self):
        """优先用手填/粘贴的凭证（管理员重新签发后），否则用本地已保存的。"""
        raw = self.cred_text.get("1.0", tk.END).strip()
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("sign"):
                    return data
            except ValueError:
                pass
        return load_credential()

    def get_input(self):
        host = self.host_var.get().strip().replace("http://", "").replace("https://", "").strip("/")
        port_text = self.port_var.get().strip() or "5000"
        if ":" in host:
            host, port_text = host.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            return None, None
        if not host or port < 1 or port > 65535:
            return None, None
        return host, port

    def connect(self):
        host, port = self.get_input()
        if not host:
            messagebox.showwarning("地址错误", "请输入正确的服务器地址和端口。")
            return
        self.status_var.set(f"正在检测 {host}:{port} ...")
        self.connect_btn.config(state=tk.DISABLED, text="正在连接...")

        def worker():
            ok = check_server(host, port)
            if not ok:
                self.root.after(0, lambda: self.after_check(False, host, port, None))
                return
            status, data, err = handshake(host, port, self.machine_code, self.read_credential(),
                                          label=socket.gethostname()[:100])
            self.root.after(0, lambda: self.after_check(True, host, port, (status, data, err)))

        threading.Thread(target=worker, daemon=True).start()

    def after_check(self, ok, host, port, result):
        self.connect_btn.config(state=tk.NORMAL, text="连接并打开登录页面")
        if not ok:
            self.status_var.set(f"无法连接 {host}:{port}，请检查服务端、防火墙和端口。")
            return

        status, data, err = result
        if status == "disabled":
            save_config(host, port)
            self.open_browser(f"http://{host}:{port}", "单机版无需设备准入，已打开登录页面")
            return

        if status == "pending":
            save_credential(data.get("credential"))
            if data.get("credential"):
                self.cred_text.delete("1.0", tk.END)
                self.cred_text.insert("1.0", json.dumps(data["credential"], ensure_ascii=False))
            save_config(host, port)
            self.status_var.set(f"已登记，等待管理员审批。机器码：{self.machine_code}")
            messagebox.showinfo("等待审批",
                                f"本设备已提交登记。\n\n机器码：{self.machine_code}\n\n"
                                f"请管理员在「系统管理 → 设备管理」中批准后，再点一次「连接」。")
            return

        if status == "approved" and data.get("token"):
            save_config(host, port)
            url = f"http://{host}:{port}/auth/login?device_token={data['token']}"
            self.open_browser(url, "准入通过，已打开登录页面")
            return

        if status == "revoked":
            self.status_var.set("本设备已被管理员撤销准入，请联系管理员。")
            messagebox.showwarning("设备已撤销", "本设备已被管理员撤销准入，请联系管理员重新签发凭证。")
            return

        self.status_var.set(f"连接被拒绝：{err}")
        messagebox.showwarning("无法连接", err or "设备未能通过准入，请联系管理员。")

    def open_browser(self, url, message):
        webbrowser.open(url)
        self.status_var.set(message)
        self.root.iconify()


def main():
    try:
        ClientApp()
    except Exception as exc:
        messagebox.showerror("启动失败", str(exc))


if __name__ == "__main__":
    main()
