# -*- coding: utf-8 -*-
"""Regresion del bug real encontrado a mano el 2026-08-30 (Jose Quiñonez,
DNI 44965255): quedo dado de baja el mismo dia que su ultima fila de
clasificacion decia VACACIONES -- como una persona Inactiva nunca vuelve a
tener una fila de clasificacion despues, calcular_viajes_vacaciones() nunca
encontraba un "regreso" y el viaje quedaba "En curso" para siempre en el
Dashboard, aunque la persona ya no trabaje mas ahi.

Usa un FakeSession en vez de Postgres real -- .query(Modelo.columna) se
resuelve por identidad de la columna, no por SQL real."""
import datetime as dt

import pandas as pd

from dimension_models import Feriado, Persona
from fact_models import ClasificacionDiaria
from vacaciones import calcular_viajes_vacaciones


class _FakeQuery:
    def __init__(self, resultado):
        self._resultado = resultado

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._resultado


class _FakeSession:
    def __init__(self, feriados=None, filas_futuras=None, inactivos=None):
        self._feriados = feriados or []
        self._filas_futuras = filas_futuras or []
        self._inactivos = inactivos or []

    def query(self, *entidades):
        primera = entidades[0]
        if primera is Feriado.fecha:
            return _FakeQuery([(f,) for f in self._feriados])
        if primera is ClasificacionDiaria.dni:
            return _FakeQuery(self._filas_futuras)
        if primera is Persona.dni:
            return _FakeQuery([(dni,) for dni in self._inactivos])
        raise AssertionError(f"query no esperado en el test: {entidades}")


def _df(filas):
    """filas: lista de (dni, nombre, fecha_str)."""
    return pd.DataFrame(
        [(dni, nombre, pd.Timestamp(fecha), "VACACIONES") for dni, nombre, fecha in filas],
        columns=["dni", "nombre", "fecha", "estado_base"],
    )


def test_persona_inactiva_sin_fila_futura_no_aparece_en_curso():
    r = _df([("123", "Jose Quinones", "2026-08-17")])
    session = _FakeSession(filas_futuras=[], inactivos=["123"])
    viajes = calcular_viajes_vacaciones(session, r, hasta=dt.date(2026, 8, 30))
    assert viajes == []


def test_persona_activa_sin_fila_futura_si_queda_en_curso():
    # Comportamiento previo, preservado: alguien que sigue activo y
    # todavia no volvio SI debe verse como "En curso" (dias=None).
    r = _df([("456", "Alguien Activo", "2026-08-26")])
    session = _FakeSession(filas_futuras=[], inactivos=[])
    viajes = calcular_viajes_vacaciones(session, r, hasta=dt.date(2026, 8, 30))
    assert len(viajes) == 1
    assert viajes[0]["dias"] is None
    assert viajes[0]["regreso"] is None


def test_persona_inactiva_con_regreso_real_no_se_excluye():
    # El fix solo debe sacar los que NUNCA cierran -- si el viaje ya tiene
    # una fecha de regreso real, se muestra igual aunque hoy este inactivo.
    r = _df([("789", "Alguien Que Volvio", "2026-08-10")])
    session = _FakeSession(
        filas_futuras=[("789", dt.date(2026, 8, 15), "ASISTIÓ A TIEMPO")],
        inactivos=["789"],
    )
    viajes = calcular_viajes_vacaciones(session, r, hasta=dt.date(2026, 8, 30))
    assert len(viajes) == 1
    assert viajes[0]["dias"] == 5
    assert viajes[0]["regreso"] == "15/08"


def test_dias_no_habiles_seguidos_no_cortan_el_mismo_viaje():
    # Un domingo/feriado sin fila VACACIONES en medio sigue siendo el
    # mismo viaje (el motor no genera fila esos dias) -- 2026-08-16 es
    # domingo.
    r = _df([
        ("111", "Persona Puente", "2026-08-15"),
        ("111", "Persona Puente", "2026-08-17"),
    ])
    session = _FakeSession(filas_futuras=[("111", dt.date(2026, 8, 20), "ASISTIÓ A TIEMPO")], inactivos=[])
    viajes = calcular_viajes_vacaciones(session, r, hasta=dt.date(2026, 8, 30))
    assert len(viajes) == 1
    assert viajes[0]["inicio"] == "15/08"
    assert viajes[0]["regreso"] == "20/08"
