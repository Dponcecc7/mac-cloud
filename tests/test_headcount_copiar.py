# -*- coding: utf-8 -*-
"""Agregar headcount > copiar de una persona activa (Davor, 2026-11-10: dos
personas en un mismo lugar por unos días, "es como una persona duplicada")."""
import datetime as dt

import pytest
from sqlalchemy import create_engine

import dimension_models
from asistencia import _datos_para_copiar
from dimension_models import Base, PatronRecurrente, Persona, get_session


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    dimension_models._engine = engine
    dimension_models._SessionLocal = None
    Base.metadata.create_all(engine)
    s = get_session()
    s.add(Persona(dni="11111111", nombre_completo="ORIGINAL UNO", estado="Activo", rol="MERCADERISTAS", canal="MULTICANAL",
                  region="Lima", ciudad="Lima", zona="Zona 5", supervisor_dni="99999999", correo="uno@x.pe",
                  analista_propietario="ana@x.pe"))
    s.add(Persona(dni="22222222", nombre_completo="DADO DE BAJA", estado="Inactivo", rol="MERCADERISTAS"))
    s.flush()
    t = dt.time
    s.add_all([
        PatronRecurrente(dni="11111111", dia_semana="miercoles", hora_entrada_prog=t(13, 0), hora_salida_prog=t(18, 0),
                         canal_dia="Autoservicio", refrigerio="Sin refrigerio"),
        PatronRecurrente(dni="11111111", dia_semana="miercoles", hora_entrada_prog=t(7, 0), hora_salida_prog=t(12, 0),
                         canal_dia="Farmacia", refrigerio=None),
        PatronRecurrente(dni="11111111", dia_semana="Lunes", hora_entrada_prog=t(8, 0), hora_salida_prog=t(17, 30),
                         canal_dia="Tradicional", refrigerio="Con refrigerio"),
    ])
    s.commit()
    yield s
    s.close()
    dimension_models._engine = None
    dimension_models._SessionLocal = None


def test_copia_datos_y_horario_ordenado_por_dia_y_hora(session):
    d = _datos_para_copiar(session, "11111111", None)
    assert (d["rol"], d["canal"], d["region"], d["ciudad"], d["zona"], d["supervisor_dni"]) == (
        "MERCADERISTAS", "MULTICANAL", "Lima", "Lima", "Zona 5", "99999999")
    assert [(f["dia"], f["entrada"], f["salida"], f["canal_dia"]) for f in d["patron"]] == [
        ("Lunes", "08:00", "17:30", "Tradicional"),
        ("Miércoles", "07:00", "12:00", "Farmacia"),       # dos turnos el mismo día, el más temprano primero
        ("Miércoles", "13:00", "18:00", "Autoservicio"),
    ]
    assert d["patron"][0]["refrigerio"] == "Con refrigerio" and d["patron"][1]["refrigerio"] == ""


def test_no_copia_dni_nombre_ni_correo_como_campos_a_llenar(session):
    d = _datos_para_copiar(session, "11111111", None)
    assert "correo" not in d                      # el correo es de la persona original
    assert d["dni"] == "11111111"                 # solo para el mensaje "copiado de ..."


def test_solo_personas_activas(session):
    assert _datos_para_copiar(session, "22222222", None) is None
    assert _datos_para_copiar(session, "00000000", None) is None


def test_respeta_el_alcance_del_usuario(session):
    fuera = Persona.analista_propietario == "otro@x.pe"
    dentro = Persona.analista_propietario == "ana@x.pe"
    assert _datos_para_copiar(session, "11111111", fuera) is None
    assert _datos_para_copiar(session, "11111111", dentro) is not None
