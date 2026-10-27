# -*- coding: utf-8 -*-
"""
Reemplazo de la lectura de 1_Maestro_Headcount.xlsx / 2A_Patron_Recurrente.xlsx
/ 5_Feriados_2026.xlsx para los scripts del pipeline -- Davor, 2026-10-27:
"todos los python deberían consultar solo a Postgres". Esos 3 Excel son
exportados DESDE Postgres por exportar_dimensiones.py (personas,
patron_recurrente, feriados) -- el pipeline los volvía a leer de vuelta en
vez de consultar directo, mismo tipo de rodeo que tenía Tabla 3 antes de
hoy.

Cada función de acá devuelve EXACTAMENTE las mismas columnas (mismo nombre,
mismo orden) que la hoja de Excel correspondiente -- para que el código que
ya consume esas columnas (filtros, renames, etc. en motor_clasificacion.py y
el resto) no necesite cambiar, solo el call-site que hoy hace leer_excel(...).

Reusa _nombre_supervisor()/NOMBRE_CORTO de exportar_dimensiones.py (no
duplica esa lógica) para que "Supervisor asignado" salga idéntico a lo que
ya exporta ese script.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import Feriado, Persona, PatronRecurrente, get_session  # noqa: E402
from exportar_dimensiones import DIAS_ORDEN, _nombre_supervisor  # noqa: E402

COLUMNAS_MAESTRO = [
    "DNI", "Nombre completo", "Fecha de ingreso", "Fecha de baja", "Estado",
    "Reemplaza_a (DNI)", "Es reingreso (Sí/No)", "Rol", "Canal", "Región",
    "Ciudad / Mercado", "Zona / Ruta asignada", "Supervisor asignado",
    "Correo corporativo", "Motivo de baja", "Dado de baja por", "Registrado por", "Fecha de registro",
]

COLUMNAS_PATRON = ["DNI", "Día de la semana", "Hora entrada programada", "Hora salida programada", "Canal del día", "Refrigerio"]

COLUMNAS_FERIADOS = ["Fecha", "Día de la semana", "Motivo", "Feriado nacional oficial"]


def cargar_maestro_headcount():
    """Mismas filas/columnas que exportar_dimensiones.py::exportar_maestro()
    -- TODAS las personas (Activo/Vacante/Inactivo), sin filtrar; cada
    llamador ya filtra lo que necesita (igual que hacía leyendo el Excel)."""
    session = get_session()
    try:
        personas = session.query(Persona).order_by(Persona.dni).all()
        personas_por_dni = {p.dni: p for p in personas}
        filas = [{
            "DNI": p.dni, "Nombre completo": p.nombre_completo,
            "Fecha de ingreso": p.fecha_ingreso, "Fecha de baja": p.fecha_baja, "Estado": p.estado,
            "Reemplaza_a (DNI)": p.reemplaza_a_dni, "Es reingreso (Sí/No)": "Sí" if p.es_reingreso else "No",
            "Rol": p.rol, "Canal": p.canal, "Región": p.region, "Ciudad / Mercado": p.ciudad,
            "Zona / Ruta asignada": p.zona, "Supervisor asignado": _nombre_supervisor(personas_por_dni, p.supervisor_dni),
            "Correo corporativo": p.correo, "Motivo de baja": p.motivo_baja,
            "Dado de baja por": p.dado_de_baja_por, "Registrado por": p.registrado_por,
            "Fecha de registro": p.fecha_registro,
        } for p in personas]
    finally:
        session.close()
    return pd.DataFrame(filas, columns=COLUMNAS_MAESTRO)


def cargar_patron_recurrente():
    """Mismas filas/columnas que exportar_dimensiones.py::exportar_patron()."""
    session = get_session()
    try:
        orden = {d: i for i, d in enumerate(DIAS_ORDEN)}
        registros = session.query(PatronRecurrente).all()
        registros.sort(key=lambda r: (r.dni, orden.get(r.dia_semana, 99)))
        filas = [{
            "DNI": r.dni, "Día de la semana": r.dia_semana,
            "Hora entrada programada": r.hora_entrada_prog, "Hora salida programada": r.hora_salida_prog,
            "Canal del día": r.canal_dia, "Refrigerio": r.refrigerio,
        } for r in registros]
    finally:
        session.close()
    return pd.DataFrame(filas, columns=COLUMNAS_PATRON)


def cargar_feriados():
    """Mismas filas/columnas que exportar_dimensiones.py::exportar_feriados()."""
    session = get_session()
    try:
        filas = [{
            "Fecha": f.fecha, "Día de la semana": f.dia_semana, "Motivo": f.motivo,
            "Feriado nacional oficial": "Sí" if f.feriado_nacional else "No",
        } for f in session.query(Feriado).order_by(Feriado.fecha).all()]
    finally:
        session.close()
    return pd.DataFrame(filas, columns=COLUMNAS_FERIADOS)
