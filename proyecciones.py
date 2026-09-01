# -*- coding: utf-8 -*-
"""Reportes -- Proyecciones (Davor, 2026-09-01, retomando el Horizonte 3 del
reporte "Radar de Indicadores de Campo"): score de riesgo de rotación,
necesidad de contratación proyectada, estacionalidad de faltas/tardanzas y
ranking de próxima falta probable. Mismo criterio que recomendaciones.py:
reglas simples y documentadas sobre datos ya validados, NO un modelo de
machine learning.

OJO -- el sistema en la nube arrancó el 2026-07-01, así que hay apenas ~2
meses de historia y pocas bajas por supervisor (20 en total, 9 supervisores).
Confirmado con Davor 2026-09-01: se construye igual, pero rotación/
contratación se marcan "preliminar" hasta tener más meses acumulados -- no
ocultar esa limitación en la interfaz."""
import datetime as dt

import pandas as pd

from dimension_models import Persona, get_session
from fact_models import ClasificacionDiaria
from recomendaciones import insights_equipo
from scoping import aplicar_filtros_extra, condicion_scope

# Mismo valor que pipeline/traer_visitas_historico.py::INICIO_SISTEMA --
# duplicado a propósito (ese módulo vive en pipeline/, que no es un paquete
# importable desde la app web, ver conftest.py) en vez de importarlo.
INICIO_SISTEMA = dt.date(2026, 7, 1)

MESES_MINIMOS_CONFIABLE = 3  # menos que esto, todo sale marcado "preliminar"
DIAS_ANTIGUEDAD_RIESGO = 90  # menos de 3 meses en el puesto = bonus de riesgo

# Pesos del score de riesgo de rotación (documentados acá, no mágicos en el
# cálculo) -- señales de Desempeño ya validadas + antigüedad + rotación
# histórica de su CIUDAD (ajustado por Davor, 2026-09-01 -- antes era
# 40/20/40 con rotación por supervisor).
PESO_SENALES = 0.60
PESO_ANTIGUEDAD = 0.20
PESO_ROTACION_CIUDAD = 0.20


def _meses_observados(hasta=None):
    hasta = hasta or dt.date.today()
    return max((hasta - INICIO_SISTEMA).days / 30.44, 1.0)


def _personas_visibles(usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None, solo_activos=True):
    session = get_session()
    try:
        query = session.query(Persona)
        if solo_activos:
            query = query.filter(Persona.estado == "Activo")
        cond_scope = condicion_scope(Persona, usuario_actual)
        if cond_scope is not None:
            query = query.filter(cond_scope)
        query = aplicar_filtros_extra(query, Persona, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro)
        return query.all()
    finally:
        session.close()


def _tasa_rotacion_por(clave_de, usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None):
    """{clave: {"headcount_actual", "bajas_historicas", "tasa_mensual_pct", "preliminar"}}
    -- tasa de rotación mensual aproximada: bajas históricas / tamaño
    promedio del grupo (activos + bajas, ya que no hay snapshots mes a mes
    de headcount) / meses observados desde INICIO_SISTEMA. `clave_de(persona)`
    decide el agrupamiento (por supervisor_dni, por ciudad, etc.) -- helper
    compartido por tasa_rotacion_por_supervisor()/tasa_rotacion_por_ciudad()
    para no duplicar esta consulta+cálculo por cada dimensión."""
    activos = _personas_visibles(
        usuario_actual, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro, solo_activos=True
    )
    session = get_session()
    try:
        query_bajas = session.query(Persona).filter(Persona.fecha_baja.isnot(None))
        cond_scope = condicion_scope(Persona, usuario_actual)
        if cond_scope is not None:
            query_bajas = query_bajas.filter(cond_scope)
        query_bajas = aplicar_filtros_extra(query_bajas, Persona, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro)
        bajas = query_bajas.all()
    finally:
        session.close()

    meses = _meses_observados()
    preliminar = meses < MESES_MINIMOS_CONFIABLE

    headcount_por_clave = {}
    for p in activos:
        clave = clave_de(p)
        if clave:
            headcount_por_clave.setdefault(clave, []).append(p)
    bajas_por_clave = {}
    for p in bajas:
        clave = clave_de(p)
        if clave:
            bajas_por_clave.setdefault(clave, []).append(p)

    claves = set(headcount_por_clave) | set(bajas_por_clave)
    resultado = {}
    for clave in claves:
        headcount_actual = len(headcount_por_clave.get(clave, []))
        bajas_historicas = len(bajas_por_clave.get(clave, []))
        tamano_promedio = max(headcount_actual + bajas_historicas, 1)
        tasa_mensual_pct = bajas_historicas / tamano_promedio / meses * 100
        resultado[clave] = {
            "headcount_actual": headcount_actual, "bajas_historicas": bajas_historicas,
            "tasa_mensual_pct": round(tasa_mensual_pct, 1), "preliminar": preliminar,
        }
    return resultado


