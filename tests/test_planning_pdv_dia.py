# -*- coding: utf-8 -*-
"""Detalle día a día del Planning (Davor, 2026-10-15: "cuando cargue el
planning el mercaderista esta asignado a una ruta... quiero que por día
pueda tener PDV visitado y PDVs fuera de lo programado al día"). Antes
guardar_planning() solo guardaba el TOTAL de días programados por PDV
(PlanningPdv.dias_programados), descartando EN QUÉ días puntuales tocaba
cada uno -- estos tests cubren la extracción de esas fechas
(_fechas_marcadas), su unión al consolidar un CO_LI repetido
(_consolidar_por_pdv), la escritura en PlanningPdvDia (guardar_planning,
contra SQLite en memoria) y la clasificación Cumplido/Pendiente/Fuera de
lo programado (cumplimiento_ruta_dia)."""
import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import create_engine, event

import dimension_models
from dimension_models import Base, Persona, PlanningPdv, PlanningPdvDia
from planning import _consolidar_por_pdv, _fechas_marcadas, cumplimiento_ruta_dia, guardar_planning


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _activar_fk(conexion_dbapi, _):
        conexion_dbapi.execute("PRAGMA foreign_keys=ON")

    dimension_models._engine = engine
    dimension_models._SessionLocal = None
    Base.metadata.create_all(engine)
    yield engine
    dimension_models._engine = None
    dimension_models._SessionLocal = None


def test_fechas_marcadas_extrae_solo_los_dias_con_x():
    df = pd.DataFrame({
        "CO_LI": ["123"],
        "1/10/2026": ["X"],
        "2/10/2026": [None],
        "3/10/2026": ["X"],
    })
    cols_fecha = ["1/10/2026", "2/10/2026", "3/10/2026"]
    fechas = _fechas_marcadas(df, cols_fecha)
    assert fechas.iloc[0] == [dt.date(2026, 10, 1), dt.date(2026, 10, 3)]


def test_fechas_marcadas_sin_columnas_de_fecha_da_listas_vacias():
    df = pd.DataFrame({"CO_LI": ["123", "456"]})
    fechas = _fechas_marcadas(df, [])
    assert list(fechas) == [[], []]


def test_consolidar_por_pdv_une_fechas_de_filas_repetidas_del_mismo_co_li():
    # Mismo CO_LI en 2 filas (2 actividades/semanas distintas del Excel,
    # ver docstring de _consolidar_por_pdv) -- las fechas programadas de
    # ambas deben unirse, no perderse ni duplicarse si coinciden.
    df = pd.DataFrame([
        {
            "co_li": "123", "tipo": "Mayorista", "nombre_pdv": "PDV A", "ciudad": "Lima", "region": "Lima",
            "subcanal": None, "mercado": None, "dni_asignado": "111", "mercaderista_nombre": "Juan",
            "supervisor": "Sup", "dias_programados": 2,
            "fechas_programadas": [dt.date(2026, 10, 1), dt.date(2026, 10, 3)],
        },
        {
            "co_li": "123", "tipo": "Mayorista", "nombre_pdv": "PDV A", "ciudad": "Lima", "region": "Lima",
            "subcanal": None, "mercado": None, "dni_asignado": "111", "mercaderista_nombre": "Juan",
            "supervisor": "Sup", "dias_programados": 2,
            "fechas_programadas": [dt.date(2026, 10, 3), dt.date(2026, 10, 5)],
        },
    ])
    out = _consolidar_por_pdv(df)
    assert len(out) == 1
    assert out.iloc[0]["fechas_programadas"] == [dt.date(2026, 10, 1), dt.date(2026, 10, 3), dt.date(2026, 10, 5)]
    # dias_programados sigue siendo la SUMA de siempre (4), sin tocar --
    # es un concepto distinto, no debe cambiar por este fix.
    assert out.iloc[0]["dias_programados"] == 4


def _plan(dni, co_li, nombre_pdv, fechas, periodo=dt.date(2026, 10, 1)):
    return pd.Series({
        "co_li": co_li, "tipo": "Mayorista", "nombre_pdv": nombre_pdv, "ciudad": "Lima", "region": "Lima",
        "subcanal": None, "mercado": None, "dni_asignado": dni, "mercaderista_nombre": "Juan Perez",
        "supervisor": "Sup Uno", "dias_programados": len(fechas), "fechas_programadas": fechas,
    })


