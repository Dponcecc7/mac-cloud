# -*- coding: utf-8 -*-
"""Agrupa días VACACIONES sueltos en "viajes" contiguos por persona --
factorizado desde app.py::dashboard() (2026-08-22) para reusar en la ficha
individual del trabajador (reportes.py::ficha()) sin duplicar la lógica.

Regla: el motor de clasificación no genera fila para domingos/feriados, así
que dos días VACACIONES separados por un domingo/feriado (sin nada en medio)
siguen siendo el MISMO viaje; si hay un día hábil de por medio sin fila
VACACIONES, es que volvió y salió de nuevo -- viaje nuevo. La duración se
cuenta en días CALENDARIO (regreso - salida), no en cantidad de filas."""
import datetime as dt

from dimension_models import Feriado
from fact_models import ClasificacionDiaria


def calcular_viajes_vacaciones(session, r, hasta):
    """`r`: DataFrame con columnas dni/nombre/fecha/estado_base ya filtrado
    al periodo deseado. `hasta`: límite de ese periodo -- se busca el
    regreso real más allá de esa fecha si hace falta (el periodo elegido
    puede cortar en medio de unas vacaciones).

    Devuelve una lista de dicts {nombre, inicio, regreso, dias} ordenada por
    inicio descendente -- inicio/regreso ya vienen formateados "DD/MM",
    dias=None si el viaje sigue en curso (mismo shape que ya consume
    dashboard.html, para no tener que tocar esa plantilla)."""
    vacaciones_rows = r[r["estado_base"] == "VACACIONES"][["dni", "nombre", "fecha"]].copy()
    vacaciones_rows["fecha"] = vacaciones_rows["fecha"].dt.date
    if not len(vacaciones_rows):
        return []

    feriados_set = {f for (f,) in session.query(Feriado.fecha).all()}

    def _no_habil(d):
        return d.weekday() == 6 or d in feriados_set

    viajes = []
    for dni, grupo in vacaciones_rows.groupby("dni"):
        nombre = grupo["nombre"].iloc[0]
        fechas = sorted(grupo["fecha"].unique())
        inicio = fin = fechas[0]
        for f in fechas[1:]:
            d = fin + dt.timedelta(days=1)
            puente_no_habil = True
            while d < f:
                if not _no_habil(d):
                    puente_no_habil = False
                    break
                d += dt.timedelta(days=1)
            if puente_no_habil:
                fin = f
            else:
                viajes.append({"dni": dni, "nombre": nombre, "inicio": inicio, "fin": fin})
                inicio = fin = f
        viajes.append({"dni": dni, "nombre": nombre, "inicio": inicio, "fin": fin})

    dnis_con_viaje = {v["dni"] for v in viajes}
    fechas_por_dni = {}
    for dni, fecha in r[r["dni"].isin(dnis_con_viaje)][["dni", "fecha"]].itertuples(index=False):
        fechas_por_dni.setdefault(dni, set()).add(fecha.date())
    filas_futuras = (
        session.query(ClasificacionDiaria.dni, ClasificacionDiaria.fecha)
        .filter(ClasificacionDiaria.dni.in_(dnis_con_viaje), ClasificacionDiaria.fecha > hasta)
        .all()
    )
    for dni, fecha in filas_futuras:
        fechas_por_dni.setdefault(dni, set()).add(fecha)

    for v in viajes:
        candidatas = sorted(f for f in fechas_por_dni.get(v["dni"], set()) if f > v["fin"])
        if candidatas:
            v["dias"] = (candidatas[0] - v["inicio"]).days
            v["regreso_dt"] = candidatas[0]
        else:
            v["dias"] = None
            v["regreso_dt"] = None
        del v["fin"], v["dni"]

    viajes.sort(key=lambda v: v["inicio"], reverse=True)
    # Sin año en la fecha (%d/%m) -- la tabla del dashboard tiene 4 columnas
    # angostas, el año sale sobreentendido del periodo elegido arriba.
    for v in viajes:
        v["inicio"] = v["inicio"].strftime("%d/%m")
        v["regreso"] = v["regreso_dt"].strftime("%d/%m") if v["regreso_dt"] else None
        del v["regreso_dt"]
    return viajes
