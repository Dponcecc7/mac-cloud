# -*- coding: utf-8 -*-
"""Registrar/editar un rango de vacaciones de una sola vez (Davor,
2026-09-14) en vez de marcar "Falta - Vacaciones" día por día -- ver
asistencia.py::_escribir_rango_vacaciones()/_validar_rango_vacaciones()/
_diff_rango_vacaciones(), usadas por marcar()/guardar()/vacaciones_registrar()/
vacaciones_editar(). Estas dos son funciones puras (sin Postgres/Graph), así
que se prueban directo -- el resto del flujo (escritura real a Tabla 3) se
verificó a mano con test_client() contra un DNI de prueba, igual que el
resto de asistencia.py (sin cobertura de integración en este repo todavía)."""
import datetime as dt

from asistencia import _diff_rango_vacaciones, _validar_rango_vacaciones


def test_validar_rango_ok():
    inicio, fin, error = _validar_rango_vacaciones("2026-09-15", "2026-09-30")
    assert error is None
    assert inicio == dt.date(2026, 9, 15)
    assert fin == dt.date(2026, 9, 30)


def test_validar_rango_faltan_fechas():
    _, _, error = _validar_rango_vacaciones("", "2026-09-30")
    assert error is not None
    _, _, error = _validar_rango_vacaciones("2026-09-15", "")
    assert error is not None


def test_validar_rango_fecha_invalida():
    _, _, error = _validar_rango_vacaciones("no-es-una-fecha", "2026-09-30")
    assert error is not None


def test_validar_rango_fin_antes_de_inicio():
    _, _, error = _validar_rango_vacaciones("2026-09-30", "2026-09-15")
    assert error is not None


def test_diff_rango_extender_hacia_adelante():
    # Rango viejo 15-20, nuevo 15-25 -- se agregan 21..25, no se quita nada.
    agregadas, quitadas = _diff_rango_vacaciones(
        dt.date(2026, 9, 15), dt.date(2026, 9, 20),
        dt.date(2026, 9, 15), dt.date(2026, 9, 25),
    )
    assert agregadas == [dt.date(2026, 9, d) for d in range(21, 26)]
    assert quitadas == []


def test_diff_rango_acortar():
    # Rango viejo 15-25, nuevo 15-20 -- se quitan 21..25 (hay que "borrar"
    # esos días en Tabla 3, MARCADOR_BORRADO), no se agrega nada.
    agregadas, quitadas = _diff_rango_vacaciones(
        dt.date(2026, 9, 15), dt.date(2026, 9, 25),
        dt.date(2026, 9, 15), dt.date(2026, 9, 20),
    )
    assert agregadas == []
    assert quitadas == [dt.date(2026, 9, d) for d in range(21, 26)]


def test_diff_rango_correr_hacia_adelante():
    # Se corren las dos puntas -- vieja 10-15, nueva 12-18: se agrega 16-18,
    # se quita 10-11.
    agregadas, quitadas = _diff_rango_vacaciones(
        dt.date(2026, 9, 10), dt.date(2026, 9, 15),
        dt.date(2026, 9, 12), dt.date(2026, 9, 18),
    )
    assert agregadas == [dt.date(2026, 9, d) for d in (16, 17, 18)]
    assert quitadas == [dt.date(2026, 9, d) for d in (10, 11)]


def test_diff_rango_sin_cambios():
    agregadas, quitadas = _diff_rango_vacaciones(
        dt.date(2026, 9, 15), dt.date(2026, 9, 20),
        dt.date(2026, 9, 15), dt.date(2026, 9, 20),
    )
    assert agregadas == [] and quitadas == []
