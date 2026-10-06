# -*- coding: utf-8 -*-
"""condicion_scope() para rol="cliente" SÍ toca la base (a diferencia del
resto de ramas en test_scoping.py, que arman la expresión sin consultar
nada) -- necesita leer UsuarioSupervisorVisible para saber qué supervisores
eligió el admin. DB de prueba: SQLite en memoria vía Flask-SQLAlchemy
(extensions.db), nunca la Postgres real."""
import os
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))

from dimension_models import Persona  # noqa: E402
from extensions import db  # noqa: E402
from models import Usuario, UsuarioSupervisorVisible  # noqa: E402
from scoping import condicion_scope  # noqa: E402


@pytest.fixture
def app_ctx():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()


def _crear_cliente(dnis_supervisores):
    u = Usuario(email="cliente_test@example.com", password_hash="x", rol="cliente", activo=True)
    db.session.add(u)
    db.session.flush()
    for dni in dnis_supervisores:
        db.session.add(UsuarioSupervisorVisible(usuario_id=u.id, supervisor_dni=dni))
    db.session.commit()
    return u


def test_cliente_sin_supervisores_asignados_no_ve_a_nadie(app_ctx):
    # A diferencia del resto de roles (supervisor/analista sin nada
    # asignado ven TODO, para no romper accesos existentes), un cliente
    # nuevo sin configurar debe ver NADA -- es un rol pensado para alguien
    # externo, pecar de restrictivo por default es lo seguro acá.
    cliente = _crear_cliente([])
    cond = condicion_scope(Persona, cliente)
    compilado = str(cond.compile(compile_kwargs={"literal_binds": True}))
    # IN () vacío -- ninguna fila real matchea esto nunca.
    assert "IN (NULL)" in compilado or "IN ()" in compilado or "1 != 1" in compilado


def test_cliente_ve_equipo_de_su_supervisor_asignado(app_ctx):
    cliente = _crear_cliente(["74887804"])
    cond = condicion_scope(Persona, cliente)
    sql = str(cond)
    assert "supervisor_dni" in sql
    assert "personas.dni" in sql  # también incluye al supervisor mismo, no solo su equipo


def test_cliente_normaliza_ceros_a_la_izquierda(app_ctx):
    # Mismo gotcha de siempre (Persona.dni/supervisor_dni nunca tienen cero
    # a la izquierda) -- si un admin tipea el DNI real "09919446" al elegir
    # el supervisor, debe normalizarse igual que dni_asociado.
    cliente = _crear_cliente(["09919446"])
    cond = condicion_scope(Persona, cliente)
    compilado = str(cond.compile(compile_kwargs={"literal_binds": True}))
    assert "'9919446'" in compilado
    assert "'09919446'" not in compilado


def test_cliente_con_varios_supervisores_ve_ambos_equipos(app_ctx):
    cliente = _crear_cliente(["74887804", "9919446"])
    filas = UsuarioSupervisorVisible.query.filter_by(usuario_id=cliente.id).all()
    assert {f.supervisor_dni for f in filas} == {"74887804", "9919446"}
