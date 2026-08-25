# -*- coding: utf-8 -*-
"""Reportes -- Horas semanales (#2 de la lista de Davor, 2026-08-22).

Puerto de MAC/reporte_semanal.py::calcular_detalle_diario() +
agregar_detalle_mercaderista() -- MISMA regla de negocio ya validada (horas
fijas L-V 8.5h / Sábado 5.5h / feriados 0h, descuenta refrigerio según el
PATRÓN no el real marcado, "Horas a trabajar" descuenta Vacante, "Horas a
trabajar sin faltas" además descuenta Falta y Vacaciones), pero leyendo todo
de Postgres en vez de Excel/Graph -- no es importable tal cual desde acá
porque esas dependencias (Maestro, Patrón, Historial) solo existen como
archivo local en el original. Ver plan de esta sesión para el detalle de
cada reemplazo.

Diferencia deliberada con el original: no filtra a Rol == "MERCADERISTAS"
-- aplica a todos los roles (pedido explícito de Davor)."""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

from alertas import _tiene_sustento
from dimension_models import Feriado, Persona, PatronRecurrente, get_session
from fact_models import ClasificacionDiaria
from scoping import aplicar_filtros_extra, condicion_scope

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_AQUI, "pipeline"))
from historial_cambios import cargar_historial, valor_efectivo  # noqa: E402

HORAS_LV = 8.5
HORAS_SABADO = 5.5
REFRIGERIO_MIN = {"con refrigerio": 60, "medio refrigerio": 30, "sin refrigerio": 0}
CIERRE_AUTOMATICO_STR = "23:30:00"  # mismo criterio que reporte_diario_9am.py -- no es una salida real
WD_NORM = {0: "lunes", 1: "martes", 2: "miercoles", 3: "jueves", 4: "viernes", 5: "sabado"}


def _sin_acentos(s):
    """Normaliza "Miércoles"/"Sábado" -> "miercoles"/"sabado" -- el patrón
    recurrente guarda el día tal cual venía del Excel, y no vale la pena
    arriesgarse a un mismatch de encoding/acento entre el dato guardado y
    weekday()."""
    if not s:
        return ""
    return (
        s.strip().lower()
        .replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    )


def semana_iso(anio, num_semana):
    """Lunes a sábado de la semana ISO `num_semana` del año `anio`."""
    lunes = dt.date.fromisocalendar(anio, num_semana, 1)
    sabado = dt.date.fromisocalendar(anio, num_semana, 6)
    return lunes, sabado


def _horas_esperadas_dia(fecha, feriados_set):
    if fecha in feriados_set:
        return 0.0
    wd = fecha.weekday()
    if wd <= 4:
        return HORAS_LV
    if wd == 5:
        return HORAS_SABADO
    return 0.0  # domingo -- el motor ni genera fila, pero por si acaso


def _motivo_falta(estado_base, estado, comentario):
    """Mismo criterio que reporte_semanal.py::_motivo_falta() -- el Estado
    ya lo dice para Vacante/Vacaciones bien clasificados; el comentario es
    solo respaldo para Falta genérica sin resolver."""
    if estado_base == "VACANTE":
        return "Vacante"
    if estado_base == "VACACIONES":
        return "Vacaciones"
    if estado_base != "FALTA":
        return None
    texto = f"{estado} {comentario or ''}".upper()
    if "VACACION" in texto:
        return "Vacaciones"
    if "VACANTE" in texto:
        return "Vacante"
    return "Falta"


