# -*- coding: utf-8 -*-
"""Reportes -- Cobertura vs Planning (canal Tradicional), 2026-09-07. Porta
la parte de cruce contra el Planning mensual de MAC/Planning/analisis_visitas_planning.py
(ver MAC/cruce_planning_pdv.py) -- lo demás de ese script (geocerca, Punto
Censo, visita larga) ya vive en cobertura.py y no se duplica acá.

Restringido a Davor (admin) y Kevin Llanos (analista, canal_asignado=TRADICIONAL)
-- ver permisos.py::_RESTRINGIDO_INDIVIDUAL. Solo canal Tradicional: se fuerza
`canal_filtro=["TRADICIONAL"]` en toda consulta de visitas de este módulo, sin
importar qué filtro elija quien mira (a diferencia de Cobertura, acá no hay
selector de canal)."""
import datetime as dt

import pandas as pd

from cobertura import _cargar_visitas
from dimension_models import PlanningPdv, get_session
from parseo_headcount import validar_columnas

COLUMNAS_MAYORISTA_ESPERADAS = [
    "CO_LI", "NOMBRE PDV", "CIUDAD", "REGIÓN", "SUBCANAL", "NOMBRE DEL MERCADO",
    "DNI", "MERCADERISTA", "SUPERVISOR",
]
COLUMNAS_MINORISTA_ESPERADAS = COLUMNAS_MAYORISTA_ESPERADAS


def _columnas_fecha(df):
    """Mismo criterio que analisis_visitas_planning.py: una columna de
    fecha es cualquier encabezado que pandas pueda parsear como fecha
    (dayfirst=True) -- el formato exacto ("1/09/2026" vs "01/09/26") varía
    entre Mayorista y Minorista, así que no se busca un formato fijo."""
    return [c for c in df.columns if isinstance(c, str) and pd.notna(pd.to_datetime(c, dayfirst=True, errors="coerce"))]


def _dias_programados(df, cols_fecha):
    if not cols_fecha:
        return pd.Series(0, index=df.index)
    return df[cols_fecha].notna().sum(axis=1)


def _tabla_normalizada(df, tipo):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cols_fecha = _columnas_fecha(df)
    dias_programados = _dias_programados(df, cols_fecha)

    co_li_num = pd.to_numeric(df["CO_LI"], errors="coerce")
    df = df[co_li_num.notna()].copy()
    dias_programados = dias_programados[co_li_num.notna()]

    salida = pd.DataFrame({
        "co_li": co_li_num[co_li_num.notna()].astype("int64").astype(str),
        "tipo": tipo,
        "nombre_pdv": df.get("NOMBRE PDV"),
        "ciudad": df.get("CIUDAD"),
        "region": df.get("REGIÓN"),
        "subcanal": df.get("SUBCANAL"),
        "mercado": df.get("NOMBRE DEL MERCADO"),
        "dni_asignado": df.get("DNI"),
        "mercaderista_nombre": df.get("MERCADERISTA"),
        "supervisor": df.get("SUPERVISOR"),
        "dias_programados": dias_programados,
    })
    salida["dni_asignado"] = salida["dni_asignado"].apply(
        lambda v: str(int(v)) if pd.notna(v) and str(v).strip() else None
    )
    for col in ("nombre_pdv", "ciudad", "region", "subcanal", "mercado", "mercaderista_nombre", "supervisor"):
        salida[col] = salida[col].apply(lambda v: str(v).strip() if pd.notna(v) and str(v).strip() else None)
    return salida


