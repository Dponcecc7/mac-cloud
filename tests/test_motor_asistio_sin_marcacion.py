# -*- coding: utf-8 -*-
"""Cuando se confirma "Asistió" desde Marcar asistencia (o desde la Power
App del supervisor) sin que llegue ninguna marcación real de la app,
motor_clasificacion.py::clasificar_dia() dejaba entrada_real/salida_real en
None -- horas_semanales.py exige AMBAS no-nulas para calcular horas
trabajadas (con_marcacion = entrada.notna() & salida.notna()), así que un
día realmente trabajado terminaba mostrando 0h si el analista no corregía
la salida a mano (Davor, 2026-09-14: "el analista se olvida colocar la
hora de salida"). Se asume el horario programado como entrada/salida real
en ese caso puntual -- este test construye los argumentos mínimos de
clasificar_dia() a mano (sin Postgres/Excel real) para verificarlo aislado."""
import sys
import os

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
from motor_clasificacion import clasificar_dia  # noqa: E402

COL_ENT, COL_SAL, COL_CANAL = "Hora entrada programada", "Hora salida programada", "Canal del día"

VISITAS_VACIA = pd.DataFrame(columns=[
    "nro_documento", "fecha_inicio_dt", "geofence_ok", "hora_inicio_td", "hora_fin_td", "canal_visita",
])


def _pat(entrada="08:00:00", salida="17:00:00", canal="Tradicional"):
    return {COL_ENT: entrada, COL_SAL: salida, COL_CANAL: canal}


def test_asistio_sin_marcacion_asume_horario_programado():
    fecha = pd.Timestamp("2026-09-14")
    registro_sup = {"Estado reportado": "Asistió"}
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )
    assert fila["Estado"] == "ASISTIÓ A TIEMPO"
    assert fila["Entrada real"] == "08:00:00"
    assert fila["Salida real"] == "17:00:00"
    assert fila["Alerta para analista"] == "SÍ"


def test_asistio_con_marcacion_real_no_se_toca():
    # Si SÍ hay una visita real ese día, el camino de "Estado reportado" ni
    # se evalúa -- este test es la red de seguridad de que el fix nuevo no
    # se mete en el camino normal (real siempre gana).
    fecha = pd.Timestamp("2026-09-14")
    visitas = pd.DataFrame([{
        "nro_documento": "12345678", "fecha_inicio_dt": fecha, "geofence_ok": True,
        "hora_inicio_td": pd.to_timedelta("08:05:00"), "hora_fin_td": pd.to_timedelta("17:10:00"),
        "canal_visita": "Tradicional",
    }])
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, visitas, None, {},
    )
    assert fila["Entrada real"] == "08:05:00"
    assert fila["Salida real"] == "17:10:00"


def test_estado_reportado_falta_no_asume_horario():
    # Un "Estado reportado" distinto de Asistió (ej. Vacante) no debe
    # activar el horario asumido -- solo el caso puntual de Asistió.
    fecha = pd.Timestamp("2026-09-14")
    registro_sup = {"Estado reportado": "Vacante"}
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )
    assert fila["Entrada real"] is None
    assert fila["Salida real"] is None
