# 村务综合管理系统

这是一个基于 Python Flask、Jinja2、Bootstrap 和 SQLite 的村务综合管理系统，支持本机和局域网运行。

## 技术栈

- Python 3.12
- Flask 3.0
- Flask-SQLAlchemy / SQLite
- Waitress
- Jinja2 / Bootstrap / jQuery
- PyInstaller（Windows 打包）

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run_server.py
```

启动后访问 `http://127.0.0.1:5000`。

## Windows 打包

```powershell
python -m pip install -r requirements-build.txt
pyinstaller shucun_server.spec
pyinstaller ShuCunLauncher.spec
pyinstaller ShuCunClient.spec
```

## 数据安全

- `shucun.db`、`uploads/`、`backups/`、`instance/` 和 `license.key` 都是本地运行数据，不进入 Git。
- `seed/shucun_seed.db` 只能保留脱敏的初始字典、角色和默认用户，不得包含真实村民数据。
- Git 只用于源码版本管理，不替代数据库备份。
- 本项目包含授权校验逻辑，远程仓库必须设置为私有。

## 版本管理建议

1. 修改前先确认 `git status`。
2. 每个功能单独提交，提交说明写清模块和目的。
3. 发布稳定版时创建标签，例如 `v2.45`。
4. EXE 和 ZIP 放在 GitHub Releases 或独立备份位置，不直接提交到源码历史。
