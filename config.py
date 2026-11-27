import os, sys, secrets

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 优先使用环境变量（由 run_server.py 设置），否则使用默认路径
DATA_DIR = os.environ.get('SHUCUN_DATA_DIR', BASE_DIR)
DB_PATH = os.environ.get('SHUCUN_DB_PATH', os.path.join(BASE_DIR, 'shucun.db'))
UPLOAD_DIR = os.environ.get('SHUCUN_UPLOAD_DIR', os.path.join(BASE_DIR, 'uploads'))
BACKUP_DIR = os.environ.get('SHUCUN_BACKUP_DIR', os.path.join(BASE_DIR, 'backups'))
LICENSE_FILE = os.environ.get('SHUCUN_LICENSE_FILE', os.path.join(DATA_DIR, 'license.key'))
TEMP_DIR = os.environ.get('SHUCUN_TEMP_DIR', os.path.join(DATA_DIR, 'temp'))
SERVER_PORT = os.environ.get('SHUCUN_PORT', '5000')


def _load_secret_key() -> str:
    """P0-1 整改：SECRET_KEY 不再硬编码，改为「环境变量 → 本地密钥文件 → 首启随机生成」。

    加载顺序：
      1. SHUCUN_SECRET_KEY 环境变量（部署方显式注入，优先级最高）
      2. DATA_DIR/secret.key（首次安装时随机生成并落盘，每实例独立）
      3. 两者皆无 → 随机生成并落盘（进程内首启告警提示）

    红线：禁止在源码中硬编码会话签名密钥；密钥每部署实例独立、随机、
    不落于可读源码。删除 secret.key 会导致所有已登录会话失效（需重新登录）。
    """
    key = os.environ.get('SHUCUN_SECRET_KEY', '').strip()
    if key:
        return key

    key_file = os.path.join(DATA_DIR, 'secret.key')
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                key = f.read().strip()
            if key:
                return key
    except OSError:
        pass

    # 首次安装：随机生成并落盘（文件权限尽量收敛到仅属主可读写）
    key = secrets.token_urlsafe(48)
    try:
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        print('[安全] 已生成随机 SECRET_KEY 并落盘：%s（请勿删除，删除将导致所有会话失效）'
              % key_file, file=sys.stderr)
    except OSError:
        print('[警告] 无法持久化 SECRET_KEY，本次会话使用进程内随机密钥'
              '（重启后所有会话失效，请检查目录写权限）', file=sys.stderr)
    return key


class Config:
    SECRET_KEY = _load_secret_key()
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{DB_PATH}?check_same_thread=False'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 30}, 'pool_pre_ping': True}
    UPLOAD_FOLDER = UPLOAD_DIR
    BACKUP_FOLDER = BACKUP_DIR
    LICENSE_FILE = LICENSE_FILE
    TEMP_FOLDER = TEMP_DIR
    EXPORTS_DIR = os.path.join(TEMP_DIR, 'exports')
    IMPORTS_DIR = os.path.join(TEMP_DIR, 'imports')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024
    # 092 模板附件区：单附件上限（可配，默认 10MB；utils/attachment_service 消费）
    ATTACHMENT_MAX_SIZE = 10 * 1024 * 1024
    SESSION_TIMEOUT_MINUTES = 30
    # P2-1 整改：会话 Cookie 加固（SameSite=Lax 防跨站 CSRF；HttpOnly 防 XSS 窃取会话）
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # HTTPS / 反代部署时通过环境变量开启 Secure 标记
    SESSION_COOKIE_SECURE = os.environ.get('SHUCUN_COOKIE_SECURE', '0') == '1'
    PER_PAGE = 20
    # R2（T13）：高龄老人动态归类阈值（可配，默认 80，U6 到位后对齐微调）
    ELDERLY_AGE_THRESHOLD = 80
    # T21-2（R10 花名册 PDF）：思源黑体（Noto Sans SC，SIL OFL 1.1）随包预置
    # frozen 打包后 BASE_DIR=可执行文件目录，static/fonts 由打包器随包收集（见计划 §风险）
    FONT_REGULAR_PATH = os.path.join(BASE_DIR, 'static', 'fonts', 'NotoSansSC-Regular.ttf')
    FONT_BOLD_PATH = os.path.join(BASE_DIR, 'static', 'fonts', 'NotoSansSC-Bold.ttf')
