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
import re
from collections import Counter

import pandas as pd

from cobertura import _cargar_visitas
from dimension_models import PlanningPdv, PlanningPdvDia, Persona, get_session
from parseo_headcount import validar_columnas
from reemplazos import resolver_cadena_vigente

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


def _fechas_marcadas(df, cols_fecha):
    """Lista de fechas (date) puntuales marcadas con "X" en cada fila --
    a diferencia de _dias_programados() (solo el TOTAL), esto guarda EN
    QUÉ días calendario le tocaba cada PDV (Davor, 2026-10-15: "quiero
    que por día pueda tener PDV visitado y PDVs fuera de lo programado al
    día"). Se usa para poblar PlanningPdvDia en guardar_planning()."""
    if not cols_fecha:
        return pd.Series([[] for _ in range(len(df))], index=df.index)
    fecha_de_col = {c: pd.to_datetime(c, dayfirst=True).date() for c in cols_fecha}
    marcado = df[cols_fecha].notna()
    return marcado.apply(lambda fila: [fecha_de_col[c] for c in cols_fecha if fila[c]], axis=1)


def _tabla_normalizada(df, tipo):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cols_fecha = _columnas_fecha(df)
    dias_programados = _dias_programados(df, cols_fecha)
    fechas_programadas = _fechas_marcadas(df, cols_fecha)

    co_li_num = pd.to_numeric(df["CO_LI"], errors="coerce")
    df = df[co_li_num.notna()].copy()
    dias_programados = dias_programados[co_li_num.notna()]
    fechas_programadas = fechas_programadas[co_li_num.notna()]

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
        "fechas_programadas": fechas_programadas,
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
    worker por timeout -- WORKER TIMEOUT real en producción, 2026-09-08.

    `fechas_programadas` (Davor, 2026-10-15) se une aparte, FUERA de este
    mismo .agg() -- es una lista por fila, no un escalar, así que no
    puede ir en el dict de arriba sin forzar igual el camino lento que el
    comentario de arriba evita a propósito. Un solo paso lineal en Python
    puro (dict + set, O(filas)) arma la unión por (co_li, tipo) antes del
    groupby vectorizado, y se la vuelve a pegar después con un merge."""
    if not len(df):
        return df
    fechas_por_grupo = {}
    for co_li, tipo, fechas in zip(df["co_li"], df["tipo"], df["fechas_programadas"]):
        fechas_por_grupo.setdefault((co_li, tipo), set()).update(fechas)

    agrupado = df.groupby(["co_li", "tipo"], as_index=False).agg({
        "nombre_pdv": "first", "ciudad": "first", "region": "first",
        "subcanal": "first", "mercado": "first", "dni_asignado": "first",
        "mercaderista_nombre": "first", "supervisor": "first",
        "dias_programados": "sum",
    })
    agrupado["fechas_programadas"] = [
        sorted(fechas_por_grupo.get((co_li, tipo), set()))
        for co_li, tipo in zip(agrupado["co_li"], agrupado["tipo"])
    ]
    return agrupado


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
    cargado para corregirlo). El DELETE de PlanningPdv se lleva en cascada
    (ondelete=CASCADE) las filas de PlanningPdvDia del mes viejo -- no hace
    falta borrarlas aparte.

    `fechas_programadas` (Davor, 2026-10-15) -- un solo session.flush() ANTES
    de crear las filas de PlanningPdvDia (no uno por PDV) para que todos los
    PlanningPdv ya tengan su `id` asignado sin pagar un round-trip extra por
    cada fila del Excel (miles de filas en el caso real)."""
    session = get_session()
    try:
        session.query(PlanningPdv).filter(PlanningPdv.periodo == periodo).delete()
        hoy = dt.date.today()
        pendientes_fechas = []
        for _, fila in df.iterrows():
            pdv = PlanningPdv(
                periodo=periodo, co_li=fila["co_li"], tipo=fila["tipo"],
                nombre_pdv=fila["nombre_pdv"], ciudad=fila["ciudad"], region=fila["region"],
                subcanal=fila["subcanal"], mercado=fila["mercado"], dni_asignado=fila["dni_asignado"],
                mercaderista_nombre=fila["mercaderista_nombre"], supervisor=fila["supervisor"],
                dias_programados=int(fila["dias_programados"]), cargado_por=cargado_por, cargado_en=hoy,
            )
            session.add(pdv)
            if fila["fechas_programadas"]:
                pendientes_fechas.append((pdv, fila["fechas_programadas"]))
        session.flush()
        for pdv, fechas in pendientes_fechas:
            for f in fechas:
                session.add(PlanningPdvDia(planning_pdv_id=pdv.id, fecha=f))
        session.commit()
        return len(df)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reasignar_pdv(pdv_id, dni_nuevo, nombre_nuevo):
    """Corrige a mano el DNI/mercaderista de UN PDV puntual del Planning ya
    cargado -- Davor, 2026-09-22: "puedo tener opción de editar el
    planning?", después de encontrar el caso real de Jose Quiroz con un
    DNI mal tipeado en una fila del Excel (ver resumen_por_mercaderista()).
    Corregir acá 1 fila no hace falta re-subir el Excel completo del mes.

    OJO: guardar_planning() BORRA y vuelve a insertar TODAS las filas del
    período en cada carga (ver su docstring) -- si alguien re-sube el
    Excel de ese mismo mes más adelante, esta corrección puntual se pierde
    igual que si nunca se hubiera hecho, porque la fila vieja (con este
    `pdv_id`) ya no existe. Sirve para corregir sin re-subir, no reemplaza
    corregir el Excel de origen si se va a volver a cargar ese mes."""
    dni_nuevo = (dni_nuevo or "").strip() or None
    nombre_nuevo = (nombre_nuevo or "").strip() or None
    if not nombre_nuevo:
        raise ValueError("El nombre del mercaderista no puede quedar vacío.")
    if dni_nuevo and not dni_nuevo.isdigit():
        raise ValueError(f'"{dni_nuevo}" no es un DNI válido -- solo dígitos.')

    session = get_session()
    try:
        pdv = session.get(PlanningPdv, pdv_id)
        if pdv is None:
            raise ValueError(f"No se encontró el PDV #{pdv_id}.")
        pdv.dni_asignado = dni_nuevo
        pdv.mercaderista_nombre = nombre_nuevo
        session.commit()
        return pdv.co_li, pdv.nombre_pdv
    except ValueError:
        session.rollback()
        raise
    finally:
        session.close()


