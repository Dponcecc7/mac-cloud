# -*- coding: utf-8 -*-
"""Reportes -- Alertas mensuales (#1 de la lista de Davor, 2026-08-22).

Regla explícita de Davor: por cada 3 tardanzas acumuladas en el mes, una
alerta de "posible memorándum"; por cada 3 faltas, una de "observación para
la renovación". "Por cada 3" se interpreta como niveles: 3-5 = nivel 1, 6-8
= nivel 2, etc. (`cantidad // 3`), no una alerta única al llegar a 3."""
import os
import sys

import pandas as pd

from cobertura import alertas_cobertura
from dimension_models import Persona, get_session
from fact_models import ClasificacionDiaria
from patron_recurrente import cargar_patron_recurrente, sin_acentos, WD_NORM
from scoping import aplicar_filtros_extra, condicion_scope

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_AQUI, "pipeline"))
from historial_cambios import cargar_historial, valor_efectivo  # noqa: E402

UMBRAL = 3
SALIDA_ANTICIPADA_MIN = 10  # mismo umbral que asistencia.py usa para resaltar "salida temprana" un día suelto
FUENTE_CORREGIDO_MANUAL = "Corregido manualmente (Tabla 3)"  # mismo texto exacto que motor_clasificacion.py escribe en fuente_dato

# Faltas "con sustento" (Davor, 2026-08-23) -- descanso médico, licencia y
# feriado regional tienen respaldo/justificación, así que NO deben sumar
# para la alerta de "observación para la renovación" (esa es para faltas
# injustificadas). Sí se siguen contando como Falta en todos lados donde ya
# contaban (Horas semanales, Dashboard) -- esto solo afecta el umbral de
# ESTA alerta puntual.
MOTIVOS_CON_SUSTENTO = ("descanso médico", "descanso medico", "licencia", "feriado regional")


# Jornada "casi nula" (Davor, 2026-08-28) -- alguien que marca entrada y a
# los pocos minutos ya marca salida, sin trabajar realmente el día (caso
# real: 07:50 a 07:53, 3 minutos). Umbral RELATIVO a lo que le tocaba
# trabajar ese día puntual (no un número fijo -- un sábado de 4h30 y un
# lunes de 8h30 netas de refrigerio no son comparables con el mismo corte):
# "que el tiempo trabajado sea menos del 25% de las horas a trabajar del
# día... ya que se le debe restar la 1h de refrigerio a la resta de hora
# programada salida - hora programada entrada".
PCT_JORNADA_CRITICA = 0.25
REFRIGERIO_MIN = {"con refrigerio": 60, "medio refrigerio": 30, "sin refrigerio": 0}


def _fmt_min(minutos):
    horas, mins = divmod(int(round(minutos)), 60)
    return f"{horas}h{mins:02d}min" if horas else f"{mins} min"


def _refrigerio_min_para(row, refrigerio_map, idx_historial):
    dia_norm = WD_NORM.get(row.fecha.weekday())
    valor_patron = refrigerio_map.get((row.dni, dia_norm))
    valor_final = valor_efectivo(idx_historial, row.dni, "Refrigerio", row.fecha, valor_patron)
    return REFRIGERIO_MIN.get(sin_acentos(valor_final), 0) if valor_final else 0