def tasa_rotacion_por_supervisor(usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None):
    """{supervisor_dni: {...}} -- alimenta necesidad_contratacion() (por
    supervisor, para saber a quién anticiparle el reclutamiento).

    Público (sin `_`) a propósito: reportes.py::proyecciones() lo calcula
    UNA sola vez y lo pasa a necesidad_contratacion() via `tasas_sup=` --
    calcularlo por cada función que lo necesita fue el mismo error de
    duplicar calcular_detalle_semana() que ya tumbó Desempeño con 503 en
    Render (Davor, 2026-09-01)."""
    return _tasa_rotacion_por(
        lambda p: p.supervisor_dni, usuario_actual, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro
    )


def tasa_rotacion_por_ciudad(usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None):
    """{ciudad: {...}} -- alimenta score_riesgo_rotacion() (ajustado por
    Davor, 2026-09-01: la rotación histórica de la CIUDAD, no del
    supervisor, es la que cuenta para el riesgo por persona)."""
    return _tasa_rotacion_por(
        lambda p: p.ciudad, usuario_actual, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro
    )


def necesidad_contratacion(usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None, tasas_sup=None):
    """Lista de dicts por supervisor: headcount actual, tasa de rotación
    mensual, bajas históricas y vacantes proyectadas a 1/3 meses -- para
    anticipar reclutamiento en vez de reaccionar recién cuando la posición
    ya está vacía. `preliminar=True` (siempre por ahora, ver docstring del
    módulo) avisa que la tasa es una primera aproximación con poca
    historia todavía.

    `tasas_sup`: si ya se calculó tasa_rotacion_por_supervisor() afuera
    (ver reportes.py::proyecciones()), se reusa en vez de volver a
    consultar."""
    tasas = tasas_sup if tasas_sup is not None else tasa_rotacion_por_supervisor(
        usuario_actual, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro
    )
    if not tasas:
        return []
    session = get_session()
    try:
        nombres = dict(session.query(Persona.dni, Persona.nombre_completo).filter(Persona.dni.in_(tasas.keys())).all())
    finally:
        session.close()

    resultado = []
    for sup_dni, datos in tasas.items():
        if datos["headcount_actual"] == 0:
            continue  # supervisor sin nadie activo hoy -- nada que proyectar
        tasa = datos["tasa_mensual_pct"] / 100
        resultado.append({
            "supervisor_dni": sup_dni, "supervisor_nombre": nombres.get(sup_dni, sup_dni),
            "headcount_actual": datos["headcount_actual"], "bajas_historicas": datos["bajas_historicas"],
            "tasa_mensual_pct": datos["tasa_mensual_pct"],
            "vacantes_1_mes": round(tasa * datos["headcount_actual"], 1),
            "vacantes_3_meses": round(tasa * datos["headcount_actual"] * 3, 1),
            "preliminar": datos["preliminar"],
        })
    resultado.sort(key=lambda r: r["tasa_mensual_pct"], reverse=True)
    return resultado


def estacionalidad_faltas(usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None):
    """% de Falta y % de Tardanza por día de la semana, desde INICIO_SISTEMA
    -- para reforzar equipo o anticipar cobertura los días que históricamente
    concentran más incidencias. Domingo no aparece (el motor no genera fila
    ese día). Solo desglose por día de semana -- día del mes/estacionalidad
    anual todavía no tiene suficiente historia (ver docstring del módulo)."""
    session = get_session()
    try:
        query = (
            session.query(ClasificacionDiaria.dia_semana, ClasificacionDiaria.estado)
            .join(Persona, Persona.dni == ClasificacionDiaria.dni)
            .filter(ClasificacionDiaria.fecha >= INICIO_SISTEMA)
        )
        cond_scope = condicion_scope(Persona, usuario_actual)
        if cond_scope is not None:
            query = query.filter(cond_scope)
        query = aplicar_filtros_extra(query, Persona, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro)
        filas = query.all()
    finally:
        session.close()
    if not filas:
        return []

    df = pd.DataFrame(filas, columns=["dia_semana", "estado"])
    df["estado_base"] = df["estado"].apply(lambda s: (s or "").split(" (")[0])

    orden_dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]
    resultado = []
    for dia in orden_dias:
        grupo = df[df["dia_semana"] == dia]
        total = len(grupo)
        if not total:
            continue
        faltas = int((grupo["estado_base"] == "FALTA").sum())
        tardanzas = int((grupo["estado_base"] == "TARDANZA").sum())
        resultado.append({
            "dia": dia, "total_dias_persona": total,
            "pct_falta": round(faltas / total * 100, 1), "pct_tardanza": round(tardanzas / total * 100, 1),
        })
    return resultado


