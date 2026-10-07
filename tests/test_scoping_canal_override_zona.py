# -*- coding: utf-8 -*-
"""condicion_canal() debe incluir también a una Persona que tenga un
override de PersonaZonaCanal para ese canal, aunque su PatronRecurrente
nunca tenga un día marcado con ese canal -- Davor, 2026-10-07: "Deberia
aparecer tambien si en zona aparece como Farma" (caso real: Lia Oro tenía
el override de zona/supervisor de Farmacia pero su patrón semanal era 100%
Autoservicio, así que no aparecía para el analista de Farmacia).

A diferencia de test_scoping.py (compara expresiones SQL sin tocar la DB),
acá se ejecuta la condición contra SQLite en memoria para verificar el
resultado real de incluir/excluir filas, no solo que la expresión se arme."""
from sqlalchemy import create_engine, event

import dimension_models
from dimension_models import Base, Persona, PersonaZonaCanal, PatronRecurrente
from scoping import condicion_canal

import pytest


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


def test_persona_con_solo_override_de_zona_aparece_para_ese_canal(db):
    session = dimension_models.get_session()
    session.add(Persona(
        dni="29538747", nombre_completo="Oro Valdivia Lia Suzann", estado="Activo",
        rol="MERCADERISTAS", canal="MULTICANAL",
    ))
    session.commit()
    # Patrón 100% Autoservicio, cero días Farmacia -- caso real de Lia.
    for dia in ("lunes", "martes", "miercoles"):
        session.add(PatronRecurrente(dni="29538747", dia_semana=dia, canal_dia="Autoservicio"))
    session.add(PersonaZonaCanal(dni="29538747", canal="FARMACIA", zona="AUTOSERVICIOS/FARMA"))
    session.commit()

    cond = condicion_canal(Persona, "FARMACIA")
    resultado = session.query(Persona).filter(cond).all()
    assert [p.dni for p in resultado] == ["29538747"]
    session.close()


def test_persona_sin_canal_ni_patron_ni_override_no_aparece(db):
    session = dimension_models.get_session()
    session.add(Persona(
        dni="11111111", nombre_completo="Alguien De Tradicional", estado="Activo",
        rol="MERCADERISTAS", canal="TRADICIONAL",
    ))
    session.commit()

    cond = condicion_canal(Persona, "FARMACIA")
    resultado = session.query(Persona).filter(cond).all()
    assert resultado == []
    session.close()
