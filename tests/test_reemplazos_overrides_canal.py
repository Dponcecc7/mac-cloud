# -*- coding: utf-8 -*-
"""procesar_reemplazo() debe copiar también los overrides de zona/supervisor
POR CANAL (PersonaZonaCanal/PersonaSupervisorCanal) de la vacante al
reemplazo, no solo Persona.zona/supervisor_dni (el valor "principal") --
Davor, 2026-10-07: caso real, Lia Oro reemplazó a Chaupi Cuba (Multicanal)
y quedó con el override de Farmacia/AU vacío a pesar de que Chaupi sí tenía
zona "AUTOSERVICIOS/FARMA" y supervisor "Jesus Marquez" ahí.

DB de prueba: SQLite en memoria, nunca la Postgres real -- se pisan los
globals de dimension_models (_engine/_SessionLocal) por test (mismo patrón
que test_sincronizar_postgres.py)."""
import datetime as dt

import pytest
from sqlalchemy import create_engine, event

import dimension_models
from dimension_models import Base, Persona, PersonaSupervisorCanal, PersonaZonaCanal
from reemplazos import procesar_reemplazo


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


def test_reemplazo_copia_overrides_de_zona_y_supervisor_por_canal(db):
    session = dimension_models.get_session()
    session.add(Persona(
        dni="44111222", nombre_completo="Jesus Marquez", estado="Activo", rol="SUPERVISOR",
    ))
    session.add(Persona(
        dni="73897404", nombre_completo="Chaupi Cuba Marilin Cecilia", estado="Activo",
        rol="MERCADERISTAS", canal="Multicanal", region="Sur", ciudad="Arequipa",
        zona=None, supervisor_dni=None,
    ))
    session.commit()
    session.add(PersonaZonaCanal(dni="73897404", canal="FARMACIA", zona="AUTOSERVICIOS/FARMA"))
    session.add(PersonaZonaCanal(dni="73897404", canal="AUTOSERVICIO", zona="AUTOSERVICIOS/FARMA"))
    session.add(PersonaSupervisorCanal(dni="73897404", canal="FARMACIA", supervisor_dni="44111222"))
    session.add(PersonaSupervisorCanal(dni="73897404", canal="AUTOSERVICIO", supervisor_dni="44111222"))
    session.commit()
    session.close()

    log = procesar_reemplazo(
        dni_vacante="73897404", dni_nuevo="99538747", nombre_nuevo="Oro Valdivia Lia Suzann",
        fecha_ingreso=dt.date(2026, 9, 24), motivo_baja="Renuncia",
    )
    assert any("Overrides de zona por canal a copiar: 2" in linea for linea in log)
    assert any("Overrides de supervisor por canal a copiar: 2" in linea for linea in log)

    session = dimension_models.get_session()
    zonas_nuevo = {
        f.canal: f.zona for f in session.query(PersonaZonaCanal).filter_by(dni="99538747").all()
    }
    supervisores_nuevo = {
        f.canal: f.supervisor_dni for f in session.query(PersonaSupervisorCanal).filter_by(dni="99538747").all()
    }
    session.close()

    assert zonas_nuevo == {"FARMACIA": "AUTOSERVICIOS/FARMA", "AUTOSERVICIO": "AUTOSERVICIOS/FARMA"}
    assert supervisores_nuevo == {"FARMACIA": "44111222", "AUTOSERVICIO": "44111222"}


def test_reemplazo_sin_overrides_en_la_vacante_no_crea_nada(db):
    session = dimension_models.get_session()
    session.add(Persona(
        dni="11111111", nombre_completo="Persona Sin Overrides", estado="Activo",
        rol="MERCADERISTAS", canal="Tradicional", zona="Zona 1", supervisor_dni=None,
    ))
    session.commit()
    session.close()

    procesar_reemplazo(
        dni_vacante="11111111", dni_nuevo="22222222", nombre_nuevo="Reemplazo De Prueba",
        fecha_ingreso=dt.date(2026, 9, 24), motivo_baja="Renuncia",
    )

    session = dimension_models.get_session()
    assert session.query(PersonaZonaCanal).filter_by(dni="22222222").count() == 0
    assert session.query(PersonaSupervisorCanal).filter_by(dni="22222222").count() == 0
    # El campo "principal" sigue heredándose como antes (CAMPOS_HEREDADOS).
    nuevo = session.get(Persona, "22222222")
    assert nuevo.zona == "Zona 1"
    session.close()


def test_reingreso_reemplaza_overrides_viejos_del_dni_nuevo(db):
    # dni_nuevo ya existía antes (es_reingreso) con sus propios overrides de
    # una etapa anterior -- deben quedar REEMPLAZADOS por los de la vacante
    # actual, no acumulados (mismo criterio que ya aplica para PatronRecurrente).
    session = dimension_models.get_session()
    session.add(Persona(dni="44111222", nombre_completo="Supervisor Viejo", estado="Activo", rol="SUPERVISOR"))
    session.add(Persona(dni="44333444", nombre_completo="Supervisor Nuevo", estado="Activo", rol="SUPERVISOR"))
    session.add(Persona(
        dni="73897404", nombre_completo="Vacante Actual", estado="Activo",
        rol="MERCADERISTAS", canal="Multicanal",
    ))
    session.add(Persona(
        dni="99538747", nombre_completo="Reingresante", estado="Inactivo",
        rol="MERCADERISTAS", canal="Multicanal", fecha_baja=dt.date(2026, 1, 1),
    ))
    session.commit()
    session.add(PersonaZonaCanal(dni="73897404", canal="FARMACIA", zona="Zona Nueva"))
    session.add(PersonaSupervisorCanal(dni="73897404", canal="FARMACIA", supervisor_dni="44333444"))
    # Override viejo del DNI que va a reingresar -- debe desaparecer.
    session.add(PersonaZonaCanal(dni="99538747", canal="FARMACIA", zona="Zona Vieja"))
    session.add(PersonaSupervisorCanal(dni="99538747", canal="FARMACIA", supervisor_dni="44111222"))
    session.commit()
    session.close()

    procesar_reemplazo(
        dni_vacante="73897404", dni_nuevo="99538747", nombre_nuevo="Reingresante",
        fecha_ingreso=dt.date(2026, 9, 24), motivo_baja="Renuncia",
    )

    session = dimension_models.get_session()
    zonas = session.query(PersonaZonaCanal).filter_by(dni="99538747").all()
    supervisores = session.query(PersonaSupervisorCanal).filter_by(dni="99538747").all()
    session.close()

    assert len(zonas) == 1 and zonas[0].zona == "Zona Nueva"
    assert len(supervisores) == 1 and supervisores[0].supervisor_dni == "44333444"
