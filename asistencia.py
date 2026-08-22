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

import openpyxl
from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from dimension_models import CatalogoMotivo, CorreccionWeb, Feriado, Persona, get_session
from fact_models import ClasificacionDiaria
from graph_client import descargar, subir_in_place
from github_actions import disparar_workflow, estado_ultima_corrida

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_AQUI, "pipeline"))
from db_lock import adquirir_lock, liberar_lock  # noqa: E402
import reporte_diario_9am as r9am  # noqa: E402 -- reusa reemplazos_pendientes()/dnis_alguna_vez_en_maestro(), no se duplica

bp = Blueprint("asistencia", __name__, url_prefix="/asistencia")

RUTA_GRAPH_MAC = "ASISTENCIA/MAC/"
TABLA3_RUTA_GRAPH = f"{RUTA_GRAPH_MAC}3_Registro_Diario_Supervisor.xlsx"
SALIDA_ANTICIPADA_MIN = 10  # mismo umbral que usaba la app local para resaltar "salida temprana"

# aplicar_correcciones.py escribe este texto exacto en el comentario cuando
# un supervisor reporta "Asistió" desde la app movil pero todavia nadie
# confirmo la hora real en "Entrada/Salida corregida" -- hay que poder
# encontrar estos casos facil para no perderlos (ver TEXTO_PENDIENTE_ENTRADA/
# TEXTO_PENDIENTE_SALIDA en pipeline/aplicar_correcciones.py).
MARCADOR_PENDIENTE = "reportado por supervisor -- confirmar en"

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


def _cargar_reporte(fecha):
    """Devuelve (resumen_dict, filas, frescura) para `fecha` -- None, None, None
    si no hay ninguna fila ese día (motor todavía no corrió para esa fecha)."""
    feriados = _cargar_feriados()
    ayer = _dia_habil_anterior(fecha, feriados)

    session = get_session()
    try:
        personas = {p.dni: p for p in session.query(Persona).all()}
        nombre_de = {dni: p.nombre_completo for dni, p in personas.items()}

        filas_hoy = (
            session.query(ClasificacionDiaria)
            .filter(ClasificacionDiaria.fecha == fecha)
            .all()
        )
        filas_ayer = {
            c.dni: c for c in
            session.query(ClasificacionDiaria).filter(ClasificacionDiaria.fecha == ayer).all()
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
            "entrada_pendiente": MARCADOR_PENDIENTE in (c.comentario_supervisor or ""),
            "salida_pendiente": MARCADOR_PENDIENTE in ((ayer_c.comentario_supervisor or "") if ayer_c else ""),
        })
        if c.procesado_en and (ultima_sync is None or c.procesado_en > ultima_sync):
            ultima_sync = c.procesado_en

    estado_base = lambda s: (s or "").split(" (")[0]  # noqa: E731
    resumen = {
        "asistio": sum(1 for f in filas if estado_base(f["estado"]) == "ASISTIÓ A TIEMPO"),
        "tardanza": sum(1 for f in filas if estado_base(f["estado"]) == "TARDANZA"),
        "falta": sum(1 for f in filas if estado_base(f["estado"]) == "FALTA"),
        "total": len(filas),
    }
    frescura = {"ultima_sync": ultima_sync, "fecha": fecha, "ayer": ayer}
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
    estado_base = lambda s: (s or "").split(" (")[0]  # noqa: E731
    return [f for f in filas if estado_base(f["estado"]) in ("FALTA", "VACANTE") and not f["comentario_entrada"]]


def _fecha_mas_reciente_con_datos():
    session = get_session()
    try:
        from sqlalchemy import func
        (fecha,) = session.query(func.max(ClasificacionDiaria.fecha)).one()
        return fecha
    finally:
        session.close()


