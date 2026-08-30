# -*- coding: utf-8 -*-
"""valor_efectivo() toma el indice `idx` como parametro explicito (el mismo
formato que arma cargar_historial(), pero sin necesidad de llamar a esa
funcion ni tocar Postgres para probarlo) -- {(dni, campo_lower): [(fecha_desde, fecha_hasta, valor, dia_semana), ...]}."""
import datetime as dt

import pandas as pd

from historial_cambios import valor_efectivo


def test_sin_override_devuelve_el_default():
    idx = {}
    assert valor_efectivo(idx, "123", "Zona", dt.date(2026, 8, 1), "Zona Original") == "Zona Original"


def test_override_vigente_dentro_del_rango_de_fechas():
    idx = {("123", "zona"): [(dt.date(2026, 8, 1), dt.date(2026, 8, 31), "Zona Nueva", pd.NA)]}
    assert valor_efectivo(idx, "123", "Zona", dt.date(2026, 8, 15), "Zona Original") == "Zona Nueva"


def test_override_fuera_del_rango_no_aplica():
    idx = {("123", "zona"): [(dt.date(2026, 8, 1), dt.date(2026, 8, 31), "Zona Nueva", pd.NA)]}
    assert valor_efectivo(idx, "123", "Zona", dt.date(2026, 9, 1), "Zona Original") == "Zona Original"


def test_override_sin_fecha_hasta_sigue_vigente_indefinidamente():
    idx = {("123", "zona"): [(dt.date(2026, 8, 1), pd.NA, "Zona Nueva", pd.NA)]}
    assert valor_efectivo(idx, "123", "Zona", dt.date(2027, 1, 1), "Zona Original") == "Zona Nueva"


def test_override_con_dia_de_la_semana_solo_aplica_ese_dia():
    # weekday() de un martes es 1.
    martes = dt.date(2026, 8, 4)
    lunes = dt.date(2026, 8, 3)
    idx = {("123", "refrigerio"): [(dt.date(2026, 8, 1), None, "Sin refrigerio", 1)]}
    assert valor_efectivo(idx, "123", "Refrigerio", martes, "Con refrigerio") == "Sin refrigerio"
    assert valor_efectivo(idx, "123", "Refrigerio", lunes, "Con refrigerio") == "Con refrigerio"


def test_dia_semana_no_reconocido_nunca_esta_vigente():
    # Regresion real (Davor, 2026-08-25/26): un dia_semana no reconocido se
    # guarda como -1, que nunca matchea date.weekday() (0-6) -- antes caia
    # en None y quedaba vigente TODOS los dias por error.
    idx = {("123", "zona"): [(dt.date(2026, 8, 1), None, "Zona Nueva", -1)]}
    assert valor_efectivo(idx, "123", "Zona", dt.date(2026, 8, 15), "Zona Original") == "Zona Original"


def test_toma_el_mas_reciente_cuando_hay_varios_vigentes():
    idx = {("123", "zona"): [
        (dt.date(2026, 1, 1), None, "Zona Vieja", pd.NA),
        (dt.date(2026, 7, 1), None, "Zona Reciente", pd.NA),
    ]}
    assert valor_efectivo(idx, "123", "Zona", dt.date(2026, 8, 1), "Zona Original") == "Zona Reciente"


def test_hora_valida_hh_mm_se_normaliza_con_segundos():
    idx = {("123", "hora entrada programada"): [(dt.date(2026, 8, 1), None, "09:15", pd.NA)]}
    assert valor_efectivo(idx, "123", "Hora entrada programada", dt.date(2026, 8, 10), "08:00:00") == "09:15:00"


def test_hora_invalida_cae_al_default_en_vez_de_tumbar_el_motor(capsys):
    # Bug real (apagon 2026-08-25/26): un valor de hora invalido no
    # rescatable no debe propagarse -- se ignora y avisa por consola.
    idx = {("123", "hora salida programada"): [(dt.date(2026, 8, 1), None, "no es una hora", pd.NA)]}
    resultado = valor_efectivo(idx, "123", "Hora salida programada", dt.date(2026, 8, 10), "17:30:00")
    assert resultado == "17:30:00"
    assert "ADVERTENCIA" in capsys.readouterr().out


def test_fecha_none_devuelve_el_default_sin_reventar():
    idx = {("123", "zona"): [(dt.date(2026, 8, 1), None, "Zona Nueva", pd.NA)]}
    assert valor_efectivo(idx, "123", "Zona", None, "Zona Original") == "Zona Original"


def test_dni_distinto_no_matchea():
    idx = {("123", "zona"): [(dt.date(2026, 8, 1), None, "Zona Nueva", pd.NA)]}
    assert valor_efectivo(idx, "999", "Zona", dt.date(2026, 8, 10), "Zona Original") == "Zona Original"