def personal_activo():
    """[{"dni", "nombre", "canal"}, ...] de Personal Activo HOY -- Davor,
    2026-10-15: "agregar... donde aparezca las personas cargadas en el
    planning con la opción de reemplazar alguno en base al personal
    activo". Usado para armar el desplegable de candidatos de
    reasignar_mercaderista(); no se restringe a canal Tradicional porque un
    Multicanal activo también puede estar cubriendo PDVs Tradicional (ver
    PersonaSupervisorCanal/PersonaZonaCanal en reemplazos.py)."""
    session = get_session()
    try:
        filas = (
            session.query(Persona.dni, Persona.nombre_completo, Persona.canal)
            .filter(Persona.estado == "Activo")
            .order_by(Persona.nombre_completo)
            .all()
        )
        return [{"dni": dni, "nombre": nombre, "canal": canal} for dni, nombre, canal in filas]
    finally:
        session.close()


def reasignar_mercaderista(periodo, dni_viejo, dni_nuevo):
    """Reemplaza de una sola vez al mercaderista asignado a TODOS los PDVs
    de `periodo` que tenían `dni_viejo`, por alguien de Personal Activo
    (Davor, 2026-10-15, mismo pedido que personal_activo()) -- a diferencia
    de reasignar_pdv() (corrige UN PDV puntual a mano), esto corre sobre
    toda la cartera del mes de una vez: pensado para cuando el Excel de
    Planning vino con el DNI de alguien que ya no corresponde (se fue antes
    de cargarlo, o el archivo trae a su reemplazo bajo el DNI viejo y
    todavía no se hizo "Agregar reemplazo" en Personal).

    `dni_nuevo` tiene que ser de alguien Activo en Personal -- a propósito
    no acepta cualquier DNI tipeado a mano (eso ya lo cubre reasignar_pdv()
    por fila) para no dejar una cartera entera apuntando a un DNI inventado
    o de alguien ya dado de baja. Devuelve (cantidad de PDVs cambiados,
    nombre del nuevo mercaderista)."""
    dni_viejo = (dni_viejo or "").strip()
    dni_nuevo = (dni_nuevo or "").strip()
    if not dni_viejo or not dni_nuevo:
        raise ValueError("Falta el DNI a reemplazar o el DNI nuevo.")
    if dni_viejo == dni_nuevo:
        raise ValueError("El DNI nuevo no puede ser igual al que ya tenía.")

    session = get_session()
    try:
        persona_nueva = session.get(Persona, dni_nuevo)
        if persona_nueva is None or persona_nueva.estado != "Activo":
            raise ValueError(f'El DNI {dni_nuevo} no está en Personal como Activo.')

        n = (
            session.query(PlanningPdv)
            .filter(PlanningPdv.periodo == periodo, PlanningPdv.dni_asignado == dni_viejo)
            .update(
                {PlanningPdv.dni_asignado: dni_nuevo, PlanningPdv.mercaderista_nombre: persona_nueva.nombre_completo},
                synchronize_session=False,
            )
        )
        session.commit()
        return n, persona_nueva.nombre_completo
    except ValueError:
        session.rollback()
        raise
    finally:
        session.close()


