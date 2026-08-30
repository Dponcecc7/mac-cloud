# -*- coding: utf-8 -*-
from patron_recurrente import WD_NORM, sin_acentos


def test_sin_acentos_normaliza_vocales_y_mayusculas():
    assert sin_acentos("Miércoles") == "miercoles"
    assert sin_acentos("Sábado") == "sabado"
    assert sin_acentos("  Lunes  ") == "lunes"


def test_sin_acentos_vacio_o_none():
    assert sin_acentos("") == ""
    assert sin_acentos(None) == ""


def test_wd_norm_cubre_lunes_a_sabado():
    assert WD_NORM == {
        0: "lunes", 1: "martes", 2: "miercoles", 3: "jueves", 4: "viernes", 5: "sabado",
    }


def test_wd_norm_no_incluye_domingo_a_proposito():
    # Comportamiento existente preservado a proposito al centralizar este
    # modulo (2026-08-30) -- ningun mercaderista tiene patron de domingo
    # hoy en la practica; cambiarlo es decision de negocio, no un bug a
    # corregir en silencio. Si este test empieza a fallar porque alguien
    # agrego domingo, confirmar con Davor antes de aceptarlo.
    assert 6 not in WD_NORM
