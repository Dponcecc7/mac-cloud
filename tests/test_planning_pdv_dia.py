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
from planning import (
    _consolidar_por_pdv, _fechas_marcadas, cumplimiento_ruta_dia, guardar_planning,
    personal_activo, planning_del_periodo, reasignar_mercaderista, valores_filtrables,
)


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
            "fecha_inicio": pd.Timestamp(2026, 10, 10),
        },
        {
            "dni": "11111111", "nombre": "Juan Perez", "ciudad": "Lima", "supervisor": "Sup Uno",
            "punto_venta_id": "300", "punto_venta": "PDV Otro Dia", "hora_inicio": "10:00:00",
            "fecha_inicio": pd.Timestamp(2026, 10, 10),
        },
        {
            "dni": "11111111", "nombre": "Juan Perez", "ciudad": "Lima", "supervisor": "Sup Uno",
            "punto_venta_id": "999", "punto_venta": "PDV Fuera De Todo", "hora_inicio": "11:00:00",
            "fecha_inicio": pd.Timestamp(2026, 10, 10),
        },
    ])

    filas = cumplimiento_ruta_dia(dia, dia, planning_filtrado, v_dia)
    por_co_li = {f["co_li"]: f["estado"] for f in filas}
    assert por_co_li["100"] == "Cumplido"
    assert por_co_li["200"] == "Pendiente"
    assert por_co_li["300"] == "Fuera de lo programado (otro día de su ruta)"
    assert por_co_li["999"] == "Fuera de planning"
    assert all(f["fecha"] == dia for f in filas)


def test_cumplimiento_ruta_dia_excluye_visitas_de_dni_fuera_del_filtro(db):
    # Davor, 2026-10-15: "los filtros superiores no afectan esta tabla" --
    # si region/ciudad/supervisor ya dejaron a un mercaderista AFUERA de
    # planning_filtrado, sus visitas de ese día no deben aparecer (ni como
    # Cumplido ni como "Fuera de planning"): el panel tiene que achicarse
    # igual que las demás tablas de la página cuando se filtra.
    df = pd.DataFrame([_plan("11111111", "100", "PDV Dentro Del Filtro", [dt.date(2026, 10, 10)])])
    guardar_planning(df, dt.date(2026, 10, 1), "tester@x.com")

    session = dimension_models.get_session()
    planning_filtrado = session.query(PlanningPdv).all()  # ya "filtrado": solo el DNI 11111111
    session.expunge_all()
    session.close()

    dia = dt.date(2026, 10, 10)
    v_dia = pd.DataFrame([
        {
            "dni": "11111111", "nombre": "Juan Perez", "ciudad": "Lima", "supervisor": "Sup Uno",
            "punto_venta_id": "100", "punto_venta": "PDV Dentro Del Filtro", "hora_inicio": "09:00:00",
            "fecha_inicio": pd.Timestamp(2026, 10, 10),
        },
        {
            "dni": "22222222", "nombre": "Otro Mercaderista", "ciudad": "Lima", "supervisor": "Sup Dos",
            "punto_venta_id": "500", "punto_venta": "PDV De Otro Supervisor", "hora_inicio": "10:00:00",
            "fecha_inicio": pd.Timestamp(2026, 10, 10),
        },
    ])

    filas = cumplimiento_ruta_dia(dia, dia, planning_filtrado, v_dia)
    dnis_en_resultado = {f["dni"] for f in filas}
    assert dnis_en_resultado == {"11111111"}


def test_cumplimiento_ruta_dia_sin_planning_pdv_dia_no_rompe(db):
    # Planning cargado ANTES de este cambio (sin filas en PlanningPdvDia) --
    # no debe reventar, solo no marcar a nadie como "programado hoy".
    df = pd.DataFrame([_plan("11111111", "100", "PDV Sin Detalle Diario", [])])
    guardar_planning(df, dt.date(2026, 10, 1), "tester@x.com")

    session = dimension_models.get_session()
    planning_filtrado = session.query(PlanningPdv).all()
    session.close()

    filas = cumplimiento_ruta_dia(dt.date(2026, 10, 10), dt.date(2026, 10, 10), planning_filtrado, pd.DataFrame())
    assert filas == []


def test_cumplimiento_ruta_dia_rango_de_varios_dias_etiqueta_la_fecha(db):
    # Davor, 2026-10-16: "adiciona una columna que diga Fecha, por si deseo
    # descargarme visitas de varios dias" -- desde != hasta debe devolver
    # una fila por (fecha, dni, co_li), no colapsar todo el rango en una.
    df = pd.DataFrame([
        _plan("11111111", "100", "PDV Lunes", [dt.date(2026, 10, 12)]),
        _plan("11111111", "200", "PDV Martes", [dt.date(2026, 10, 13)]),
    ])
    guardar_planning(df, dt.date(2026, 10, 1), "tester@x.com")

    session = dimension_models.get_session()
    planning_filtrado = session.query(PlanningPdv).all()
    session.expunge_all()
    session.close()

    v_rango = pd.DataFrame([
        {
            "dni": "11111111", "nombre": "Juan Perez", "ciudad": "Lima", "supervisor": "Sup Uno",
            "punto_venta_id": "100", "punto_venta": "PDV Lunes", "hora_inicio": "09:00:00",
            "fecha_inicio": pd.Timestamp(2026, 10, 12),
        },
    ])

    filas = cumplimiento_ruta_dia(dt.date(2026, 10, 12), dt.date(2026, 10, 13), planning_filtrado, v_rango)
    por_fecha_co_li = {(f["fecha"], f["co_li"]): f["estado"] for f in filas}
    assert por_fecha_co_li[(dt.date(2026, 10, 12), "100")] == "Cumplido"
    assert por_fecha_co_li[(dt.date(2026, 10, 13), "200")] == "Pendiente"
    assert len(filas) == 2


