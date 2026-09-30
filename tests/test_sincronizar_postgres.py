# -*- coding: utf-8 -*-
"""Caracterización de _sincronizar_postgres() (motor_clasificacion.py) ANTES
de reescribirla a escritura en bloque (Davor, 2026-09-30: el bucle fila por
fila -- ventana de reproceso normal + 1161 y subiendo (dni,fecha) que alguna
vez tuvieron una corrección -- se volvió tan lento que "Datos hasta las X"
podía quedarse pegado horas si la corrida se cortaba antes del commit único
de al final). Estos tests fijan el comportamiento actual (huérfanos,
Multicanal, borrado de filas obsoletas, respeto de claves_recalculadas) para
poder reescribir el guardado en bloque sin romper ninguno de esos casos.

DB de prueba: SQLite en memoria, nunca la Postgres real -- se pisan los
globals de dimension_models (_engine/_SessionLocal) por test."""
import os
import sys

import pandas as pd
import pytest
from sqlalchemy import create_engine, event

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))

import dimension_models  # noqa: E402
from dimension_models import Base, Persona  # noqa: E402
from fact_models import ClasificacionDiaria  # noqa: E402
from motor_clasificacion import _sincronizar_postgres  # noqa: E402

COLUMNAS_RES = [
    "DNI", "Fecha", "Día", "Canal esperado (Patrón)", "Canal(es) marcado(s)",
    "Entrada esperada", "Entrada real", "Salida esperada", "Salida real",
    "Estado", "Salida anticipada (min)", "Trabajó para otro canal",
    "Alerta geofence (solo Punto Censo/fuera de rango)", "Fuente del dato",
    "Comentario supervisor", "Alerta para analista",
]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")

    # SQLite no aplica FKs por default -- sin esto, el caso de "DNI huérfano"
    # (que en Postgres SÍ rompe con IntegrityError) pasaría de largo en el
    # test, dando un falso positivo.
    @event.listens_for(engine, "connect")
    def _activar_fk(conexion_dbapi, _):
        conexion_dbapi.execute("PRAGMA foreign_keys=ON")

    dimension_models._engine = engine
    dimension_models._SessionLocal = None
    Base.metadata.create_all(engine)
    yield engine
    dimension_models._engine = None
    dimension_models._SessionLocal = None


def _persona(session, dni):
    session.add(Persona(dni=dni, nombre_completo=f"Persona {dni}", estado="Activo"))
    session.commit()


def _fila(dni, fecha, canal="Tradicional", estado="ASISTIÓ A TIEMPO", comentario=None):
    # .date() -- clasificar_dia() SIEMPRE devuelve la Fecha como date(), no
    # Timestamp (motor_clasificacion.py línea ~442); existentes_por_clave
    # (leído de Postgres) también usa date() -- si acá se dejara Timestamp,
    # ninguna clave calzaría nunca contra lo ya guardado y TODO se
    # borraría/reinsertaría cada corrida.
    return {
        "DNI": dni, "Fecha": fecha.date() if hasattr(fecha, "date") else fecha, "Día": "Lunes",
        "Canal esperado (Patrón)": canal,
        "Canal(es) marcado(s)": canal, "Entrada esperada": "09:00:00", "Entrada real": "09:00:00",
        "Salida esperada": "18:00:00", "Salida real": "18:00:00", "Estado": estado,
        "Salida anticipada (min)": None, "Trabajó para otro canal": "NO",
        "Alerta geofence (solo Punto Censo/fuera de rango)": "NO", "Fuente del dato": "Aplicativo",
        "Comentario supervisor": comentario, "Alerta para analista": "NO",
    }


def _res(filas):
    return pd.DataFrame(filas, columns=COLUMNAS_RES)


def _dump(session_factory):
    session = session_factory()
    try:
        return {(c.dni, c.fecha, c.canal_turno): c.estado for c in session.query(ClasificacionDiaria).all()}
    finally:
        session.close()


def test_inserta_fila_nueva(db):
    session = dimension_models.get_session()
    _persona(session, "11111111")
    session.close()

    fecha = pd.Timestamp("2026-09-15")
    res = _res([_fila("11111111", fecha, estado="ASISTIÓ A TIEMPO")])
    claves = {("11111111", fecha.date(), "Tradicional")}

    _sincronizar_postgres(res, claves)

    filas = _dump(dimension_models.get_session)
    assert filas == {("11111111", fecha.date(), "Tradicional"): "ASISTIÓ A TIEMPO"}


