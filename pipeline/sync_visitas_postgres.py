# -*- coding: utf-8 -*-
"""Fase 6+ (2026-08-23): sincroniza el detalle de visitas (Visitas/*.xlsx,
ya en disco por traer_visitas_historico.py -- no hace falta releer Athena)
a la tabla Postgres `visitas` -- solo los últimos VENTANA_DIAS días en cada
corrida del pipeline (las visitas más viejas ya quedaron sincronizadas en
corridas anteriores y no cambian, son inmutables una vez registradas), para
no sobrecargar la base con las ~44,000 filas/mes completas en cada corrida
de 5 minutos. Un solo INSERT...ON CONFLICT DO NOTHING por lote -- ver
dimension_models.py::Visita para la clave de dedup (misma que ya usa
traer_visitas()/motor_clasificacion.py en todos lados).

El histórico completo se carga UNA sola vez con un backfill manual aparte
(no en este script -- ver plan de la sesión), no repitiendo esto en cada
corrida."""
import datetime as dt
import glob
import os
import sys

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

# Columnas que SÍ pueden cambiar para una visita ya sincronizada -- ej.
# tipo_negocio, si se corrige la regla de clasificación en athena_client.py
# (como pasó 2026-08-26: se cambió de adivinar por nombre de cadena a usar
# campana_id). Las columnas de la UNIQUE constraint (dni, punto_venta_id,
# fecha_inicio, hora_inicio, fecha_fin, hora_fin) identifican la visita y
# nunca deberían cambiar, así que quedan afuera.
COLUMNAS_ACTUALIZABLES = ["punto_venta", "tipo_negocio", "distancia_metros_inicio", "motivo_visita"]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import get_engine, Visita  # noqa: E402

VENTANA_DIAS = 10
TAMANO_LOTE = 2000  # bien debajo del límite de 65535 parámetros por sentencia de Postgres (10 columnas x 2000 = 20000)

COLUMNAS = [
    "dni", "punto_venta_id", "punto_venta", "tipo_negocio",
    "fecha_inicio", "hora_inicio", "fecha_fin", "hora_fin",
    "distancia_metros_inicio", "motivo_visita",
]


def preparar_filas(df):
    """df: mismo formato que devuelve athena_client.traer_visitas() (columna
    nro_documento en vez de dni, fechas como texto "%d-%m-%Y"). Devuelve una
    lista de dicts lista para upsert_visitas()."""
    r = df.rename(columns={"nro_documento": "dni"})[COLUMNAS].copy()
    r["fecha_inicio"] = pd.to_datetime(r["fecha_inicio"], format="%d-%m-%Y", errors="coerce").dt.date
    r["fecha_fin"] = pd.to_datetime(r["fecha_fin"], format="%d-%m-%Y", errors="coerce").dt.date
    r = r.dropna(subset=["fecha_inicio", "dni", "punto_venta_id"])
    # fecha_fin puede venir vacía (visita que nunca cerró en Athena, ~180
    # casos reales) -- un NULL de Postgres NUNCA es "igual" a otro NULL para
    # la UNIQUE constraint (a diferencia de pandas, donde NaN == NaN sí
    # deduplica), así que dos visitas "iguales" con fecha_fin vacía se
    # insertaban de nuevo en cada corrida -- bug real, encontrado corriendo
    # el upsert dos veces seguidas antes de tocar el pipeline de producción.
    # fecha_inicio como respaldo no cambia ningún cálculo (fecha_fin no se
    # usa para nada más que este dedup hoy), solo hace que el "vacío" sea
    # comparable entre corridas.
    r["fecha_fin"] = r["fecha_fin"].fillna(r["fecha_inicio"])
    # "hora_fin" NaN llega como el texto literal "nan" (Series.astype(str)
    # sobre un float NaN en athena_client.py), no como NULL real -- se
    # limpia a vacío para que ese texto no aparezca en ningún reporte.
    r["hora_fin"] = r["hora_fin"].astype(str).replace({"nan": "", "NaN": "", "NaT": ""})
    r = r.where(pd.notna(r), None)
    return r.to_dict("records")


def upsert_visitas(filas):
    """Inserta `filas` (lista de dicts, columnas = COLUMNAS, fechas ya como
    date de Python) en lotes -- una visita ya sincronizada actualiza
    COLUMNAS_ACTUALIZABLES en vez de ignorarse (antes era
    on_conflict_do_nothing puro: una visita dentro de la ventana de 10 días
    que ya existiera en Postgres nunca se corregía aunque el ETL cambiara de
    criterio, ej. el fix de tipo_negocio por campana_id del 2026-08-26 no se
    reflejaba en nada ya sincronizado). Devuelve cuántas filas se
    procesaron (no necesariamente todas nuevas)."""
    if not filas:
        return 0
    engine = get_engine()
    procesadas = 0
    with engine.begin() as conn:
        for i in range(0, len(filas), TAMANO_LOTE):
            lote = filas[i:i + TAMANO_LOTE]
            stmt = pg_insert(Visita.__table__).values(lote)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_visita_dedup",
                set_={col: getattr(stmt.excluded, col) for col in COLUMNAS_ACTUALIZABLES},
            )
            conn.execute(stmt)
            procesadas += len(lote)
    return procesadas


def main():
    desde = dt.date.today() - dt.timedelta(days=VENTANA_DIAS)
    dfs = []
    for f in sorted(glob.glob("Visitas/*.xlsx")):
        dfs.append(pd.read_excel(f))
    if not dfs:
        print("No hay archivos Visitas/*.xlsx -- nada que sincronizar.")
        return
    v = pd.concat(dfs, ignore_index=True)
    v["_fecha_inicio_dt"] = pd.to_datetime(v["fecha_inicio"], format="%d-%m-%Y", errors="coerce")
    v_reciente = v[v["_fecha_inicio_dt"] >= pd.Timestamp(desde)].drop(columns=["_fecha_inicio_dt"])
    print(f"Filas totales en Visitas/*.xlsx: {len(v)} -- filtradas a los últimos {VENTANA_DIAS} días: {len(v_reciente)}")

    filas = preparar_filas(v_reciente)
    procesadas = upsert_visitas(filas)
    print(f"Sincronizadas (upsert) a Postgres: {procesadas} filas.")


if __name__ == "__main__":
    main()