def _dias_persona_por_dia_semana(usuario_actual, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro):
    """{dni: {dia_semana: pct_falta}} -- estacionalidad a nivel INDIVIDUAL,
    para ranking_proxima_falta(). Si una persona tiene muy pocos días
    observados un día de semana puntual (< 3), ese día no entra al dict --
    ranking_proxima_falta() cae al promedio del equipo (estacionalidad_faltas())
    en ese caso, para no sacar un % de 1 solo día como si fuera un patrón."""
    session = get_session()
    try:
        query = (
            session.query(ClasificacionDiaria.dni, ClasificacionDiaria.dia_semana, ClasificacionDiaria.estado)
            .join(Persona, Persona.dni == ClasificacionDiaria.dni)
            .filter(ClasificacionDiaria.fecha >= INICIO_SISTEMA)
        )
        cond_scope = condicion_scope(Persona, usuario_actual)
        if cond_scope is not None:
            query = query.filter(cond_scope)
        query = aplicar_filtros_extra(query, Persona, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro)
        filas = query.all()
    finally:
        session.close()
    if not filas:
        return {}

    df = pd.DataFrame(filas, columns=["dni", "dia_semana", "estado"])
    df["estado_base"] = df["estado"].apply(lambda s: (s or "").split(" (")[0])

    resultado = {}
    for (dni, dia), grupo in df.groupby(["dni", "dia_semana"]):
        if len(grupo) < 3:
            continue
        pct = (grupo["estado_base"] == "FALTA").sum() / len(grupo) * 100
        resultado.setdefault(dni, {})[dia] = pct
    return resultado


def score_riesgo_rotacion(usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None, tasas_ciudad=None, señales=None):
    """Lista de dicts {dni, nombre, score, detalle} ordenada de mayor a
    menor riesgo -- puntaje 0-100 combinando (pesos en PESO_SENALES/
    PESO_ANTIGUEDAD/PESO_ROTACION_CIUDAD, ajustados por Davor 2026-09-01):
    cuántas señales de Desempeño tiene activas ahora mismo, si es de
    ingreso reciente (< DIAS_ANTIGUEDAD_RIESGO días), y la tasa de
    rotación histórica de su CIUDAD relativa al promedio del equipo
    visible.

    `tasas_ciudad`/`señales`: si ya se calcularon afuera (ver
    reportes.py::proyecciones()), se reusan en vez de volver a consultar
    -- insights_equipo() es lo más pesado de todo el reporte (llama a
    calcular_detalle_semana()), no se puede dar el lujo de recalcularlo
    por cada sección que lo necesita."""
    personas = _personas_visibles(usuario_actual, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro, solo_activos=True)
    if not personas:
        return []

    if señales is None:
        señales = insights_equipo(
            usuario_actual, rol_filtro=rol_filtro, region_filtro=region_filtro,
            supervisor_filtro=supervisor_filtro, ciudad_filtro=ciudad_filtro, canal_filtro=canal_filtro,
        )
    MAX_SENALES = 4  # tardanza_creciente, cumplimiento_bajando, riesgo_semana, cerca_alerta_(falta|tardanza) cuenta como 1
    señales_por_dni = {}
    for s in señales:
        señales_por_dni[s["dni"]] = señales_por_dni.get(s["dni"], 0) + 1

    if tasas_ciudad is None:
        tasas_ciudad = tasa_rotacion_por_ciudad(usuario_actual, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro)
    tasas_validas = [d["tasa_mensual_pct"] for d in tasas_ciudad.values()]
    tasa_promedio_equipo = sum(tasas_validas) / len(tasas_validas) if tasas_validas else 0

    hoy = dt.date.today()
    resultado = []
    for p in personas:
        n_señales = min(señales_por_dni.get(p.dni, 0), MAX_SENALES)
        comp_señales = n_señales / MAX_SENALES * 100

        dias_antiguedad = (hoy - p.fecha_ingreso).days if p.fecha_ingreso else None
        comp_antiguedad = 100.0 if dias_antiguedad is not None and dias_antiguedad < DIAS_ANTIGUEDAD_RIESGO else 0.0

        tasa_ciudad = tasas_ciudad.get(p.ciudad, {}).get("tasa_mensual_pct", 0) if p.ciudad else 0
        if tasa_promedio_equipo > 0:
            comp_rotacion = min(tasa_ciudad / tasa_promedio_equipo * 50, 100.0)
        else:
            comp_rotacion = 0.0

        score = comp_señales * PESO_SENALES + comp_antiguedad * PESO_ANTIGUEDAD + comp_rotacion * PESO_ROTACION_CIUDAD
        resultado.append({
            "dni": p.dni, "nombre": p.nombre_completo,
            "score": round(score, 1),
            "n_señales": n_señales, "ingreso_reciente": comp_antiguedad == 100.0,
            "tasa_rotacion_ciudad": tasa_ciudad,
        })
    resultado.sort(key=lambda r: r["score"], reverse=True)
    return resultado