def _detalle_salida(row, refrigerio_map, idx_historial):
    """Detalle de UNA ocurrencia de salida anticipada -- (texto, es_critico).
    Antes solo se acumulaba la fecha ("8 días este periodo"), sin decir a
    qué hora entró/salió ni cuánto trabajó realmente ese día."""
    entrada = (row.entrada_real or "—")[:5]
    salida = (row.salida_real or "—")[:5]
    antes_txt = _fmt_min(row.salida_anticipada_min) if pd.notna(row.salida_anticipada_min) else "—"

    minutos_trab = None
    if row.entrada_real and row.salida_real:
        delta = pd.to_timedelta(str(row.salida_real)) - pd.to_timedelta(str(row.entrada_real))
        minutos_trab = delta.total_seconds() / 60

    es_critico = False
    if minutos_trab is not None and minutos_trab >= 0 and row.entrada_esperada and row.salida_esperada:
        delta_prog = pd.to_timedelta(str(row.salida_esperada)) - pd.to_timedelta(str(row.entrada_esperada))
        refrigerio_min = _refrigerio_min_para(row, refrigerio_map, idx_historial)
        minutos_prog_netos = delta_prog.total_seconds() / 60 - refrigerio_min
        if minutos_prog_netos > 0:
            es_critico = minutos_trab < minutos_prog_netos * PCT_JORNADA_CRITICA

    trab_txt = _fmt_min(minutos_trab) if minutos_trab is not None and minutos_trab >= 0 else "—"
    fecha_str = row.fecha.strftime("%d/%m")
    texto = f"{fecha_str}: entró {entrada}, salió {salida} -- trabajó {trab_txt} ({antes_txt} antes de lo programado)"
    if es_critico:
        texto = f"🚨 {texto} -- menos del {int(PCT_JORNADA_CRITICA * 100)}% de lo programado ese día"
    return texto, es_critico


def _tiene_sustento(comentario):
    # pd.isna(), no "not comentario" -- un comentario vacío puede llegar acá
    # como NaN de pandas (no None) segun como se construyó el DataFrame, y
    # "not nan" da False (nan es truthy), asi que se colaba hasta el
    # .lower() y explotaba (float no tiene .lower()).
    if pd.isna(comentario):
        return False
    return any(m in str(comentario).lower() for m in MOTIVOS_CON_SUSTENTO)


