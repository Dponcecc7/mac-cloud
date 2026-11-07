# -*- coding: utf-8 -*-
"""Motivo que compensa las horas (Davor, 2026-11-06, Ficha > "Tardanzas y
salidas anticipadas"): un analista marca si el motivo compensa la tardanza,
la salida anticipada o ambas. Con todo lo que aplica ese día marcado, el
faltante deja de restar en Horas programadas vs trabajadas y el día cuenta
como recuperado; con solo una, se descuenta únicamente esa parte. Sin tocar
Horas trabajadas."""
import pandas as pd

from horas_semanales import aplicar_compensaciones, resumen_por_persona

DNI = "11111111"
D1, D2 = pd.Timestamp("2026-11-03"), pd.Timestamp("2026-11-04")

COLUMNAS = [
    "dni", "nombre", "supervisor", "ciudad", "region", "fecha",
    "motivo_falta", "horas_trabajadas", "horas_a_trabajar",
    "_horas_vacante_dia", "_horas_sustento_dia", "_horas_descanso_dia", "_horas_sin_marcacion_dia",
    "es_tardanza", "recupero_dia", "es_salida_temprana", "es_descanso_medico", "_dia_activo",
    "_entrada_esp_td", "_entrada_real_td", "salida_anticipada_min",
]


def _fila(fecha, trabajadas, a_trabajar, entrada_real="08:00:00", salida_min=0, tardanza=False, salida=False):
    """Entrada programada 08:00:00; `entrada_real` y `salida_min` (minutos de
    salida anticipada) definen cuánto de lo que faltó es tardanza y cuánto
    salida."""
    return {
        "dni": DNI, "nombre": "Persona de prueba", "supervisor": "Sup", "ciudad": "Lima", "region": "Norte",
        "fecha": fecha, "motivo_falta": None, "horas_trabajadas": trabajadas, "horas_a_trabajar": a_trabajar,
        "_horas_vacante_dia": 0.0, "_horas_sustento_dia": 0.0, "_horas_descanso_dia": 0.0,
        "_horas_sin_marcacion_dia": 0.0,
        "es_tardanza": tardanza, "es_salida_temprana": salida, "es_descanso_medico": False,
        "recupero_dia": trabajadas is not None and trabajadas >= a_trabajar,
        "_dia_activo": trabajadas is not None,
        "_entrada_esp_td": pd.Timedelta("08:00:00"), "_entrada_real_td": pd.Timedelta(entrada_real),
        "salida_anticipada_min": salida_min,
    }


def _detalle(*filas):
    return pd.DataFrame(list(filas), columns=COLUMNAS)


def _dia_04_11():
    """El caso de la captura de Davor: entra 08:16:48 (16.8 min tarde = 0.28 h),
    sale 4 h 48 min antes (4.8 h). Trabajó 3.42 de 8.5 -> faltan 5.08 h."""
    return _fila(D2, 3.42, 8.5, entrada_real="08:16:48", salida_min=288, tardanza=True, salida=True)


def _resumen(r, compensaciones):
    return resumen_por_persona(aplicar_compensaciones(r, compensaciones)).iloc[0]


def test_compensar_ambos_descuenta_todo_el_faltante():
    fila = _resumen(_detalle(_dia_04_11()), {(DNI, D2.date()): (True, True, "Permiso aprobado")})
    assert fila["horas_trabajadas"] == 3.42           # lo realmente marcado, sin inflar
    assert fila["horas_a_trabajar"] == 3.42           # 8.5 - 5.08
    assert fila["diferencia_h"] == 0.0
    assert fila["dias_compensados"] == 1
    assert fila["tardanzas_recuperadas"] == 1 and fila["salidas_cumplieron"] == 1


def test_compensar_solo_la_tardanza_descuenta_solo_esos_minutos():
    fila = _resumen(_detalle(_dia_04_11()), {(DNI, D2.date()): (True, False, "Tráfico por paro")})
    assert fila["horas_a_trabajar"] == 8.22           # 8.5 - 0.28
    assert fila["dias_compensados"] == 1
    assert fila["tardanzas_recuperadas"] == 0         # sigue sin cubrir sus horas


def test_compensar_solo_la_salida_descuenta_solo_esa_parte():
    fila = _resumen(_detalle(_dia_04_11()), {(DNI, D2.date()): (False, True, "Cita médica")})
    assert fila["horas_a_trabajar"] == 3.7            # 8.5 - 4.8
    assert fila["dias_compensados"] == 1
    assert fila["salidas_cumplieron"] == 0


def test_motivo_sin_compensar_deja_las_horas_como_estaban():
    r = aplicar_compensaciones(_detalle(_dia_04_11()), {(DNI, D2.date()): (False, False, "Se fue sin avisar")})
    assert r.iloc[0]["motivo_compensacion"] == "Se fue sin avisar"
    fila = resumen_por_persona(r).iloc[0]
    assert fila["horas_a_trabajar"] == 8.5
    assert fila["dias_compensados"] == 0


def test_dia_con_un_solo_tipo_compensa_todo_al_marcar_ese_tipo():
    # Solo salió antes (no llegó tarde): marcar la salida ya cubre todo lo
    # que aplica ese día, así que cuenta como compensado por completo.
    dia = _fila(D1, 4.04, 8.5, salida_min=257, salida=True)
    fila = _resumen(_detalle(dia), {(DNI, D1.date()): (False, True, "Permiso")})
    assert fila["horas_a_trabajar"] == 4.04
    assert fila["salidas_cumplieron"] == 1


def test_sin_registro_no_cambia_nada():
    r = aplicar_compensaciones(_detalle(_dia_04_11()), {})
    assert r.iloc[0]["motivo_compensacion"] is None
    fila = resumen_por_persona(r).iloc[0]
    assert fila["horas_a_trabajar"] == 8.5
    assert fila["dias_compensados"] == 0


def test_dia_sin_marcacion_no_tiene_faltante_que_compensar():
    dia = _fila(D1, None, 8.5, tardanza=True)
    r = aplicar_compensaciones(_detalle(dia), {(DNI, D1.date()): (True, True, "Motivo")})
    assert r.iloc[0]["_horas_compensadas_dia"] == 0.0
    assert not r.iloc[0]["recupero_dia"]


def test_dos_turnos_el_mismo_dia_compensan_cada_faltante_y_cuentan_un_dia():
    r = _detalle(_fila(D1, 2.0, 5.0, tardanza=True), _fila(D1, 3.0, 5.0, tardanza=True))
    fila = _resumen(r, {(DNI, D1.date()): (True, False, "Reunión")})
    assert fila["horas_trabajadas"] == 5.0
    assert fila["horas_a_trabajar"] == 5.0            # 10 - (3 + 2)
    assert fila["dias_compensados"] == 1
    assert fila["tardanzas_recuperadas"] == 1


def test_resumen_acepta_detalle_sin_columnas_de_compensacion():
    # Los llamadores viejos (y los tests que arman el DataFrame a mano) no
    # traen estas columnas -- tiene que seguir funcionando.
    fila = resumen_por_persona(_detalle(_fila(D1, 8.0, 8.5, tardanza=True))).iloc[0]
    assert fila["dias_compensados"] == 0
    assert fila["horas_a_trabajar"] == 8.5
