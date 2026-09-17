# -*- coding: utf-8 -*-
""""Marcar asistencia" ahora deja escribir la hora de ingreso real al
confirmar "✓ Asistió" (Davor, 2026-09-17: "cuando marcó asistió no me
sale para colocar la hora de ingreso") -- eso llega al motor como
"Hora entrada corregida" en Tabla 3, SIN comentario. Sin el fix de este
archivo, si nunca llega una marcación real de salida, salida_real quedaba
en None para siempre (mismo síntoma que el bug de "Asistió sin marcación"
arreglado el 2026-09-14: horas_semanales.py exige entrada Y salida no
nulas, así que el día quedaba en 0h pese a tener una hora de entrada
real). Ahora se asume la salida programada en ese caso puntual, igual que
ya se hace para la confirmación sin ninguna hora."""
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


def test_entrada_corregida_sin_comentario_asume_salida_programada():
    fecha = pd.Timestamp("2026-09-17")
    registro_sup = {"Hora entrada corregida": "08:10:00"}
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )
    assert fila["Entrada real"] == "08:10:00"
    assert fila["Salida real"] == "17:00:00"
    assert fila["Estado"] == "ASISTIÓ A TIEMPO"
    assert fila["Alerta para analista"] == "SÍ"


def test_entrada_y_salida_corregidas_no_se_toca_la_salida():
    # Si YA viene una salida real corregida, el fallback no debe pisarla.
    fecha = pd.Timestamp("2026-09-17")
    registro_sup = {"Hora entrada corregida": "08:10:00", "Hora salida corregida": "18:30:00"}
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )
    assert fila["Salida real"] == "18:30:00"


def test_entrada_corregida_con_comentario_no_asume_salida():
    # Con un comentario explícito (ej. un "Falta"/"Vacante" que dejó una
    # hora vieja pegada de una corrección anterior), no se asume nada --
    # ver el guard de "FALTA"/"DESCANSO" más arriba en el motor, que ya
    # limpia la hora en ese caso; acá se prueba un comentario que SÍ
    # sobrevive (no empieza con Falta/Descanso) para confirmar que el
    # fallback es estrictamente "sin comentario".
    fecha = pd.Timestamp("2026-09-17")
    registro_sup = {"Hora entrada corregida": "08:10:00", "Comentario": "Recupera 31/08"}
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )
    assert fila["Salida real"] is None
