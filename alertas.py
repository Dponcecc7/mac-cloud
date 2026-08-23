# -*- coding: utf-8 -*-
"""Reportes -- Alertas mensuales (#1 de la lista de Davor, 2026-08-22).

Regla explícita de Davor: por cada 3 tardanzas acumuladas en el mes, una
alerta de "posible memorándum"; por cada 3 faltas, una de "observación para
la renovación". "Por cada 3" se interpreta como niveles: 3-5 = nivel 1, 6-8
= nivel 2, etc. (`cantidad // 3`), no una alerta única al llegar a 3."""
import pandas as pd

from dimension_models import Persona, get_session
from fact_models import ClasificacionDiaria
from scoping import condicion_scope

UMBRAL = 3


def alertas_periodo(desde, hasta, usuario_actual, dni_filtro=None):
    """Devuelve la lista de alertas (tardanza y falta) para el rango
    [desde, hasta], acotada al scope de `usuario_actual` -- o a un solo
    `dni_filtro` si se pasa (el acceso ya se valida aparte, ver
    reportes.py::ficha()). Ordenada por nivel de severidad descendente."""
    session = get_session()
    try:
        query = (
            session.query(ClasificacionDiaria.dni, Persona.nombre_completo, ClasificacionDiaria.fecha,
                           ClasificacionDiaria.estado, ClasificacionDiaria.comentario_supervisor)
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

    if not filas:
        return []

    r = pd.DataFrame(filas, columns=["dni", "nombre", "fecha", "estado", "comentario"])
    r["estado_base"] = r["estado"].apply(lambda s: s.split(" (")[0])

    alertas = []
    for tipo, estado_objetivo, mensaje_base in (
        ("tardanza", "TARDANZA", "Posible memorándum"),
        ("falta", "FALTA", "Observación para la renovación"),
    ):
        grupo_tipo = r[r["estado_base"] == estado_objetivo]
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
                "motivos": sorted({(c or "Sin motivo").strip() for c in grupo["comentario"]}) if tipo == "falta" else [],
            })
    alertas.sort(key=lambda a: (-a["nivel"], -a["cantidad"], a["nombre"]))
    return alertas
