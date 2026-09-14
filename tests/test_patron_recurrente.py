# -*- coding: utf-8 -*-
from patron_recurrente import WD_NORM, sin_acentos


def test_sin_acentos_normaliza_vocales_y_mayusculas():
    assert sin_acentos("Miércoles") == "miercoles"
    assert sin_acentos("Sábado") == "sabado"
    assert sin_acentos("  Lunes  ") == "lunes"


def test_sin_acentos_vacio_o_none():
    assert sin_acentos("") == ""
    assert sin_acentos(None) == ""


def test_wd_norm_cubre_lunes_a_domingo():
    assert WD_NORM == {
        0: "lunes", 1: "martes", 2: "miercoles", 3: "jueves", 4: "viernes", 5: "sabado", 6: "domingo",
    }


def test_wd_norm_incluye_domingo_desde_2026_09_14():
    # Decision de negocio revertida a proposito (Davor, 2026-09-14): el
    # equipo de Autoservicio SI trabaja domingo y debe contar "en todos los
    # analisis, si el analista lo sube en su patron recurrente". Guiado por
    # datos: alguien sin fila de domingo en su propio PatronRecurrente sigue
    # sin tener nada que este mapeo resuelva para ese dia. Si este test
    # empieza a fallar porque alguien saco domingo, confirmar con Davor
    # antes de aceptarlo -- fue un pedido explicito, no un descuido.
    assert 6 in WD_NORM