def test_actualiza_fila_existente_no_duplica(db):
    session = dimension_models.get_session()
    _persona(session, "11111111")
    session.close()

    fecha = pd.Timestamp("2026-09-15")
    claves = {("11111111", fecha.date(), "Tradicional")}

    _sincronizar_postgres(_res([_fila("11111111", fecha, estado="FALTA (sin marcación)")]), claves)
    _sincronizar_postgres(_res([_fila("11111111", fecha, estado="ASISTIÓ A TIEMPO")]), claves)

    session = dimension_models.get_session()
    try:
        filas = session.query(ClasificacionDiaria).filter_by(dni="11111111", fecha=fecha.date()).all()
        assert len(filas) == 1
        assert filas[0].estado == "ASISTIÓ A TIEMPO"
    finally:
        session.close()


def test_dni_huerfano_no_bloquea_las_demas(db):
    session = dimension_models.get_session()
    _persona(session, "11111111")
    # "22222222" NO se da de alta -- huérfano a propósito.
    session.close()

    fecha = pd.Timestamp("2026-09-15")
    res = _res([
        _fila("11111111", fecha, estado="ASISTIÓ A TIEMPO"),
        _fila("22222222", fecha, estado="FALTA (sin marcación)"),
    ])
    claves = {("11111111", fecha.date(), "Tradicional"), ("22222222", fecha.date(), "Tradicional")}

    _sincronizar_postgres(res, claves)  # no debe lanzar

    filas = _dump(dimension_models.get_session)
    assert filas == {("11111111", fecha.date(), "Tradicional"): "ASISTIÓ A TIEMPO"}


def test_multicanal_dos_turnos_mismo_dia_no_se_pisan(db):
    session = dimension_models.get_session()
    _persona(session, "33333333")
    session.close()

    fecha = pd.Timestamp("2026-09-15")
    res = _res([
        _fila("33333333", fecha, canal="Tradicional", estado="ASISTIÓ A TIEMPO"),
        _fila("33333333", fecha, canal="Autoservicio", estado="TARDANZA (20 min)"),
    ])
    claves = {("33333333", fecha.date(), "Tradicional"), ("33333333", fecha.date(), "Autoservicio")}

    _sincronizar_postgres(res, claves)

    filas = _dump(dimension_models.get_session)
    assert filas == {
        ("33333333", fecha.date(), "Tradicional"): "ASISTIÓ A TIEMPO",
        ("33333333", fecha.date(), "Autoservicio"): "TARDANZA (20 min)",
    }


def test_borra_fila_que_ya_no_corresponde(db):
    session = dimension_models.get_session()
    _persona(session, "11111111")
    session.close()

    fecha = pd.Timestamp("2026-09-15")
    claves = {("11111111", fecha.date(), "Tradicional")}
    _sincronizar_postgres(_res([_fila("11111111", fecha)]), claves)
    assert _dump(dimension_models.get_session)  # existe

    # Segunda corrida: `res` ya NO trae esa fila (ej. se dio de baja antes de
    # esa fecha) -- debe desaparecer de Postgres aunque no esté en
    # claves_recalculadas (el borrado se rige por lo que falta en `res`
    # completo, no solo por lo reprocesado esta corrida).
    _sincronizar_postgres(_res([]), set())

    assert _dump(dimension_models.get_session) == {}


def test_no_toca_fila_fuera_de_claves_recalculadas(db):
    session = dimension_models.get_session()
    _persona(session, "11111111")
    session.close()

    fecha = pd.Timestamp("2026-09-15")
    claves = {("11111111", fecha.date(), "Tradicional")}
    _sincronizar_postgres(_res([_fila("11111111", fecha, estado="ASISTIÓ A TIEMPO")]), claves)

    # `res` sigue trayendo la fila (sigue siendo parte del universo completo,
    # no se borra) pero con OTRO estado -- si NO está en claves_recalculadas,
    # no debe reescribirse (mismo criterio que usa motor_clasificacion.py
    # para no tocar días ya procesados fuera de la ventana sin corrección).
    _sincronizar_postgres(_res([_fila("11111111", fecha, estado="FALTA (sin marcación)")]), set())

    filas = _dump(dimension_models.get_session)
    assert filas == {("11111111", fecha.date(), "Tradicional"): "ASISTIÓ A TIEMPO"}