def _vista_reporte(fecha_str, vista):
    """vista: 'reporte' (columnas + edicion completa) o 'cliente' (columnas
    reducidas, sin Hora entrada/salida corregida)."""
    try:
        fecha = dt.date.fromisoformat(fecha_str)
    except ValueError:
        fecha = dt.date.today()
        fecha_str = fecha.isoformat()

    resumen, filas, frescura = _cargar_reporte(fecha)
    pendientes = _pendientes_de_marcar(filas) if filas and vista == "reporte" else None
    # Si no hay datos para la fecha pedida (ej. hoy, si el pipeline todavia
    # no corrio), sugerir la fecha mas reciente que si tiene -- para poder
    # seguir corrigiendo el pasado sin quedar en un callejon sin salida.
    fecha_reciente = None if filas else _fecha_mas_reciente_con_datos()
    return render_template(
        "asistencia.html", usuario=current_user, activo=vista, vista=vista,
        fecha_reciente=fecha_reciente,
        fecha_str=fecha_str, resumen=resumen, filas=filas, frescura=frescura,
        pendientes=pendientes, motivos=_motivos_falta() if pendientes else None,
        marcado=request.args.get("marcado"),
    )


@bp.get("")
@login_required
def reporte():
    fecha_str = request.args.get("fecha") or dt.date.today().isoformat()
    return _vista_reporte(fecha_str, "reporte")


@bp.get("/cliente")
@login_required
def cliente():
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
    ws.cell(row=fila_libre, column=4, value=comentario)
    ws.cell(row=fila_libre, column=5, value="Web mac_cloud")
    ws.cell(row=fila_libre, column=6, value=dt.datetime.now().strftime("%H:%M"))
    if hora_ent:
        ws.cell(row=fila_libre, column=7, value=str(hora_ent))
    if hora_sal:
        ws.cell(row=fila_libre, column=8, value=str(hora_sal))


