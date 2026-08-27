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
    # cliente_id de Athena (livetradebi.dim_lf_general_visitas.cliente_id) que
    # este analista gestiona -- un analista = un cliente fijo. El pipeline lo
    # usa para saber que workgroup/cliente_id consultar para las Personas que
    # ese analista cargo (Persona.analista_propietario == este email).
    cliente_id_athena = db.Column(db.Integer, nullable=True)
    # Canal que este analista gestiona (p.ej. "FARMACIA", "AUTOSERVICIO",
    # "TRADICIONAL") -- Davor, 2026-08-27: Diego y Yeny comparten el mismo
    # cliente_id_athena que Kevin/Edith/Davor, así que antes de esto veían
    # TODO el grupo (headcount Tradicional incluido) en vez de solo su
    # propio canal. Si está seteado, scoping.py filtra por canal en vez de
    # por analista_propietario/cliente_id_athena. None = sin cambio de
    # comportamiento (sigue el fallback anterior).
    canal_asignado = db.Column(db.String(50), nullable=True)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    def get_id(self):
        return str(self.id)

    @property
    def is_active(self):
        return self.activo