def aplicar_reemplazos_vigentes(periodo):
    """Persiste en la base el reemplazo vigente de cada PDV del período --
    Davor, 2026-09-22: "puedes reemplazar los mercaderistas que han sido
    reemplazados por su reemplazo". planning_del_periodo() ya resuelve
    esto AL MOSTRAR la pantalla (y al exportar), sin tocar la base -- esto
    es para el que además quiera dejarlo escrito de una vez en
    `planning_pdv` (ej. si algo más adelante consulta esa tabla directo,
    sin pasar por planning_del_periodo()). Devuelve la cantidad de filas
    que realmente cambiaron -- 0 si ya estaba todo al día.

    Es SEGURO correrlo de nuevo tantas veces como haga falta: una fila que
    ya tiene al reemplazo vigente no vuelve a cambiar (dni_actual ==
    dni_original en resolver_cadena_vigente())."""
    session = get_session()
    try:
        filas = session.query(PlanningPdv).filter(PlanningPdv.periodo == periodo).all()
        resueltos = resolver_cadena_vigente({p.dni_asignado for p in filas})
        n_cambiados = 0
        for p in filas:
            r = resueltos.get(p.dni_asignado)
            if r and r["cambio"]:
                p.dni_asignado = r["dni"]
                p.mercaderista_nombre = r["nombre"] or p.mercaderista_nombre
                n_cambiados += 1
        if n_cambiados:
            session.commit()
        return n_cambiados
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def planning_del_periodo(periodo, region_filtro=None, ciudad_filtro=None, supervisor_filtro=None, mercado_filtro=None):
    """`mercado_filtro`: lista de nombres de mercado YA limpios (ver
    valores_filtrables/_mercado_sin_palabra), selección múltiple (Davor,
    2026-10-15: "adiciona también como filtro, nombre del mercado, con
    opción múltiple"). Se filtra en Python, no en SQL -- PlanningPdv.mercado
    guarda el texto crudo del Excel ("Mercado Central", con la palabra), así
    que hay que limpiarlo igual que al armar las opciones del desplegable
    para poder compararlo."""
    session = get_session()
    try:
        query = session.query(PlanningPdv).filter(PlanningPdv.periodo == periodo)
        if region_filtro:
            query = query.filter(PlanningPdv.region == region_filtro)
        if ciudad_filtro:
            query = query.filter(PlanningPdv.ciudad == ciudad_filtro)
        if supervisor_filtro:
            query = query.filter(PlanningPdv.supervisor == supervisor_filtro)
        filas = query.all()
    finally:
        session.close()

    if mercado_filtro:
        mercados_elegidos = set(mercado_filtro)
        filas = [p for p in filas if _mercado_sin_palabra(p.mercado) in mercados_elegidos]

    # Reemplazo vigente (Davor, 2026-09-22: "que también automáticamente se
    # cambiara la persona según entraba su reemplazo") -- dni_asignado/
    # mercaderista_nombre son el snapshot del Excel al momento de la carga;
    # acá se pisan EN MEMORIA (las filas ya están fuera de la sesión, esto
    # no toca la base) con quien las reemplazó, si a esa fecha ya hubo un
    # reemplazo procesado por "Agregar reemplazo". `mercaderista_nombre_original`
    # queda aparte para poder avisarlo en pantalla en vez de pisarlo en
    # silencio -- ver reportes_planning.html.
    resueltos = resolver_cadena_vigente({p.dni_asignado for p in filas})
    for p in filas:
        r = resueltos.get(p.dni_asignado)
        if r and r["cambio"]:
            p.mercaderista_nombre_original = p.mercaderista_nombre
            p.dni_asignado = r["dni"]
            p.mercaderista_nombre = r["nombre"] or p.mercaderista_nombre
    return filas


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
    """["2026-09", ...] -- meses con al menos un PlanningPdv cargado, más
    recientes primero, en el MISMO formato "YYYY-MM" que ?mes= / periodo_str
    (default: mes actual, aunque todavía no tenga nada cargado).

    String, no `date` (Davor, 2026-09-22) -- el template compara
    `periodo_str in periodos_disponibles` para decidir si mostrar
    "Descargar lo ya cargado de <mes>"; comparar un str contra `date`
    nunca da True, así que ese botón no aparecía NUNCA aunque el mes sí
    tuviera Planning cargado (hallazgo real: "donde descargo el planning
    actual tal cual cargué en el mismo formato" -- el botón ya existía,
    pero la condición lo escondía siempre)."""
    session = get_session()
    try:
        periodos = sorted({p for (p,) in session.query(PlanningPdv.periodo).distinct().all()}, reverse=True)
        return [p.strftime("%Y-%m") for p in periodos]
    finally:
        session.close()