@bp.post("/guardar")
@login_required
def guardar():
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
        entrada_corr = request.form.get(f"entrada_corr_{dni}", "").strip() if vista == "reporte" else ""
        salida_corr = request.form.get(f"salida_corr_{dni}", "").strip() if vista == "reporte" else ""
        if comentario_entrada or comentario_salida or entrada_corr or salida_corr:
            ediciones.append({
                "dni": dni, "comentario_entrada": comentario_entrada, "comentario_salida": comentario_salida,
                "entrada_corr": entrada_corr, "salida_corr": salida_corr,
            })

    if not ediciones:
        return redirect(url_for(f"asistencia.{'cliente' if vista == 'cliente' else 'reporte'}", fecha=fecha_str))

    ok_lock, motivo_lock = adquirir_lock("tabla3_web", f"web:{current_user.email}", max_minutos=2)
    if not ok_lock:
        return render_template(
            "asistencia_resultado.html", usuario=current_user, activo=vista,
            titulo="No se pudo guardar", ok=False,
            detalle=f"{motivo_lock} -- probá de nuevo en un minuto.",
            volver=url_for(f"asistencia.{'cliente' if vista == 'cliente' else 'reporte'}", fecha=fecha_str),
        )
    try:
        wb = openpyxl.load_workbook(io.BytesIO(descargar(TABLA3_RUTA_GRAPH)))
        ws = wb["Registro diario supervisor"]
        fila_libre = ws.max_row + 1

        session = get_session()
        try:
            for e in ediciones:
                if e["comentario_entrada"] or e["entrada_corr"]:
                    _agregar_fila_tabla3(ws, fila_libre, e["dni"], fecha, e["comentario_entrada"], hora_ent=e["entrada_corr"])
                    fila_libre += 1
                    session.add(CorreccionWeb(
                        dni=e["dni"], fecha=fecha,
                        comentario_entrada=e["comentario_entrada"] or None,
                        hora_entrada_corregida=e["entrada_corr"] or None,
                        registrado_por=current_user.email,
                    ))
                if e["comentario_salida"] or e["salida_corr"]:
                    _agregar_fila_tabla3(ws, fila_libre, e["dni"], ayer, e["comentario_salida"], hora_sal=e["salida_corr"])
                    fila_libre += 1
                    session.add(CorreccionWeb(
                        dni=e["dni"], fecha=ayer,
                        comentario_salida=e["comentario_salida"] or None,
                        hora_salida_corregida=e["salida_corr"] or None,
                        registrado_por=current_user.email,
                    ))
            session.commit()
        finally:
            session.close()

        subir_in_place(TABLA3_RUTA_GRAPH, wb)
    finally:
        liberar_lock("tabla3_web")

    plural = "personas" if len(ediciones) != 1 else "persona"
    return render_template(
        "asistencia_resultado.html", usuario=current_user, activo=vista,
        titulo="Correcciones guardadas", ok=True,
        detalle=(f"Se guardaron los cambios de {len(ediciones)} {plural}. Se van a reflejar en el reporte en "
                 "unos minutos -- si querés verlos ya, usá \"Actualizar ahora\"."),
        volver=url_for(f"asistencia.{'cliente' if vista == 'cliente' else 'reporte'}", fecha=fecha_str),
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

    if accion not in ACCIONES_MARCAR:
        return redirect(url_for("asistencia.reporte", fecha=fecha_str))
    if accion == "Falta" and not motivo:
        return redirect(url_for("asistencia.reporte", fecha=fecha_str, marcado="falta_sin_motivo"))

    ok_lock, motivo_lock = adquirir_lock("tabla3_web", f"web:{current_user.email}", max_minutos=2)
    if not ok_lock:
        return render_template(
            "asistencia_resultado.html", usuario=current_user, activo="reporte",
            titulo="No se pudo guardar", ok=False,
            detalle=f"{motivo_lock} -- probá de nuevo en un minuto.",
            volver=url_for("asistencia.reporte", fecha=fecha_str),
        )
    try:
        wb = openpyxl.load_workbook(io.BytesIO(descargar(TABLA3_RUTA_GRAPH)))
        ws = wb["Registro diario supervisor"]
        fila_libre = ws.max_row + 1
        if accion == "Falta":
            _agregar_fila_tabla3(ws, fila_libre, dni, fecha, f"Falta - {motivo}")
        else:
            _agregar_fila_tabla3(ws, fila_libre, dni, fecha, None, estado_reportado=accion)
        subir_in_place(TABLA3_RUTA_GRAPH, wb)
    finally:
        liberar_lock("tabla3_web")

    return redirect(url_for("asistencia.reporte", fecha=fecha_str, marcado="ok"))


@bp.post("/motivos/agregar")
@login_required
def motivos_agregar():
    fecha_str = request.form.get("fecha") or dt.date.today().isoformat()
    motivo = request.form.get("motivo", "").strip()
    if not motivo:
        return redirect(url_for("asistencia.reporte", fecha=fecha_str))

    session = get_session()
    try:
        ya_existe = session.query(CatalogoMotivo).filter(CatalogoMotivo.motivo.ilike(motivo)).first()
        if not ya_existe:
            session.add(CatalogoMotivo(motivo=motivo, categoria="Falta"))
            session.commit()
    finally:
        session.close()

    return redirect(url_for("asistencia.reporte", fecha=fecha_str, marcado="motivo_agregado"))


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


@bp.get("/estado_workflow")
@login_required
def estado_workflow():
    nombre = request.args.get("wf", "")
    return jsonify(estado_ultima_corrida(nombre) or {})


@bp.get("/reemplazo")
@login_required
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
@login_required
def reemplazo_submit():
    from reemplazos import procesar_reemplazo

    dni_vacante = request.form["dni_vacante"].strip()
    dni_nuevo = request.form["dni_nuevo"].strip()
    nombre_nuevo = request.form["nombre_nuevo"].strip()
    fecha_ingreso = dt.date.fromisoformat(request.form["fecha_ingreso"].strip())
    motivo_baja = request.form.get("motivo_baja", "").strip() or None

    try:
        log = procesar_reemplazo(dni_vacante, dni_nuevo, nombre_nuevo, fecha_ingreso, motivo_baja=motivo_baja)
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
