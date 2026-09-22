# -*- coding: utf-8 -*-
"""Turno único que cubre VARIOS canales con el MISMO horario (Davor,
2026-09-22, caso real: Maritza, dni 46064286 -- "los días lunes/miércoles/
viernes ve Tradicional Y Autoservicio", un solo horario compartido, NO 2
horarios distintos). Distinto del caso Chaupi (test_motor_dos_turnos.py):
ahí son 2 turnos con horario PROPIO cada uno; acá es 1 solo turno/jornada
que cubre 2+ canales -- no debe filtrar visitas por canal (usa TODAS,
mismo criterio de "1 solo turno" de siempre) ni duplicar horas (eso se
verifica en horas_semanales.py -- acá solo el motor)."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
from motor_clasificacion import clasificar_dia  # noqa: E402

COL_ENT, COL_SAL, COL_CANAL = "Hora entrada programada", "Hora salida programada", "Canal del día"
FECHA = pd.Timestamp("2026-09-15")  # martes -- lunes/miércoles/viernes real de Maritza, pero cualquier fecha sirve acá


def _pat(canal, entrada, salida):
    return {COL_ENT: entrada, COL_SAL: salida, COL_CANAL: canal}


def _visita(canal, hora_inicio, hora_fin, geofence_ok=True):
    return {
        "nro_documento": "46064286", "fecha_inicio_dt": FECHA, "geofence_ok": geofence_ok,
        "hora_inicio_td": pd.to_timedelta(hora_inicio), "hora_fin_td": pd.to_timedelta(hora_fin),
        "canal_visita": canal,
    }


def test_turno_multicanal_no_filtra_visitas_usa_todas():
    # Maritza visita Tradicional a la mañana y Autoservicio a la tarde --
    # es UNA sola jornada (mismo horario programado para ambos canales),
    # así que entrada/salida real deben abarcar TODO el día, no solo una
    # de las 2 visitas.
    v = pd.DataFrame([
        _visita("Tradicional", "07:05:00", "11:00:00"),
        _visita("Autoservicio", "12:00:00", "16:55:00"),
    ])
    canales_turno = {"Tradicional", "Autoservicio"}

    fila = clasificar_dia(
        "46064286", "Maritza", FECHA, FECHA.weekday(), _pat("Tradicional", "07:00:00", "17:00:00"),
        COL_ENT, COL_SAL, COL_CANAL, v, None, {},
        canales_programados_hoy=canales_turno, canales_turno=canales_turno,
    )
    assert fila["Entrada real"] == "07:05:00"
    assert fila["Salida real"] == "16:55:00"
    assert fila["Canal esperado (Patrón)"] == "Autoservicio, Tradicional"
    assert fila["Trabajó para otro canal"] == "NO"


def test_turno_multicanal_visita_de_un_tercer_canal_se_marca():
    v = pd.DataFrame([
        _visita("Tradicional", "07:05:00", "11:00:00"),
        _visita("Autoservicio", "12:00:00", "16:55:00"),
        _visita("Farmacia", "17:00:00", "17:20:00"),
    ])
    canales_turno = {"Tradicional", "Autoservicio"}

    fila = clasificar_dia(
        "46064286", "Maritza", FECHA, FECHA.weekday(), _pat("Tradicional", "07:00:00", "17:00:00"),
        COL_ENT, COL_SAL, COL_CANAL, v, None, {},
        canales_programados_hoy=canales_turno, canales_turno=canales_turno,
    )
    assert fila["Trabajó para otro canal"] == "SÍ"
    assert "Farmacia" in fila["Canal(es) marcado(s)"]


def test_una_sola_llamada_es_un_solo_grupo_no_hace_falta_pasar_canales_programados_hoy():
    # Si canales_programados_hoy no se pasa (o es igual a canales_turno),
    # el motor debe tratarlo como el único grupo del día -- sin filtrar.
    v = pd.DataFrame([
        _visita("Tradicional", "07:05:00", "11:00:00"),
        _visita("Autoservicio", "12:00:00", "16:55:00"),
    ])
    canales_turno = {"Tradicional", "Autoservicio"}

    fila = clasificar_dia(
        "46064286", "Maritza", FECHA, FECHA.weekday(), _pat("Tradicional", "07:00:00", "17:00:00"),
        COL_ENT, COL_SAL, COL_CANAL, v, None, {}, canales_turno=canales_turno,
    )
    assert fila["Entrada real"] == "07:05:00"
    assert fila["Salida real"] == "16:55:00"
    assert fila["Trabajó para otro canal"] == "NO"
