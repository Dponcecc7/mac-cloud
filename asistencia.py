# -*- coding: utf-8 -*-
"""
Blueprint de corrección diaria de asistencia -- version web de
C:\\Archivos Python\\One Page\\asistencia_app\\app.py, para que varias
personas lo usen en vez de solo desde la laptop de Davor.

Diferencia de diseño clave respecto al original: la app local lee/escribe
Reportes_Diarios/Reporte_Asistencia_*.xlsx en disco y llama a
reporte_diario_9am.py/aplicar_correcciones.py como subprocesos -- nada de
eso existe en Render. Acá se lee directo de Postgres (clasificacion_diaria +
personas, ya mantenido fresco por el pipeline en la nube, Fase 6) y las
operaciones largas (Athena) se disparan como workflow de GitHub Actions
(github_actions.py) en vez de correrlas sincronicas en el request.

Guardar una corrección escribe DOBLE (decision del usuario, 2026-08-21):
Tabla 3 en SharePoint via Graph (la fuente REAL que lee el motor de
clasificacion, local y nube -- cero riesgo de romper el flujo que ya
funciona) y ademas una copia en Postgres (correcciones_web, solo auditoria).
"""
import datetime as dt
import io
import os
import re
import sys
from functools import wraps
from zoneinfo import ZoneInfo

import openpyxl
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from dimension_models import CatalogoMotivo, CorreccionWeb, Feriado, Persona, get_session
from excel_safety import texto_seguro_excel
from fact_models import ClasificacionDiaria
from graph_client import descargar, subir_in_place
from github_actions import disparar_workflow, estado_ultima_corrida
from scoping import aplicar_filtros_extra, condicion_scope
from sqlalchemy.orm import aliased

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_AQUI, "pipeline"))
from db_lock import adquirir_lock, liberar_lock  # noqa: E402
import reporte_diario_9am as r9am  # noqa: E402 -- reusa reemplazos_pendientes()/dnis_alguna_vez_en_maestro(), no se duplica

bp = Blueprint("asistencia", __name__, url_prefix="/asistencia")


