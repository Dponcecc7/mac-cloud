# -*- coding: utf-8 -*-
"""Días en estado DESCANSO (médico, regular, etc.) no deben contar como
"horas a trabajar" de la semana -- Davor, 2026-10-06: "los descansos
médicos deben restar las horas a trabajar de la semana" (caso real: 28% de
cumplimiento en una semana con 5 días de licencia médica, porque esos días
seguían sumando 8.5h "debidas" cada uno). Mismo patrón que ya existía para
Vacante/Falta con sustento -- ver horas_semanales.py."""
import pandas as pd

from horas_semanales import resumen_por_persona

COLUMNAS = [
    "dni", "nombre", "supervisor", "ciudad", "region", "fecha",
    "motivo_falta", "horas_trabajadas", "horas_a_trabajar",
    "_horas_vacante_dia", "_horas_sustento_dia", "_horas_descanso_dia", "_horas_sin_marcacion_dia",
    "es_tardanza", "recupero_dia", "es_salida_temprana",
]


def _fila(dni, fecha, horas_trab, horas_a_trab, horas_descanso=0.0, motivo_falta=None):
    return {
        "dni": dni, "nombre": "Persona de prueba", "supervisor": "Sup", "ciudad": "Lima", "region": "Lima",
        "fecha": fecha, "motivo_falta": motivo_falta,
        "horas_trabajadas": horas_trab, "horas_a_trabajar": horas_a_trab,
        "_horas_vacante_dia": 0.0, "_horas_sustento_dia": 0.0, "_horas_descanso_dia": horas_descanso,
        "_horas_sin_marcacion_dia": 0.0,
        "es_tardanza": False, "recupero_dia": False, "es_salida_temprana": False,
    }


def test_semana_con_descanso_medico_no_cuenta_esas_horas():
    # Caso real: 1 día trabajado (8.5h/8.5h) + 5 días de descanso médico
    # (8.5h programadas cada uno, pero NO deben contar como "debidas").
    fechas = pd.date_range("2026-09-21", periods=6, freq="D")
    filas = [_fila("11111111", fechas[0], horas_trab=8.5, horas_a_trab=8.5)]
    filas += [
        _fila("11111111", f, horas_trab=None, horas_a_trab=8.5, horas_descanso=8.5)
        for f in fechas[1:]
    ]
    detalle = pd.DataFrame(filas, columns=COLUMNAS)

    resumen = resumen_por_persona(detalle)
    assert len(resumen) == 1
    fila = resumen.iloc[0]
    # 6 días x 8.5h = 51h programadas, menos 5 días de descanso (42.5h) = 8.5h reales.
    assert fila["horas_a_trabajar"] == 8.5
    assert fila["horas_trabajadas"] == 8.5
    assert fila["pct_cumplimiento"] == 100.0


def test_semana_sin_descanso_no_cambia():
    # Nadie en descanso esta semana -- mismo resultado que antes del fix.
    fecha = pd.Timestamp("2026-09-21")
    detalle = pd.DataFrame([_fila("22222222", fecha, horas_trab=4.0, horas_a_trab=8.5)], columns=COLUMNAS)
    resumen = resumen_por_persona(detalle)
    assert resumen.iloc[0]["horas_a_trabajar"] == 8.5
    assert resumen.iloc[0]["pct_cumplimiento"] == round(4.0 / 8.5 * 100, 1)