def alertas_periodo(desde, hasta, usuario_actual, dni_filtro=None,
                     rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None):
    """Devuelve la lista de alertas (tardanza y falta) para el rango
    [desde, hasta], acotada al scope de `usuario_actual` -- o a un solo
    `dni_filtro` si se pasa (el acceso ya se valida aparte, ver
    reportes.py::ficha()). `rol_filtro`/`region_filtro`/`supervisor_filtro`/
    `ciudad_filtro`: filtros extra de Reportes (solo admin/analista, ver
    scoping.aplicar_filtros_extra). Ordenada por nivel de severidad
    descendente."""
    session = get_session()
    try:
        query = (
            session.query(ClasificacionDiaria.dni, Persona.nombre_completo, ClasificacionDiaria.fecha,
                           ClasificacionDiaria.estado, ClasificacionDiaria.comentario_supervisor,
                           ClasificacionDiaria.trabajo_otro_canal, ClasificacionDiaria.salida_anticipada_min,
                           ClasificacionDiaria.canal_esperado, ClasificacionDiaria.canales_marcados,
                           ClasificacionDiaria.fuente_dato, ClasificacionDiaria.entrada_real, ClasificacionDiaria.salida_real,
                           ClasificacionDiaria.entrada_esperada, ClasificacionDiaria.salida_esperada)
            .join(Persona, Persona.dni == ClasificacionDiaria.dni)
            .filter(ClasificacionDiaria.fecha >= desde, ClasificacionDiaria.fecha <= hasta)
        )
        if dni_filtro:
            query = query.filter(ClasificacionDiaria.dni == dni_filtro)
        else:
            cond_scope = condicion_scope(Persona, usuario_actual)
            if cond_scope is not None:
                query = query.filter(cond_scope)
            query = aplicar_filtros_extra(query, Persona, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro)
        filas = query.all()
        # Para el % de jornada casi nula (25% de las horas NETAS de
        # refrigerio) -- mismo patrón que horas_semanales.py: patrón base +
        # override vigente en Historial de cambios, resuelto por fecha.
        refrigerio_map = cargar_patron_recurrente(session, "refrigerio")
    finally:
        session.close()
    idx_historial = cargar_historial()

    alertas = []

    if filas:
        r = pd.DataFrame(filas, columns=[
            "dni", "nombre", "fecha", "estado", "comentario",
            "trabajo_otro_canal", "salida_anticipada_min", "canal_esperado", "canales_marcados", "fuente_dato",
            "entrada_real", "salida_real", "entrada_esperada", "salida_esperada",
        ])
        r["estado_base"] = r["estado"].apply(lambda s: s.split(" (")[0])
    else:
        r = pd.DataFrame(columns=[
            "dni", "nombre", "fecha", "estado", "comentario", "estado_base",
            "trabajo_otro_canal", "salida_anticipada_min", "canal_esperado", "canales_marcados", "fuente_dato",
            "entrada_real", "salida_real", "entrada_esperada", "salida_esperada",
        ])

    for tipo, estado_objetivo, mensaje_base in (
        ("tardanza", "TARDANZA", "Posible memorándum"),
        ("falta", "FALTA", "Observación para la renovación"),
    ):
        grupo_tipo = r[r["estado_base"] == estado_objetivo]
        # len(grupo_tipo) > 0 antes de filtrar -- Series.apply() sobre una
        # columna de 0 filas a veces devuelve un resultado con el índice
        # roto (pandas no tiene datos para inferir la forma), y el
        # boolean-indexing con eso deja un DataFrame de 0 columnas (ni
        # "dni"), reventando el groupby de abajo con KeyError.
        if tipo == "falta" and len(grupo_tipo):
            grupo_tipo = grupo_tipo[~grupo_tipo["comentario"].apply(_tiene_sustento)]
        for dni, grupo in grupo_tipo.groupby("dni"):
            cantidad = len(grupo)
            if cantidad < UMBRAL:
                continue
            nivel = cantidad // UMBRAL
            alertas.append({
                "dni": dni, "nombre": grupo["nombre"].iloc[0], "tipo": tipo,
                "cantidad": cantidad, "nivel": nivel,
                "mensaje": f"{mensaje_base} ({nivel}x -- {cantidad} {tipo}s este periodo)",
                "fechas": sorted(f.strftime("%d/%m") for f in grupo["fecha"]),
                # pd.isna(), no "c or ..." -- un comentario vacío puede llegar
                # como NaN de pandas (float), y "NaN or x" da NaN (NaN es
                # truthy), así que se colaba hasta el .strip() y explotaba.
                "motivos": sorted({("Sin motivo" if pd.isna(c) else str(c).strip()) for c in grupo["comentario"]}) if tipo == "falta" else [],
            })

    # Trabajó en canal distinto al asignado -- informativo, no disciplinario
    # (a veces es una cobertura real pedida por el supervisor), pero vale la
    # pena que el supervisor lo vea si se repite.
    grupo_canal = r[r["trabajo_otro_canal"] == True]  # noqa: E712 -- comparación explícita, no "is True", por si viene como NaN/None
    if len(grupo_canal):
        for dni, grupo in grupo_canal.groupby("dni"):
            cantidad = len(grupo)
            if cantidad < UMBRAL:
                continue
            nivel = cantidad // UMBRAL
            alertas.append({
                "dni": dni, "nombre": grupo["nombre"].iloc[0], "tipo": "otro_canal",
                "cantidad": cantidad, "nivel": nivel,
                "mensaje": f"Trabajó en canal distinto al asignado ({nivel}x -- {cantidad} días este periodo)",
                "fechas": sorted(f.strftime("%d/%m") for f in grupo["fecha"]),
                "motivos": sorted({
                    f"{(row.canal_esperado or '—')} → {(row.canales_marcados or '—')}"
                    for row in grupo.itertuples()
                }),
            })

    # Salida anticipada recurrente -- mismo umbral (10 min) que ya usa
    # asistencia.py para resaltar un día suelto, ahora acumulado en el mes.
    # Davor, 2026-08-28: "esa salida anticipada no me dice nada... trabajó
    # solo tal horas, salió 4h antes" -- ahora cada ocurrencia muestra
    # entrada/salida real y cuánto trabajó (antes solo se acumulaba la fecha).
    grupo_salida = r[r["salida_anticipada_min"].fillna(0) > SALIDA_ANTICIPADA_MIN]
    if len(grupo_salida):
        for dni, grupo in grupo_salida.groupby("dni"):
            cantidad = len(grupo)
            if cantidad < UMBRAL:
                continue
            nivel = cantidad // UMBRAL
            detalles = [_detalle_salida(row, refrigerio_map, idx_historial) for row in grupo.sort_values("fecha").itertuples()]
            alertas.append({
                "dni": dni, "nombre": grupo["nombre"].iloc[0], "tipo": "salida_temprana",
                "cantidad": cantidad, "nivel": nivel,
                "mensaje": f"Salida anticipada recurrente ({nivel}x -- {cantidad} días este periodo)",
                "fechas": sorted(f.strftime("%d/%m") for f in grupo["fecha"]),
                "motivos": [texto for texto, _critico in detalles],
            })

    # Jornada casi nula -- caso real que motivó esto (Davor, 2026-08-28):
    # Solis Manihuari entró 07:50 y salió 07:53 (3 min trabajados) y solo
    # figuraba como "1 día más" de salida anticipada, sin nada que resaltara
    # que esa jornada fue casi nula ("nosotros no nos dimos ni cuenta").
    # A diferencia del resto de alertas, acá NO hace falta que se repita 3
    # veces (UMBRAL) para avisar -- una sola vez ya es grave. Umbral relativo
    # (25% de las horas programadas NETAS de refrigerio, no un número fijo de
    # minutos) porque un sábado de 4h30 y un lunes de 8h30 netas no son
    # comparables con el mismo corte.
    grupo_marcado = r[r["entrada_real"].notna() & r["salida_real"].notna()
                       & r["entrada_esperada"].notna() & r["salida_esperada"].notna()]
    if len(grupo_marcado):
        for dni, grupo in grupo_marcado.groupby("dni"):
            filas_criticas = [
                (row.fecha.strftime("%d/%m"), texto)
                for row in grupo.sort_values("fecha").itertuples()
                for texto, es_critico in [_detalle_salida(row, refrigerio_map, idx_historial)]
                if es_critico
            ]
            if not filas_criticas:
                continue
            cantidad = len(filas_criticas)
            alertas.append({
                "dni": dni, "nombre": grupo["nombre"].iloc[0], "tipo": "jornada_critica",
                "cantidad": cantidad, "nivel": cantidad, "critico": True,
                "mensaje": f"Jornada casi nula -- trabajó menos del {int(PCT_JORNADA_CRITICA * 100)}% de lo programado ({cantidad} día{'s' if cantidad != 1 else ''} este periodo)",
                "fechas": [f for f, _ in filas_criticas],
                "motivos": [texto for _, texto in filas_criticas],
            })

    # Hora puesta a mano por un analista/supervisor (Entrada/Salida
    # corregida en Reporte diario), en vez de marcación real del
    # mercaderista -- para mapear a quién hay que estarle corrigiendo la
    # hora seguido porque no está marcando por su cuenta (Davor, 2026-08-23).
    grupo_manual = r[r["fuente_dato"] == FUENTE_CORREGIDO_MANUAL]
    if len(grupo_manual):
        for dni, grupo in grupo_manual.groupby("dni"):
            cantidad = len(grupo)
            if cantidad < UMBRAL:
                continue
            nivel = cantidad // UMBRAL
            alertas.append({
                "dni": dni, "nombre": grupo["nombre"].iloc[0], "tipo": "corregido_manual",
                "cantidad": cantidad, "nivel": nivel,
                "mensaje": f"No está marcando por su cuenta -- hora puesta a mano {cantidad} días este periodo",
                "fechas": sorted(f.strftime("%d/%m") for f in grupo["fecha"]),
                "motivos": [],
            })

    # Visita larga / Punto Censo -- tabla `visitas` (Postgres), ver
    # cobertura.py. Antes esto ya se calculaba cada 5 min en el pipeline
    # (pipeline/alerta_visita_larga.py) pero el resultado se descartaba al
    # terminar la corrida; ahora vive en Postgres y se puede mostrar acá.
    alertas.extend(alertas_cobertura(
        desde, hasta, usuario_actual, dni_filtro=dni_filtro,
        rol_filtro=rol_filtro, region_filtro=region_filtro, supervisor_filtro=supervisor_filtro,
        ciudad_filtro=ciudad_filtro, canal_filtro=canal_filtro,
    ))

    # "Jornada casi nula" primero que nada -- no importa el nivel/cantidad
    # de las demás, esto es más grave que un patrón acumulado de tardanzas.
    alertas.sort(key=lambda a: (0 if a.get("critico") else 1, -a["nivel"], -a["cantidad"], a["nombre"]))
    return alertas