def ranking_proxima_falta(usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None, riesgo=None, estacionalidad_equipo=None):
    """El techo de las 3 funciones de arriba: score 0-100 por persona que
    combina estacionalidad individual (o del equipo si no hay suficiente
    historia propia) del día de hoy, su score_riesgo_rotacion(), y sus
    faltas/tardanzas de los últimos 14 días (recencia simple: cuentan
    doble las de la última semana) -- para decirle al supervisor a quién
    prestarle atención ANTES de que falte.

    `riesgo`/`estacionalidad_equipo`: si ya se calcularon afuera (ver
    reportes.py::proyecciones()), se reusan -- mismo motivo que
    tasas_sup/señales en score_riesgo_rotacion()."""
    if riesgo is None:
        riesgo = score_riesgo_rotacion(usuario_actual, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro)
    if not riesgo:
        return []
    riesgo_por_dni = {r["dni"]: r["score"] for r in riesgo}

    hoy = dt.date.today()
    dia_semana_hoy = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][hoy.weekday()]

    estacionalidad_individual = _dias_persona_por_dia_semana(usuario_actual, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro)
    if estacionalidad_equipo is None:
        estacionalidad_equipo = estacionalidad_faltas(usuario_actual, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro)
    estacionalidad_equipo = {e["dia"]: e["pct_falta"] for e in estacionalidad_equipo}
    pct_falta_hoy_equipo = estacionalidad_equipo.get(dia_semana_hoy, 0)

    session = get_session()
    try:
        dnis = list(riesgo_por_dni.keys())
        desde_reciente = hoy - dt.timedelta(days=14)
        desde_semana = hoy - dt.timedelta(days=7)
        filas_recientes = (
            session.query(ClasificacionDiaria.dni, ClasificacionDiaria.fecha, ClasificacionDiaria.estado)
            .filter(ClasificacionDiaria.dni.in_(dnis), ClasificacionDiaria.fecha >= desde_reciente, ClasificacionDiaria.fecha < hoy)
            .all()
        )
    finally:
        session.close()

    incidencias_recientes = {}
    for dni, fecha, estado in filas_recientes:
        estado_base = (estado or "").split(" (")[0]
        if estado_base not in ("FALTA", "TARDANZA"):
            continue
        peso = 2 if fecha >= desde_semana else 1
        incidencias_recientes[dni] = incidencias_recientes.get(dni, 0) + peso
    max_incidencias = max(incidencias_recientes.values()) if incidencias_recientes else 1

    resultado = []
    for r in riesgo:
        dni = r["dni"]
        pct_falta_hoy = estacionalidad_individual.get(dni, {}).get(dia_semana_hoy, pct_falta_hoy_equipo)
        comp_estacionalidad = min(pct_falta_hoy, 100.0)
        comp_riesgo = riesgo_por_dni.get(dni, 0)
        comp_reciente = min(incidencias_recientes.get(dni, 0) / max_incidencias * 100, 100.0)

        score = comp_estacionalidad * 0.3 + comp_riesgo * 0.4 + comp_reciente * 0.3
        resultado.append({
            "dni": dni, "nombre": r["nombre"], "score": round(score, 1),
            "pct_falta_dia_hoy": round(pct_falta_hoy, 1), "dia_semana_hoy": dia_semana_hoy,
        })
    resultado.sort(key=lambda r: r["score"], reverse=True)
    return resultado
