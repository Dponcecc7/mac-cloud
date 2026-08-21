# -*- coding: utf-8 -*-
from datetime import datetime

from flask_login import UserMixin

from extensions import db


class Usuario(db.Model, UserMixin):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.Enum("admin", "analista", "supervisor", name="rol_usuario"), nullable=False)
    dni_asociado = db.Column(db.String(8), nullable=True)  # FK a personas.dni cuando esa tabla exista (Fase 2)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    def get_id(self):
        return str(self.id)

    @property
    def is_active(self):
        return self.activo