def valores_filtrables(periodo):
    """Región/Ciudad/Supervisor/Mercado distintos DENTRO del planning de
    `periodo` -- a propósito no reusa los desplegables de Persona
    (reportes._filtros_admin) porque son texto libre tipeado en el Excel de
    Planning, no siempre calzan letra por letra con lo que hay en la tabla
    Persona.

    `mercados` usa el nombre YA limpio (_mercado_sin_palabra, sin la
    palabra "mercado") -- mismo texto que ve el usuario en la tabla
    "Mercaderista x Ciudad", para que el filtro y lo que se muestra
    coincidan letra por letra."""
    session = get_session()
    try:
        query = session.query(PlanningPdv).filter(PlanningPdv.periodo == periodo)
        filas = query.with_entities(PlanningPdv.region, PlanningPdv.ciudad, PlanningPdv.supervisor, PlanningPdv.mercado).all()
        regiones = sorted({r for r, _, _, _ in filas if r})
        ciudades = sorted({c for _, c, _, _ in filas if c})
        supervisores = sorted({s for _, _, s, _ in filas if s})
        mercados = sorted({_mercado_sin_palabra(m) for _, _, _, m in filas if _mercado_sin_palabra(m)})
        return regiones, ciudades, supervisores, mercados
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
        "id": p.id, "co_li": p.co_li, "nombre_pdv": p.nombre_pdv, "tipo": p.tipo, "ciudad": p.ciudad,
        "region": p.region, "mercado": p.mercado,
        "mercaderista": p.mercaderista_nombre, "dni_asignado": p.dni_asignado, "supervisor": p.supervisor,
        "reemplazo_de": getattr(p, "mercaderista_nombre_original", None),
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
            "id": p.id, "co_li": p.co_li, "nombre_pdv": p.nombre_pdv, "tipo": p.tipo, "ciudad": p.ciudad,
            "region": p.region, "mercaderista": p.mercaderista_nombre, "dni_asignado": p.dni_asignado, "supervisor": p.supervisor,
            "reemplazo_de": getattr(p, "mercaderista_nombre_original", None),
            "visitas_planificadas": planificadas, "visitas_realizadas": realizadas,
            "pct_cumplimiento": round(realizadas / planificadas * 100, 1) if planificadas else None,
            "fecha_ultima_visita": ultima.get(p.co_li).date() if p.co_li in ultima and pd.notna(ultima.get(p.co_li)) else None,
        })
    filas.sort(key=lambda f: (f["pct_cumplimiento"] if f["pct_cumplimiento"] is not None else -1))
    return filas


