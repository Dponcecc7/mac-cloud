# -*- coding: utf-8 -*-
"""
Reemplazo de la lectura de Visitas/*.xlsx para motor_clasificacion.py /
alerta_visita_larga.py / motor_compensacion_indicador.py -- Davor,
2026-10-27: "¿no puede ser directa a Postgres?". Confirmado que la tabla
Postgres `visitas` ya tiene el histórico completo (138,263 filas desde
2026-07-01, backfill + sync incremental de cada corrida -- ver
traer_visitas_historico.py), así que releer el Excel que ese mismo paso
escribía en el disco efímero del runner era 100% redundante.

cargar_visitas() devuelve EXACTAMENTE las mismas columnas (mismo nombre,
mismo formato de texto "%d-%m-%Y" para las fechas) que ya devolvía
pd.read_excel(Visitas/*.xlsx) -- para que el código que ya consume ese
DataFrame (parseo de hora_inicio_td/fecha_inicio_dt, geofence, etc. en los 3
scripts de arriba) no necesite cambiar, solo el call-site.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import Visita, get_session  # noqa: E402

COLUMNAS = [
    "nro_documento", "punto_venta_id", "punto_venta", "tipo_negocio",
    "fecha_inicio", "hora_inicio", "fecha_fin", "hora_fin",
    "distancia_metros_inicio", "motivo_visita",
]


def cargar_visitas():
    session = get_session()
    try:
        filas = (
            session.query(
                Visita.dni, Visita.punto_venta_id, Visita.punto_venta, Visita.tipo_negocio,
                Visita.fecha_inicio, Visita.hora_inicio, Visita.fecha_fin, Visita.hora_fin,
                Visita.distancia_metros_inicio, Visita.motivo_visita,
            )
            .all()
        )
    finally:
        session.close()
    df = pd.DataFrame(filas, columns=COLUMNAS)
    for col in ("fecha_inicio", "fecha_fin"):
        df[col] = df[col].apply(lambda d: d.strftime("%d-%m-%Y") if pd.notna(d) else None)

    # Dedup por (dni, punto_venta_id, fecha_inicio, hora_inicio) -- hallazgo
    # real (Davor, 2026-10-27): la UNIQUE constraint de `visitas` incluye
    # hora_fin, así que cuando una visita pasa de "abierta" (sincronizada
    # ANTES de cerrar, hora_fin vacío) a "cerrada" (hora_fin ya con valor)
    # en una corrida posterior, el upsert no actualiza la fila -- INSERTA
    # una nueva, porque la clave cambió. Esto deja un duplicado "fantasma"
    # (la versión abierta) que Visitas/*.xlsx (snapshot fresco de Athena,
    # siempre con el estado FINAL) nunca tuvo. Validado en vivo: sin este
    # dedup, Postgres traía ~5270 filas para una ventana donde Athena (y
    # Postgres deduplicado) da exactamente 3161 -- idéntico 1 a 1 tras
    # quedarse con la fila de hora_fin no vacío.
    # No basta "isna() | == ''": hay filas viejas (de antes de un fix en
    # traer_visitas_historico.py::preparar_filas) con el texto LITERAL
    # "NaN"/"nan"/"NaT" guardado en hora_fin en vez de vacío real -- y como
    # hora_fin no está en COLUMNAS_ACTUALIZABLES del upsert, esas filas
    # nunca se autocorrigen. ~contains(":") (una hora real siempre tiene
    # ":") es lo único que distingue de forma confiable "vacío" de "con
    # valor" para las dos variantes.
    df["_hora_fin_vacio"] = ~df["hora_fin"].fillna("").str.contains(":", regex=False)
    df = (
        df.sort_values("_hora_fin_vacio", ascending=False)
        .drop_duplicates(subset=["nro_documento", "punto_venta_id", "fecha_inicio", "hora_inicio"], keep="last")
        .drop(columns=["_hora_fin_vacio"])
        .reset_index(drop=True)
    )
    return df
