# -*- coding: utf-8 -*-
"""Reportes -- Cobertura Diaria (visitas a PDV), 2026-08-23. Lee de la
tabla Postgres `visitas` (dimension_models.Visita), sincronizada por
pipeline/sync_visitas_postgres.py -- ya NO hace falta Excel/Graph para esto.

Mismo criterio que ya usaba MAC/Planning/analisis_visitas_planning.py y
pipeline/alerta_visita_larga.py (geofence_ok = distancia ≤ 200m, Punto
Censo aparte porque no tiene ubicación fija real), reimplementado contra
Postgres en vez de los Excel de Athena."""
import os
import sys

import pandas as pd

from dimension_models import Persona, PatronRecurrente, Visita, get_session
from scoping import aplicar_filtros_extra, condicion_scope

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_AQUI, "pipeline"))
from historial_cambios import cargar_historial, valor_efectivo  # noqa: E402

GEOFENCE_MAX_M = 200
UMBRAL_VISITA_LARGA_MIN = 60
UMBRAL_DIAS_CENSO = 3
UMBRAL_DURACION_CENSO_MIN = 120
CIERRE_AUTOMATICO_TD = pd.Timedelta(hours=23, minutes=30, seconds=0)
PUNTOS_EXCLUIDOS = ("AMOF", "OVERALL", "PUNTO CENSO")
WD_NORM = {0: "lunes", 1: "martes", 2: "miercoles", 3: "jueves", 4: "viernes", 5: "sabado"}


def _sin_acentos(s):
    if not s:
        return ""
    return (
        s.strip().lower()
        .replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    )


def _cargar_visitas(desde, hasta, usuario_actual, dni_filtro=None,
                     rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None):
    """Visitas [desde, hasta] con nombre/región/ciudad ya unidos, acotadas al
    scope de `usuario_actual` (o a un solo `dni_filtro`). Devuelve un
    DataFrame vacío (sin columnas) si no hay nada."""
    session = get_session()
    try:
        query = (
            session.query(
                Visita.dni, Persona.nombre_completo, Persona.ciudad, Persona.supervisor_dni,
                Visita.punto_venta_id, Visita.punto_venta, Visita.tipo_negocio, Visita.fecha_inicio,
                Visita.hora_inicio, Visita.hora_fin, Visita.distancia_metros_inicio,
            )
            .join(Persona, Persona.dni == Visita.dni)
            .filter(Visita.fecha_inicio >= desde, Visita.fecha_inicio <= hasta)
        )
        if dni_filtro:
            query = query.filter(Visita.dni == dni_filtro)
        else:
            cond_scope = condicion_scope(Persona, usuario_actual)
            if cond_scope is not None:
                query = query.filter(cond_scope)
            query = aplicar_filtros_extra(query, Persona, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro)
        filas = query.all()
        if not filas:
            return pd.DataFrame()

        dnis_sup = {f.supervisor_dni for f in filas if f.supervisor_dni}
        nombre_sup = {}
        if dnis_sup:
            nombre_sup = dict(
                session.query(Persona.dni, Persona.nombre_completo).filter(Persona.dni.in_(dnis_sup)).all()
            )
    finally:
        session.close()

    v = pd.DataFrame(filas, columns=[
        "dni", "nombre", "ciudad", "supervisor_dni", "punto_venta_id", "punto_venta", "tipo_negocio",
        "fecha_inicio", "hora_inicio", "hora_fin", "distancia_metros_inicio",
    ])
    v["supervisor"] = v["supervisor_dni"].map(nombre_sup)
    v["fecha_inicio"] = pd.to_datetime(v["fecha_inicio"])
    v["hora_inicio_td"] = pd.to_timedelta(v["hora_inicio"].astype(str), errors="coerce")
    v["hora_fin_td"] = pd.to_timedelta(v["hora_fin"].astype(str), errors="coerce")
    v["geofence_ok"] = v["distancia_metros_inicio"].fillna(99999) <= GEOFENCE_MAX_M
    v["es_censo"] = v["punto_venta"].astype(str).str.upper().str.contains("PUNTO CENSO", na=False)
    return v