class _UnionFind:
    """Union-Find mínimo (sin rank, con path compression) -- suficiente
    para el volumen de este Planning (unos cientos de mercaderistas, no
    millones de nodos)."""

    def __init__(self):
        self.padre = {}

    def find(self, x):
        self.padre.setdefault(x, x)
        raiz = x
        while self.padre[raiz] != raiz:
            raiz = self.padre[raiz]
        while self.padre[x] != raiz:
            self.padre[x], x = raiz, self.padre[x]
        return raiz

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.padre[ra] = rb


def _ciudad_normalizada(ciudad):
    """None si `ciudad` es basura de tipeo (vacío, o solo puntuación/
    espacios, ej. "." tal cual se vio en producción: "Jose Quiroz" con
    Ciudad="." en el Excel) -- Davor, 2026-09-22: "porque ciudad sale
    blanco". No es un bug de este código: el Excel de Planning trae eso
    tal cual en esa fila -- esto solo evita mostrar el "." pelado y evita
    que compita como una "ciudad" distinta de un simple campo vacío."""
    texto = (ciudad or "").strip()
    return texto if texto.strip(".-_/ ") else None


_RE_PALABRA_MERCADO = re.compile(r"\bmercado\b", re.IGNORECASE)


def _mercado_sin_palabra(mercado):
    """Nombre del mercado sin la palabra "mercado" (Davor, 2026-10-13:
    "ponles... el nombre del mercado, pero excluye la palabra mercado") --
    ya está claro por el encabezado de la columna, repetirla en cada fila
    es ruido ("Mercado Central" -> "Central"). None si no queda nada útil
    (ej. el PDV se llamaba literal "Mercado")."""
    if not mercado:
        return None
    limpio = re.sub(r"\s{2,}", " ", _RE_PALABRA_MERCADO.sub("", mercado)).strip(" -,")
    return limpio or None


