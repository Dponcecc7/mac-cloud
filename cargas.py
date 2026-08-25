# -*- coding: utf-8 -*-
"""
Fase 4: carga de Headcount (Maestro + Patrón) por otros analistas, no solo
Davor -- cada quien sube su propio equipo. Los datos quedan separados por
`Persona.analista_propietario` (el correo de quien los subió): un DNI nuevo
se crea a nombre del que lo sube; un DNI que ya existe y es de OTRO analista
nunca se pisa en silencio -- se reporta como conflicto para revisión manual
(probablemente un DNI mal tipeado).
"""
import datetime as dt
import io
from functools import wraps

import openpyxl
import pandas as pd
from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from openpyxl.styles import Font

from dimension_models import Persona, PatronRecurrente, get_session
from parseo_headcount import (
    COLUMNAS_MAESTRO_ESPERADAS, COLUMNAS_PATRON_ESPERADAS,
    parsear_maestro, parsear_patron, validar_columnas,
)

bp = Blueprint("cargas", __name__, url_prefix="/cargas")


def _analista_requerido(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.rol not in ("analista", "admin"):
            flash("Esta sección es solo para analistas.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper


def _dni(valor):
    if pd.isna(valor):
        return None
    if isinstance(valor, float):
        return str(int(valor))
    return str(valor).strip()


def _texto(valor):
    if pd.isna(valor):
        return None
    return str(valor).strip() or None


def _fecha(valor):
    if pd.isna(valor):
        return None
    if isinstance(valor, dt.date) and not isinstance(valor, dt.datetime):
        return valor
    return pd.Timestamp(valor).date()


def _si_no_a_bool(valor):
    return str(valor).strip().upper() == "SÍ" if pd.notna(valor) else False


def _hora(valor):
    return valor if pd.notna(valor) else None


def crear_persona_individual(
    propietario, dni, nombre, rol, canal, region, ciudad, zona,
    supervisor_dni, correo, fecha_ingreso, patron_dias,
):
    """Alta de UNA persona nueva a Headcount sin pasar por el Excel de
    "Cargar Headcount" -- Davor, 2026-08-25: "si quiero agregar un
    mercaderista nuevo a mi personal, como lo haria... donde dice agregar
    reemplazo, debe haber agregar nuevo headcount". A diferencia de
    "Agregar reemplazo" (reemplazos.py), acá NO hace falta un DNI de
    vacante existente del cual heredar datos -- es headcount genuinamente
    nuevo, no la cobertura de una posición que ya existía.

    `patron_dias`: lista de dicts {dia_semana, hora_entrada, hora_salida,
    canal_dia, refrigerio} -- se ignoran los días sin hora de entrada Y
    salida (persona sin ese día en su semana laboral, mismo criterio que
    "sin fila = no trabaja ese día" que ya usa todo el pipeline)."""
    session = get_session()
    try:
        existente = session.get(Persona, dni)
        if existente and existente.analista_propietario and existente.analista_propietario != propietario:
            raise ValueError(f"El DNI {dni} ya existe y pertenece a otro analista ({existente.analista_propietario}).")
        if existente and existente.estado == "Activo":
            raise ValueError(f"El DNI {dni} ya está Activo en el sistema -- usá Historial de cambios para modificar sus datos.")

        es_reingreso = existente is not None
        persona = existente or Persona(dni=dni)
        if not es_reingreso:
            session.add(persona)
        persona.nombre_completo = nombre
        persona.rol = rol
        persona.canal = canal
        persona.region = region
        persona.ciudad = ciudad
        persona.zona = zona
        persona.supervisor_dni = supervisor_dni
        persona.correo = correo
        persona.fecha_ingreso = fecha_ingreso
        persona.fecha_baja = None
        persona.estado = "Activo"
        persona.es_reingreso = es_reingreso
        persona.motivo_baja = None
        persona.registrado_por = propietario
        persona.fecha_registro = dt.date.today()
        persona.analista_propietario = propietario
        session.flush()  # la persona ya debe existir en la sesion antes de tocar Patron (FK)

        session.query(PatronRecurrente).filter_by(dni=dni).delete()
        session.flush()
        n_patron = 0
        for fila in patron_dias:
            if not fila.get("hora_entrada") or not fila.get("hora_salida"):
                continue
            session.add(PatronRecurrente(
                dni=dni, dia_semana=fila["dia_semana"],
                hora_entrada_prog=fila["hora_entrada"], hora_salida_prog=fila["hora_salida"],
                canal_dia=fila.get("canal_dia") or canal, refrigerio=fila.get("refrigerio"),
            ))
            n_patron += 1

        session.commit()
        return es_reingreso, n_patron
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _plantilla_excel(nombre_hoja, titulo, columnas, fila_ejemplo):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = nombre_hoja
    ws.cell(row=1, column=1, value=titulo).font = Font(bold=True, size=13)
    ws.cell(row=2, column=1, value="Plantilla en blanco -- completá una fila por persona/día y subila en \"Cargar Headcount\".")
    for i, col in enumerate(columnas, start=1):
        celda = ws.cell(row=4, column=i, value=col)
        celda.font = Font(bold=True)
        ws.column_dimensions[celda.column_letter].width = max(14, len(col) + 2)
    for i, valor in enumerate(fila_ejemplo, start=1):
        celda = ws.cell(row=5, column=i, value=valor)
        celda.font = Font(italic=True, color="9C6B13")
    ws.cell(row=6, column=1, value="↑ borrá esta fila de ejemplo antes de subir el archivo").font = Font(italic=True, size=9, color="9C6B13")
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@bp.get("/plantilla/maestro.xlsx")
@_analista_requerido
def plantilla_maestro():
    # DNI de ejemplo = "EJEMPLO" (no numérico) a propósito -- parsear_maestro()
    # descarta filas cuyo DNI no es numérico, así que si alguien sube el
    # archivo sin borrar la fila de ejemplo, no crea una persona fantasma.
    buf = _plantilla_excel(
        "Maestro Headcount", "Maestro Headcount", COLUMNAS_MAESTRO_ESPERADAS,
        ["EJEMPLO", "APELLIDOS NOMBRES", dt.date.today(), None, "Activo", None, "No",
         "MERCADERISTAS", "FARMACIA", "LIMA", "LIMA", "NOMBRE DE ZONA", "NOMBRE DEL SUPERVISOR",
         None, None, "Analista MAC", dt.date.today()],
    )
    return send_file(buf, as_attachment=True, download_name="Plantilla_Maestro_Headcount.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.get("/plantilla/patron.xlsx")
@_analista_requerido
def plantilla_patron():
    buf = _plantilla_excel(
        "Patrón recurrente", "Patrón Recurrente", COLUMNAS_PATRON_ESPERADAS,
        ["EJEMPLO", "lunes", dt.time(8, 0), dt.time(17, 30), "Farmacia", "Con refrigerio"],
    )
    return send_file(buf, as_attachment=True, download_name="Plantilla_Patron_Recurrente.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.get("/headcount")
@_analista_requerido
def headcount_form():
    return render_template("cargas_headcount.html", usuario=current_user, resultado=False)


@bp.post("/headcount")
@_analista_requerido
def headcount_submit():
    archivo_maestro = request.files.get("maestro")
    archivo_patron = request.files.get("patron")
    if not archivo_maestro or archivo_maestro.filename == "":
        flash("Tenés que subir el archivo de Maestro Headcount.", "error")
        return redirect(url_for("cargas.headcount_form"))

    propietario = current_user.email

    try:
        m = parsear_maestro(io.BytesIO(archivo_maestro.read()))
        validar_columnas(m, COLUMNAS_MAESTRO_ESPERADAS, archivo_maestro.filename)
    except Exception as e:
        flash(f"Error leyendo el Maestro Headcount: {e}", "error")
        return redirect(url_for("cargas.headcount_form"))

    p = None
    if archivo_patron and archivo_patron.filename:
        try:
            p = parsear_patron(io.BytesIO(archivo_patron.read()))
            validar_columnas(p, COLUMNAS_PATRON_ESPERADAS, archivo_patron.filename)
        except Exception as e:
            flash(f"Error leyendo el Patrón Recurrente: {e}", "error")
            return redirect(url_for("cargas.headcount_form"))

    session = get_session()
    try:
        nuevas, actualizadas, conflictos = [], [], []
        supervisores_propios = {
            per.nombre_completo.strip().upper(): per.dni
            for per in session.query(Persona).filter_by(analista_propietario=propietario, rol="SUPERVISORES").all()
        }

        for _, row in m.iterrows():
            dni = _dni(row["DNI"])
            existente = session.get(Persona, dni)
            if existente and existente.analista_propietario != propietario:
                conflictos.append({
                    "dni": dni, "nombre": _texto(row["Nombre completo"]),
                    "dueño_actual": existente.analista_propietario,
                })
                continue

            sup_texto = _texto(row["Supervisor asignado"])
            supervisor_dni = supervisores_propios.get(sup_texto.strip().upper()) if sup_texto else None
            nombre = _texto(row["Nombre completo"]) or "(sin nombre)"
            rol = _texto(row["Rol"])

            datos = dict(
                nombre_completo=nombre, rol=rol, canal=_texto(row["Canal"]), region=_texto(row["Región"]),
                ciudad=_texto(row["Ciudad / Mercado"]), zona=_texto(row["Zona / Ruta asignada"]),
                supervisor_dni=supervisor_dni, correo=_texto(row["Correo corporativo"]),
                fecha_ingreso=_fecha(row["Fecha de ingreso"]), fecha_baja=_fecha(row["Fecha de baja"]),
                estado=(_texto(row["Estado"]) or "Inactivo"),
                es_reingreso=_si_no_a_bool(row["Es reingreso (Sí/No)"]),
                motivo_baja=_texto(row["Motivo de baja"]), registrado_por=_texto(row["Registrado por"]) or propietario,
                fecha_registro=_fecha(row["Fecha de registro"]) or dt.date.today(),
                analista_propietario=propietario,
            )
            if existente:
                for k, v in datos.items():
                    setattr(existente, k, v)
                actualizadas.append(dni)
            else:
                session.add(Persona(dni=dni, **datos))
                nuevas.append(dni)
            if rol == "SUPERVISORES":
                supervisores_propios[nombre.strip().upper()] = dni

        session.flush()  # las personas nuevas ya deben existir en la sesion antes de tocar Patron (FK)

        n_patron = 0
        if p is not None:
            dnis_propios = set(nuevas) | set(actualizadas)
            dnis_con_patron_nuevo = {d for d in p["DNI"].apply(_dni) if d in dnis_propios}
            for dni in dnis_con_patron_nuevo:
                session.query(PatronRecurrente).filter_by(dni=dni).delete()
            session.flush()
            for _, row in p.iterrows():
                dni = _dni(row["DNI"])
                if dni not in dnis_con_patron_nuevo:
                    continue
                session.add(PatronRecurrente(
                    dni=dni, dia_semana=_texto(row["Día de la semana"]),
                    hora_entrada_prog=_hora(row["Hora entrada programada"]),
                    hora_salida_prog=_hora(row["Hora salida programada"]),
                    canal_dia=_texto(row["Canal del día"]), refrigerio=_texto(row["Refrigerio"]),
                ))
                n_patron += 1

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    flash(
        f"Carga completa: {len(nuevas)} personas nuevas, {len(actualizadas)} actualizadas, "
        f"{len(conflictos)} en conflicto, {n_patron} filas de Patrón agregadas.",
        "ok" if not conflictos else "error",
    )
    return render_template(
        "cargas_headcount.html", usuario=current_user, resultado=True,
        nuevas=nuevas, actualizadas=actualizadas, conflictos=conflictos, n_patron=n_patron,
    )