def _analista_requerido(f):
    """Igual que cargas.py::_analista_requerido -- "Agregar reemplazo" da de
    baja/alta gente de verdad en Postgres (no es solo ver o marcar
    asistencia), asi que queda reservado a analista/admin, no cualquier
    rol logueado."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.rol not in ("analista", "admin"):
            flash("Esta sección es solo para analistas.", "error")
            return redirect(url_for("asistencia.reporte"))
        return f(*args, **kwargs)
    return wrapper


RUTA_GRAPH_MAC = "ASISTENCIA/MAC/"
TABLA3_RUTA_GRAPH = f"{RUTA_GRAPH_MAC}3_Registro_Diario_Supervisor.xlsx"
SALIDA_ANTICIPADA_MIN = 10  # mismo umbral que usaba la app local para resaltar "salida temprana"

PERU_TZ = ZoneInfo("America/Lima")  # sin horario de verano -- offset fijo UTC-5

# aplicar_correcciones.py escribe este texto exacto en el comentario cuando
# un supervisor reporta "Asistió" desde la app movil pero todavia nadie
# confirmo la hora real en "Entrada/Salida corregida" -- hay que poder
# encontrar estos casos facil para no perderlos (ver TEXTO_PENDIENTE_ENTRADA/
# TEXTO_PENDIENTE_SALIDA en pipeline/aplicar_correcciones.py).
MARCADOR_PENDIENTE = "reportado por supervisor -- confirmar en"

# motor_clasificacion.py escribe este sufijo exacto en el Estado cuando el
# dato viene del botón "Marcar asistencia" de acá (Asistió/Apoyo zona/Vacante
# sin marcación real de GPS) -- mismo caso que MARCADOR_PENDIENTE pero para el
# flujo nuevo: acá el aviso vive en el propio Estado, no en el comentario del
# supervisor, asi que hay que buscarlo aparte.
MARCADOR_SIN_APP = "según supervisor, sin marcación app"

# Tabla 3 es append-only -- el motor arma cada (DNI, Fecha) fusionando todas
# sus filas y quedandose con el ultimo valor NO VACÍO de cada columna (ver
# motor_clasificacion.py::main(), el groupby de "sup"). Por eso no existe
# forma de "vaciar" un comentario escribiendo un texto vacío -- una celda
# vacía simplemente se ignora en esa fusión y el comentario viejo (equivocado)
# sigue ganando. La solución es escribir ESTE texto puntual: no vacío (así sí
# gana la fusión) pero tampoco empieza con "FALTA" ni contiene
# "VACANTE"/"VACACIONES" ni llena "Estado reportado" -- el motor no lo
# reconoce como ninguna de sus palabras clave, así que no fuerza ningún
# estado y la persona vuelve a clasificarse solo por su marcación real (o
# "Falta (sin marcación)" si de verdad no hay ninguna).
MARCADOR_BORRADO = "(comentario borrado desde mac_cloud)"

# Solo Asistió/Apoyo zona implican una hora real pendiente de escribir --
# Vacante/Falta no tienen entrada/salida que confirmar, asi que no deben
# mostrar el aviso aunque tambien vengan de "Marcar asistencia".
_ESTADOS_CON_HORA_PENDIENTE = ("ASISTIÓ", "APOYO ZONA")


def _estado_base(estado):
    return (estado or "").split(" (")[0]

_RE_PREFIJO_FALTA = re.compile(r"^falta\s*[-–]?\s*", re.IGNORECASE)


def _homologar_motivo(texto):
    """Version tolerante de reporte_diario_9am.py::limpiar_motivo() solo
    para lo que se muestra/edita acá -- saca el prefijo "Falta" repetido con
    cualquier variante de formato (con/sin guion, con/sin espacios,
    mayúsculas), no solo el "Falta - " exacto que espera el motor. Ej.:
    "Falta-Falta Injustificada" -> "Injustificada". No toca limpiar_motivo()
    en sí (esa la usa el motor de clasificación real, no se arriesga)."""
    if not texto:
        return texto
    texto = str(texto).strip()
    while True:
        m = _RE_PREFIJO_FALTA.match(texto)
        if not m or not m.group(0) or m.end() == 0:
            break
        resto = texto[m.end():].strip()
        if resto == texto:
            break
        texto = resto
    # Recorta cualquier paréntesis final -- de paso, esto es lo que hace que
    # MARCADOR_BORRADO (que es 100% un paréntesis) se muestre vacío en el
    # reporte sin necesitar un caso especial acá.
    texto = re.sub(r"\s*\([^)]*\)\s*$", "", texto).strip()
    if not texto:
        return None
    return texto[0].upper() + texto[1:]


def _cargar_feriados():
    session = get_session()
    try:
        filas = session.query(Feriado.fecha).all()
    finally:
        session.close()
    return {f for (f,) in filas}


def _dia_habil_anterior(fecha, feriados_set):
    d = fecha - dt.timedelta(days=1)
    while d.weekday() == 6 or d in feriados_set:
        d -= dt.timedelta(days=1)
    return d


def _cargar_reporte(fecha, usuario_actual=None, rol_filtro=None, region_filtro=None, supervisor_filtro=None):
    """Devuelve (resumen_dict, filas, frescura) para `fecha` -- None, None, None
    si no hay ninguna fila ese día (motor todavía no corrió para esa fecha).

    `usuario_actual`: si se pasa, acota a las Personas visibles para ese
    usuario (ver scoping.py) -- sin esto, un analista de otro cliente veía
    nombres/DNI de todos los clientes, y un supervisor veía todo el equipo
    en vez de solo el suyo. `rol_filtro`/`region_filtro`/`supervisor_filtro`:
    filtros adicionales de Marcar asistencia (solo admin/analista, ver
    _filtros_marcar()), encima del scope de acceso, no en vez de."""
    feriados = _cargar_feriados()
    ayer = _dia_habil_anterior(fecha, feriados)

    session = get_session()
    try:
        query_personas = session.query(Persona)
        cond_scope = condicion_scope(Persona, usuario_actual) if usuario_actual else None
        if cond_scope is not None:
            query_personas = query_personas.filter(cond_scope)
        query_personas = aplicar_filtros_extra(query_personas, Persona, rol_filtro, region_filtro, supervisor_filtro)
        personas = {p.dni: p for p in query_personas.all()}
        nombre_de = {dni: p.nombre_completo for dni, p in personas.items()}

        filas_hoy = (
            session.query(ClasificacionDiaria)
            .filter(ClasificacionDiaria.fecha == fecha, ClasificacionDiaria.dni.in_(personas.keys()))
            .all()
        )
        filas_ayer = {
            c.dni: c for c in
            session.query(ClasificacionDiaria)
            .filter(ClasificacionDiaria.fecha == ayer, ClasificacionDiaria.dni.in_(personas.keys()))
            .all()
        }
        # DNIs ya marcados hoy desde "Marcar asistencia" (correcciones_web) --
        # la Tabla 3 ya tiene su fila, pero clasificacion_diaria.comentario_supervisor
        # recien se actualiza cuando el motor vuelve a correr. Sin esto, la
        # persona seguia apareciendo en "Pendientes" varios minutos despues
        # de haberla marcado.
        dnis_marcados_hoy = {
            dni for (dni,) in session.query(CorreccionWeb.dni).filter(CorreccionWeb.fecha == fecha).all()
        }
    finally:
        session.close()

    if not filas_hoy:
        return None, None, None

    filas = []
    ultima_sync = None
    for c in filas_hoy:
        p = personas.get(c.dni)
        ayer_c = filas_ayer.get(c.dni)
        supervisor_nombre = nombre_de.get(p.supervisor_dni) if p else None
        salida_anticipada = ayer_c.salida_anticipada_min if ayer_c else None
        filas.append({
            "dni": c.dni,
            "mercaderista": nombre_de.get(c.dni, c.dni),
            "supervisor": supervisor_nombre,
            "region": p.region if p else None,
            "ciudad": p.ciudad if p else None,
            "canal": p.canal if p else None,
            "rol": p.rol if p else None,
            "mercado": p.zona if p else None,
            "canal_hoy": c.canales_marcados or "",
            "entrada_prog": c.entrada_esperada,
            "entrada_real": c.entrada_real,
            "entrada_corregida": c.fuente_dato == "Corregido manualmente (Tabla 3)",
            "estado": c.estado,
            # _homologar_motivo() saca el prefijo redundante "Falta" (el
            # badge de Estado ya dice "Falta"), tolerando variantes de
            # formato -- ver docstring de la función.
            "comentario_entrada": _homologar_motivo(c.comentario_supervisor) or "",
            "salida_prog": ayer_c.salida_esperada if ayer_c else None,
            "salida_real": ayer_c.salida_real if ayer_c else None,
            "salida_corregida": bool(ayer_c and ayer_c.fuente_dato == "Corregido manualmente (Tabla 3)"),
            "salida_temprana": bool(salida_anticipada and salida_anticipada > SALIDA_ANTICIPADA_MIN),
            "canal_ayer": (ayer_c.canales_marcados or "") if ayer_c else "",
            "comentario_salida": (_homologar_motivo(ayer_c.comentario_supervisor) or "") if ayer_c else "",
            "entrada_pendiente": (
                MARCADOR_PENDIENTE in (c.comentario_supervisor or "")
                or (
                    MARCADOR_SIN_APP in (c.estado or "")
                    and _estado_base(c.estado) in _ESTADOS_CON_HORA_PENDIENTE
                )
            ),
            "salida_pendiente": (
                MARCADOR_PENDIENTE in ((ayer_c.comentario_supervisor or "") if ayer_c else "")
                or (
                    MARCADOR_SIN_APP in ((ayer_c.estado or "") if ayer_c else "")
                    and _estado_base(ayer_c.estado if ayer_c else None) in _ESTADOS_CON_HORA_PENDIENTE
                )
            ),
            "marcado_web": c.dni in dnis_marcados_hoy,
        })
        if c.procesado_en and (ultima_sync is None or c.procesado_en > ultima_sync):
            ultima_sync = c.procesado_en

    resumen = {
        "asistio": sum(1 for f in filas if _estado_base(f["estado"]) == "ASISTIÓ A TIEMPO"),
        "tardanza": sum(1 for f in filas if _estado_base(f["estado"]) == "TARDANZA"),
        "falta": sum(1 for f in filas if _estado_base(f["estado"]) == "FALTA"),
        "total": len(filas),
    }
    # procesado_en se guarda con func.now() de Postgres -- en UTC, no hora
    # Peru. Sin esto, "Datos hasta las X" mostraba la hora UTC directo (5
    # horas atrás de la hora real de Peru), pareciendo desactualizado.
    ultima_sync_peru = (
        ultima_sync.replace(tzinfo=ZoneInfo("UTC")).astimezone(PERU_TZ) if ultima_sync else None
    )
    frescura = {"ultima_sync": ultima_sync_peru, "fecha": fecha, "ayer": ayer}
    return resumen, filas, frescura


def _motivos_falta():
    session = get_session()
    try:
        return [m for (m,) in session.query(CatalogoMotivo.motivo).order_by(CatalogoMotivo.motivo).all()]
    finally:
        session.close()


def _pendientes_de_marcar(filas):
    """Personas con Falta/Vacante que todavía no tienen ningún comentario --
    ni de la app móvil de supervisores ni de acá -- igual al panel
    "Pendientes" de la Power App (galPendientes, ver GUIA_POWER_APPS_SUPERVISOR.md)."""
    return [
        f for f in filas
        if _estado_base(f["estado"]) in ("FALTA", "VACANTE") and not f["comentario_entrada"] and not f["marcado_web"]
    ]


def _ya_marcaron(filas):
    """Personas del equipo que tienen al menos una visita REAL del
    aplicativo hoy. Usa `canal_hoy` (canales_marcados), no `entrada_real`
    -- una corrección manual de hora en Tabla 3 pisa `entrada_real` igual
    aunque no haya ninguna visita real ese día (ver docstring de
    _marcado_manual_sin_sincronizar), mientras que `canales_marcados` solo
    se llena a partir de visitas reales y el bloque de corrección de
    motor_clasificacion.py nunca lo toca -- es la señal confiable de "esto
    es una marcación real, no una que le puso el supervisor" (Davor,
    2026-08-24: reportó ver a alguien en "Ya marcaron" que en realidad
    todavía no había marcado, solo tenía la hora que él le puso a mano)."""
    return sorted(
        (f for f in filas if f["canal_hoy"]),
        key=lambda f: f["entrada_real"] or "",
    )


def _faltas_vacaciones_vacantes(filas):
    """Faltas/Vacaciones/Vacantes registradas hoy, resueltas o no -- para
    revisar al cierre del día qué quedó cargado en total, no solo lo
    todavía pendiente (a diferencia de _pendientes_de_marcar) (Davor,
    2026-08-24)."""
    return sorted(
        (f for f in filas if _estado_base(f["estado"]) in ("FALTA", "VACANTE", "VACACIONES")),
        key=lambda f: (_estado_base(f["estado"]), f["mercaderista"]),
    )


def _marcado_manual_sin_sincronizar(filas):
    """Gente confirmada a mano hoy -- con el botón rápido "Asistió"/"Apoyo
    zona" (`entrada_pendiente`, MARCADOR_SIN_APP en el Estado) O con una
    hora tipeada directamente (`entrada_corregida`, ej. para poder mandar
    el reporte temprano con captura) -- que a esta hora TODAVÍA no tiene
    ninguna visita real del aplicativo (`canal_hoy` vacío). Antes esto
    miraba solo `entrada_pendiente`, que no cubría el caso de una hora
    tipeada (`entrada_corregida`) -- ese caso queda con `entrada_real`
    poblado igual (por la hora que se tipeó), así que sin este chequeo
    aparte una persona así ni salía acá ni se distinguía de una marcación
    real en "Ya marcaron" (Davor, 2026-08-24: "hoy le puse 08:00 María
    Calderón... pero aún no marca... confirmé porque necesité mandar mi
    asistencia con las capturas")."""
    return [
        f for f in filas
        if not f["canal_hoy"] and (f["entrada_corregida"] or f["entrada_pendiente"])
    ]


def _reemplazos_hoy(fecha, usuario_actual):
    """Personas dadas de alta HOY como reemplazo de una vacante (ver
    reemplazos.py::procesar_reemplazo(), que estampa fecha_registro=hoy y
    reemplaza_a_dni) -- para que el supervisor vea en la misma pantalla que
    se procesó un reemplazo, no solo que una vacante desapareció."""
    session = get_session()
    try:
        Reemplazada = aliased(Persona)
        query = (
            session.query(Persona, Reemplazada.nombre_completo)
            .outerjoin(Reemplazada, Reemplazada.dni == Persona.reemplaza_a_dni)
            .filter(Persona.fecha_registro == fecha, Persona.reemplaza_a_dni.isnot(None))
        )
        cond_scope = condicion_scope(Persona, usuario_actual) if usuario_actual else None
        if cond_scope is not None:
            query = query.filter(cond_scope)
        filas_reemplazo = query.all()
    finally:
        session.close()
    return [
        {
            "nombre_nuevo": p.nombre_completo, "dni_nuevo": p.dni,
            "nombre_reemplazado": nombre_reemplazada or p.reemplaza_a_dni,
            "fecha_ingreso": p.fecha_ingreso,
        }
        for p, nombre_reemplazada in filas_reemplazo
    ]


def _fecha_mas_reciente_con_datos():
    session = get_session()
    try:
        from sqlalchemy import func
        (fecha,) = session.query(func.max(ClasificacionDiaria.fecha)).one()
        return fecha
    finally:
        session.close()


def _filtros_marcar():
    """Filtros de Supervisor/Región/Rol en Marcar asistencia -- Davor,
    2026-08-25: "poner filtro supervisor, region, rol, solo para el admin y
    analista, para supervisor no deberia aparecer filtros". A diferencia de
    reportes.py::_filtros_admin() (admin únicamente), acá también alcanza a
    analista -- un analista ya está acotado a su propio cliente por
    condicion_scope(), así que los desplegables igual solo muestran lo suyo."""
    if current_user.rol == "supervisor":
        return {"rol": "", "region": "", "supervisor": ""}, [], [], []
    filtro_args = {
        "rol": request.args.get("rol") or "",
        "region": request.args.get("region") or "",
        "supervisor": request.args.get("supervisor") or "",
    }
    session = get_session()
    try:
        cond_scope = condicion_scope(Persona, current_user)

        def _valores(columna):
            q = session.query(columna).filter(columna.isnot(None))
            if cond_scope is not None:
                q = q.filter(cond_scope)
            return sorted({v for (v,) in q.distinct().all() if v})

        roles_disponibles = _valores(Persona.rol)
        regiones_disponibles = _valores(Persona.region)

        SupervisorPersona = aliased(Persona)
        q_sup = (
            session.query(Persona.supervisor_dni, SupervisorPersona.nombre_completo)
            .join(SupervisorPersona, SupervisorPersona.dni == Persona.supervisor_dni)
            .filter(Persona.supervisor_dni.isnot(None))
        )
        if cond_scope is not None:
            q_sup = q_sup.filter(cond_scope)
        supervisores_disponibles = sorted(set(q_sup.distinct().all()), key=lambda t: (t[1] or "").title())
    finally:
        session.close()
    return filtro_args, roles_disponibles, regiones_disponibles, supervisores_disponibles


def _vista_reporte(fecha_str, vista):
    """vista: 'reporte' (columnas + edicion completa) o 'cliente' (columnas
    reducidas, sin Hora entrada/salida corregida)."""
    try:
        fecha = dt.date.fromisoformat(fecha_str)
    except ValueError:
        fecha = dt.date.today()
        fecha_str = fecha.isoformat()

    resumen, filas, frescura = _cargar_reporte(fecha, usuario_actual=current_user)
    # Si no hay datos para la fecha pedida (ej. hoy, si el pipeline todavia
    # no corrio), sugerir la fecha mas reciente que si tiene -- para poder
    # seguir corrigiendo el pasado sin quedar en un callejon sin salida.
    fecha_reciente = None if filas else _fecha_mas_reciente_con_datos()
    return render_template(
        "asistencia.html", usuario=current_user, activo=vista, vista=vista,
        fecha_reciente=fecha_reciente,
        fecha_str=fecha_str, resumen=resumen, filas=filas, frescura=frescura,
    )


@bp.get("/marcar")
@login_required
def marcar_vista():
    """Pestaña propia (version web de galPendientes de la Power App) --
    antes vivia dentro de "Reporte diario", ahora tiene su propio lugar
    para no mezclar "ver/corregir el detalle" con "marcar rapido a los
    pendientes de hoy"."""
    fecha_str = request.args.get("fecha") or dt.date.today().isoformat()
    try:
        fecha = dt.date.fromisoformat(fecha_str)
    except ValueError:
        fecha = dt.date.today()
        fecha_str = fecha.isoformat()

    filtro_args, roles_disponibles, regiones_disponibles, supervisores_disponibles = _filtros_marcar()
    resumen, filas, frescura = _cargar_reporte(
        fecha, usuario_actual=current_user,
        rol_filtro=filtro_args["rol"], region_filtro=filtro_args["region"], supervisor_filtro=filtro_args["supervisor"],
    )
    pendientes = _pendientes_de_marcar(filas) if filas else []
    marcaron = _ya_marcaron(filas) if filas else []
    faltas_vacaciones_vacantes = _faltas_vacaciones_vacantes(filas) if filas else []
    sin_sincronizar = _marcado_manual_sin_sincronizar(filas) if filas else []
    reemplazos = _reemplazos_hoy(fecha, current_user)
    fecha_reciente = None if filas else _fecha_mas_reciente_con_datos()
    return render_template(
        "asistencia_marcar.html", usuario=current_user, activo="marcar",
        fecha_str=fecha_str, resumen=resumen, frescura=frescura, fecha_reciente=fecha_reciente,
        pendientes=pendientes, marcaron=marcaron, motivos=_motivos_falta() if (pendientes or marcaron) else None,
        faltas_vacaciones_vacantes=faltas_vacaciones_vacantes, sin_sincronizar=sin_sincronizar,
        reemplazos=reemplazos, marcado=request.args.get("marcado"),
        filtro_args=filtro_args, roles_disponibles=roles_disponibles,
        regiones_disponibles=regiones_disponibles, supervisores_disponibles=supervisores_disponibles,
    )


@bp.get("")
@login_required
def reporte():
    # El supervisor solo debe poder marcar asistencia de su equipo -- ver/
    # editar el detalle completo (horas corregidas, borrar comentarios) es
    # una herramienta de analista.
    if current_user.rol == "supervisor":
        return redirect(url_for("asistencia.marcar_vista"))
    fecha_str = request.args.get("fecha") or dt.date.today().isoformat()
    return _vista_reporte(fecha_str, "reporte")


@bp.get("/cliente")
@login_required
def cliente():
    if current_user.rol == "supervisor":
        return redirect(url_for("asistencia.marcar_vista"))
    fecha_str = request.args.get("fecha") or dt.date.today().isoformat()
    return _vista_reporte(fecha_str, "cliente")


def _agregar_fila_tabla3(ws, fila_libre, dni, fecha, comentario, hora_ent=None, hora_sal=None, estado_reportado=None):
    """Mismo formato/columnas que aplicar_correcciones.py::_agregar_fila_t3()
    -- no cambiar sin revisar ese archivo primero, el motor de clasificacion
    (local y nube) depende de este layout exacto.

    `estado_reportado` (columna 3, "Estado reportado") es la MISMA columna
    que llena la app Power Apps de los supervisores para "Asistió"/"Apoyo
    zona"/"Vacante" -- ver GUIA_POWER_APPS_SUPERVISOR.md sección 3/4. Hasta
    ahora esta función nunca la llenaba (solo Comentario), asi que esas 3
    acciones no tenian forma de reportarse desde la web. "Falta" sigue
    usando el Comentario con el prefijo "Falta - {motivo}" (columna 3 vacía),
    igual que ya hace el resto del flujo -- motor_clasificacion.py lee
    "Estado reportado" solo como respaldo cuando NO hay un comentario que
    empiece con "FALTA"/tenga "VACANTE"/"VACACIONES".
    """
    ws.cell(row=fila_libre, column=1, value=str(dni))
    ws.cell(row=fila_libre, column=2, value=str(fecha))
    if estado_reportado:
        ws.cell(row=fila_libre, column=3, value=estado_reportado)
    # texto_seguro_excel(): comentario es texto libre tipeado por el usuario
    # (o un motivo agregado por otro usuario) -- sin esto, un valor que
    # empiece con =/+/-/@ se interpretaria como formula al abrir la Tabla 3
    # en Excel (CSV/Excel Injection).
    ws.cell(row=fila_libre, column=4, value=texto_seguro_excel(comentario))
    ws.cell(row=fila_libre, column=5, value="Web mac_cloud")
    ws.cell(row=fila_libre, column=6, value=dt.datetime.now().strftime("%H:%M"))
    if hora_ent:
        ws.cell(row=fila_libre, column=7, value=str(hora_ent))
    if hora_sal:
        ws.cell(row=fila_libre, column=8, value=str(hora_sal))


@bp.post("/guardar")
@login_required
def guardar():
    # Mismo criterio que reporte()/cliente(): editar horas/comentarios del
    # detalle es cosa de analista, no del supervisor (que solo marca).
    if current_user.rol == "supervisor":
        flash("Esta sección es solo para analistas.", "error")
        return redirect(url_for("asistencia.marcar_vista"))
    fecha_str = request.form["fecha"]
    vista = request.form.get("vista") or "reporte"
    fecha = dt.date.fromisoformat(fecha_str)
    feriados = _cargar_feriados()
    ayer = _dia_habil_anterior(fecha, feriados)

    dnis = request.form.getlist("dni")
    ediciones = []
    for dni in dnis:
        comentario_entrada = request.form.get(f"comentario_entrada_{dni}", "").strip()
        comentario_salida = request.form.get(f"comentario_salida_{dni}", "").strip()
        entrada_corr = request.form.get(f"entrada_corr_{dni}", "").strip() if vista in ("reporte", "marcar") else ""
        salida_corr = request.form.get(f"salida_corr_{dni}", "").strip() if vista == "reporte" else ""

        # "Ya marcaron" (Marcar asistencia) arma el comentario desde un
        # Motivo + Detalle separados en vez de un solo cuadro de texto libre
        # (mismo criterio que ya usa marcar() para Pendientes) -- si vienen,
        # pisan lo que haya en comentario_entrada_{dni}.
        motivo_falta = request.form.get(f"motivo_falta_{dni}", "").strip()
        if motivo_falta:
            detalle_falta = request.form.get(f"detalle_falta_{dni}", "").strip()
            comentario_entrada = f"Falta - {motivo_falta}"
            if detalle_falta:
                comentario_entrada += f" ({detalle_falta})"

        # "Borrar" gana sobre lo que haya quedado tipeado en el cuadro de
        # texto -- el usuario tildó el check porque quiere deshacer el
        # comentario, no reemplazarlo por otra cosa a medio escribir.
        if request.form.get(f"borrar_entrada_{dni}"):
            comentario_entrada = MARCADOR_BORRADO
        if request.form.get(f"borrar_salida_{dni}"):
            comentario_salida = MARCADOR_BORRADO

        # Una hora de entrada corregida y un comentario "Falta" son
        # contradictorios -- motor_clasificacion.py prioriza la hora
        # corregida (recalcula ASISTIÓ/TARDANZA) e ignora que el comentario
        # diga "Falta" cuando las dos llegan juntas en la misma fila de
        # Tabla 3 (Davor, 2026-08-25: "mercaderista que marca y se va...
        # deberia ser Falta"). Si el analista cargó las dos a la vez, gana
        # la conversión a Falta -- es la acción explícita, la hora corregida
        # ahí sería un resto de haber tocado el campo por error.
        if comentario_entrada.upper().startswith("FALTA") and entrada_corr:
            entrada_corr = ""

        if comentario_entrada or comentario_salida or entrada_corr or salida_corr:
            ediciones.append({
                "dni": dni, "comentario_entrada": comentario_entrada, "comentario_salida": comentario_salida,
                "entrada_corr": entrada_corr, "salida_corr": salida_corr,
            })

    endpoint_vista = {"cliente": "asistencia.cliente", "marcar": "asistencia.marcar_vista"}.get(vista, "asistencia.reporte")

    if not ediciones:
        return redirect(url_for(endpoint_vista, fecha=fecha_str))

    ok_lock, motivo_lock = adquirir_lock("tabla3_web", f"web:{current_user.email}", max_minutos=2)
    if not ok_lock:
        return render_template(
            "asistencia_resultado.html", usuario=current_user, activo=vista,
            titulo="No se pudo guardar", ok=False,
            detalle=f"{motivo_lock} -- probá de nuevo en un minuto.",
            volver=url_for(endpoint_vista, fecha=fecha_str),
        )
    try:
        wb = openpyxl.load_workbook(io.BytesIO(descargar(TABLA3_RUTA_GRAPH)))
        ws = wb["Registro diario supervisor"]
        fila_libre = ws.max_row + 1

        # Se arma todo en memoria y se sube a SharePoint (T3, la fuente
        # real que lee el motor) ANTES de comitear a Postgres -- igual que
        # marcar() y el cierre de Falta por reemplazo. Al revés (comitear
        # primero) era el único lugar que lo hacía así: si subir_in_place
        # fallaba después del commit, la fila CorreccionWeb ya existía y
        # sacaba a la persona de "Pendientes de marcar" aunque la
        # corrección nunca hubiera llegado a T3 -- se perdía en silencio.
        correcciones_web = []
        for e in ediciones:
            if e["comentario_entrada"] or e["entrada_corr"]:
                _agregar_fila_tabla3(ws, fila_libre, e["dni"], fecha, e["comentario_entrada"], hora_ent=e["entrada_corr"])
                fila_libre += 1
                correcciones_web.append(CorreccionWeb(
                    dni=e["dni"], fecha=fecha,
                    comentario_entrada=e["comentario_entrada"] or None,
                    hora_entrada_corregida=e["entrada_corr"] or None,
                    registrado_por=current_user.email,
                ))
            if e["comentario_salida"] or e["salida_corr"]:
                _agregar_fila_tabla3(ws, fila_libre, e["dni"], ayer, e["comentario_salida"], hora_sal=e["salida_corr"])
                fila_libre += 1
                correcciones_web.append(CorreccionWeb(
                    dni=e["dni"], fecha=ayer,
                    comentario_salida=e["comentario_salida"] or None,
                    hora_salida_corregida=e["salida_corr"] or None,
                    registrado_por=current_user.email,
                ))

        subir_in_place(TABLA3_RUTA_GRAPH, wb)

        session = get_session()
        try:
            for c in correcciones_web:
                session.add(c)
            session.commit()
        finally:
            session.close()
    finally:
        liberar_lock("tabla3_web")

    plural = "personas" if len(ediciones) != 1 else "persona"
    return render_template(
        "asistencia_resultado.html", usuario=current_user, activo=vista,
        titulo="Correcciones guardadas", ok=True,
        detalle=(f"Se guardaron los cambios de {len(ediciones)} {plural}. Se van a reflejar en el reporte en "
                 "unos minutos -- si querés verlos ya, usá \"Actualizar ahora\"."),
        volver=url_for(endpoint_vista, fecha=fecha_str),
    )


ACCIONES_MARCAR = ("Asistió", "Apoyo zona", "Vacante", "Falta")


@bp.post("/marcar")
@login_required
def marcar():
    """Version web de los botones Asistió/Apoyo zona/Vacante/Falta de la
    Power App (galPendientes -- ver GUIA_POWER_APPS_SUPERVISOR.md secciones
    3/4). Escribe a la MISMA Tabla 3 que la app móvil, con el mismo criterio
    que usa motor_clasificacion.py para interpretarlo -- "Falta" via
    Comentario="Falta - {motivo}" (mismo patron que ya usan las correcciones
    web y la app), las otras 3 via la columna "Estado reportado"."""
    dni = request.form["dni"].strip()
    fecha_str = request.form["fecha"]
    fecha = dt.date.fromisoformat(fecha_str)
    accion = request.form.get("accion", "").strip()
    motivo = request.form.get("motivo", "").strip()
    comentario_extra = request.form.get("comentario", "").strip()

    if accion not in ACCIONES_MARCAR:
        return redirect(url_for("asistencia.marcar_vista", fecha=fecha_str))
    if accion == "Falta" and not motivo:
        return redirect(url_for("asistencia.marcar_vista", fecha=fecha_str, marcado="falta_sin_motivo"))

    # El detalle adicional va entre paréntesis al final -- _homologar_motivo()
    # / _motivo_limpio() ya recortan cualquier paréntesis final al agrupar
    # "Faltas por motivo" (mismo mecanismo que ya usa MARCADOR_BORRADO), así
    # que el motivo sigue agrupando bien aunque cada persona escriba un
    # detalle distinto acá.
    comentario_falta = f"Falta - {motivo}"
    if comentario_extra:
        comentario_falta += f" ({comentario_extra})"

    ok_lock, motivo_lock = adquirir_lock("tabla3_web", f"web:{current_user.email}", max_minutos=2)
    if not ok_lock:
        return render_template(
            "asistencia_resultado.html", usuario=current_user, activo="marcar",
            titulo="No se pudo guardar", ok=False,
            detalle=f"{motivo_lock} -- probá de nuevo en un minuto.",
            volver=url_for("asistencia.marcar_vista", fecha=fecha_str),
        )
    try:
        wb = openpyxl.load_workbook(io.BytesIO(descargar(TABLA3_RUTA_GRAPH)))
        ws = wb["Registro diario supervisor"]
        fila_libre = ws.max_row + 1
        if accion == "Falta":
            _agregar_fila_tabla3(ws, fila_libre, dni, fecha, comentario_falta)
        else:
            _agregar_fila_tabla3(ws, fila_libre, dni, fecha, None, estado_reportado=accion)
        subir_in_place(TABLA3_RUTA_GRAPH, wb)

        # Ademas de Tabla 3 (lo que lee el motor), se guarda en correcciones_web
        # -- asi "Pendientes de marcar" puede sacar a esta persona de la
        # lista de una, sin esperar a que el motor vuelva a correr.
        session = get_session()
        try:
            session.add(CorreccionWeb(
                dni=dni, fecha=fecha,
                comentario_entrada=(comentario_falta if accion == "Falta" else accion),
                registrado_por=current_user.email,
            ))
            session.commit()
        finally:
            session.close()
    finally:
        liberar_lock("tabla3_web")

    return redirect(url_for("asistencia.marcar_vista", fecha=fecha_str, marcado="ok"))


@bp.post("/motivos/agregar")
@login_required
def motivos_agregar():
    fecha_str = request.form.get("fecha") or dt.date.today().isoformat()
    motivo = request.form.get("motivo", "").strip()
    if not motivo:
        return redirect(url_for("asistencia.marcar_vista", fecha=fecha_str))

    session = get_session()
    try:
        ya_existe = session.query(CatalogoMotivo).filter(CatalogoMotivo.motivo.ilike(motivo)).first()
        if not ya_existe:
            session.add(CatalogoMotivo(motivo=motivo, categoria="Falta"))
            session.commit()
    finally:
        session.close()

    return redirect(url_for("asistencia.marcar_vista", fecha=fecha_str, marcado="motivo_agregado"))


@bp.post("/actualizar")
@login_required
def actualizar():
    # Dispara el pipeline COMPLETO (no solo reporte_9am.yml) a proposito:
    # reporte_9am.yml unicamente regenera el reporte con lo YA calculado en
    # Postgres -- aplicar_correcciones.py (su unico paso relevante) solo
    # empuja ediciones hechas sobre el snapshot de auditoria Reporte_Asistencia_*.xlsx,
    # y si no encuentra ninguna ahi (que es el caso normal: tanto el guardado
    # de mac_cloud como la app movil de supervisores escriben DIRECTO a
    # Tabla 3) se salta motor_clasificacion.py por completo -- los
    # comentarios/motivos nuevos en Tabla 3 quedan sin aplicar. pipeline_completo.yml
    # si corre motor_clasificacion.py siempre, asi que es lo unico que
    # garantiza reflejar comentarios nuevos (via web o app movil).
    fecha_str = request.form["fecha"]
    vista = request.form.get("vista") or "reporte"
    ok, mensaje = disparar_workflow("pipeline_completo.yml")
    return render_template(
        "asistencia_resultado.html", usuario=current_user, activo=vista,
        titulo="Actualización disparada" if ok else "No se pudo disparar", ok=ok, detalle=mensaje,
        volver=url_for(f"asistencia.{'cliente' if vista == 'cliente' else 'reporte'}", fecha=fecha_str),
        poll_workflow="pipeline_completo.yml" if ok else None,
    )


@bp.get("/actualizar")
@login_required
def actualizar_get():
    # A diferencia de /guardar y /marcar (que redirigen tras el POST), esta
    # vista renderiza el resultado directo en la URL del POST -- si el
    # navegador vuelve a pedir esa misma URL más tarde (recuperar pestaña
    # tras cerrar Chrome, refrescar, etc.) lo hace por GET, sin el body del
    # formulario, y Flask tira "Método no permitido" (Davor, 2026-08-24: se
    # quedó viendo esa pantalla en blanco). No dispara el workflow de nuevo
    # (eso sería un GET con efecto secundario real) -- solo manda de vuelta
    # al reporte del día.
    return redirect(url_for("asistencia.reporte"))


@bp.get("/estado_workflow")
@login_required
def estado_workflow():
    nombre = request.args.get("wf", "")
    return jsonify(estado_ultima_corrida(nombre) or {})


@bp.get("/reemplazo")
@_analista_requerido
def reemplazo_form():
    hoy = dt.date.today().isoformat()
    try:
        pendientes = r9am.reemplazos_pendientes(r9am.dnis_alguna_vez_en_maestro())
        error_pendientes = None
    except Exception as e:
        pendientes = []
        error_pendientes = str(e)

    dni_prellenado = request.args.get("dni_vacante", "").strip()
    nombre_prellenado = None
    if dni_prellenado:
        session = get_session()
        try:
            p = session.query(Persona).filter(Persona.dni == dni_prellenado).first()
            nombre_prellenado = p.nombre_completo if p else None
        finally:
            session.close()

    return render_template(
        "asistencia_reemplazo.html", usuario=current_user, activo="reemplazo",
        hoy=hoy, pendientes=pendientes, error_pendientes=error_pendientes,
        dni_prellenado=dni_prellenado, nombre_prellenado=nombre_prellenado,
    )


@bp.post("/reemplazo")
@_analista_requerido
def reemplazo_submit():
    from reemplazos import procesar_reemplazo

    dni_vacante = request.form["dni_vacante"].strip()
    dni_nuevo = request.form["dni_nuevo"].strip()
    nombre_nuevo = request.form["nombre_nuevo"].strip()
    fecha_ingreso = dt.date.fromisoformat(request.form["fecha_ingreso"].strip())
    motivo_baja = request.form.get("motivo_baja", "").strip() or None

    try:
        log = procesar_reemplazo(dni_vacante, dni_nuevo, nombre_nuevo, fecha_ingreso, motivo_baja=motivo_baja)

        # Cierra el pendiente de HOY de la persona reemplazada -- sin esto,
        # "Agregar reemplazo" resuelve el futuro (quien entra) pero la
        # persona saliente seguia apareciendo en "Pendientes de marcar"
        # porque su Falta de hoy nunca queda justificada por separado.
        try:
            hoy = dt.date.today()
            ok_lock, _ = adquirir_lock("tabla3_web", f"web:{current_user.email}", max_minutos=2)
            if ok_lock:
                try:
                    wb = openpyxl.load_workbook(io.BytesIO(descargar(TABLA3_RUTA_GRAPH)))
                    ws = wb["Registro diario supervisor"]
                    _agregar_fila_tabla3(ws, ws.max_row + 1, dni_vacante, hoy, f"Falta - {motivo_baja or 'Reemplazo'}")
                    subir_in_place(TABLA3_RUTA_GRAPH, wb)
                    session = get_session()
                    try:
                        session.add(CorreccionWeb(
                            dni=dni_vacante, fecha=hoy, comentario_entrada=f"Falta - {motivo_baja or 'Reemplazo'}",
                            registrado_por=current_user.email,
                        ))
                        session.commit()
                    finally:
                        session.close()
                finally:
                    liberar_lock("tabla3_web")
        except Exception:
            pass  # el reemplazo en si ya se guardo bien -- esto es solo prolijidad, no bloquea el flujo principal

        ok_disparo, _ = disparar_workflow("pipeline_completo.yml")
        detalle = "\n".join(log)
        if not ok_disparo:
            detalle += "\n\nEl reemplazo se guardó, pero no se pudo iniciar la actualización automática -- probá \"Actualizar ahora\" desde el reporte diario."
        titulo = "Reemplazo agregado"
    except Exception as e:
        detalle = str(e)
        titulo = "Falló"
        ok_disparo = False

    return render_template(
        "asistencia_resultado.html", usuario=current_user, activo="reemplazo",
        titulo=titulo, ok=(titulo == "Reemplazo agregado"), detalle=detalle,
        volver=url_for("asistencia.reemplazo_form"),
        poll_workflow="pipeline_completo.yml" if ok_disparo else None,
    )


# Sin domingo -- mismo criterio que WD_NORM en horas_semanales.py, ningún
# módulo del pipeline espera un día de semana "Domingo" en PatronRecurrente.
DIAS_PATRON = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]