def resumen_por_mercaderista(planning, pendientes):
    """Por mercaderista: cuántos PDVs tiene asignados en el Planning del
    período (sumando TODAS sus ciudades en una sola fila), cuántos de esos
    le quedan pendientes de visitar, y su DNI -- Davor, 2026-09-22:
    "agregar un resumen de mercaderista x ciudad con la cantidad de PDVs
    que tiene para visitar" + "agregar dni y opción para exportar", y
    2026-09-22 (mismo día, seguimiento): "quiero un total único por
    persona, sin partir por ciudad" -- la primera versión agrupaba también
    por ciudad, así que alguien con PDVs en 2 ciudades (ej. Eddie Macalopu:
    3 en Piura + 32 en Talara) salía partido en 2 filas chicas en vez de
    una con el total real (35). Si la vista ya viene filtrada a una sola
    ciudad (ver `ciudad_f` en reportes.py::planning()), esto no cambia
    nada -- solo importa cuando se ven todas las ciudades juntas. `ciudad`
    ahora lista las ciudades distintas del grupo (ordenadas, separadas por
    coma) en vez de una sola -- sigue siendo informativo sin volver a
    esconder que la persona cubre más de una.

    Identifica a cada mercaderista con un Union-Find sobre NOMBRE
    normalizado Y dni_asignado -- dos filas del Excel son "la misma
    persona" si comparten el nombre normalizado O el DNI (o ambos),
    encadenado transitivamente. Ni el nombre ni el DNI solos alcanzan en
    este Excel (1 fila tipeada a mano por PDV):
    - Agrupar solo por DNI partía a "Jose Quiroz" en ~15 filas de 1 PDV
      porque el DNI traía un typo distinto en cada fila (hallazgo real,
      2026-09-22).
    - Agrupar SOLO por nombre (el fix de ese día) resultaba en el problema
      inverso: si en ALGUNAS filas el nombre también venía escrito
      distinto pero el DNI sí calzaba con otras filas suyas, esas filas
      quedaban afuera de su grupo -- "Eddie Macalopu tiene más clientes en
      el Planning" de lo que mostraba esta tabla (Davor, 2026-09-22).
    El Union-Find une ambos criterios transitivamente: si la fila A
    comparte nombre con B, y B comparte DNI con C, las 3 quedan en el
    mismo grupo aunque A y C no compartan nada directamente entre sí.

    `ciudad` ya NO agrupa (ver docstring de arriba) -- solo se normaliza
    (mismo criterio de siempre, evita que un espacio/mayúscula de más la
    liste 2 veces para la misma persona -- caso real: "Gianella Tantalean"
    repetida bajo "Chiclayo" y "CHICLAYO ") y se junta en un set por grupo,
    mostrado ordenado y separado por coma. Un Ciudad vacío o solo
    puntuación (ver _ciudad_normalizada()) cae en el bucket "—", no compite
    como ciudad real.

    `dni`/`mercaderista`: el más repetido del grupo (Counter.most_common)
    -- no es "adivinar cuál es el correcto", solo el más frecuente tal
    cual aparece en el Excel. Las variantes de DNI se listan en
    `dni_variantes` para avisar la inconsistencia en vez de esconderla.

    Reusa `pendientes` (ya calculado por pdvs_pendientes() en la misma
    request) en vez de volver a cruzar contra las visitas."""
    uf = _UnionFind()
    for p in planning:
        nombre_norm = (p.mercaderista_nombre or "").strip().upper()
        if nombre_norm and p.dni_asignado:
            uf.union(("nombre", nombre_norm), ("dni", p.dni_asignado))
        elif nombre_norm:
            uf.find(("nombre", nombre_norm))
        elif p.dni_asignado:
            uf.find(("dni", p.dni_asignado))

    def _grupo_persona(p):
        nombre_norm = (p.mercaderista_nombre or "").strip().upper()
        if nombre_norm:
            return uf.find(("nombre", nombre_norm))
        if p.dni_asignado:
            return uf.find(("dni", p.dni_asignado))
        return ("sin_asignar",)

    co_lis_pendientes = {p["co_li"] for p in pendientes}
    conteo = {}
    for p in planning:
        clave = _grupo_persona(p)
        fila = conteo.setdefault(clave, {
            "ciudades": {}, "asignados": 0, "pendientes": 0,
            "dnis": Counter(), "nombres": Counter(),
            "tipos": set(), "mercados": {},
        })
        ciudad_norm = _ciudad_normalizada(p.ciudad)
        # dict en vez de set: dedupea case-insensitive (misma ciudad tipeada
        # "CHICLAYO" en una fila y "Chiclayo" en otra no debe listarse 2
        # veces) quedándose con la forma que apareció primero, mismo criterio
        # que ya usaba la agrupación vieja por (persona, ciudad.upper()).
        fila["ciudades"].setdefault((ciudad_norm or "—").upper(), ciudad_norm or "—")
        fila["asignados"] += 1
        if p.co_li in co_lis_pendientes:
            fila["pendientes"] += 1
        if p.dni_asignado:
            fila["dnis"][p.dni_asignado] += 1
        if p.mercaderista_nombre:
            fila["nombres"][p.mercaderista_nombre] += 1
        fila["tipos"].add(p.tipo)
        # Mercado (Davor, 2026-10-13: "Para los mercaderistas que tienen
        # todo CANAL = Mayorista, ponles en esta tabla el nombre del
        # mercado") -- se junta igual que "ciudades" (puede trabajar más
        # de un mercado), pero recién se muestra más abajo SOLO si el
        # grupo es 100% Mayorista -- un mercaderista mixto (Mayorista +
        # Minorista) tiene PDVs que no son de ningún mercado puntual, así
        # que mostrar "mercado" ahí sería engañoso.
        mercado_limpio = _mercado_sin_palabra(p.mercado)
        if mercado_limpio:
            fila["mercados"].setdefault(mercado_limpio.upper(), mercado_limpio)

    filas = []
    for v in conteo.values():
        dnis_ordenados = v["dnis"].most_common()
        dni_principal = dnis_ordenados[0][0] if dnis_ordenados else None
        dni_variantes = [d for d, _n in dnis_ordenados[1:]]
        nombre_principal = v["nombres"].most_common(1)[0][0] if v["nombres"] else None
        es_solo_mayorista = v["tipos"] == {"Mayorista"}
        mercado_str = ", ".join(sorted(v["mercados"].values())) if es_solo_mayorista and v["mercados"] else None
        filas.append({
            "mercaderista": nombre_principal or "(Sin asignar)", "ciudad": ", ".join(sorted(v["ciudades"].values())),
            "mercado": mercado_str,
            "dni": dni_principal, "dni_variantes": dni_variantes,
            "pdvs_asignados": v["asignados"], "pdvs_pendientes": v["pendientes"],
            "pct_cumplimiento": round((v["asignados"] - v["pendientes"]) / v["asignados"] * 100, 1) if v["asignados"] else None,
        })
    filas.sort(key=lambda f: f["mercaderista"])
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


