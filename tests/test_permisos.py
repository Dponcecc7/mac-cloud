# -*- coding: utf-8 -*-
"""tiene_acceso()/paginas_de() toman `usuario` como parametro explicito
(no el current_user de Flask-Login), asi que se pueden probar con un
objeto liviano cualquiera que tenga .rol/.paginas_permitidas/
.is_authenticated -- sin Flask, sin base de datos."""
import json

import pytest

from permisos import (
    DEFAULT_POR_ROL, PAGINAS_REPORTES, PAGINAS_TOP, TODAS_LAS_CLAVES,
    paginas_de, tiene_acceso,
)


class UsuarioFalso:
    def __init__(self, rol, paginas_permitidas=None):
        self.rol = rol
        self.paginas_permitidas = paginas_permitidas
        self.is_authenticated = True


def test_admin_ve_todo_sin_importar_paginas_permitidas():
    admin = UsuarioFalso("admin", paginas_permitidas=json.dumps([]))
    assert paginas_de(admin) == TODAS_LAS_CLAVES
    for clave, _ in PAGINAS_TOP + PAGINAS_REPORTES:
        assert tiene_acceso(admin, clave)


def test_usuario_no_autenticado_nunca_tiene_acceso():
    anonimo = UsuarioFalso("analista")
    anonimo.is_authenticated = False
    assert tiene_acceso(anonimo, "personal") is False


def test_usuario_none_nunca_tiene_acceso():
    assert tiene_acceso(None, "personal") is False


@pytest.mark.parametrize("rol", ["analista", "supervisor"])
def test_sin_configurar_usa_el_default_del_rol(rol):
    usuario = UsuarioFalso(rol, paginas_permitidas=None)
    assert paginas_de(usuario) == DEFAULT_POR_ROL[rol]


def test_default_supervisor_no_incluye_paginas_solo_analista():
    # Regresion del bug real encontrado 2026-08-29: el primer intento de
    # DEFAULT_POR_ROL le daba a supervisor las mismas paginas que a
    # analista por accidente (compartian la misma lista _TOP_SIN_HISTORICO).
    supervisor = UsuarioFalso("supervisor")
    assert not tiene_acceso(supervisor, "cargar_headcount")
    assert not tiene_acceso(supervisor, "historial")
    assert not tiene_acceso(supervisor, "reportes_historico")


def test_default_analista_si_incluye_paginas_solo_analista():
    analista = UsuarioFalso("analista")
    assert tiene_acceso(analista, "cargar_headcount")
    assert tiene_acceso(analista, "historial")
    assert tiene_acceso(analista, "reportes_historico")


def test_default_supervisor_si_ve_reportes_y_sus_subpaginas_normales():
    supervisor = UsuarioFalso("supervisor")
    assert tiene_acceso(supervisor, "personal")
    assert tiene_acceso(supervisor, "asistencia")
    assert tiene_acceso(supervisor, "reportes")
    assert tiene_acceso(supervisor, "reportes_cobertura")
    assert tiene_acceso(supervisor, "reportes_perfil")


def test_permiso_explicito_reemplaza_al_default():
    usuario = UsuarioFalso("supervisor", paginas_permitidas=json.dumps(["personal"]))
    assert tiene_acceso(usuario, "personal")
    assert not tiene_acceso(usuario, "reportes")  # ya no hereda el default


def test_permiso_explicito_vacio_bloquea_todo():
    usuario = UsuarioFalso("analista", paginas_permitidas=json.dumps([]))
    assert paginas_de(usuario) == []
    assert not tiene_acceso(usuario, "personal")


def test_subpagina_de_reportes_exige_reportes_top_level():
    # Si a alguien le dan "reportes_alertas" pero le sacaron "reportes",
    # no deberia poder entrar igual por la subpagina puntual.
    usuario = UsuarioFalso("analista", paginas_permitidas=json.dumps(["reportes_alertas"]))
    assert not tiene_acceso(usuario, "reportes_alertas")


def test_json_invalido_en_paginas_permitidas_cae_al_default_sin_reventar():
    usuario = UsuarioFalso("analista", paginas_permitidas="esto no es json valido")
    assert paginas_de(usuario) == DEFAULT_POR_ROL["analista"]


def test_todas_las_claves_son_unicas():
    assert len(TODAS_LAS_CLAVES) == len(set(TODAS_LAS_CLAVES))


def test_usuarios_ni_dashboard_son_paginas_configurables():
    # Decision explicita de Davor: Usuarios siempre admin, Dashboard
    # siempre visible para todos -- ninguno de los dos debe aparecer como
    # clave togglable.
    assert "usuarios" not in TODAS_LAS_CLAVES
    assert "dashboard" not in TODAS_LAS_CLAVES
