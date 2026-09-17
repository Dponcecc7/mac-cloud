# -*- coding: utf-8 -*-
""""Reunión de trabajo" masiva (Davor, 2026-09-17) -- escribe "Estado
reportado"=Asistió (para que el motor asuma el horario programado
completo, igual que el botón "✓ Asistió") MÁS un comentario libre con el
horario de la reunión (para que quede trazable por qué no hay marcación
real ese día). Este test confirma que la combinación funciona: el
comentario no empieza con ninguna palabra clave (FALTA/DESCANSO/VACANTE/
VACACIONES), así que no interfiere con la rama de "Estado reportado" que
ya asume entrada y salida = programada."""
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
    return {COL_ENT: "08:00:00", COL_SAL: "17:00:00", COL_CANAL: "Tradicional"}


def test_reunion_de_trabajo_asume_horario_programado_completo():
    fecha = pd.Timestamp("2026-09-17")
    registro_sup = {
        "Estado reportado": "Asistió",
        "Comentario": "Reunión de trabajo (09:00-13:00) - Capacitación mensual",
    }
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )
    assert fila["Estado"] == "ASISTIÓ A TIEMPO"
    assert fila["Entrada real"] == "08:00:00"
    assert fila["Salida real"] == "17:00:00"
    assert fila["Comentario supervisor"] == "Reunión de trabajo (09:00-13:00) - Capacitación mensual"