def cumplimiento_ruta_dia(desde, hasta, planning_filtrado, v_rango):
    """Cumplimiento de ruta de un RANGO de días (uno o varios), por
    mercaderista (Davor, 2026-10-15: "cuando cargue el planning el
    mercaderista esta asignado a una ruta... quiero que por día pueda
    tener PDV visitado y PDVs fuera de lo programado al día"; 2026-10-16:
    "adiciona una columna que diga Fecha, por si deseo descargarme visitas
    de varios dias" -- originalmente era un solo día puntual, `desde ==
    hasta` sigue siendo el caso de uso normal del día a día).

    Una fila por (fecha, dni, co_li) que ESE día del rango estaba
    programado (vía PlanningPdvDia.fecha) y/o fue realmente visitado:
    - "Cumplido": estaba programado ese día y lo visitó ese mismo día.
    - "Pendiente": estaba programado ese día y todavía no lo visitó.
    - "Fuera de lo programado (otro día de su ruta)": lo visitó ese día, el
      PDV SÍ es parte de su cartera del mes, pero no le tocaba justo ese día.
    - "Fuera de planning": lo visitó ese día y el PDV ni siquiera está en
      su Planning de este mes.

    `planning_filtrado`: lista de PlanningPdv ya acotada por región/ciudad/
    supervisor/mercado (ver planning_del_periodo()) -- puede abarcar más de
    un mes si el rango de días cruza fin de mes. `v_rango`: visitas
    Tradicional de TODO el rango [desde, hasta] (visitas_tradicional(desde,
    hasta, usuario_actual)), SIN acotar por región/ciudad/supervisor (ver
    docstring de visitas_tradicional() -- esos filtros nunca se aplican ahí
    porque comparar el supervisor de Planning (texto libre) contra
    Persona.dni rompía la consulta). Para que los filtros de arriba sí
    afecten este panel (Davor, 2026-10-15: "los filtros superiores no
    afectan esta tabla"), acá se acota `v_rango` por los DNI que SÍ
    quedaron dentro de `planning_filtrado` -- un mercaderista fuera del
    filtro no debe aparecer ni como "cumplido" ni como "fuera de planning".

    Requiere que el mes se haya cargado DESPUÉS de este cambio (2026-10-15)
    -- un Planning viejo no tiene filas en PlanningPdvDia, así que ningún
    PDV sale "programado" para ningún día del rango (hay que volver a
    subir el Excel del mes para tener detalle día a día)."""
    dnis_en_scope = {p.dni_asignado for p in planning_filtrado if p.dni_asignado}
    ids_planning = [p.id for p in planning_filtrado]
    pdv_por_id = {p.id: p for p in planning_filtrado}

    programado_por_fecha = {}
    if ids_planning:
        session = get_session()
        try:
            filas_rango = (
                session.query(PlanningPdvDia.fecha, PlanningPdvDia.planning_pdv_id)
                .filter(PlanningPdvDia.fecha.between(desde, hasta), PlanningPdvDia.planning_pdv_id.in_(ids_planning))
                .all()
            )
        finally:
            session.close()
        for fecha, pid in filas_rango:
            programado_por_fecha.setdefault(fecha, set()).add(pid)

    co_lis_en_planning_del_dni = {
        (p.dni_asignado, p.co_li) for p in planning_filtrado if p.dni_asignado
    }

    visitas = {}
    if len(v_rango):
        for _, r in v_rango.iterrows():
            if r["dni"] not in dnis_en_scope:
                continue
            fecha_visita = r["fecha_inicio"].date() if hasattr(r["fecha_inicio"], "date") else r["fecha_inicio"]
            visitas[(fecha_visita, r["dni"], str(r["punto_venta_id"]))] = r

    filas = []
    vistos = set()
    for fecha, ids_programados in programado_por_fecha.items():
        for pid in ids_programados:
            p = pdv_por_id.get(pid)
            if p is None or not p.dni_asignado:
                continue
            clave = (fecha, p.dni_asignado, p.co_li)
            vistos.add(clave)
            visita = visitas.get(clave)
            filas.append({
                "fecha": fecha, "dni": p.dni_asignado, "mercaderista": p.mercaderista_nombre, "co_li": p.co_li,
                "nombre_pdv": p.nombre_pdv, "ciudad": p.ciudad, "supervisor": p.supervisor,
                "estado": "Cumplido" if visita is not None else "Pendiente",
                "hora_visita": visita["hora_inicio"] if visita is not None else None,
            })

    for (fecha, dni, co_li), r in visitas.items():
        if (fecha, dni, co_li) in vistos:
            continue
        en_planning = (dni, co_li) in co_lis_en_planning_del_dni
        filas.append({
            "fecha": fecha, "dni": dni, "mercaderista": r["nombre"], "co_li": co_li,
            "nombre_pdv": r["punto_venta"], "ciudad": r["ciudad"], "supervisor": r.get("supervisor"),
            "estado": "Fuera de lo programado (otro día de su ruta)" if en_planning else "Fuera de planning",
            "hora_visita": r["hora_inicio"],
        })

    orden_estado = {"Cumplido": 0, "Pendiente": 1, "Fuera de lo programado (otro día de su ruta)": 2, "Fuera de planning": 3}
    filas.sort(key=lambda f: (f["fecha"], (f["mercaderista"] or "").upper(), orden_estado.get(f["estado"], 9)))
    return filas
