# -*- coding: utf-8 -*-
"""Soporte de 2 turnos/día para Multicanal (Davor, 2026-09-22, caso real:
Chaupi Cuba Marilin Cecilia dni 73897404) -- un mercaderista puede tener 2
turnos genuinos el mismo día calendario (ej. Farmacia 07-12, Autoservicio
13-18), cada uno con su propio pat/canal. clasificar_dia() ahora recibe
`canales_programados_hoy` (el set de TODOS los canales de la persona ese
día) para poder filtrar las visitas de cada turno por su propio canal, sin
perder la detección de "trabajó en canal distinto" para una visita que no
matchea NINGUNO de los turnos programados.

El camino de 1 solo turno (canales_programados_hoy con 1 solo elemento, o
sin pasar el argumento -- default None) debe seguir exactamente igual que
antes de esta feature: ver test_motor_domingo_indexerror.py y los demás
tests de motor_clasificacion.py, que llaman clasificar_dia() sin este
argumento y deben seguir pasando sin tocarlos."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
from motor_clasificacion import clasificar_dia  # noqa: E402

COL_ENT, COL_SAL, COL_CANAL = "Hora entrada programada", "Hora salida programada", "Canal del día"
FECHA = pd.Timestamp("2026-09-14")  # lunes


def _pat(canal, entrada, salida):
    return {COL_ENT: entrada, COL_SAL: salida, COL_CANAL: canal}


def _visita(canal, hora_inicio, hora_fin, geofence_ok=True):
    return {
        "nro_documento": "73897404", "fecha_inicio_dt": FECHA, "geofence_ok": geofence_ok,
        "hora_inicio_td": pd.to_timedelta(hora_inicio), "hora_fin_td": pd.to_timedelta(hora_fin),
        "canal_visita": canal,
    }


def test_cada_turno_solo_ve_las_visitas_de_su_propio_canal():
    # Farmacia 07-12, Autoservicio 13-18 -- visitas reales de ambos turnos
    # el mismo día, más una salida a horario en cada canal.
    v = pd.DataFrame([
        _visita("Farmacia", "07:05:00", "11:55:00"),
        _visita("Autoservicio", "13:10:00", "17:50:00"),
    ])
    canales_hoy = {"Farmacia", "Autoservicio"}

    fila_farmacia = clasificar_dia(
        "73897404", "Chaupi Cuba", FECHA, FECHA.weekday(), _pat("Farmacia", "07:00:00", "12:00:00"),
        COL_ENT, COL_SAL, COL_CANAL, v, None, {}, canales_programados_hoy=canales_hoy,
    )
    fila_autoservicio = clasificar_dia(
        "73897404", "Chaupi Cuba", FECHA, FECHA.weekday(), _pat("Autoservicio", "13:00:00", "18:00:00"),
        COL_ENT, COL_SAL, COL_CANAL, v, None, {}, canales_programados_hoy=canales_hoy,
    )

    assert fila_farmacia["Entrada real"] == "07:05:00"
    assert fila_farmacia["Salida real"] == "11:55:00"
    assert fila_farmacia["Canal esperado (Patrón)"] == "Farmacia"
    assert fila_farmacia["Trabajó para otro canal"] == "NO"

    assert fila_autoservicio["Entrada real"] == "13:10:00"
    assert fila_autoservicio["Salida real"] == "17:50:00"
    assert fila_autoservicio["Canal esperado (Patrón)"] == "Autoservicio"
    assert fila_autoservicio["Trabajó para otro canal"] == "NO"


def test_visita_de_un_tercer_canal_se_marca_en_ambos_turnos():
    # Decisión explícita de Davor (2026-09-22): una visita que no matchea
    # NINGUNO de los 2 canales programados hoy se marca "trabajó en canal
    # distinto" en AMBOS turnos, no se descarta ni se atribuye a uno solo.
    v = pd.DataFrame([
        _visita("Farmacia", "07:05:00", "11:55:00"),
        _visita("Autoservicio", "13:10:00", "17:50:00"),
        _visita("Tradicional", "19:00:00", "19:30:00"),
    ])
    canales_hoy = {"Farmacia", "Autoservicio"}

    fila_farmacia = clasificar_dia(
        "73897404", "Chaupi Cuba", FECHA, FECHA.weekday(), _pat("Farmacia", "07:00:00", "12:00:00"),
        COL_ENT, COL_SAL, COL_CANAL, v, None, {}, canales_programados_hoy=canales_hoy,
    )
    fila_autoservicio = clasificar_dia(
        "73897404", "Chaupi Cuba", FECHA, FECHA.weekday(), _pat("Autoservicio", "13:00:00", "18:00:00"),
        COL_ENT, COL_SAL, COL_CANAL, v, None, {}, canales_programados_hoy=canales_hoy,
    )

    assert fila_farmacia["Trabajó para otro canal"] == "SÍ"
    assert fila_autoservicio["Trabajó para otro canal"] == "SÍ"
    # La visita ajena queda visible en el informativo de ambos, no oculta.
    assert "Tradicional" in fila_farmacia["Canal(es) marcado(s)"]
    assert "Tradicional" in fila_autoservicio["Canal(es) marcado(s)"]
    # Pero entrada/salida real de cada turno sigue acotada a SU propio canal.
    assert fila_farmacia["Entrada real"] == "07:05:00"
    assert fila_farmacia["Salida real"] == "11:55:00"


def test_un_solo_turno_sigue_exactamente_igual_que_antes():
    # canales_programados_hoy=None (default, como llaman los tests
    # existentes) -- una visita a otro canal el mismo día NO debe filtrarse
    # de entrada/salida real (comportamiento histórico: min/max de TODO el
    # día, sin importar canal), y "trabajó en canal distinto" sigue
    # calculándose con la fórmula original (canal_esp_norm not in
    # canales_marcados), no con la lógica nueva de "canal ajeno a ambos".
    v = pd.DataFrame([
        _visita("Autoservicio", "07:00:00", "10:00:00"),
        _visita("Farmacia", "11:00:00", "16:00:00"),
    ])
    fila = clasificar_dia(
        "73897404", "Persona de 1 turno", FECHA, FECHA.weekday(), _pat("Autoservicio", "07:00:00", "16:00:00"),
        COL_ENT, COL_SAL, COL_CANAL, v, None, {},
    )
    # Sin filtrar por canal, entrada/salida real abarca TODO el día (min/max
    # de ambas visitas), igual que siempre.
    assert fila["Entrada real"] == "07:00:00"
    assert fila["Salida real"] == "16:00:00"
    # canal_esp_norm ("Autoservicio") SÍ está en canales_marcados
    # (["Autoservicio", "Farmacia"]) -- la fórmula original no marca alerta.
    assert fila["Trabajó para otro canal"] == "NO"
