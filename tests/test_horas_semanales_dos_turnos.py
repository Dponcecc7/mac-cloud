# -*- coding: utf-8 -*-
"""Soporte de 2 turnos/día para Multicanal (Davor, 2026-09-22) --
resumen_por_persona() no debe contar "días de tardanza"/"días de falta"
por FILA sino por FECHA DISTINTA: alguien con 2 turnos el mismo día,
tardanza en ambos, tuvo tardanza UN día, no dos. Las HORAS sí deben sumar
los 2 turnos -- eso no cambia."""
import datetime as dt

import pandas as pd

from horas_semanales import resumen_por_persona

COLUMNAS = [
    "dni", "nombre", "supervisor", "ciudad", "region", "fecha",
    "motivo_falta", "horas_trabajadas", "horas_a_trabajar",
    "_horas_vacante_dia", "_horas_sustento_dia", "_horas_sin_marcacion_dia",
    "es_tardanza", "recupero_dia", "es_salida_temprana",
]


def _fila(dni, fecha, horas_trab, horas_a_trab, es_tardanza=False, es_salida_temprana=False, motivo_falta=None, recupero=False):
    return {
        "dni": dni, "nombre": "Persona de prueba", "supervisor": "Sup", "ciudad": "Arequipa", "region": "Sur",
        "fecha": fecha, "motivo_falta": motivo_falta,
        "horas_trabajadas": horas_trab, "horas_a_trabajar": horas_a_trab,
        "_horas_vacante_dia": 0.0, "_horas_sustento_dia": 0.0, "_horas_sin_marcacion_dia": 0.0,
        "es_tardanza": es_tardanza, "recupero_dia": recupero, "es_salida_temprana": es_salida_temprana,
    }


def test_2_turnos_tardanza_el_mismo_dia_cuenta_como_1_dia_no_2():
    fecha = pd.Timestamp("2026-09-14")
    detalle = pd.DataFrame([
        _fila("73897404", fecha, horas_trab=2.0, horas_a_trab=5.0, es_tardanza=True),
        _fila("73897404", fecha, horas_trab=3.0, horas_a_trab=5.0, es_tardanza=True),
    ], columns=COLUMNAS)

    resumen = resumen_por_persona(detalle)
    assert len(resumen) == 1
    fila = resumen.iloc[0]
    # HORAS de los 2 turnos SÍ suman.
    assert fila["horas_trabajadas"] == 5.0
    assert fila["horas_a_trabajar"] == 10.0
    # pero fue tardanza UN día, no dos.
    assert fila["dias_tardanza"] == 1


def test_1_solo_turno_sigue_contando_normal():
    detalle = pd.DataFrame([
        _fila("11111111", pd.Timestamp("2026-09-14"), horas_trab=8.0, horas_a_trab=8.5, es_tardanza=True),
        _fila("11111111", pd.Timestamp("2026-09-15"), horas_trab=8.5, horas_a_trab=8.5, es_tardanza=False),
    ], columns=COLUMNAS)

    resumen = resumen_por_persona(detalle)
    fila = resumen.iloc[0]
    assert fila["dias_tardanza"] == 1
    assert fila["horas_trabajadas"] == 16.5
