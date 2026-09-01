# -*- coding: utf-8 -*-
"""Control de acceso por pestaña/subpestaña, configurable por usuario desde
Usuarios (Davor, 2026-08-30: "falta agregar que pestañas generales y
subpestañas visualizara cada usuario... yo como admin debo seleccionar que
accesos doy").

Dos niveles, igual que la navegación real del sitio:
- PAGINAS_TOP: las pestañas del header (Personal, Asistencia diaria,
  Reportes, Cargar Headcount, Historial de cambios). Dashboard y Usuarios
  quedan AFUERA a propósito -- Dashboard es adonde cae todo el mundo
  después de loguearse (Davor: "que quede siempre visible para todos"), y
  Usuarios sigue siendo SOLO admin sin importar lo que se marque acá
  (Davor: "siempre admin, no togglable" -- es la pantalla que controla los
  permisos de todos, no tiene sentido que forme parte de su propio sistema).
- PAGINAS_REPORTES: las sub-pestañas dentro de Reportes. Solo importan si
  el usuario tiene "reportes" habilitado en PAGINAS_TOP -- ver tiene_acceso().

Es un control de acceso REAL, no solo cosmético (Davor: "bloqueo real"):
`requiere_pagina()` protege la ruta en el servidor, no solo esconde el link
del nav -- si alguien escribe la URL a mano sin permiso, lo redirige.

`paginas_permitidas` (Usuario, ver models.py) es NULL para todo usuario que
nunca se tocó desde esta pantalla -- en ese caso se usa DEFAULT_POR_ROL,
que reproduce el acceso que YA tenían antes de que existiera este sistema
(Davor: "mantienen acceso actual" -- ningún usuario existente pierde nada
en silencio). Recién cuando un admin guarda una selección puntual desde
Usuarios, esa persona pasa a regirse por la lista explícita en vez del
default de su rol.

IMPORTANTE: esto controla VISTA/NAVEGACIÓN de estas pestañas, no acciones
de escritura sensibles que ya tenían su propio control de rol (crear/editar
Historial de cambios, procesar una carga de Headcount) -- esas siguen
exigiendo rol analista/admin sin cambios, para no aflojar sin querer un
permiso de escritura solo por darle a alguien la vista de una pestaña."""
import json
from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required

PAGINAS_TOP = [
    ("personal", "Personal"),
    ("asistencia", "Asistencia diaria"),
    ("reportes", "Reportes"),
    ("cargar_headcount", "Cargar Headcount"),
    ("historial", "Historial de cambios"),
]

PAGINAS_REPORTES = [
    ("reportes_recomendaciones", "Desempeño"),
    ("reportes_horas", "Horas semanales"),
    ("reportes_alertas", "Alertas"),
    ("reportes_cobertura", "Cobertura"),
    ("reportes_marcaciones", "Marcaciones"),
    ("reportes_tareo", "Tareo"),
    ("reportes_historico", "Histórico diario"),
    ("reportes_perfil", "Perfil mercaderista"),
    ("reportes_proyecciones", "Proyecciones"),
]

TODAS_LAS_CLAVES = [c for c, _ in PAGINAS_TOP] + [c for c, _ in PAGINAS_REPORTES]

# Antes de este sistema, "Cargar Headcount" y "Historial de cambios"
# exigían rol analista/admin (_analista_requerido); Personal/Asistencia/
# Reportes eran @login_required nomas, abiertos a cualquier rol.
_ABIERTAS_A_TODOS = ["personal", "asistencia", "reportes"]
_SOLO_ANALISTA_ADMIN = ["cargar_headcount", "historial"]
# "reportes_historico" era la única subpágina de Reportes que ya exigía
# analista/admin (historial._analista_requerido) -- el resto estaba
# abierto a cualquier logueado. "reportes_proyecciones" (Davor, 2026-09-01)
# se suma a esa excepción a propósito: riesgo de rotación/contratación
# proyectada por persona es mas sensible que un reporte operativo, no
# abierto a supervisores por defecto.
_SOLO_ANALISTA_ADMIN_REPORTES = ("reportes_historico", "reportes_proyecciones")
_REPORTES_ABIERTOS_A_TODOS = [c for c, _ in PAGINAS_REPORTES if c not in _SOLO_ANALISTA_ADMIN_REPORTES]

# Reproduce el acceso que cada rol YA tenía antes de este sistema (ver
# docstring de más arriba).
DEFAULT_POR_ROL = {
    "admin": TODAS_LAS_CLAVES,
    "analista": _ABIERTAS_A_TODOS + _SOLO_ANALISTA_ADMIN + _REPORTES_ABIERTOS_A_TODOS + list(_SOLO_ANALISTA_ADMIN_REPORTES),
    "supervisor": _ABIERTAS_A_TODOS + _REPORTES_ABIERTOS_A_TODOS,
}


def paginas_de(usuario):
    """Lista de claves habilitadas para `usuario` -- explícitas si ya se
    configuraron desde Usuarios, si no el default de su rol."""
    if usuario.rol == "admin":
        return TODAS_LAS_CLAVES
    if usuario.paginas_permitidas:
        try:
            return json.loads(usuario.paginas_permitidas)
        except (TypeError, ValueError):
            pass
    return DEFAULT_POR_ROL.get(usuario.rol, [])


def tiene_acceso(usuario, clave):
    """True si `usuario` puede ver la pestaña/subpestaña `clave`. Para las
    claves de Reportes ("reportes_*") también exige "reportes" -- si un
    admin le saca Reportes a alguien, no tiene sentido que igual entre por
    una subpágina puntual."""
    if not usuario or not usuario.is_authenticated:
        return False
    habilitadas = paginas_de(usuario)
    if clave.startswith("reportes_") and "reportes" not in habilitadas:
        return False
    return clave in habilitadas


def requiere_pagina(clave):
    """Decorator: bloqueo real en el servidor, no solo ocultar el link del
    nav (Davor: "bloqueo real... si escribe la URL directa igual entra" era
    la opción que NO se eligió)."""
    def decorador(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if not tiene_acceso(current_user, clave):
                flash("No tenés acceso a esa sección.", "error")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return wrapper
    return decorador


def requiere_analista_admin(redirigir_a="dashboard"):
    """Decorator factory para acciones de ESCRITURA reservadas a
    analista/admin (crear/editar Historial de cambios, Agregar reemplazo,
    dar de alta/baja gente de verdad en Postgres) -- a propósito NO usa el
    sistema de permisos por página de arriba: ese controla qué pestañas
    navega cada usuario, esto es un piso de rol fijo para escrituras
    sensibles que no debería aflojarse solo por darle a alguien la vista
    de una pestaña (ver docstring del módulo).

    `redirigir_a`: endpoint al que mandar a quien no califica -- por
    defecto "dashboard" (igual que historial.py); asistencia.py usaba
    "asistencia.reporte" en su copia (que a su vez manda a un supervisor a
    "Marcar asistencia"), así que queda como parámetro en vez de fijo,
    para no cambiarle el comportamiento a nadie al centralizar. Antes
    estaba copiado byte por byte en asistencia.py e historial.py
    (2026-08-24, hallazgo de revisión de código) -- centralizado acá
    2026-08-30."""
    def decorador(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.rol not in ("analista", "admin"):
                flash("Esta sección es solo para analistas.", "error")
                return redirect(url_for(redirigir_a))
            return f(*args, **kwargs)
        return wrapper
    return decorador