def _hora_form(valor):
    if not valor:
        return None
    try:
        return dt.time.fromisoformat(valor)
    except ValueError:
        return None


@bp.get("/headcount-nuevo")
@_analista_requerido
def headcount_nuevo_form():
    # Los desplegables se arman con los valores que YA existen en Postgres
    # (acotados al scope del usuario) en vez de una lista fija a mano --
    # mismo criterio que reportes.py::_filtros_admin() -- para no
    # desincronizarse si el Maestro usa un Rol/Canal nuevo que acá no
    # estaba contemplado.
    session = get_session()
    try:
        cond_scope = condicion_scope(Persona, current_user)

        def _valores(columna):
            q = session.query(columna).filter(columna.isnot(None))
            if cond_scope is not None:
                q = q.filter(cond_scope)
            return sorted({v for (v,) in q.distinct().all() if v})

        roles_disponibles = _valores(Persona.rol)
        canales_disponibles = _valores(Persona.canal)
        regiones_disponibles = _valores(Persona.region)
        ciudades_disponibles = _valores(Persona.ciudad)
        zonas_disponibles = _valores(Persona.zona)

        q_sup = session.query(Persona.dni, Persona.nombre_completo).filter(
            Persona.rol == "SUPERVISORES", Persona.estado == "Activo"
        )
        if cond_scope is not None:
            q_sup = q_sup.filter(cond_scope)
        supervisores_disponibles = sorted(q_sup.distinct().all(), key=lambda t: (t[1] or "").title())
    finally:
        session.close()

    return render_template(
        "asistencia_headcount_nuevo.html", usuario=current_user, activo="headcount_nuevo",
        hoy=dt.date.today().isoformat(), dias_patron=DIAS_PATRON,
        roles_disponibles=roles_disponibles, canales_disponibles=canales_disponibles,
        regiones_disponibles=regiones_disponibles, ciudades_disponibles=ciudades_disponibles,
        zonas_disponibles=zonas_disponibles, supervisores_disponibles=supervisores_disponibles,
    )


