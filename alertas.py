# -*- coding: utf-8 -*-
"""Reportes -- Alertas mensuales (#1 de la lista de Davor, 2026-08-22).

Regla explícita de Davor: por cada 3 tardanzas acumuladas en el mes, una
alerta de "posible memorándum"; por cada 3 faltas, una de "observación para
la renovación". "Por cada 3" se interpreta como niveles: 3-5 = nivel 1, 6-8
= nivel 2, etc. (`cantidad // 3`), no una alerta única al llegar a 3."""
import pandas as pd

from cobertura import alertas_cobertura
from dimension_models import Persona, get_session
from fact_models import ClasificacionDiaria
from scoping import condicion_scope

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


def _tiene_sustento(comentario):
    # pd.isna(), no "not comentario" -- un comentario vacío puede llegar acá
    # como NaN de pandas (no None) segun como se construyó el DataFrame, y
    # "not nan" da False (nan es truthy), asi que se colaba hasta el
    # .lower() y explotaba (float no tiene .lower()).
    if pd.isna(comentario):
        return False
    return any(m in str(comentario).lower() for m in MOTIVOS_CON_SUSTENTO)


def alertas_periodo(desde, hasta, usuario_actual, dni_filtro=None):
    """Devuelve la lista de alertas (tardanza y falta) para el rango
    [desde, hasta], acotada al scope de `usuario_actual` -- o a un solo
    `dni_filtro` si se pasa (el acceso ya se valida aparte, ver
    reportes.py::ficha()). Ordenada por nivel de severidad descendente."""
    session = get_session()
    try:
        query = (
            session.query(ClasificacionDiaria.dni, Persona.nombre_completo, ClasificacionDiaria.fecha,
                           ClasificacionDiaria.estado, ClasificacionDiaria.comentario_supervisor,
                           ClasificacionDiaria.trabajo_otro_canal, ClasificacionDiaria.salida_anticipada_min,
                           ClasificacionDiaria.canal_esperado, ClasificacionDiaria.canales_marcados,
                           ClasificacionDiaria.fuente_dato)
            .join(Persona, Persona.dni == ClasificacionDiaria.dni)
            .filter(ClasificacionDiaria.fecha >= desde, ClasificacionDiaria.fecha <= hasta)
        )
        if dni_filtro:
            query = query.filter(ClasificacionDiaria.dni == dni_filtro)
        else:
            cond_scope = condicion_scope(Persona, usuario_actual)
            if cond_scope is not None:
                query = query.filter(cond_scope)
        filas = query.all()
    finally:
        session.close()

    alertas = []

    if filas:
        r = pd.DataFrame(filas, columns=[
            "dni", "nombre", "fecha", "estado", "comentario",
            "trabajo_otro_canal", "salida_anticipada_min", "canal_esperado", "canales_marcados", "fuente_dato",
        ])
        r["estado_base"] = r["estado"].apply(lambda s: s.split(" (")[0])
    else:
        r = pd.DataFrame(columns=[
            "dni", "nombre", "fecha", "estado", "comentario", "estado_base",
            "trabajo_otro_canal", "salida_anticipada_min", "canal_esperado", "canales_marcados", "fuente_dato",
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
    grupo_salida = r[r["salida_anticipada_min"].fillna(0) > SALIDA_ANTICIPADA_MIN]
    if len(grupo_salida):
        for dni, grupo in grupo_salida.groupby("dni"):
            cantidad = len(grupo)
            if cantidad < UMBRAL:
                continue
            nivel = cantidad // UMBRAL
            alertas.append({
                "dni": dni, "nombre": grupo["nombre"].iloc[0], "tipo": "salida_temprana",
                "cantidad": cantidad, "nivel": nivel,
                "mensaje": f"Salida anticipada recurrente ({nivel}x -- {cantidad} días este periodo)",
                "fechas": sorted(f.strftime("%d/%m") for f in grupo["fecha"]),
                "motivos": [],
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
    alertas.extend(alertas_cobertura(desde, hasta, usuario_actual, dni_filtro=dni_filtro))

    alertas.sort(key=lambda a: (-a["nivel"], -a["cantidad"], a["nombre"]))
    return alertas
