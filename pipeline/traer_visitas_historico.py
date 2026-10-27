# -*- coding: utf-8 -*-
"""
Fase 6: primer paso del pipeline en la nube -- trae el historico de Athena
(misma ventana movil de 400 dias que ya usa MAC/pipeline_completo.py).

Davor, 2026-10-27: "¿no puede ser directa a Postgres?" -- confirmado que sí:
Postgres (tabla `visitas`) ya tenía el histórico completo (138,263 filas,
2026-07-01 a hoy, backfill + sync incremental de cada corrida), así que
Visitas/*.xlsx era un paso intermedio 100% redundante entre este script y
motor_clasificacion.py/alerta_visita_larga.py/motor_compensacion_indicador.py
(todos migrados a pipeline/visitas_motor.py::cargar_visitas()). Este script
ahora hace el upsert a Postgres DIRECTO (misma lógica que tenía
sync_visitas_postgres.py, ahora retirado -- fusionada acá), en vez de
escribir un Excel que el siguiente paso volvía a leer.

Mismo criterio de ventana que tenía sync_visitas_postgres.py: solo se
UPSERTEA a Postgres la ventana de los últimos VENTANA_DIAS_VISITAS días
(default 10) -- las visitas más viejas que trae Athena en esta misma
corrida ya están sincronizadas de corridas anteriores y son inmutables una
vez registradas, así que reinsertar las ~44,000 filas/mes completas en cada
corrida de 5 minutos sería puro desperdicio.

Uso: python traer_visitas_historico.py [YYYY-MM-DD]
     (sin argumento, usa la ventana movil de 400 dias)
"""
import datetime as dt
import os
import sys

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import Visita  # noqa: E402
from dimension_models import get_engine  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from athena_client import traer_visitas  # noqa: E402

INICIO_SISTEMA = dt.date(2026, 7, 1)
VENTANA_HISTORICO_DIAS = 400

# Override opcional (Davor, 2026-08-29) -- para un re-sync manual más ancho
# después de corregir una regla de clasificación que afecta visitas más
# viejas que la ventana normal. Sin la variable de entorno, sin cambios.
VENTANA_DIAS_SYNC = int(os.environ.get("VENTANA_DIAS_VISITAS", "10"))
TAMANO_LOTE = 2000  # bien debajo del límite de 65535 parámetros por sentencia de Postgres (10 columnas x 2000 = 20000)

# Columnas que SÍ pueden cambiar para una visita ya sincronizada. dni+
# punto_venta_id+fecha_inicio+hora_inicio (la UNIQUE constraint) identifican
# la visita y nunca deberían cambiar. fecha_fin/hora_fin SÍ deben poder
# actualizarse -- Davor, 2026-10-27: hasta hoy estaban en la constraint, así
# que cuando Athena corregía la hora de salida (de un cierre automático a la
# real) la fila vieja quedaba huérfana con una hora de salida equivocada
# para siempre, en vez de corregirse (811 visitas de ~138k encontradas con
# conflicto, ver dimension_models.py::Visita).
COLUMNAS_ACTUALIZABLES = ["punto_venta", "tipo_negocio", "distancia_metros_inicio", "motivo_visita", "fecha_fin", "hora_fin"]

COLUMNAS = [
    "dni", "punto_venta_id", "punto_venta", "tipo_negocio",
    "fecha_inicio", "hora_inicio", "fecha_fin", "hora_fin",
    "distancia_metros_inicio", "motivo_visita",
]


def _desde_por_defecto():
    ventana = dt.date.today() - dt.timedelta(days=VENTANA_HISTORICO_DIAS)
    return max(INICIO_SISTEMA, ventana).isoformat()


def preparar_filas(df):
    """df: mismo formato que devuelve athena_client.traer_visitas() (columna
    nro_documento en vez de dni, fechas como texto "%d-%m-%Y"). Devuelve una
    lista de dicts lista para upsert_visitas()."""
    r = df.rename(columns={"nro_documento": "dni"})[COLUMNAS].copy()
    # .str.zfill(8) (Davor, 2026-09-24) -- aunque athena_client.py ya deja
    # "nro_documento" zero-padded a 8 dígitos en memoria, esto es defensa
    # barata por si algún día esa columna se vuelve a inferir como número
    # (mismo gotcha que ya rompió Maestro/Patron/Tabla 3 vía Excel).
    r["dni"] = r["dni"].astype(str).str.zfill(8)
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
    COLUMNAS_ACTUALIZABLES en vez de ignorarse. Devuelve cuántas filas se
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
    desde = sys.argv[1] if len(sys.argv) > 1 else _desde_por_defecto()
    # dt.date.fromisoformat() valida el formato ANTES de concatenar en el
    # f-string de abajo -- desde viene de sys.argv[1] cuando se corre a
    # mano por CLI, sin este chequeo un valor con comillas/SQL se metería
    # directo en la query de Athena (hallazgo de revisión de código,
    # 2026-08-24; hoy no alcanzable desde ningún workflow de GitHub
    # Actions, solo corriendo el script a mano, pero es defensa barata).
    desde = dt.date.fromisoformat(desde).isoformat()
    filtro = f"AND date_parse(v.fecha_inicio, '%d-%m-%Y') >= date '{desde}'"
    out = traer_visitas("dim_lf_general_visitas", filtro)
    print(f"Traídas de Athena: {len(out)} filas (desde {desde}).")

    desde_sync = pd.Timestamp(dt.date.today() - dt.timedelta(days=VENTANA_DIAS_SYNC))
    out["_fecha_inicio_dt"] = pd.to_datetime(out["fecha_inicio"], format="%d-%m-%Y", errors="coerce")
    out_reciente = out[out["_fecha_inicio_dt"] >= desde_sync].drop(columns=["_fecha_inicio_dt"])
    print(f"Filtradas a los últimos {VENTANA_DIAS_SYNC} días para sincronizar a Postgres: {len(out_reciente)}")

    filas = preparar_filas(out_reciente)
    procesadas = upsert_visitas(filas)
    print(f"Sincronizadas (upsert) a Postgres: {procesadas} filas.")


if __name__ == "__main__":
    main()
