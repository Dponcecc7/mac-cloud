# -*- coding: utf-8 -*-
"""Tabla inferior "Compensaciones del mes" de la Ficha (Davor, 2026-11-06):
cada motivo registrado, con lo que pasó ese día, qué compensa y cuántas
horas quedaron compensadas / pendientes."""
import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import create_engine, event

import dimension_models
from dimension_models import Base, CompensacionHoras, Persona, get_session
from horas_semanales import aplicar_compensaciones, compensaciones_del_mes

DNI = "11111111"
D3, D4 = dt.date(2026, 11, 3), dt.date(2026, 11, 4)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _activar_fk(conexion_dbapi, _):
        conexion_dbapi.execute("PRAGMA foreign_keys=ON")

    dimension_models._engine = engine
    dimension_models._SessionLocal = None
    dimension_models._tabla_compensaciones_lista = False
    Base.metadata.create_all(engine)
    s = get_session()
    s.add(Persona(dni=DNI, nombre_completo="PERSONA PRUEBA", estado="Activo"))
    s.commit()
    s.close()
    yield engine
    dimension_models._engine = None
    dimension_models._SessionLocal = None
    dimension_models._tabla_compensaciones_lista = False


def _guardar(fecha, motivo, tarde, salida, por="admin@x.pe"):
    s = get_session()
    s.add(CompensacionHoras(
        dni=DNI, fecha=fecha, motivo=motivo, compensa_tardanza=tarde, compensa_salida=salida,
        compensa=tarde or salida, registrado_por=por, updated_at=dt.datetime(2026, 11, 5, 16, 58),
    ))
    s.commit()
    s.close()


def _detalle(compensaciones):
    """04/11: entra 16.8 min tarde y sale 4.8 h antes (faltan 5.08 h).
    03/11: solo sale 4.46 h antes."""
    filas = []
    for fecha, trab, entrada, sal_min, tarde, salida in [
        (pd.Timestamp(D3), 4.04, "08:00:00", 267, False, True),
        (pd.Timestamp(D4), 3.42, "08:16:48", 288, True, True),
    ]:
        filas.append({
            "dni": DNI, "fecha": fecha, "horas_trabajadas": trab, "horas_a_trabajar": 8.5,
            "es_tardanza": tarde, "es_salida_temprana": salida, "recupero_dia": False,
            "_entrada_esp_td": pd.Timedelta("08:00:00"), "_entrada_real_td": pd.Timedelta(entrada),
            "salida_anticipada_min": sal_min,
        })
    return aplicar_compensaciones(pd.DataFrame(filas), compensaciones)


def test_lista_el_detalle_mas_reciente_primero_con_horas_compensadas_y_pendientes(db):
    _guardar(D3, "Cita médica", False, True)
    _guardar(D4, "Tráfico por paro", True, False)
    detalle = _detalle({(DNI, D3): (False, True, "Cita médica"), (DNI, D4): (True, False, "Tráfico por paro")})

    filas = compensaciones_del_mes(DNI, dt.date(2026, 11, 1), dt.date(2026, 11, 30), detalle)

    assert [f["fecha"] for f in filas] == ["04/11", "03/11"]
    f4, f3 = filas
    assert (f4["hubo_tardanza"], f4["hubo_salida"]) == (True, True)
    assert (f4["compensa_tardanza"], f4["compensa_salida"]) == (True, False)
    assert f4["horas_compensadas"] == 0.28        # solo los minutos de tardanza
    assert f4["horas_pendientes"] == 4.8          # 5.08 de faltante - 0.28
    assert f4["motivo"] == "Tráfico por paro" and f4["registrado_por"] == "admin@x.pe"
    assert f4["registrado_el"] == "05/11/2026 16:58"
    assert (f3["hubo_tardanza"], f3["hubo_salida"]) == (False, True)
    assert f3["horas_compensadas"] == 4.46        # compensa todo lo que aplicó ese día
    assert f3["horas_pendientes"] == 0.0


def test_motivo_sin_compensar_deja_todo_pendiente(db):
    _guardar(D4, "Se fue sin avisar", False, False)
    detalle = _detalle({(DNI, D4): (False, False, "Se fue sin avisar")})
    f = compensaciones_del_mes(DNI, dt.date(2026, 11, 1), dt.date(2026, 11, 30), detalle)[0]
    assert f["horas_compensadas"] == 0.0 and f["horas_pendientes"] == 5.08


def test_solo_trae_el_rango_y_la_persona_pedidos(db):
    _guardar(D4, "Octubre no entra", True, True)
    s = get_session()
    s.add(CompensacionHoras(dni=DNI, fecha=dt.date(2026, 10, 30), motivo="Mes anterior", compensa=True,
                            compensa_tardanza=True, compensa_salida=True))
    s.commit()
    s.close()
    filas = compensaciones_del_mes(DNI, dt.date(2026, 11, 1), dt.date(2026, 11, 30), _detalle({}))
    assert [f["motivo"] for f in filas] == ["Octubre no entra"]
    assert compensaciones_del_mes("99999999", dt.date(2026, 11, 1), dt.date(2026, 11, 30), _detalle({})) == []


def test_sin_detalle_del_dia_igual_lista_el_motivo(db):
    _guardar(D4, "Motivo sin clasificación", True, True)
    f = compensaciones_del_mes(DNI, dt.date(2026, 11, 1), dt.date(2026, 11, 30), pd.DataFrame())[0]
    assert f["motivo"] == "Motivo sin clasificación"
    assert f["horas_compensadas"] == 0.0 and f["horas_pendientes"] == 0.0
    assert not f["hubo_tardanza"] and not f["hubo_salida"]
