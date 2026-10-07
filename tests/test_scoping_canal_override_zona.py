# -*- coding: utf-8 -*-
"""Guarda de regresión: condicion_canal() NO debe usar PersonaZonaCanal
como criterio de visibilidad -- Davor, 2026-10-07, mismo día: se probó
agregarlo (porque Lia Oro, con override de zona/supervisor de Farmacia
pero sin ningún día de PatronRecurrente en Farmacia, no aparecía para el
analista de Farmacia) y se revirtió horas después porque el campo
"Farmacia/AU" de la pantalla Personal es una sola caja combinada que
app.py escribe en los canales FARMACIA y AUTOSERVICIO a la vez
(personal_editar_masivo()) -- casi cualquier Multicanal con algo ahí
termina con una fila PersonaZonaCanal(canal="FARMACIA") aunque trabaje
100% Autoservicio, así que ese criterio exponía mercaderistas ajenos al
analista de Farmacia (caso real: Mamani Phocco, Tasayco Veliz, Mollinedo
Tarqui y otros, todos sin un solo día de Farmacia en su patrón real, le
aparecían a Diego igual).

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


def test_override_de_zona_sin_dia_real_en_el_patron_no_da_visibilidad(db):
    # Mismo patrón que Mamani Phocco/Tasayco/Mollinedo en producción: canal
    # principal Multicanal, override de zona Farmacia (heredado del campo
    # combinado "Farmacia/AU"), pero CERO días de Farmacia en el patrón real
    # -- no debe aparecer para el analista de Farmacia.
    session = dimension_models.get_session()
    session.add(Persona(
        dni="42361240", nombre_completo="Mamani Phocco Jackeline", estado="Activo",
        rol="MERCADERISTAS", canal="MULTICANAL",
    ))
    session.commit()
    for dia, canal_dia in (("lunes", "Autoservicio"), ("martes", "Tradicional"), ("miercoles", "Autoservicio")):
        session.add(PatronRecurrente(dni="42361240", dia_semana=dia, canal_dia=canal_dia))
    session.add(PersonaZonaCanal(dni="42361240", canal="FARMACIA", zona="AUTOSERVICIOS"))
    session.add(PersonaZonaCanal(dni="42361240", canal="AUTOSERVICIO", zona="AUTOSERVICIOS"))
    session.commit()

    cond = condicion_canal(Persona, "FARMACIA")
    resultado = session.query(Persona).filter(cond).all()
    assert resultado == []
    session.close()


def test_persona_con_dia_real_de_farmacia_en_el_patron_si_aparece(db):
    # Contraste: si SÍ tiene un día real de Farmacia en el patrón, debe
    # seguir apareciendo (comportamiento preexistente, sin tocar).
    session = dimension_models.get_session()
    session.add(Persona(
        dni="21568074", nombre_completo="Choque Fuentes Edda Giannina", estado="Activo",
        rol="MERCADERISTAS", canal="MULTICANAL",
    ))
    session.commit()
    session.add(PatronRecurrente(dni="21568074", dia_semana="martes", canal_dia="Farmacia"))
    session.add(PatronRecurrente(dni="21568074", dia_semana="lunes", canal_dia="Autoservicio"))
    session.commit()

    cond = condicion_canal(Persona, "FARMACIA")
    resultado = session.query(Persona).filter(cond).all()
    assert [p.dni for p in resultado] == ["21568074"]
    session.close()
