# -*- coding: utf-8 -*-
"""Blueprint de reportes nuevos para supervisores (2026-08-22, lista de
ideas de Davor) -- Horas semanales (#2) primero; Alertas (#1) y Ficha del
trabajador (#6) se agregan en los siguientes pasos del mismo plan."""
import calendar
import datetime as dt
import io
import re

import openpyxl
import pandas as pd
from flask import Blueprint, flash, redirect, request, render_template, send_file, url_for
from flask_login import current_user, login_required
from openpyxl.styles import Font
from sqlalchemy.orm import aliased

from alertas import alertas_periodo
from asistencia import _homologar_motivo
from cobertura import matriz_cobertura
from dimension_models import HistorialCambio, Persona, get_session
from fact_models import ClasificacionDiaria
from historial import CAMPOS_VALIDOS, DIAS_SEMANA as DIAS_SEMANA_HISTORIAL
from horas_semanales import semana_iso, calcular_detalle_semana, resumen_por_persona
from recomendaciones import insights_equipo, resumen_perfil_equipo
from scoping import condicion_scope
from vacaciones import calcular_viajes_vacaciones

bp = Blueprint("reportes", __name__, url_prefix="/reportes")

DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

# Mismo texto corto que ya usa Asistencia diaria (asistencia.html) para el
# badge de estado -- Davor pidió que la Ficha del trabajador "jale el mismo
# formato" en vez de mostrar el estado crudo ("ASISTIÓ A TIEMPO", 2026-08-24).
ESTADO_CORTO = {"ASISTIÓ A TIEMPO": "Asistió", "TARDANZA": "Tardanza", "FALTA": "Falta", "VACANTE": "Vacante", "VACACIONES": "Vacaciones"}

COLUMNAS_HORAS = [
    ("nombre", "Nombre"), ("supervisor", "Supervisor"), ("ciudad", "Ciudad"), ("region", "Región"),
    ("dias_falta_vacante", "Días Falta/Vacante"), ("horas_trabajadas", "Horas trabajadas"),
    ("horas_a_trabajar", "Horas a trabajar"), ("horas_a_trabajar_sin_faltas", "Horas a trabajar sin faltas"),
    ("diferencia_h", "Diferencia (h)"), ("pct_cumplimiento", "% Cumplimiento"),
    ("pct_cumplimiento_sin_faltas", "% Cumplimiento sin faltas"),
]


def _filtros_admin():
    """Filtros de Supervisor/Región/Rol para las 4 pestañas de Reportes --
    SOLO para admin (Davor, 2026-08-24: "solo debe haber filtros para el
    perfil admin, para los supervisores no debe haber filtros"). Para
    cualquier otro rol devuelve todo vacío -- las plantillas ocultan la
    barra de filtros cuando `roles_disponibles` (etc.) viene vacío.
    Devuelve (filtro_args, roles_disponibles, regiones_disponibles,
    supervisores_disponibles)."""
    if current_user.rol != "admin":
        return {"rol": "", "region": "", "supervisor": ""}, [], [], []

    filtro_args = {
        "rol": request.args.get("rol") or "",
        "region": request.args.get("region") or "",
        "supervisor": request.args.get("supervisor") or "",
    }
    session = get_session()
    try:
        roles_disponibles = sorted({
            r for (r,) in session.query(Persona.rol).filter(Persona.rol.isnot(None)).distinct().all()
        })
        regiones_disponibles = sorted({
            r for (r,) in session.query(Persona.region).filter(Persona.region.isnot(None)).distinct().all()
        })
        SupervisorPersona = aliased(Persona)
        supervisores_disponibles = sorted(
            set(
                session.query(Persona.supervisor_dni, SupervisorPersona.nombre_completo)
                .join(SupervisorPersona, SupervisorPersona.dni == Persona.supervisor_dni)
                .filter(Persona.supervisor_dni.isnot(None)).distinct().all()
            ),
            key=lambda t: (t[1] or "").title(),
        )
    finally:
        session.close()
    return filtro_args, roles_disponibles, regiones_disponibles, supervisores_disponibles


def _semana_desde_query():
    """Lee ?semana=2026-W34 (formato de <input type=week>) -- si falta o es
    inválido, usa la semana ISO actual."""
    hoy = dt.date.today()
    semana_str = request.args.get("semana", "")
    if len(semana_str) == 8 and semana_str[4] == "-" and semana_str[5] == "W":
        try:
            anio, num = int(semana_str[:4]), int(semana_str[6:])
            desde, hasta = semana_iso(anio, num)
            return desde, hasta, semana_str
        except ValueError:
            pass
    anio, num, _ = hoy.isocalendar()
    desde, hasta = semana_iso(anio, num)
    return desde, hasta, f"{anio}-W{num:02d}"


