# -*- coding: utf-8 -*-
"""Regresion del bug real encontrado a mano el 2026-08-29: un match de
substring "ALM" para excluir Almacenes de "Visita larga" tambien
matcheaba "ALMANZA" (apellido real que aparece en nombres de PDV). El fix
fue una regex que exige un punto despues de "ALM" (con espacios
opcionales) -- este test fija ese comportamiento para que no se rompa sin
querer si alguien "simplifica" el patron mas adelante."""
import pandas as pd

from cobertura import PUNTOS_EXCLUIDOS, PUNTOS_NO_VISITA


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


def _filtrar_no_visita(nombres_pdv):
    # Misma máscara que usa _cargar_visitas() (cobertura.py) para descartar
    # filas de PUNTOS_NO_VISITA ANTES de que lleguen a cualquier
    # consumidor -- Davor, 2026-10-16: "REFRIGERIO... no consideres como
    # visitados en toda la MAC... tampoco que sume en los indicadores de
    # cobertura" (PDV ficticio que usan los mercaderistas para marcar su
    # hora de almuerzo, punto_venta_id="1" real en Postgres).
    v = pd.DataFrame({"punto_venta": nombres_pdv})
    return v[~v["punto_venta"].astype(str).str.strip().str.upper().isin(PUNTOS_NO_VISITA)]["punto_venta"].tolist()


def test_refrigerio_se_descarta_de_cargar_visitas():
    quedan = _filtrar_no_visita(["REFRIGERIO", "refrigerio", " Refrigerio ", "BODEGA SAN MARTIN", "FARMACIA CENTRAL"])
    assert quedan == ["BODEGA SAN MARTIN", "FARMACIA CENTRAL"]