def _consolidar_por_pdv(df):
    """Un mismo CO_LI puede aparecer en MÁS DE UNA fila del Planning --
    distintas actividades/semanas del mismo PDV en el mes, mismo criterio
    que ya documentaba analisis_visitas_planning.py ("Visitas_planificadas
    = suma de ese conteo por fila", ver comentario de _dias_programados).
    Sin agrupar acá, la 2da fila del mismo CO_LI+tipo rompía la restricción
    UNIQUE(periodo, co_li, tipo) al guardar -- error real en producción,
    2026-09-08 ("me sale error 500 al cargar el planning").

    "first" (no una función Python propia) a propósito: GroupBy.first() ya
    toma el primer valor NO NULO de cada columna (skipna=True por default)
    con la implementación vectorizada de pandas -- una función Python
    personalizada en .agg() fuerza el camino "pure python" (una llamada por
    grupo, en Python puro, no vectorizado), que con el volumen real del
    Excel de Planning (miles de filas) tardaba tanto que gunicorn mataba el
    worker por timeout -- WORKER TIMEOUT real en producción, 2026-09-08."""
    if not len(df):
        return df
    return df.groupby(["co_li", "tipo"], as_index=False).agg({
        "nombre_pdv": "first", "ciudad": "first", "region": "first",
        "subcanal": "first", "mercado": "first", "dni_asignado": "first",
        "mercaderista_nombre": "first", "supervisor": "first",
        "dias_programados": "sum",
    })


def parsear_planning(archivo):
    """`archivo`: ruta o file-like (BytesIO de un upload), igual que
    parseo_headcount.parsear_maestro(). Lee las hojas "Mayorista" (header en
    la fila 1) y "Minorista" (header en la fila 4 -- 3 filas de título arriba,
    mismo formato real que ya usaba el Excel de Planning en MAC/Planning/REPORTES/).
    Devuelve un DataFrame combinado, UNA fila por (co_li, tipo) -- ver
    _consolidar_por_pdv() para el caso de un CO_LI repetido dentro del mismo
    archivo."""
    mayorista = pd.read_excel(archivo, sheet_name="Mayorista", header=0)
    validar_columnas(mayorista, COLUMNAS_MAYORISTA_ESPERADAS, "Mayorista")
    minorista = pd.read_excel(archivo, sheet_name="Minorista", header=3)
    validar_columnas(minorista, COLUMNAS_MINORISTA_ESPERADAS, "Minorista")

    combinado = pd.concat([
        _tabla_normalizada(mayorista, "Mayorista"),
        _tabla_normalizada(minorista, "Minorista"),
    ], ignore_index=True)
    return _consolidar_por_pdv(combinado)


def guardar_planning(df, periodo, cargado_por):
    """Reemplaza TODAS las filas de `periodo` (sin importar el tipo) por las
    de `df` -- más simple que un upsert fila por fila y evita dejar basura de
    PDVs que salieron del planning ese mes (Davor podría re-subir un mes ya
    cargado para corregirlo)."""
    session = get_session()
    try:
        session.query(PlanningPdv).filter(PlanningPdv.periodo == periodo).delete()
        hoy = dt.date.today()
        for _, fila in df.iterrows():
            session.add(PlanningPdv(
                periodo=periodo, co_li=fila["co_li"], tipo=fila["tipo"],
                nombre_pdv=fila["nombre_pdv"], ciudad=fila["ciudad"], region=fila["region"],
                subcanal=fila["subcanal"], mercado=fila["mercado"], dni_asignado=fila["dni_asignado"],
                mercaderista_nombre=fila["mercaderista_nombre"], supervisor=fila["supervisor"],
                dias_programados=int(fila["dias_programados"]), cargado_por=cargado_por, cargado_en=hoy,
            ))
        session.commit()
        return len(df)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def planning_del_periodo(periodo, region_filtro=None, ciudad_filtro=None, supervisor_filtro=None):
    session = get_session()
    try:
        query = session.query(PlanningPdv).filter(PlanningPdv.periodo == periodo)
        if region_filtro:
            query = query.filter(PlanningPdv.region == region_filtro)
        if ciudad_filtro:
            query = query.filter(PlanningPdv.ciudad == ciudad_filtro)
        if supervisor_filtro:
            query = query.filter(PlanningPdv.supervisor == supervisor_filtro)
        return query.all()
    finally:
        session.close()