@bp.post("/headcount-nuevo")
@_analista_requerido
def headcount_nuevo_submit():
    from cargas import crear_persona_individual

    dni = request.form.get("dni", "").strip()
    nombre = request.form.get("nombre", "").strip()
    rol = request.form.get("rol", "").strip()
    canal = request.form.get("canal", "").strip() or None
    region = request.form.get("region", "").strip() or None
    ciudad = request.form.get("ciudad", "").strip() or None
    zona = request.form.get("zona", "").strip() or None
    supervisor_dni = request.form.get("supervisor_dni", "").strip() or None
    correo = request.form.get("correo", "").strip() or None
    fecha_ingreso_str = request.form.get("fecha_ingreso", "").strip()

    if not dni or not nombre or not rol or not fecha_ingreso_str:
        return render_template(
            "asistencia_resultado.html", usuario=current_user, activo="headcount_nuevo",
            titulo="Falló", ok=False, detalle="Faltan campos obligatorios (DNI, Nombre, Rol, Fecha de ingreso).",
            volver=url_for("asistencia.headcount_nuevo_form"),
        )
    try:
        fecha_ingreso = dt.date.fromisoformat(fecha_ingreso_str)
    except ValueError:
        return render_template(
            "asistencia_resultado.html", usuario=current_user, activo="headcount_nuevo",
            titulo="Falló", ok=False, detalle="Fecha de ingreso inválida.",
            volver=url_for("asistencia.headcount_nuevo_form"),
        )

    patron_dias = [
        {
            "dia_semana": dia,
            "hora_entrada": _hora_form(request.form.get(f"entrada_{dia.lower()}", "").strip()),
            "hora_salida": _hora_form(request.form.get(f"salida_{dia.lower()}", "").strip()),
            "canal_dia": request.form.get(f"canal_{dia.lower()}", "").strip() or None,
            "refrigerio": request.form.get(f"refrigerio_{dia.lower()}", "").strip() or None,
        }
        for dia in DIAS_PATRON
    ]

    try:
        es_reingreso, n_patron = crear_persona_individual(
            current_user.email, dni, nombre, rol, canal, region, ciudad, zona,
            supervisor_dni, correo, fecha_ingreso, patron_dias,
        )
        detalle = f"{nombre.title()} (DNI {dni}) fue dado de alta{' como reingreso' if es_reingreso else ''}."
        if n_patron:
            detalle += f" Se guardaron {n_patron} días de horario."
        else:
            detalle += (
                " No se cargó ningún horario -- completalo en \"Cargar Headcount\" (Patrón) o en "
                "\"Historial de cambios\" antes de que empiece a trabajar, sino no se va a poder "
                "clasificar su asistencia."
            )
        ok_disparo, _ = disparar_workflow("pipeline_completo.yml")
        if not ok_disparo:
            detalle += "\n\nNo se pudo iniciar la actualización automática -- probá \"Actualizar ahora\" desde el reporte diario."
        titulo = "Headcount agregado"
    except Exception as e:
        detalle = str(e)
        titulo = "Falló"
        ok_disparo = False

    return render_template(
        "asistencia_resultado.html", usuario=current_user, activo="headcount_nuevo",
        titulo=titulo, ok=(titulo == "Headcount agregado"), detalle=detalle,
        volver=url_for("asistencia.headcount_nuevo_form"),
        poll_workflow="pipeline_completo.yml" if titulo == "Headcount agregado" and ok_disparo else None,
    )
