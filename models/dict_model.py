from . import db
from datetime import datetime

class DictCategory(db.Model):
    __tablename__ = 'dict_categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    items = db.relationship('DictItem', backref='category', lazy='dynamic',
                            order_by='DictItem.sort_order',
                            cascade='all, delete-orphan')

class DictItem(db.Model):
    __tablename__ = 'dict_items'
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('dict_categories.id'), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    value = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