def calcular_detalle_semana(desde, hasta, usuario_actual, dni_filtro=None,
                             rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None):
    """Detalle día-persona entre [desde, hasta] (inclusive), acotado al
    scope de acceso de `usuario_actual` -- o a un solo `dni_filtro` si se
    pasa (para la ficha individual, donde el acceso ya se validó aparte).
    `rol_filtro`/`region_filtro`/`supervisor_filtro`/`ciudad_filtro`: filtros
    extra de Reportes (solo tienen efecto para admin/analista, ver
    scoping.aplicar_filtros_extra). Devuelve un DataFrame vacío (sin
    columnas) si no hay filas."""
    session = get_session()
    try:
        query = (
            session.query(
                ClasificacionDiaria.dni, Persona.nombre_completo, ClasificacionDiaria.fecha,
                ClasificacionDiaria.estado, ClasificacionDiaria.comentario_supervisor,
                ClasificacionDiaria.entrada_esperada, ClasificacionDiaria.entrada_real,
                ClasificacionDiaria.salida_esperada, ClasificacionDiaria.salida_real,
                Persona.region, Persona.ciudad, Persona.supervisor_dni,
            )
            .join(Persona, Persona.dni == ClasificacionDiaria.dni)
            .filter(ClasificacionDiaria.fecha >= desde, ClasificacionDiaria.fecha <= hasta)
        )
        if dni_filtro:
            query = query.filter(ClasificacionDiaria.dni == dni_filtro)
        else:
            cond_scope = condicion_scope(Persona, usuario_actual)
            if cond_scope is not None:
                query = query.filter(cond_scope)
            query = aplicar_filtros_extra(query, Persona, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro)
        filas = query.all()
        if not filas:
            return pd.DataFrame()

        dnis_supervisores = {f.supervisor_dni for f in filas if f.supervisor_dni}
        nombre_supervisor = {}
        if dnis_supervisores:
            nombre_supervisor = dict(
                session.query(Persona.dni, Persona.nombre_completo)
                .filter(Persona.dni.in_(dnis_supervisores)).all()
            )

        feriados_set = {f for (f,) in session.query(Feriado.fecha).all()}
        refrigerio_map = {
            (p.dni, _sin_acentos(p.dia_semana)): p.refrigerio
            for p in session.query(PatronRecurrente).all()
        }
        idx_historial = cargar_historial()
    finally:
        session.close()

    r = pd.DataFrame(filas, columns=[
        "dni", "nombre", "fecha", "estado", "comentario",
        "entrada_esperada", "entrada_real", "salida_esperada", "salida_real",
        "region", "ciudad", "supervisor_dni",
    ])
    r["fecha"] = pd.to_datetime(r["fecha"])
    r["estado_base"] = r["estado"].apply(lambda s: s.split(" (")[0])
    r["supervisor"] = r["supervisor_dni"].map(nombre_supervisor)
    r["motivo_falta"] = r.apply(
        lambda row: _motivo_falta(row["estado_base"], row["estado"], row["comentario"]), axis=1
    )

    def _refrigerio_min_para(row):
        dia_norm = WD_NORM.get(row["fecha"].weekday())
        valor_patron = refrigerio_map.get((row["dni"], dia_norm))
        valor_final = valor_efectivo(idx_historial, row["dni"], "Refrigerio", row["fecha"], valor_patron)
        return REFRIGERIO_MIN.get(_sin_acentos(valor_final), 0) if valor_final else 0

    r["_refrigerio_min"] = r.apply(_refrigerio_min_para, axis=1)
    r["horas_a_trabajar"] = r["fecha"].apply(lambda f: _horas_esperadas_dia(f.date(), feriados_set))

    r["_entrada_esp_td"] = pd.to_timedelta(r["entrada_esperada"].astype(str), errors="coerce")
    r["_entrada_real_td"] = pd.to_timedelta(r["entrada_real"].astype(str), errors="coerce")
    es_cierre_automatico = r["salida_real"].astype(str) == CIERRE_AUTOMATICO_STR
    r["_salida_real_td"] = pd.to_timedelta(r["salida_real"].astype(str), errors="coerce").where(~es_cierre_automatico)
    r["_refrigerio_td"] = pd.to_timedelta(r["_refrigerio_min"], unit="m")

    con_marcacion = r["_entrada_real_td"].notna() & r["_salida_real_td"].notna()
    r["_horas_trab_td"] = (r["_salida_real_td"] - r["_entrada_real_td"] - r["_refrigerio_td"]).where(con_marcacion)
    r["horas_trabajadas"] = r["_horas_trab_td"].apply(
        lambda td: None if pd.isna(td) else round(td.total_seconds() / 3600, 2)
    )

    # Faltas "con sustento" (descanso médico/licencia/feriado regional,
    # Davor 2026-08-23) se tratan como Vacante para las horas: no le "debe"
    # esas horas a nadie, tiene justificación real -- se descuentan de la
    # base "Horas a trabajar", no solo de la variante "sin faltas".
    es_con_sustento = (r["motivo_falta"] == "Falta") & r["comentario"].apply(_tiene_sustento)
    r["_horas_vacante_dia"] = r["horas_a_trabajar"].where(r["motivo_falta"] == "Vacante", 0.0)
    r["_horas_sustento_dia"] = r["horas_a_trabajar"].where(es_con_sustento, 0.0)

    # "Horas a trabajar sin faltas" (Davor, 2026-08-23): SOLO cuenta los días
    # que la persona realmente vino -- con marcación real (entrada Y salida),
    # sin importar el motivo por el que un día no tiene marcación. Esto
    # incluye Falta/Vacaciones normales, pero también un caso que la
    # clasificación por motivo no cubría: "Asistió (según supervisor, sin
    # marcación app)" -- alguien que el supervisor marcó presente desde
    # "Marcar asistencia" pero todavía nadie cargó la hora real (ver
    # asistencia.py, el aviso "⚠ Confirmar") quedaba contando su jornada
    # completa como si hubiese trabajado, sin ninguna marcación real detrás.
    # Los días ya excluidos de la base (Vacante/con sustento) no se
    # descuentan de nuevo acá -- ya salieron antes.
    ya_excluido_de_base = (r["motivo_falta"] == "Vacante") | es_con_sustento
    r["_horas_sin_marcacion_dia"] = r["horas_a_trabajar"].where(~ya_excluido_de_base & ~con_marcacion, 0.0)

    return r


