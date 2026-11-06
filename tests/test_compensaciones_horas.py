# -*- coding: utf-8 -*-
"""Motivo que compensa las horas (Davor, 2026-11-06, Ficha > "Tardanzas y
salidas anticipadas"): si un analista confirma que el motivo compensa TODAS
las horas de ese día, el faltante deja de restar en Horas programadas vs
trabajadas y el día cuenta como recuperado. Sin tocar Horas trabajadas."""
import pandas as pd

from horas_semanales import aplicar_compensaciones, resumen_por_persona

DNI = "11111111"
D1, D2 = pd.Timestamp("2026-11-03"), pd.Timestamp("2026-11-04")

COLUMNAS = [
    "dni", "nombre", "supervisor", "ciudad", "region", "fecha",
    "motivo_falta", "horas_trabajadas", "horas_a_trabajar",
    "_horas_vacante_dia", "_horas_sustento_dia", "_horas_descanso_dia", "_horas_sin_marcacion_dia",
    "es_tardanza", "recupero_dia", "es_salida_temprana", "es_descanso_medico", "_dia_activo",
]


def _fila(fecha, trabajadas, a_trabajar, tardanza=False, salida=False):
    trab = trabajadas
    return {
        "dni": DNI, "nombre": "Persona de prueba", "supervisor": "Sup", "ciudad": "Lima", "region": "Norte",
        "fecha": fecha, "motivo_falta": None, "horas_trabajadas": trab, "horas_a_trabajar": a_trabajar,
        "_horas_vacante_dia": 0.0, "_horas_sustento_dia": 0.0, "_horas_descanso_dia": 0.0,
        "_horas_sin_marcacion_dia": 0.0,
        "es_tardanza": tardanza, "es_salida_temprana": salida, "es_descanso_medico": False,
        "recupero_dia": trab is not None and trab >= a_trabajar,
        "_dia_activo": trab is not None,
    }


def _detalle(*filas):
    return pd.DataFrame(list(filas), columns=COLUMNAS)


def test_compensar_descuenta_el_faltante_de_la_base_sin_tocar_horas_trabajadas():
    r = _detalle(_fila(D1, 4.04, 8.5, salida=True), _fila(D2, 8.5, 8.5))
    r = aplicar_compensaciones(r, {(DNI, D1.date()): (True, "Permiso aprobado")})
    fila = resumen_por_persona(r).iloc[0]
    assert fila["horas_trabajadas"] == 12.5          # lo realmente marcado, sin inflar
    assert fila["horas_a_trabajar"] == 12.5          # 17.0 - 4.46 compensadas (redondeo a 1 decimal)
    assert fila["diferencia_h"] == 0.0
    assert fila["pct_cumplimiento_sin_faltas"] == 100.0
    assert fila["dias_compensados"] == 1
    assert fila["salidas_cumplieron"] == 1           # ese día cuenta como cumplido


def test_no_compensa_deja_el_motivo_pero_las_horas_siguen_restando():
    r = _detalle(_fila(D1, 4.04, 8.5, salida=True))
    r = aplicar_compensaciones(r, {(DNI, D1.date()): (False, "Se fue por una cita")})
    assert r.iloc[0]["motivo_compensacion"] == "Se fue por una cita"
    fila = resumen_por_persona(r).iloc[0]
    assert fila["horas_a_trabajar"] == 8.5
    assert fila["dias_compensados"] == 0
    assert fila["salidas_cumplieron"] == 0


def test_sin_registro_no_cambia_nada():
    r = _detalle(_fila(D1, 4.04, 8.5, salida=True))
    r = aplicar_compensaciones(r, {})
    assert r.iloc[0]["motivo_compensacion"] is None
    fila = resumen_por_persona(r).iloc[0]
    assert fila["horas_a_trabajar"] == 8.5
    assert fila["dias_compensados"] == 0


def test_dia_sin_marcacion_no_tiene_faltante_que_compensar():
    r = _detalle(_fila(D1, None, 8.5, tardanza=True))
    r = aplicar_compensaciones(r, {(DNI, D1.date()): (True, "Motivo")})
    assert r.iloc[0]["_horas_compensadas_dia"] == 0.0
    assert not r.iloc[0]["recupero_dia"]


def test_dos_turnos_el_mismo_dia_compensan_cada_faltante_y_cuentan_un_dia():
    r = _detalle(_fila(D1, 2.0, 5.0, tardanza=True), _fila(D1, 3.0, 5.0, tardanza=True))
    r = aplicar_compensaciones(r, {(DNI, D1.date()): (True, "Reunión")})
    fila = resumen_por_persona(r).iloc[0]
    assert fila["horas_trabajadas"] == 5.0
    assert fila["horas_a_trabajar"] == 5.0           # 10 - (3 + 2)
    assert fila["dias_compensados"] == 1
    assert fila["tardanzas_recuperadas"] == 1


def test_resumen_acepta_detalle_sin_columnas_de_compensacion():
    # Los llamadores viejos (y los tests que arman el DataFrame a mano) no
    # traen estas columnas -- tiene que seguir funcionando.
    fila = resumen_por_persona(_detalle(_fila(D1, 8.0, 8.5, tardanza=True))).iloc[0]
    assert fila["dias_compensados"] == 0
    assert fila["horas_a_trabajar"] == 8.5