def test_guardar_planning_persiste_planning_pdv_dia(db):
    df = pd.DataFrame([
        _plan("11111111", "100", "PDV Uno", [dt.date(2026, 10, 1), dt.date(2026, 10, 3)]),
        _plan("11111111", "200", "PDV Dos", []),
    ])
    n = guardar_planning(df, dt.date(2026, 10, 1), "tester@x.com")
    assert n == 2

    session = dimension_models.get_session()
    pdv100 = session.query(PlanningPdv).filter_by(co_li="100").first()
    pdv200 = session.query(PlanningPdv).filter_by(co_li="200").first()
    dias100 = session.query(PlanningPdvDia).filter_by(planning_pdv_id=pdv100.id).all()
    dias200 = session.query(PlanningPdvDia).filter_by(planning_pdv_id=pdv200.id).all()
    session.close()

    assert sorted(d.fecha for d in dias100) == [dt.date(2026, 10, 1), dt.date(2026, 10, 3)]
    assert dias200 == []


def test_guardar_planning_reemplaza_el_mes_en_cascada(db):
    # Re-subir el mismo mes borra el PlanningPdv viejo -- la fila de
    # PlanningPdvDia asociada debe caer con el ondelete=CASCADE, no quedar
    # huérfana apuntando a un id que ya no existe.
    df1 = pd.DataFrame([_plan("11111111", "100", "PDV Uno", [dt.date(2026, 10, 1)])])
    guardar_planning(df1, dt.date(2026, 10, 1), "tester@x.com")

    df2 = pd.DataFrame([_plan("22222222", "100", "PDV Uno Corregido", [dt.date(2026, 10, 5)])])
    guardar_planning(df2, dt.date(2026, 10, 1), "tester@x.com")

    session = dimension_models.get_session()
    total_dia = session.query(PlanningPdvDia).count()
    pdv = session.query(PlanningPdv).filter_by(co_li="100").first()
    dias = session.query(PlanningPdvDia).filter_by(planning_pdv_id=pdv.id).all()
    session.close()
    assert total_dia == 1  # no quedó la fila vieja del primer upload
    assert dias[0].fecha == dt.date(2026, 10, 5)


def test_cumplimiento_ruta_dia_clasifica_los_4_casos(db):
    # Cumplido: programado hoy y visitado. Pendiente: programado hoy, sin
    # visitar. Fuera de lo programado (otro día): visitó un PDV de su
    # propia cartera, pero programado para OTRO día. Fuera de planning:
    # visitó un PDV que ni está en su Planning del mes.
    df = pd.DataFrame([
        _plan("11111111", "100", "PDV Cumplido", [dt.date(2026, 10, 10)]),
        _plan("11111111", "200", "PDV Pendiente", [dt.date(2026, 10, 10)]),
        _plan("11111111", "300", "PDV Otro Dia", [dt.date(2026, 10, 15)]),
    ])
    guardar_planning(df, dt.date(2026, 10, 1), "tester@x.com")

    session = dimension_models.get_session()
    planning_filtrado = session.query(PlanningPdv).all()
    session.expunge_all()
    session.close()

    dia = dt.date(2026, 10, 10)
    v_dia = pd.DataFrame([
        {
            "dni": "11111111", "nombre": "Juan Perez", "ciudad": "Lima", "supervisor": "Sup Uno",
            "punto_venta_id": "100", "punto_venta": "PDV Cumplido", "hora_inicio": "09:00:00",
        },
        {
            "dni": "11111111", "nombre": "Juan Perez", "ciudad": "Lima", "supervisor": "Sup Uno",
            "punto_venta_id": "300", "punto_venta": "PDV Otro Dia", "hora_inicio": "10:00:00",
        },
        {
            "dni": "11111111", "nombre": "Juan Perez", "ciudad": "Lima", "supervisor": "Sup Uno",
            "punto_venta_id": "999", "punto_venta": "PDV Fuera De Todo", "hora_inicio": "11:00:00",
        },
    ])

    filas = cumplimiento_ruta_dia(dia, planning_filtrado, v_dia)
    por_co_li = {f["co_li"]: f["estado"] for f in filas}
    assert por_co_li["100"] == "Cumplido"
    assert por_co_li["200"] == "Pendiente"
    assert por_co_li["300"] == "Fuera de lo programado (otro día de su ruta)"
    assert por_co_li["999"] == "Fuera de planning"


def test_cumplimiento_ruta_dia_sin_planning_pdv_dia_no_rompe(db):
    # Planning cargado ANTES de este cambio (sin filas en PlanningPdvDia) --
    # no debe reventar, solo no marcar a nadie como "programado hoy".
    df = pd.DataFrame([_plan("11111111", "100", "PDV Sin Detalle Diario", [])])
    guardar_planning(df, dt.date(2026, 10, 1), "tester@x.com")

    session = dimension_models.get_session()
    planning_filtrado = session.query(PlanningPdv).all()
    session.close()

    filas = cumplimiento_ruta_dia(dt.date(2026, 10, 10), planning_filtrado, pd.DataFrame())
    assert filas == []