def matriz_cobertura(desde, hasta, usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None):
    """PDVs distintos visitados por persona y día -- Punto Censo se cuenta
    aparte (cada relevo es 1, no tiene ubicación real que colapsar), mismo
    criterio que analisis_visitas_planning.py. Devuelve
    (personas, fechas, celdas, categorias) -- celdas: {(dni, fecha): cantidad},
    categorias: {(dni, fecha): ["au"|"farma"|"censo", ...]} para marcar en qué
    canal(es) trabajó ese día (Davor, 2026-08-23: "resaltar cuando son PDVs
    de AU, de Farma, o día de Punto Censo" -- Tradicional queda sin marca por
    ser el canal base/esperado de la mayoría)."""
    v = _cargar_visitas(desde, hasta, usuario_actual,
                        rol_filtro=rol_filtro, region_filtro=region_filtro, supervisor_filtro=supervisor_filtro,
                        ciudad_filtro=ciudad_filtro)
    if not len(v):
        return [], [], {}, {}

    v_normal = v[v["geofence_ok"] & ~v["es_censo"]]
    normal = v_normal.groupby(["dni", "fecha_inicio"])["punto_venta_id"].nunique()
    v_censo = v[v["es_censo"]]
    censo = v_censo.groupby(["dni", "fecha_inicio"]).size()
    total = normal.add(censo, fill_value=0)

    dias_au = set(v_normal[v_normal["tipo_negocio"] == "AUTOSERVICIOS"].groupby(["dni", "fecha_inicio"]).groups.keys())
    dias_farma = set(v_normal[v_normal["tipo_negocio"] == "FARMACIA"].groupby(["dni", "fecha_inicio"]).groups.keys())
    dias_censo = set(censo.index)

    info_persona = v.drop_duplicates("dni").set_index("dni")[["nombre", "ciudad", "supervisor"]]
    personas = [
        {"dni": dni, "nombre": row["nombre"], "ciudad": row["ciudad"], "supervisor": row["supervisor"]}
        for dni, row in info_persona.sort_values("nombre").iterrows()
    ]
    fechas = sorted(v["fecha_inicio"].dt.date.unique())
    celdas = {(dni, fecha.date()): int(cant) for (dni, fecha), cant in total.items()}
    categorias = {}
    for (dni, fecha) in total.index:
        clave = (dni, fecha)
        tags = []
        if clave in dias_au:
            tags.append("au")
        if clave in dias_farma:
            tags.append("farma")
        if clave in dias_censo:
            tags.append("censo")
        if tags:
            categorias[(dni, fecha.date())] = tags
    return personas, fechas, celdas, categorias


def marcaciones_del_dia(fecha, usuario_actual, dni_filtro=None, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None):
    """Detalle fila por fila de cada marcación GPS (visita a PDV) de un día
    puntual -- a diferencia de matriz_cobertura() (resumen agregado, un
    número por día), esto es la bitácora cruda: hora de inicio/fin, PDV,
    canal y si quedó dentro del geofence. Para ver a qué hora marcó
    REALMENTE alguien, ej. cuando una corrección manual en "Marcar
    asistencia" no coincide con lo que el aplicativo registró (Davor,
    2026-08-25, sobre un caso real: puso 08:50 a mano pero la marcación
    real fue a las 11:23). No pesa nada -- reusa la misma `_cargar_visitas`
    que ya trae Cobertura, acotada a un solo día en vez de un rango, y el
    volumen por persona/día es de unas pocas visitas, no miles."""
    v = _cargar_visitas(fecha, fecha, usuario_actual, dni_filtro=dni_filtro,
                        rol_filtro=rol_filtro, region_filtro=region_filtro, supervisor_filtro=supervisor_filtro,
                        ciudad_filtro=ciudad_filtro)
    if not len(v):
        return []

    # Athena entrega la MISMA visita dos veces mientras está en curso -- una
    # fila apenas empieza (hora_fin literalmente el texto "NaN", no NULL) y
    # otra cuando cierra, con la hora real. Como la unicidad de `visitas`
    # incluye hora_fin, ambas se guardan (correcto para no perder historial),
    # pero acá mostrar las dos duplicaría cada visita cerrada. Se prefiere la
    # fila con hora_fin real; si todavía no cerró, se muestra como "en curso".
    v = v.copy()
    v["hora_fin_valida"] = v["hora_fin"].where(v["hora_fin"].notna() & (v["hora_fin"] != "NaN"))
    v = v.sort_values(["dni", "punto_venta_id", "hora_inicio", "hora_fin_valida"], na_position="first")
    v = v.drop_duplicates(subset=["dni", "punto_venta_id", "fecha_inicio", "hora_inicio"], keep="last")

    personas = []
    for (dni, nombre, ciudad, supervisor), grupo in v.groupby(["dni", "nombre", "ciudad", "supervisor_dni"], dropna=False, sort=False):
        marcaciones = [
            {
                "hora_inicio": row["hora_inicio"],
                # pd.isna(), no "if hora_fin_valida" -- NaN es truthy en
                # Python (bool(float('nan')) == True), así que un chequeo
                # ingenuo dejaba pasar el NaN de pandas hasta la plantilla.
                "hora_fin": None if pd.isna(row["hora_fin_valida"]) else row["hora_fin_valida"],
                "punto_venta": row["punto_venta"], "canal": row["tipo_negocio"],
                "geofence_ok": bool(row["geofence_ok"]),
                "distancia": None if pd.isna(row["distancia_metros_inicio"]) else round(row["distancia_metros_inicio"]),
            }
            for _, row in grupo.sort_values("hora_inicio_td").iterrows()
        ]
        personas.append({
            "dni": dni, "nombre": nombre, "ciudad": ciudad,
            "supervisor": grupo["supervisor"].iloc[0], "marcaciones": marcaciones,
        })
    personas.sort(key=lambda p: (p["nombre"] or "").title())
    return personas