def test_valores_filtrables_incluye_mercados_ya_limpios(db):
    # Davor, 2026-10-15: "adiciona también como filtro, nombre del mercado"
    # -- valores_filtrables() debe ofrecer el nombre YA sin la palabra
    # "mercado" (igual que se muestra en pantalla), no el texto crudo del
    # Excel.
    fila1 = _plan("11111111", "100", "PDV Uno", [])
    fila1["mercado"] = "Mercado Central"
    fila2 = _plan("22222222", "200", "PDV Dos", [])
    fila2["mercado"] = None
    guardar_planning(pd.DataFrame([fila1, fila2]), dt.date(2026, 10, 1), "tester@x.com")

    _, _, _, mercados = valores_filtrables(dt.date(2026, 10, 1))
    assert mercados == ["Central"]


def test_planning_del_periodo_filtra_por_mercado_multiple(db):
    fila_central = _plan("11111111", "100", "PDV Central", [])
    fila_central["mercado"] = "Mercado Central"
    fila_norte = _plan("22222222", "200", "PDV Norte", [])
    fila_norte["mercado"] = "Mercado Norte"
    fila_sin_mercado = _plan("33333333", "300", "PDV Sin Mercado", [])
    fila_sin_mercado["mercado"] = None
    guardar_planning(pd.DataFrame([fila_central, fila_norte, fila_sin_mercado]), dt.date(2026, 10, 1), "tester@x.com")

    sin_filtro = planning_del_periodo(dt.date(2026, 10, 1))
    assert {p.co_li for p in sin_filtro} == {"100", "200", "300"}

    con_filtro = planning_del_periodo(dt.date(2026, 10, 1), mercado_filtro=["Central", "Norte"])
    assert {p.co_li for p in con_filtro} == {"100", "200"}


def test_personal_activo_solo_devuelve_personas_con_estado_activo(db):
    session = dimension_models.get_session()
    session.add(Persona(dni="11111111", nombre_completo="Juan Perez", canal="TRADICIONAL", estado="Activo"))
    session.add(Persona(dni="22222222", nombre_completo="Pedro Gomez", canal="TRADICIONAL", estado="Inactivo"))
    session.commit()
    session.close()

    activos = personal_activo()
    assert [a["dni"] for a in activos] == ["11111111"]


def test_reasignar_mercaderista_cambia_toda_la_cartera_del_dni(db):
    # A diferencia de reasignar_pdv() (un PDV puntual), esto reasigna TODOS
    # los PDVs del mes que tenía ese DNI de una sola vez (Davor, 2026-10-15:
    # "reemplazar alguno en base al personal activo... el campo a cambiar
    # seria el DNI").
    df = pd.DataFrame([
        _plan("11111111", "100", "PDV Uno", []),
        _plan("11111111", "200", "PDV Dos", []),
        _plan("99999999", "300", "PDV De Otro Mercaderista", []),
    ])
    guardar_planning(df, dt.date(2026, 10, 1), "tester@x.com")

    session = dimension_models.get_session()
    session.add(Persona(dni="22222222", nombre_completo="Reemplazo Nuevo", canal="TRADICIONAL", estado="Activo"))
    session.commit()
    session.close()

    n, nombre_nuevo = reasignar_mercaderista(dt.date(2026, 10, 1), "11111111", "22222222")
    assert n == 2
    assert nombre_nuevo == "Reemplazo Nuevo"

    session = dimension_models.get_session()
    por_co_li = {p.co_li: (p.dni_asignado, p.mercaderista_nombre) for p in session.query(PlanningPdv).all()}
    session.close()
    assert por_co_li["100"] == ("22222222", "Reemplazo Nuevo")
    assert por_co_li["200"] == ("22222222", "Reemplazo Nuevo")
    assert por_co_li["300"] == ("99999999", "Juan Perez")  # no tocado, dni distinto


def test_reasignar_mercaderista_rechaza_dni_nuevo_que_no_este_activo(db):
    df = pd.DataFrame([_plan("11111111", "100", "PDV Uno", [])])
    guardar_planning(df, dt.date(2026, 10, 1), "tester@x.com")

    session = dimension_models.get_session()
    session.add(Persona(dni="22222222", nombre_completo="Inactivo Reciente", canal="TRADICIONAL", estado="Inactivo"))
    session.commit()
    session.close()

    with pytest.raises(ValueError):
        reasignar_mercaderista(dt.date(2026, 10, 1), "11111111", "22222222")
    with pytest.raises(ValueError):
        reasignar_mercaderista(dt.date(2026, 10, 1), "11111111", "00000000")  # ni siquiera existe en Personal