def visitas_tradicional(desde, hasta, usuario_actual):
    """Igual que cobertura._cargar_visitas(), pero forzando canal Tradicional
    -- esta pestaña no tiene selector de canal, siempre es Tradicional.

    A propósito SIN región/ciudad/supervisor -- esos filtros acotan cuáles
    PDVs del Planning se muestran (ver planning_del_periodo(), que sí los
    aplica sobre PlanningPdv, texto libre del Excel), pero acá se necesita
    el universo COMPLETO de visitas Tradicional para poder responder "¿este
    CO_LI lo visitó alguien?" sin importar el filtro de pantalla. Antes se
    reusaban region_filtro/ciudad_filtro/supervisor_filtro de
    aplicar_filtros_extra(), que filtran sobre Persona (supervisor_dni es un
    DNI) -- al pasarle el NOMBRE de supervisor del Planning (texto libre,
    "Ana Carbajal") esa comparación nunca matcheaba ningún DNI real y la
    consulta de visitas volvía vacía en silencio, mostrando 0% de
    cumplimiento aunque sí hubiera visitas reales (2026-09-08, reporte real
    de Davor: "es imposible que ningun PDV se haya visitado hasta ahora")."""
    return _cargar_visitas(desde, hasta, usuario_actual, canal_filtro=["TRADICIONAL"])


def periodos_disponibles():
    """Meses con al menos un PlanningPdv cargado, más recientes primero --
    para el selector de período (default: mes actual, aunque todavía no
    tenga nada cargado)."""
    session = get_session()
    try:
        periodos = sorted({p for (p,) in session.query(PlanningPdv.periodo).distinct().all()}, reverse=True)
        return periodos
    finally:
        session.close()


def valores_filtrables(periodo):
    """Región/Ciudad/Supervisor distintos DENTRO del planning de `periodo` --
    a propósito no reusa los desplegables de Persona (reportes._filtros_admin)
    porque son texto libre tipeado en el Excel de Planning, no siempre calzan
    letra por letra con lo que hay en la tabla Persona."""
    session = get_session()
    try:
        query = session.query(PlanningPdv).filter(PlanningPdv.periodo == periodo)
        filas = query.with_entities(PlanningPdv.region, PlanningPdv.ciudad, PlanningPdv.supervisor).all()
        regiones = sorted({r for r, _, _ in filas if r})
        ciudades = sorted({c for _, c, _ in filas if c})
        supervisores = sorted({s for _, _, s in filas if s})
        return regiones, ciudades, supervisores
    finally:
        session.close()


def resumen_planning(planning, v):
    """KPIs del período: headcount con planning asignado, PDVs en planning,
    PDVs visitados (de ese planning), % cumplimiento -- cruce por CO_LI
    nomás, sin importar quién hizo la visita (Davor, 2026-09-08: "no
    importa si lo visito el mercaderista asignado, la idea es que haya
    sido visitado"). Antes cruzaba por (dni_asignado, co_li) -- mismo
    criterio que analisis_visitas_planning.py, pensado para no "compartir"
    una visita entre 2 mercaderistas con el mismo CO_LI en turnos
    rotativos -- pero eso significaba que un PDV SÍ visitado (por
    cualquiera) contaba como pendiente si no fue justo el DNI asignado en
    el Excel quien lo visitó, dando un % de cumplimiento más bajo que el
    real y, peor, INCONSISTENTE con pdvs_pendientes()/pdvs_detalle() (que
    siempre cruzaron por CO_LI a secas) -- mismo cruce en las 4 funciones
    ahora.

    `planning` (lista de PlanningPdv) y `v` (DataFrame de visitas_tradicional)
    se cargan UNA sola vez en la ruta que llama a esta función (y a
    pdvs_pendientes/pdvs_detalle/pdvs_fuera_planning) -- antes cada una hacía
    su propia consulta completa a Postgres, 4 veces la misma carga de
    visitas del canal Tradicional por cada vista de la página, lo que
    colgaba el worker con el volumen real de datos (502 en producción,
    2026-09-08)."""
    dnis_planning = {p.dni_asignado for p in planning if p.dni_asignado}
    co_lis_planning = {p.co_li for p in planning}
    co_lis_visitados = set(v["punto_venta_id"].astype(str)) if len(v) else set()

    n_planning = len(co_lis_planning)
    n_visitados = len(co_lis_planning & co_lis_visitados)
    pct_cumplimiento = round(n_visitados / n_planning * 100, 1) if n_planning else None

    return {
        "headcount": len(dnis_planning),
        "pdvs_planning": n_planning,
        "pdvs_visitados": n_visitados,
        "pct_cumplimiento": pct_cumplimiento,
    }


