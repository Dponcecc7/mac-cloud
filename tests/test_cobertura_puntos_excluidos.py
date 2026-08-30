# -*- coding: utf-8 -*-
"""Regresion del bug real encontrado a mano el 2026-08-29: un match de
substring "ALM" para excluir Almacenes de "Visita larga" tambien
matcheaba "ALMANZA" (apellido real que aparece en nombres de PDV). El fix
fue una regex que exige un punto despues de "ALM" (con espacios
opcionales) -- este test fija ese comportamiento para que no se rompa sin
querer si alguien "simplifica" el patron mas adelante."""
import pandas as pd

from cobertura import PUNTOS_EXCLUIDOS


def _excluido(nombre_pdv):
    patron = "|".join(PUNTOS_EXCLUIDOS)
    serie = pd.Series([nombre_pdv]).astype(str).str.upper()
    return bool(serie.str.contains(patron, na=False).iloc[0])


def test_almacen_con_punto_se_excluye():
    assert _excluido("ALM. 10 CHICLAYO")
    assert _excluido("ALM . 2.COLLIQUE")
    assert _excluido("ALM. LINCE")


def test_almanza_no_se_excluye():
    # El caso real que rompia con un match de substring "ALM" sin el
    # punto: "ALMANZA" es un apellido, no un almacen.
    assert not _excluido("23438 - JUAN ALMANZA")
    assert not _excluido("BODEGA ALMANZA")


def test_ubicaciones_administrativas_se_excluyen():
    for nombre in ("AMOF", "OVERALL", "PUNTO CENSO", "PUNTO_ANALISIS", "PUNTO ANALISIS"):
        assert _excluido(nombre)


def test_pdv_normal_no_se_excluye():
    assert not _excluido("BODEGA SAN MARTIN")
    assert not _excluido("FARMACIA CENTRAL")