def resumen_por_persona(detalle_semana):
    """Agregado semanal por persona -- mismas columnas/regla que
    reporte_semanal.py::agregar_detalle_mercaderista()."""
    if detalle_semana is None or not len(detalle_semana):
        return pd.DataFrame()

    g = detalle_semana.groupby("dni").agg(
        nombre=("nombre", "first"), supervisor=("supervisor", "first"),
        ciudad=("ciudad", "first"), region=("region", "first"),
        _dias_falta=("motivo_falta", lambda s: int((s == "Falta").sum())),
        _dias_vacante=("motivo_falta", lambda s: int((s == "Vacante").sum())),
        horas_trabajadas=("horas_trabajadas", "sum"),
        horas_a_trabajar=("horas_a_trabajar", "sum"),
        _horas_vacante=("_horas_vacante_dia", "sum"),
        _horas_sustento=("_horas_sustento_dia", "sum"),
        _horas_sin_marcacion=("_horas_sin_marcacion_dia", "sum"),
    ).reset_index()

    g["dias_falta_vacante"] = (
        "Falta: " + g["_dias_falta"].astype(str) + " / Vacante: " + g["_dias_vacante"].astype(str)
    )
    g["horas_trabajadas"] = g["horas_trabajadas"].round(1)
    # Vacante y faltas CON sustento (descanso médico/licencia/feriado
    # regional) se descuentan de la base -- a nadie se le puede "deber"
    # horas que tienen justificación real o que nadie estaba para trabajar.
    g["horas_a_trabajar"] = (g["horas_a_trabajar"] - g["_horas_vacante"] - g["_horas_sustento"]).round(1)
    # "sin faltas" descuenta además cualquier día sin marcación real
    # (Falta/Vacaciones normales, o un "Asistió" todavía sin hora cargada) --
    # compara solo contra los días que sí tienen una jornada real registrada.
    g["horas_a_trabajar_sin_faltas"] = (g["horas_a_trabajar"] - g["_horas_sin_marcacion"]).round(1)
    g["diferencia_h"] = (g["horas_trabajadas"] - g["horas_a_trabajar_sin_faltas"]).round(1)

    # Division por cero (ej. toda la semana Vacante) -- deja el % en blanco
    # en vez de infinito/NaN feo.
    g["pct_cumplimiento"] = (g["horas_trabajadas"] / g["horas_a_trabajar"].replace(0, np.nan) * 100).round(1)
    g["pct_cumplimiento_sin_faltas"] = (
        g["horas_trabajadas"] / g["horas_a_trabajar_sin_faltas"].replace(0, np.nan) * 100
    ).round(1)

    g = g.drop(columns=["_dias_falta", "_dias_vacante", "_horas_vacante", "_horas_sustento", "_horas_sin_marcacion"])
    g = g[[
        "dni", "nombre", "supervisor", "ciudad", "region", "dias_falta_vacante",
        "horas_trabajadas", "horas_a_trabajar", "horas_a_trabajar_sin_faltas",
        "diferencia_h", "pct_cumplimiento", "pct_cumplimiento_sin_faltas",
    ]]
    return g.sort_values("pct_cumplimiento_sin_faltas", na_position="first")
