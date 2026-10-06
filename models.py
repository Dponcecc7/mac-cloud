# -*- coding: utf-8 -*-
from datetime import datetime

from flask_login import UserMixin

from extensions import db


class Usuario(db.Model, UserMixin):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.Enum("admin", "analista", "supervisor", "cliente", "coordinador", name="rol_usuario"), nullable=False)
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
    # Lista JSON de claves de pagina/subpagina que este usuario puede ver
    # (ver permisos.py) -- Davor, 2026-08-30: "yo como admin debo
    # seleccionar que accesos doy". NULL = sin configurar todavia -> usa el
    # default de su rol (permisos.DEFAULT_POR_ROL), asi que ningun usuario
    # existente pierde acceso hasta que un admin lo ajuste a mano.
    paginas_permitidas = db.Column(db.Text, nullable=True)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    def get_id(self):
        return str(self.id)

    @property
    def is_active(self):
        return self.activo


class UsuarioSupervisorVisible(db.Model):
    """Qué supervisores puede ver un usuario rol="cliente" o rol="coordinador"
    (Davor, 2026-10-06: "agregar también seleccionar los supervisores, que
    podrá ver toda información de sus equipos"; coordinador agregado el
    mismo día, mismo mecanismo -- la diferencia entre ambos roles es
    páginas/permisos por default, no el alcance de datos) -- uno de estos
    usuarios puede tener VARIOS supervisores asignados (a diferencia de
    dni_asociado de Usuario, que es uno solo, pensado para cuando el propio
    usuario ES ese supervisor).
    supervisor_dni es texto libre (igual que Usuario.dni_asociado) en vez
    de FK real -- Persona vive en el Base standalone de dimension_models.py,
    no en el de Flask-SQLAlchemy (db.Model) de este archivo, mismo motivo
    por el que dni_asociado tampoco tiene FK formal."""
    __tablename__ = "usuario_supervisor_visible"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    supervisor_dni = db.Column(db.String(15), nullable=False)

    __table_args__ = (db.UniqueConstraint("usuario_id", "supervisor_dni", name="uq_usuario_supervisor_visible"),)