def alertas_cobertura(desde, hasta, usuario_actual, dni_filtro=None,
                       rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None):
    """Visita larga (última visita del día > 60 min y antes de la hora de
    salida programada) + abuso de Punto Censo -- mismo cálculo que ya hacía
    (y descartaba) pipeline/alerta_visita_larga.py, ahora contra Postgres.
    Mismo shape de dict que alertas.py::alertas_periodo() para poder
    mezclarse en la misma lista."""
    v = _cargar_visitas(desde, hasta, usuario_actual, dni_filtro=dni_filtro,
                        rol_filtro=rol_filtro, region_filtro=region_filtro, supervisor_filtro=supervisor_filtro,
                        ciudad_filtro=ciudad_filtro)
    if not len(v):
        return []

    idx_historial = cargar_historial()
    session = get_session()
    try:
        patrones = session.query(PatronRecurrente).all()
    finally:
        session.close()
    salida_prog_map = {}
    for p in patrones:
        if p.hora_salida_prog is None:
            continue
        salida_prog_map[(p.dni, _sin_acentos(p.dia_semana))] = p.hora_salida_prog

    alertas = []

    # -- Visita larga --
    patron_excluido = "|".join(PUNTOS_EXCLUIDOS)
    v_ok = v[
        v["geofence_ok"] & (v["hora_fin_td"] != CIERRE_AUTOMATICO_TD)
        & ~v["punto_venta"].astype(str).str.upper().str.contains(patron_excluido, na=False)
    ].copy()
    if len(v_ok):
        v_ok["duracion_min"] = (v_ok["hora_fin_td"] - v_ok["hora_inicio_td"]).dt.total_seconds() / 60
        idx_ultima = v_ok.groupby(["dni", "fecha_inicio"])["hora_inicio_td"].idxmax()
        ultimas = v_ok.loc[idx_ultima]
        por_persona = {}
        for _, row in ultimas.iterrows():
            weekday = row["fecha_inicio"].weekday()
            salida_prog = salida_prog_map.get((row["dni"], WD_NORM.get(weekday)))
            salida_prog = valor_efectivo(idx_historial, row["dni"], "Hora salida programada", row["fecha_inicio"], salida_prog)
            if salida_prog is None:
                continue
            salida_prog_td = pd.to_timedelta(str(salida_prog))
            if row["duracion_min"] > UMBRAL_VISITA_LARGA_MIN and row["hora_inicio_td"] < salida_prog_td:
                por_persona.setdefault(row["dni"], []).append(row)
        for dni, filas_persona in por_persona.items():
            cantidad = len(filas_persona)
            nombre = filas_persona[0]["nombre"]
            alertas.append({
                "dni": dni, "nombre": nombre, "tipo": "visita_larga",
                "cantidad": cantidad, "nivel": max(1, cantidad // 3),
                "mensaje": f"Última visita del día inusualmente larga ({cantidad}x este periodo, antes de la hora de salida programada)",
                "fechas": sorted(f["fecha_inicio"].strftime("%d/%m") for f in filas_persona),
                "motivos": sorted({f"{f['punto_venta']} ({f['duracion_min']:.0f} min)" for f in filas_persona}),
            })

    # -- Punto Censo --
    censo = v[v["es_censo"]].copy()
    if len(censo):
        censo["duracion_min"] = (censo["hora_fin_td"] - censo["hora_inicio_td"]).dt.total_seconds() / 60
        resumen = censo.groupby("dni").agg(
            nombre=("nombre", "first"),
            dias_distintos=("fecha_inicio", "nunique"),
            duracion_max_min=("duracion_min", "max"),
            duracion_total_min=("duracion_min", "sum"),
        )
        alertados = resumen[
            (resumen["dias_distintos"] > UMBRAL_DIAS_CENSO) | (resumen["duracion_max_min"] > UMBRAL_DURACION_CENSO_MIN)
        ]
        for dni, row in alertados.iterrows():
            alertas.append({
                "dni": dni, "nombre": row["nombre"], "tipo": "punto_censo",
                "cantidad": int(row["dias_distintos"]), "nivel": max(1, int(row["dias_distintos"]) // UMBRAL_DIAS_CENSO),
                "mensaje": f"Uso de Punto Censo por encima de lo esporádico ({int(row['dias_distintos'])} días distintos, {row['duracion_total_min']:.0f} min en total)",
                "fechas": sorted(f.strftime("%d/%m") for f in censo[censo["dni"] == dni]["fecha_inicio"].unique()),
                "motivos": [],
            })

    return alertas
