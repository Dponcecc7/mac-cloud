# -*- coding: utf-8 -*-
"""Horas decimales -> HH:MM (Davor, 2026-11-06: "cuando restamos horas, sale
8.5 trabajadas, trabaja 08:30"). Solo cambia cómo se muestra."""
import math

import pytest

from horas_semanales import formatear_horas


@pytest.mark.parametrize("valor, esperado", [
    (8.5, "08:30"),
    (8.54, "08:32"),
    (8.48, "08:29"),        # en decimal parece 8 h 48 min; son 8 h 28.8 min
    (3.42, "03:25"),
    (4.04, "04:02"),
    (0, "00:00"),
    (0.0, "00:00"),
    (5.5, "05:30"),
    (120.08, "120:05"),     # semanas/meses pasan de 99 h
    (7.5, "07:30"),
])
def test_formatea_horas_decimales_a_hh_mm(valor, esperado):
    assert formatear_horas(valor) == esperado


def test_diferencia_negativa_lleva_signo_adelante():
    assert formatear_horas(-4.4) == "-04:24"
    assert formatear_horas(-0.5) == "-00:30"


def test_un_negativo_que_redondea_a_cero_no_lleva_signo():
    assert formatear_horas(-0.004) == "00:00"


@pytest.mark.parametrize("vacio", [None, float("nan")])
def test_sin_dato_muestra_guion(vacio):
    assert formatear_horas(vacio) == "—"


def test_acepta_numeros_de_numpy_y_pandas():
    import numpy as np
    assert formatear_horas(np.float64(8.5)) == "08:30"
    assert formatear_horas(np.int64(9)) == "09:00"
    assert not math.isnan(1.0)
