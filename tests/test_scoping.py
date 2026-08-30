# -*- coding: utf-8 -*-
"""condicion_scope()/condicion_canal() arman una expresión SQLAlchemy y la
devuelven SIN ejecutarla -- construir un BinaryExpression no toca la base,
así que se puede probar la lógica de qué expresión sale para cada rol sin
Postgres. Se usa el modelo Persona real (dimension_models) en vez de un
mock -- comparar Persona.dni == "123" ya funciona sin conexión, es
exactamente lo que hace la app antes de pasarlo a .filter()."""
from dimension_models import Persona
from scoping import condicion_canal, condicion_scope


class UsuarioFalso:
    def __init__(self, rol, dni_asociado=None, canal_asignado=None, cliente_id_athena=None):
        self.rol = rol
        self.dni_asociado = dni_asociado
        self.canal_asignado = canal_asignado
        self.cliente_id_athena = cliente_id_athena


def test_admin_no_tiene_restriccion():
    admin = UsuarioFalso("admin")
    assert condicion_scope(Persona, admin) is None


def test_supervisor_sin_dni_asociado_no_tiene_restriccion():
    # Comportamiento explicito documentado: "un usuario sin nada de esto
    # asignado todavia tambien ve todo -- para no romper accesos existentes
    # en silencio".
    supervisor = UsuarioFalso("supervisor", dni_asociado=None)
    assert condicion_scope(Persona, supervisor) is None


def test_supervisor_con_dni_asociado_se_acota_a_su_equipo():
    supervisor = UsuarioFalso("supervisor", dni_asociado="9919446")
    cond = condicion_scope(Persona, supervisor)
    assert cond is not None
    sql = str(cond)
    assert "supervisor_dni" in sql
    # Se excluye a si mismo de su propio equipo (auto-referencia real en
    # el Maestro Headcount, Davor 2026-08-24: "no tendria sentido").
    assert "personas.dni !=" in sql or "personas.dni <>" in sql or "!=" in sql


def test_dni_asociado_con_ceros_a_la_izquierda_se_normaliza():
    # Persona.dni/supervisor_dni salen sin cero a la izquierda del ETL --
    # sin normalizar esto el supervisor veia 0 personas.
    supervisor = UsuarioFalso("supervisor", dni_asociado="09919446")
    cond = condicion_scope(Persona, supervisor)
    # No hay forma limpia de leer el valor "bindeado" de un BinaryExpression
    # sin compilarlo -- se compila con literal_binds para verificar el
    # valor real que va a viajar a Postgres.
    compilado = cond.compile(compile_kwargs={"literal_binds": True})
    assert "'9919446'" in str(compilado)
    assert "'09919446'" not in str(compilado)


def test_analista_de_canal_usa_condicion_canal():
    analista_canal = UsuarioFalso("analista", canal_asignado="FARMACIA")
    cond = condicion_scope(Persona, analista_canal)
    assert cond is not None
    assert str(cond) == str(condicion_canal(Persona, "FARMACIA"))


def test_condicion_canal_normaliza_mayusculas_y_espacios():
    cond1 = condicion_canal(Persona, "farmacia")
    cond2 = condicion_canal(Persona, "  Farmacia  ")
    cond3 = condicion_canal(Persona, "FARMACIA")
    assert str(cond1) == str(cond2) == str(cond3)


def test_usuario_sin_nada_asignado_no_tiene_restriccion():
    # cliente_id_athena=None hace que la funcion nunca llegue a consultar
    # Usuario.query (esa rama solo se ejecuta si NO es None) -- por eso se
    # puede probar sin base de datos.
    usuario = UsuarioFalso("analista", cliente_id_athena=None)
    assert condicion_scope(Persona, usuario) is None