def pdvs_pendientes(planning, v):
    """PDVs del planning que NO tienen ninguna visita (de nadie) en el
    período -- responde "qué me falta visitar"."""
    visitados = set(v["punto_venta_id"].astype(str)) if len(v) else set()

    pendientes = [p for p in planning if p.co_li not in visitados]
    pendientes.sort(key=lambda p: (p.nombre_pdv or ""))
    return [{
        "co_li": p.co_li, "nombre_pdv": p.nombre_pdv, "tipo": p.tipo, "ciudad": p.ciudad,
        "region": p.region, "mercaderista": p.mercaderista_nombre, "supervisor": p.supervisor,
    } for p in pendientes]


def pdvs_detalle(planning, v):
    """Por cada CO_LI del planning: golpes de visita PLANIFICADOS (columnas
    de fecha marcadas en el Excel, `dias_programados` -- ver
    _consolidar_por_pdv()) vs REALIZADOS (cualquier persona) y fecha de la
    última -- responde "le programamos 10 golpes, cuántos tiene de
    verdad" (Davor, 2026-09-08). % cumplimiento por PDV, no confundir con
    el % cumplimiento del resumen (ese es binario -- visitado sí/no -- este
    es realizados/planificados)."""
    conteo, ultima = {}, {}
    if len(v):
        v = v.copy()
        v["punto_venta_id"] = v["punto_venta_id"].astype(str)
        conteo = v.groupby("punto_venta_id").size().to_dict()
        ultima = v.groupby("punto_venta_id")["fecha_inicio"].max().to_dict()

    filas = []
    for p in planning:
        realizadas = int(conteo.get(p.co_li, 0))
        planificadas = int(p.dias_programados or 0)
        filas.append({
            "co_li": p.co_li, "nombre_pdv": p.nombre_pdv, "tipo": p.tipo, "ciudad": p.ciudad,
            "region": p.region, "mercaderista": p.mercaderista_nombre, "supervisor": p.supervisor,
            "visitas_planificadas": planificadas, "visitas_realizadas": realizadas,
            "pct_cumplimiento": round(realizadas / planificadas * 100, 1) if planificadas else None,
            "fecha_ultima_visita": ultima.get(p.co_li).date() if p.co_li in ultima and pd.notna(ultima.get(p.co_li)) else None,
        })
    filas.sort(key=lambda f: (f["pct_cumplimiento"] if f["pct_cumplimiento"] is not None else -1))
    return filas


def pdvs_fuera_planning(planning, v):
    """Visitas Tradicional del período cuyo punto_venta_id no está en el
    planning de ese período -- responde "hay visitas de clientes que no
    están en el planning". Simplificación conocida del MVP: no audita contra
    Athena (dim_lf_general_campana_pdv) si el punto_venta_id es un PDV real
    de campaña Tradicional -- ver plan."""
    co_lis_planning = {p.co_li for p in planning}
    if not len(v):
        return []

    v = v.copy()
    v["punto_venta_id"] = v["punto_venta_id"].astype(str)
    fuera = v[~v["punto_venta_id"].isin(co_lis_planning)]
    if not len(fuera):
        return []

    resumen = fuera.groupby(["punto_venta_id", "punto_venta", "dni", "nombre"]).agg(
        veces_visitado=("fecha_inicio", "count"),
        primera_visita=("fecha_inicio", "min"),
        ultima_visita=("fecha_inicio", "max"),
    ).reset_index()
    resumen = resumen.sort_values("veces_visitado", ascending=False)
    return [{
        "punto_venta_id": r["punto_venta_id"], "punto_venta": r["punto_venta"],
        "dni": r["dni"], "nombre": r["nombre"], "veces_visitado": int(r["veces_visitado"]),
        "primera_visita": r["primera_visita"].date(), "ultima_visita": r["ultima_visita"].date(),
    } for _, r in resumen.iterrows()]
