# -*- coding: utf-8 -*-
from patron_recurrente import WD_NORM, canal_para_historial, sin_acentos, valor_patron_para_canal


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


# -- valor_patron_para_canal() / canal_para_historial() (Davor, 2026-09-22,
# turno único que cubre varios canales con el mismo horario -- caso real:
# Maritza, dni 46064286) --

def test_valor_patron_para_canal_un_solo_canal_sin_cambios():
    mapa = {("46064286", "lunes", "Tradicional"): "Con refrigerio"}
    assert valor_patron_para_canal(mapa, "46064286", "lunes", "Tradicional") == "Con refrigerio"


def test_valor_patron_para_canal_compuesto_prueba_cada_canal():
    mapa = {("46064286", "lunes", "Autoservicio"): "Sin refrigerio"}
    # canal_esperado compuesto (como lo arma clasificar_dia() para un turno
    # que cubre 2 canales) -- el mapa solo tiene la fila de Autoservicio,
    # pero igual debe encontrarla.
    assert valor_patron_para_canal(mapa, "46064286", "lunes", "Autoservicio, Tradicional") == "Sin refrigerio"


def test_valor_patron_para_canal_sin_ninguna_fila_devuelve_none():
    assert valor_patron_para_canal({}, "46064286", "lunes", "Autoservicio, Tradicional") is None


def test_canal_para_historial_un_solo_canal_pasa_igual():
    assert canal_para_historial("Tradicional") == "Tradicional"
    assert canal_para_historial(None) is None


def test_canal_para_historial_compuesto_se_vuelve_none():
    # Un turno que cubre varios canales no tiene una identidad única a la
    # que un override de Historial pueda apuntar -- solo ve overrides
    # genéricos (sin canal).
    assert canal_para_historial("Autoservicio, Tradicional") is None
