# -*- coding: utf-8 -*-
"""
Fase 6: version portable de MAC/historial_cambios.py -- mismo indice y
`valor_efectivo()` (logica pura, sin cambios), pero `cargar_historial()` lee
de Postgres (tabla `historial_cambios`) en vez de 8_Historial_Cambios.xlsx.

Hallazgo real durante el port (2026-08-21): el original lee ese Excel con
pd.read_excel() PURO -- ni siquiera pasa por Graph como el resto del
proyecto. En un runner de GitHub Actions ese archivo no existe nunca, y el
`except FileNotFoundError: return {}` original (pensado para "todavia no se
creo el archivo") se dispararia SIEMPRE, ignorando en silencio todos los
cambios de Historial en cada corrida en la nube -- por eso esta version lee
directo de Postgres en vez de intentar portar el mismo Graph-download que
usan otros modulos.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import HistorialCambio, get_session  # noqa: E402

DIA_SEMANA_MAP = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}


def cargar_historial():
    """Devuelve un indice {(dni, campo_lower): [(fecha_desde, fecha_hasta, valor, dia_semana), ...]},
    identico en forma al original -- valor_efectivo() no necesita cambios."""
    session = get_session()
    try:
        filas = session.query(HistorialCambio).all()
    finally:
        session.close()

    idx = {}
    for h in filas:
        if h.fecha_desde is None:
            continue
        if h.dia_semana:
            dia_semana_norm = h.dia_semana.strip().lower()
            if dia_semana_norm in DIA_SEMANA_MAP:
                dia_semana = DIA_SEMANA_MAP[dia_semana_norm]
            else:
                # Antes esto quedaba en None -- pd.isna(None) es True, así
                # que valor_efectivo() lo trataba igual que "sin día
                # específico" y la corrección quedaba vigente TODOS los
                # días en vez de ninguno. -1 nunca matchea fecha.weekday()
                # (0-6), así que ahora falla cerrado (nunca vigente) en vez
                # de abierto (siempre vigente), y queda visible en el log.
                print(f"ADVERTENCIA historial_cambios: día de la semana no reconocido "
                      f"'{h.dia_semana}' para DNI {h.dni}, campo '{h.campo}' -- se ignora "
                      f"esta fila en vez de aplicarla todos los días.")
                dia_semana = -1
        else:
            dia_semana = pd.NA
        key = (str(h.dni).strip(), h.campo.strip().lower())
        idx.setdefault(key, []).append((h.fecha_desde, h.fecha_hasta, h.valor_nuevo, dia_semana))
    return idx


def valor_efectivo(idx, dni, campo, fecha, valor_default):
    """Si hay un cambio vigente en `fecha` para (dni, campo), devuelve ese
    valor; si no, devuelve valor_default (lo que diga Maestro/Patron). Si la
    fila tiene un "Día de la semana" especifico, solo se considera vigente
    cuando `fecha` cae en ese dia -- permite cambios permanentes limitados a
    un dia de la semana (ej. "todos los martes desde tal fecha")."""
    if fecha is None:
        return valor_default
    if hasattr(fecha, "date"):
        fecha = fecha.date()
    candidatos = idx.get((str(dni).strip(), campo.lower()), [])
    vigentes = [
        (d, h, v) for d, h, v, dia_semana in candidatos
        if d <= fecha and (pd.isna(h) or h >= fecha) and (pd.isna(dia_semana) or dia_semana == fecha.weekday())
    ]
    if not vigentes:
        return valor_default
    vigentes.sort(key=lambda x: x[0])
    return vigentes[-1][2]