@bp.get("/horas")
@login_required
def horas():
    desde, hasta, semana_str = _semana_desde_query()
    filtro_args, roles_disp, regiones_disp, supervisores_disp = _filtros_admin()
    detalle = calcular_detalle_semana(
        desde, hasta, current_user,
        rol_filtro=filtro_args["rol"], region_filtro=filtro_args["region"], supervisor_filtro=filtro_args["supervisor"],
    )
    resumen = resumen_por_persona(detalle)
    filas = resumen.to_dict("records") if len(resumen) else []
    return render_template(
        "reportes_horas.html", usuario=current_user, activo="horas",
        semana_str=semana_str, desde=desde, hasta=hasta, filas=filas,
        filtro_args=filtro_args, roles_disponibles=roles_disp,
        regiones_disponibles=regiones_disp, supervisores_disponibles=supervisores_disp,
    )


@bp.get("/horas/exportar")
@login_required
def horas_exportar():
    desde, hasta, semana_str = _semana_desde_query()
    filtro_args, _roles_disp, _regiones_disp, _supervisores_disp = _filtros_admin()
    detalle = calcular_detalle_semana(
        desde, hasta, current_user,
        rol_filtro=filtro_args["rol"], region_filtro=filtro_args["region"], supervisor_filtro=filtro_args["supervisor"],
    )
    resumen = resumen_por_persona(detalle)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Horas semanales"
    ws.append([titulo for _clave, titulo in COLUMNAS_HORAS])
    for celda in ws[1]:
        celda.font = Font(bold=True)
    for fila in resumen.to_dict("records"):
        ws.append([fila.get(clave) for clave, _titulo in COLUMNAS_HORAS])
    for i, (_clave, titulo) in enumerate(COLUMNAS_HORAS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(12, len(titulo) + 2)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True, download_name=f"Horas_Semanales_{semana_str}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _mes_desde_query():
    """Lee ?mes=2026-08 -- si falta o es inválido, usa el mes calendario actual."""
    hoy = dt.date.today()
    mes_str = request.args.get("mes", "")
    if re.match(r"^\d{4}-\d{2}$", mes_str):
        anio, mes = int(mes_str[:4]), int(mes_str[5:7])
    else:
        anio, mes = hoy.year, hoy.month
    desde = dt.date(anio, mes, 1)
    hasta = dt.date(anio, mes, calendar.monthrange(anio, mes)[1])
    return desde, hasta, f"{anio}-{mes:02d}"


@bp.get("/alertas")
@login_required
def alertas():
    desde, hasta, mes_str = _mes_desde_query()
    filtro_args, roles_disp, regiones_disp, supervisores_disp = _filtros_admin()
    lista = alertas_periodo(
        desde, hasta, current_user,
        rol_filtro=filtro_args["rol"], region_filtro=filtro_args["region"], supervisor_filtro=filtro_args["supervisor"],
    )
    return render_template(
        "reportes_alertas.html", usuario=current_user, activo="alertas",
        mes_str=mes_str, desde=desde, hasta=hasta, alertas=lista,
        filtro_args=filtro_args, roles_disponibles=roles_disp,
        regiones_disponibles=regiones_disp, supervisores_disponibles=supervisores_disp,
    )


def _hoy_ref_desde_mes(mes_str, desde, hasta):
    """Para insights_equipo()/resumen_perfil_equipo(), que trabajan contra
    un `hoy` de referencia (no un rango desde/hasta): si el mes elegido es
    el actual, usa la fecha real de hoy (para que las reglas de "semana en
    curso"/"días hábiles transcurridos" sigan siendo correctas); si es un
    mes pasado, usa el último día de ese mes (todo el mes cuenta como
    "transcurrido")."""
    hoy_real = dt.date.today()
    if (desde.year, desde.month) == (hoy_real.year, hoy_real.month):
        return hoy_real
    return hasta


@bp.get("/recomendaciones")
@login_required
def recomendaciones():
    desde, hasta, mes_str = _mes_desde_query()
    hoy_ref = _hoy_ref_desde_mes(mes_str, desde, hasta)
    filtro_args, roles_disp, regiones_disp, supervisores_disp = _filtros_admin()
    lista = insights_equipo(
        current_user, hoy=hoy_ref,
        rol_filtro=filtro_args["rol"], region_filtro=filtro_args["region"], supervisor_filtro=filtro_args["supervisor"],
    )
    perfiles = resumen_perfil_equipo(
        current_user, hoy=hoy_ref,
        rol_filtro=filtro_args["rol"], region_filtro=filtro_args["region"], supervisor_filtro=filtro_args["supervisor"],
    )
    return render_template(
        "reportes_recomendaciones.html", usuario=current_user, activo="recomendaciones",
        insights=lista, perfiles=perfiles, mes_str=mes_str,
        filtro_args=filtro_args, roles_disponibles=roles_disp,
        regiones_disponibles=regiones_disp, supervisores_disponibles=supervisores_disp,
    )


def _rango_desde_query(dias_por_defecto=21):
    """Lee ?desde=&hasta= -- si faltan o son inválidos, usa los últimos
    `dias_por_defecto` días hasta hoy (mismo patrón que el Dashboard)."""
    hoy = dt.date.today()
    try:
        hasta = dt.date.fromisoformat(request.args["hasta"]) if request.args.get("hasta") else hoy
    except ValueError:
        hasta = hoy
    try:
        desde = dt.date.fromisoformat(request.args["desde"]) if request.args.get("desde") else hasta - dt.timedelta(days=dias_por_defecto)
    except ValueError:
        desde = hasta - dt.timedelta(days=dias_por_defecto)
    if desde > hasta:
        desde, hasta = hasta, desde
    return desde, hasta


@bp.get("/cobertura")
@login_required
def cobertura():
    desde, hasta = _rango_desde_query()
    filtro_args, roles_disp, regiones_disp, supervisores_disp = _filtros_admin()
    personas, fechas, celdas, categorias = matriz_cobertura(
        desde, hasta, current_user,
        rol_filtro=filtro_args["rol"], region_filtro=filtro_args["region"], supervisor_filtro=filtro_args["supervisor"],
    )

    # Resaltado simple: celda por debajo de la mitad del promedio de ESA
    # persona en el periodo elegido -- mismo criterio "umbral simple" del
    # plan, no intenta reproducir el cruce con Falta/Vacaciones del mockup.
    promedios = {}
    for p in personas:
        valores = [celdas[(p["dni"], f)] for f in fechas if (p["dni"], f) in celdas]
        promedios[p["dni"]] = (sum(valores) / len(valores)) if valores else 0

    return render_template(
        "reportes_cobertura.html", usuario=current_user, activo="cobertura",
        desde=desde, hasta=hasta, personas=personas, fechas=fechas, celdas=celdas,
        categorias=categorias, promedios=promedios,
        filtro_args=filtro_args, roles_disponibles=roles_disp,
        regiones_disponibles=regiones_disp, supervisores_disponibles=supervisores_disp,
    )


@bp.get("/cobertura/exportar")
@login_required
def cobertura_exportar():
    desde, hasta = _rango_desde_query()
    filtro_args, _roles_disp, _regiones_disp, _supervisores_disp = _filtros_admin()
    personas, fechas, celdas, _categorias = matriz_cobertura(
        desde, hasta, current_user,
        rol_filtro=filtro_args["rol"], region_filtro=filtro_args["region"], supervisor_filtro=filtro_args["supervisor"],
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cobertura Diaria"
    encabezado = ["DNI", "Nombre", "Ciudad", "Supervisor"] + [f.strftime("%d/%m") for f in fechas]
    ws.append(encabezado)
    for celda in ws[1]:
        celda.font = Font(bold=True)
    for p in personas:
        fila = [p["dni"], p["nombre"], p["ciudad"], p["supervisor"]]
        fila += [celdas.get((p["dni"], f), "") for f in fechas]
        ws.append(fila)
    for i in range(1, 5):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 22

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True, download_name=f"Cobertura_Diaria_{desde}_a_{hasta}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.get("/ficha/<dni>")
@login_required
def ficha(dni):
    # Mismo criterio de acceso que el resto del sitio: si la Persona no
    # entra en el scope de current_user (su equipo/cliente), 404 lógico en
    # vez de dejar entrar por URL directa a alguien fuera de su alcance.
    session = get_session()
    try:
        query = session.query(Persona).filter(Persona.dni == dni)
        cond_scope = condicion_scope(Persona, current_user)
        if cond_scope is not None:
            query = query.filter(cond_scope)
        persona = query.first()

        nombre_supervisor = None
        if persona and persona.supervisor_dni:
            sup = session.query(Persona.nombre_completo).filter(Persona.dni == persona.supervisor_dni).first()
            nombre_supervisor = sup[0] if sup else None
    finally:
        session.close()

    if not persona:
        flash("No se encontró a esa persona o no tenés acceso a su ficha.", "error")
        return redirect(url_for("reportes.horas"))

    hoy = dt.date.today()
    mes_desde = hoy.replace(day=1)
    mes_hasta = dt.date(hoy.year, hoy.month, calendar.monthrange(hoy.year, hoy.month)[1])
    ventana_desde = hoy - dt.timedelta(days=180)

    # Indicador de asistencia del mes -- misma regla de horas ya validada
    # (Horas semanales), aplicada al rango de un mes completo en vez de una
    # semana, para UNA sola persona.
    detalle_mes = calcular_detalle_semana(mes_desde, mes_hasta, None, dni_filtro=dni)
    resumen_mes = resumen_por_persona(detalle_mes)
    indicador_mes = resumen_mes.iloc[0].to_dict() if len(resumen_mes) else None

    # Vacaciones (últimos 180 días) -- reusa el mismo agrupador en "viajes"
    # que ya usa el Dashboard.
    session = get_session()
    try:
        filas_periodo = (
            session.query(ClasificacionDiaria.dni, Persona.nombre_completo, ClasificacionDiaria.fecha, ClasificacionDiaria.estado)
            .join(Persona, Persona.dni == ClasificacionDiaria.dni)
            .filter(ClasificacionDiaria.dni == dni, ClasificacionDiaria.fecha >= ventana_desde, ClasificacionDiaria.fecha <= hoy)
            .all()
        )
        r_periodo = pd.DataFrame(filas_periodo, columns=["dni", "nombre", "fecha", "estado"])
        if len(r_periodo):
            r_periodo["fecha"] = pd.to_datetime(r_periodo["fecha"])
            r_periodo["estado_base"] = r_periodo["estado"].apply(lambda s: s.split(" (")[0])
        viajes_vacaciones = calcular_viajes_vacaciones(session, r_periodo, hoy) if len(r_periodo) else []

        # Descansos médicos (últimos 180 días) -- subconjunto de Falta cuyo
        # comentario del supervisor menciona "descanso médico".
        descansos = (
            session.query(ClasificacionDiaria.fecha, ClasificacionDiaria.comentario_supervisor)
            .filter(
                ClasificacionDiaria.dni == dni,
                ClasificacionDiaria.fecha >= ventana_desde, ClasificacionDiaria.fecha <= hoy,
                ClasificacionDiaria.estado.ilike("FALTA%"),
                ClasificacionDiaria.comentario_supervisor.ilike("%descanso%medic%"),
            )
            .order_by(ClasificacionDiaria.fecha.desc())
            .all()
        )

        # Historial de cambios (overrides de horario/canal/supervisor/zona/
        # refrigerio) vigentes para esta persona -- ya se validó el scope de
        # `persona` arriba, no hace falta repetirlo acá.
        historial_persona = (
            session.query(HistorialCambio)
            .filter(HistorialCambio.dni == dni)
            .order_by(HistorialCambio.fecha_desde.desc())
            .all()
        )
    finally:
        session.close()

    # Cumplimiento semanal, últimas 6 semanas ISO (incluye la actual) --
    # mismo cálculo de Horas semanales, una corrida por semana. Cada semana
    # queda linkeable (?semana=) para ver su detalle día a día abajo.
    anio_actual, num_actual, _ = hoy.isocalendar()
    cumplimiento_semanal = []
    for i in range(5, -1, -1):
        fecha_ref = hoy - dt.timedelta(weeks=i)
        anio_s, num_s, _ = fecha_ref.isocalendar()
        desde_s, hasta_s = semana_iso(anio_s, num_s)
        detalle_s = calcular_detalle_semana(desde_s, hasta_s, None, dni_filtro=dni)
        resumen_s = resumen_por_persona(detalle_s)
        pct = resumen_s.iloc[0]["pct_cumplimiento_sin_faltas"] if len(resumen_s) else None
        cumplimiento_semanal.append({
            "semana": f"S{num_s}", "semana_str": f"{anio_s}-W{num_s:02d}",
            "desde": desde_s, "pct": None if pct is None or pd.isna(pct) else pct,
        })

    # Detalle día a día -- para ver DÓNDE está el cuello de botella detrás
    # de un % bajo (Davor, 2026-08-23), no solo el número agregado. Semana
    # seleccionable igual que Horas semanales (?semana=2026-W34); si no se
    # pasa, la última semana con datos reales (no necesariamente la
    # calendario actual, que puede no tener nada cargado todavía).
    semana_str_qs = request.args.get("semana", "")
    if len(semana_str_qs) == 8 and semana_str_qs[4] == "-" and semana_str_qs[5] == "W":
        try:
            anio_v, num_v = int(semana_str_qs[:4]), int(semana_str_qs[6:])
        except ValueError:
            anio_v, num_v = anio_actual, num_actual
    else:
        anio_v, num_v = anio_actual, num_actual
    desde_v, hasta_v = semana_iso(anio_v, num_v)
    detalle_semana_vista = calcular_detalle_semana(desde_v, hasta_v, None, dni_filtro=dni)
    detalle_dias = []
    if len(detalle_semana_vista):
        for _, row in detalle_semana_vista.sort_values("fecha").iterrows():
            estado_base = row["estado"].split(" (")[0]
            detalle_dias.append({
                "fecha": row["fecha"].strftime("%d/%m"),
                "dia_semana": DIAS_ES[row["fecha"].weekday()],
                "estado": row["estado"],
                "estado_base": estado_base,
                "estado_corto": ESTADO_CORTO.get(estado_base, estado_base.title()),
                "motivo": _homologar_motivo(row["comentario"]) if estado_base == "FALTA" else None,
                "entrada_real": row["entrada_real"] or "—",
                "entrada_esperada": row["entrada_esperada"] or "—",
                "salida_real": row["salida_real"] or "—",
                "salida_esperada": row["salida_esperada"] or "—",
                "horas_trabajadas": row["horas_trabajadas"] if pd.notna(row["horas_trabajadas"]) else None,
                "horas_a_trabajar": row["horas_a_trabajar"],
            })
    semana_vista_str = f"{anio_v}-W{num_v:02d}"

    # Tardanzas del mes -- lista explícita (no solo el conteo agregado),
    # para ver a simple vista qué días llegó tarde y por cuánto.
    tardanzas_mes = []
    if len(detalle_mes):
        for _, row in detalle_mes[detalle_mes["estado"].str.startswith("TARDANZA")].sort_values("fecha", ascending=False).iterrows():
            tardanzas_mes.append({
                "fecha": row["fecha"].strftime("%d/%m"),
                "estado": row["estado"],
                "estado_corto": ESTADO_CORTO.get(row["estado"].split(" (")[0], "Tardanza"),
                "entrada_real": row["entrada_real"] or "—",
                "entrada_esperada": row["entrada_esperada"] or "—",
            })

    # Faltas del mes -- mismo criterio que Tardanzas del mes (Davor,
    # 2026-08-24: "tiene una falta pero no me sale en el detalle del
    # perfil" -- el indicador de arriba ya contaba la falta, pero "Detalle
    # día a día" solo muestra la semana seleccionada, y esta era la única
    # sección que le faltaba su propia lista explícita). Cuenta TODAS las
    # faltas del mes (con o sin sustento), igual que indicador_mes.dias_falta_vacante.
    faltas_mes = []
    if len(detalle_mes):
        for _, row in detalle_mes[detalle_mes["estado"].str.startswith("FALTA")].sort_values("fecha", ascending=False).iterrows():
            faltas_mes.append({
                "fecha": row["fecha"].strftime("%d/%m"),
                "motivo": _homologar_motivo(row["comentario"]) or "Sin motivo",
            })

    alertas_mes = alertas_periodo(mes_desde, mes_hasta, None, dni_filtro=dni)
    insights = insights_equipo(None, dni_filtro=dni, hoy=hoy)

    return render_template(
        "reportes_ficha.html", usuario=current_user, activo="ficha",
        persona=persona, nombre_supervisor=nombre_supervisor,
        indicador_mes=indicador_mes, viajes_vacaciones=viajes_vacaciones,
        descansos=descansos, cumplimiento_semanal=cumplimiento_semanal,
        detalle_dias=detalle_dias, semana_vista_str=semana_vista_str,
        desde_v=desde_v, hasta_v=hasta_v, tardanzas_mes=tardanzas_mes, faltas_mes=faltas_mes,
        alertas_mes=alertas_mes, insights=insights,
        historial_persona=historial_persona, campos_historial=CAMPOS_VALIDOS, dias_semana_historial=DIAS_SEMANA_HISTORIAL,
    )
