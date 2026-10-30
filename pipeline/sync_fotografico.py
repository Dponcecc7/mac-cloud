# -*- coding: utf-8 -*-
"""
Sincroniza dim_lf_general_fotografico_antes_despues (Athena) -- Davor,
2026-10-30: "vamos a cruzar con otras 2 tablas del ETL... considerar PDVs
con ejecución: relevos que el mercaderista relevó tanto foto". Mismo patrón
que traer_visitas_historico.py (Athena -> Postgres directo, sin Excel
intermedio).

"Comenzemos cruzando solo el mes de Octubre 2026 hacia adelante" -- el
histórico desde el 2026-09-30 (arranque) se sincronizó UNA vez a mano
completo; de ahí en adelante, mismo criterio que ya corrigió este problema
para Visitas (traer_visitas_historico.py): ventana móvil de los últimos
VENTANA_DIAS días en cada corrida automática, no todo el período completo
cada vez -- este paso corre cada 5 min dentro de pipeline_completo.py, y
re-traer un período que solo crece desde Athena en cada corrida es el mismo
error que ya rompió el pipeline antes.

Uso: python sync_fotografico.py [YYYY-MM-DD]
     (sin argumento, usa la ventana móvil de VENTANA_DIAS días)
"""
import datetime as dt
import os
import sys

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import EjecucionFotografica, get_engine  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from athena_client import traer_fotografico  # noqa: E402

FECHA_INICIO_SISTEMA = "2026-09-30"
VENTANA_DIAS = int(os.environ.get("VENTANA_DIAS_FOTOGRAFICO", "15"))
TAMANO_LOTE = 2000


def _desde_por_defecto():
    ventana = (dt.date.today() - dt.timedelta(days=VENTANA_DIAS)).isoformat()
    return max(FECHA_INICIO_SISTEMA, ventana)


def _categoria(etiqueta):
    if "Femenino" in etiqueta:
        return "FEMENINO"
    if "Adulto" in etiqueta:
        return "ADULTO"
    return None


def _lado(etiqueta):
    # "Despues"/"Después" -- ver nota de acentos en athena_client.py.
    if etiqueta.startswith("Antes"):
        return "ANTES"
    if etiqueta.startswith("Despues") or etiqueta.startswith("Después"):
        return "DESPUES"
    return None


def pivotar(df):
    """Devuelve una fila por (visita_id, categoria) con con_ejecucion
    resuelto (True si hay foto Antes Y foto Despues -- cualquier variante
    numerada de Despues cuenta -- ambas con valor no vacío)."""
    if not len(df):
        return pd.DataFrame(columns=[
            "visita_id", "punto_venta_id", "fecha", "categoria", "con_ejecucion",
            "dni_usuario", "nombre_usuario", "actividad_id",
        ])

    df = df.copy()
    df["categoria"] = df["componente_etiqueta"].apply(_categoria)
    df["lado"] = df["componente_etiqueta"].apply(_lado)
    df = df.dropna(subset=["categoria", "lado"])
    df["tiene_valor"] = df["componente_valor"].notna() & (df["componente_valor"].astype(str).str.strip() != "")
    df = df[df["tiene_valor"]]

    presentes = (
        df.groupby(["visita_id", "categoria", "lado"]).size().unstack(fill_value=0)
    )
    con_ejecucion = (presentes.get("ANTES", 0) > 0) & (presentes.get("DESPUES", 0) > 0)

    contexto = (
        df.drop_duplicates(subset=["visita_id", "categoria"])
        .set_index(["visita_id", "categoria"])[["punto_venta_id", "fecha", "dni_usuario", "nombre_usuario", "actividad_id"]]
    )
    resultado = contexto.join(con_ejecucion.rename("con_ejecucion")).reset_index()
    return resultado


def preparar_filas(resultado):
    r = resultado.copy()
    r["visita_id"] = r["visita_id"].astype(str).str.replace(r"\.0$", "", regex=True)
    r["punto_venta_id"] = r["punto_venta_id"].astype(str)
    r["fecha"] = pd.to_datetime(r["fecha"], format="%Y-%m-%d", errors="coerce").dt.date
    r["dni_usuario"] = r["dni_usuario"].astype(str).str.zfill(8)
    r["actividad_id"] = pd.to_numeric(r["actividad_id"], errors="coerce").astype("Int64")
    r = r.dropna(subset=["visita_id", "punto_venta_id", "fecha", "categoria"])
    return r[[
        "visita_id", "punto_venta_id", "fecha", "categoria", "con_ejecucion",
        "dni_usuario", "nombre_usuario", "actividad_id",
    ]].to_dict("records")


def upsert_ejecucion(filas):
    if not filas:
        return 0
    engine = get_engine()
    procesadas = 0
    with engine.begin() as conn:
        for i in range(0, len(filas), TAMANO_LOTE):
            lote = filas[i:i + TAMANO_LOTE]
            stmt = pg_insert(EjecucionFotografica.__table__).values(lote)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_ejecucion_fotografica_visita_categoria",
                set_={
                    "con_ejecucion": stmt.excluded.con_ejecucion,
                    "punto_venta_id": stmt.excluded.punto_venta_id,
                    "fecha": stmt.excluded.fecha,
                    "dni_usuario": stmt.excluded.dni_usuario,
                    "nombre_usuario": stmt.excluded.nombre_usuario,
                    "actividad_id": stmt.excluded.actividad_id,
                },
            )
            conn.execute(stmt)
            procesadas += len(lote)
    return procesadas


def main():
    desde = sys.argv[1] if len(sys.argv) > 1 else _desde_por_defecto()
    desde = dt.date.fromisoformat(desde).isoformat()  # valida formato antes de concatenar en SQL
    df = traer_fotografico(desde)
    resultado = pivotar(df)
    print(f"Pares visita+categoría resueltos: {len(resultado)} ({int(resultado['con_ejecucion'].sum())} con ejecución)")
    filas = preparar_filas(resultado)
    procesadas = upsert_ejecucion(filas)
    print(f"Sincronizadas (upsert) a Postgres: {procesadas} filas.")


if __name__ == "__main__":
    main()
