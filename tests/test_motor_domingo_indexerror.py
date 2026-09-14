# -*- coding: utf-8 -*-
"""Regresión de un bug real encontrado el 2026-09-14: la lista de nombres
de día en clasificar_dia() (la que arma la columna "Día" del resultado)
tenía solo 6 elementos (Lunes..Sábado) -- para domingo (weekday=6),
["Lunes",...,"Sábado"][6] tira IndexError. Ese error queda silenciado por
el try/except de main() ("ADVERTENCIA: no se pudo clasificar... se
omite"), así que NINGÚN domingo se clasificó nunca en toda la historia de
ClasificacionDiaria (0 filas, verificado en Postgres), pese a que el
bloqueo explícito "if weekday == 6: continue" ya se había sacado en una
sesión anterior -- la persona correcta llegaba hasta el final de
clasificar_dia() y recién ahí explotaba."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
from motor_clasificacion import clasificar_dia  # noqa: E402

COL_ENT, COL_SAL, COL_CANAL = "Hora entrada programada", "Hora salida programada", "Canal del día"

VISITAS_VACIA = pd.DataFrame(columns=[
    "nro_documento", "fecha_inicio_dt", "geofence_ok", "hora_inicio_td", "hora_fin_td", "canal_visita",
])


def _pat():
    return {COL_ENT: "07:00:00", COL_SAL: "16:00:00", COL_CANAL: "Autoservicio"}


def test_domingo_no_revienta_y_dia_correcto():
    # 2026-09-13 es un domingo real -- weekday() debe dar 6.
    fecha = pd.Timestamp("2026-09-13")
    assert fecha.weekday() == 6
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, None, {},
    )
    assert fila["Día"] == "Domingo"


def test_los_otros_6_dias_siguen_bien():
    nombres_esperados = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]
    # 2026-09-07 es lunes -- recorre lunes..sábado desde ahí.
    for offset, esperado in enumerate(nombres_esperados):
        fecha = pd.Timestamp("2026-09-07") + pd.Timedelta(days=offset)
        fila = clasificar_dia(
            "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
            COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, None, {},
        )
        assert fila["Día"] == esperado
