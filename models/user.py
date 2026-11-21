from . import db
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    real_name = db.Column(db.String(50))
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'))
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    # 057b 在线口径（G-02）：心跳刷新，/api/online-count 取 now - last_active < 60s
    last_active = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)
    # P1-2 整改：首次登录强制改密标记（默认账号/管理员重置后置 True）
    must_change_password = db.Column(db.Boolean, default=False)

    role = db.relationship('Role', backref='users')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))
    permissions = db.Column(db.Text)  # JSON string of permissions
    is_system = db.Column(db.Boolean, default=False)  # can't delete system roles
    created_at = db.Column(db.DateTime, default=datetime.now)
