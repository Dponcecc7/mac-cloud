# -*- coding: utf-8 -*-
"""Reportes -- Horas semanales (#2 de la lista de Davor, 2026-08-22).

Puerto de MAC/reporte_semanal.py::calcular_detalle_diario() +
agregar_detalle_mercaderista() -- MISMA regla de negocio ya validada (horas
fijas L-V 8.5h / Sábado 5.5h / feriados 0h, descuenta refrigerio según el
PATRÓN no el real marcado, "Horas a trabajar" descuenta Vacante y Descanso
(2026-10-06), "Horas a trabajar sin faltas" además descuenta Falta y
Vacaciones), pero leyendo todo
de Postgres en vez de Excel/Graph -- no es importable tal cual desde acá
porque esas dependencias (Maestro, Patrón, Historial) solo existen como
archivo local en el original. Ver plan de esta sesión para el detalle de
cada reemplazo.

Diferencia deliberada con el original: no filtra a Rol == "MERCADERISTAS"
-- aplica a todos los roles (pedido explícito de Davor)."""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

from alertas import _tiene_sustento, SALIDA_ANTICIPADA_MIN
from dimension_models import CompensacionHoras, Feriado, Persona, asegurar_tabla_compensaciones, get_session
from fact_models import ClasificacionDiaria
from patron_recurrente import cargar_patron_recurrente, canal_para_historial, sin_acentos, valor_patron_para_canal, WD_NORM
from scoping import aplicar_filtros_extra, canal_del_dia_coincide, canonizar_canal, condicion_scope, overrides_supervisor_canal

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_AQUI, "pipeline"))
from historial_cambios import cargar_historial, valor_efectivo  # noqa: E402

HORAS_LV = 8.5
HORAS_SABADO = 5.5
REFRIGERIO_MIN = {"con refrigerio": 60, "medio refrigerio": 30, "sin refrigerio": 0}
CIERRE_AUTOMATICO_STR = "23:30:00"  # mismo criterio que reporte_diario_9am.py -- no es una salida real


def formatear_horas(valor):
    """Horas decimales -> "HH:MM" (Davor, 2026-11-06: "cuando restamos horas,
    sale 8.5 trabajadas, trabaja 08:30") -- 8.5 es 08:30 pero en decimal se
    confunde (8.48 parece 8 h 48 min y son 8 h 29 min). Solo cambia cómo se
    MUESTRA: todos los cálculos siguen en horas decimales. None/NaN -> "—".
    Una diferencia negativa lleva el signo adelante ("-04:24"); un resultado
    que redondea a 00:00 no lleva signo."""
    if valor is None or pd.isna(valor):
        return "—"
    minutos = int(round(abs(float(valor)) * 60))
    horas, mins = divmod(minutos, 60)
    signo = "-" if valor < 0 and minutos else ""
    return f"{signo}{horas:02d}:{mins:02d}"


def semana_iso(anio, num_semana):
    """Lunes a domingo de la semana ISO `num_semana` del año `anio` --
    hasta 2026-09-14 llegaba solo a sábado; se extendió a domingo (Davor:
    Autoservicio trabaja domingo y debe contar "en todos los análisis")
    porque este es el único punto que alimenta el rango [desde, hasta] de
    calcular_detalle_semana() en los 4 lugares donde se usa (Horas
    semanales, Desempeño, Ficha del trabajador x2) -- alguien sin fila de
    domingo en su Patrón Recurrente simplemente no tiene fila ese día,
    domingo extra no cambia nada para el resto del equipo."""
    lunes = dt.date.fromisocalendar(anio, num_semana, 1)
    domingo = dt.date.fromisocalendar(anio, num_semana, 7)
    return lunes, domingo


def _horas_esperadas_dia(row, feriados_set):
    fecha = row["fecha"].date() if hasattr(row["fecha"], "date") else row["fecha"]
    if fecha in feriados_set:
        return 0.0
    wd = fecha.weekday()
    # Multicanal con 2 turnos genuinos ese día (Davor, 2026-09-22) -- la
    # constante plana HORAS_LV/HORAS_SABADO asume 1 sola jornada por día;
    # sumarla una vez POR TURNO (ver _turnos_hoy, calculado antes de llamar
    # esta función) infla "horas a trabajar" a más del doble de lo real
    # (ej. 17h para un día real de 10h repartido en 2 turnos). En ese caso
    # se cae a la MISMA fórmula que ya usa la rama de domingo de acá abajo
    # -- el camino de 1 turno (99% de la gente, cualquier día) sigue con la
    # constante de siempre, sin cambios.
    es_dia_de_2_turnos = row.get("_turnos_hoy", 1) > 1
    if wd <= 4 and not es_dia_de_2_turnos:
        return HORAS_LV
    if wd == 5 and not es_dia_de_2_turnos:
        return HORAS_SABADO
    # Domingo (Davor, 2026-09-14: Autoservicio trabaja domingo, debe contar
    # "si el analista lo sube en su patrón recurrente"), o cualquier día con
    # 2 turnos -- a propósito SIN una constante fija tipo HORAS_LV/
    # HORAS_SABADO: se deriva de la entrada/salida esperada YA CLASIFICADA
    # para esa fila/turno (que el motor sacó del propio Patrón Recurrente de
    # esa persona), menos el refrigerio. Sin fila de domingo en su Patrón,
    # el motor nunca generó esta fila -- no hay nada que sumar, mismo
    # resultado 0.0 de siempre para cualquiera que no lo tenga cargado.
    entrada_td = pd.to_timedelta(str(row["entrada_esperada"]), errors="coerce")
    salida_td = pd.to_timedelta(str(row["salida_esperada"]), errors="coerce")
    if pd.isna(entrada_td) or pd.isna(salida_td):
        return 0.0
    refrigerio_td = pd.to_timedelta(row.get("_refrigerio_min", 0), unit="m")
    return round(max((salida_td - entrada_td - refrigerio_td).total_seconds() / 3600, 0.0), 2)


def _motivo_falta(estado_base, estado, comentario):
    """Mismo criterio que reporte_semanal.py::_motivo_falta() -- el Estado
    ya lo dice para Vacante/Vacaciones bien clasificados; el comentario es
    solo respaldo para Falta genérica sin resolver."""
    if estado_base == "VACANTE":
        return "Vacante"
    if estado_base == "VACACIONES":
        return "Vacaciones"
    if estado_base != "FALTA":
        return None
    texto = f"{estado} {comentario or ''}".upper()
    if "VACACION" in texto:
        return "Vacaciones"
    if "VACANTE" in texto:
        return "Vacante"
    return "Falta"


def calcular_detalle_semana(desde, hasta, usuario_actual, dni_filtro=None,
                             rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None,
                             canal_filtro=None, estado_filtro=None, canal_dia_estricto=False):
    """Detalle día-persona entre [desde, hasta] (inclusive), acotado al
    scope de acceso de `usuario_actual` -- o a un solo `dni_filtro` si se
    pasa (para la ficha individual, donde el acceso ya se validó aparte).
    `rol_filtro`/`region_filtro`/`supervisor_filtro`/`ciudad_filtro`/
    `estado_filtro`: filtros extra de Reportes (solo tienen efecto para
    admin/analista, ver scoping.aplicar_filtros_extra). `estado_filtro`
    (Davor, 2026-10-09, Horas programadas vs trabajadas: "filtro, para
    mostrar solo a los activos") es Persona.estado (Activo/Inactivo/
    Vacante), NO el estado de la clasificación del día (Falta/Tardanza/
    etc.) -- alguien que este mes estuvo Activo y ahora ya no, sigue
    teniendo filas reales de ClasificacionDiaria de cuando sí trabajaba;
    sin filtrar por Persona.estado, ese historial real sigue apareciendo
    en el reporte aunque la persona ya no esté activa hoy.

    `canal_dia_estricto` (Davor, 2026-10-16, Horas programadas vs
    trabajadas: "agregar la condicional del dashboard, solo días
    trabajados en ese canal") -- mismo filtro que ya tenía el Dashboard
    (ver scoping.canal_del_dia_coincide()): `canal_filtro` de arriba
    acota PERSONAS (alguien que ALGUNA vez trabaja ese canal), esto acota
    además a nivel DÍA -- un Multicanal de Tradicional que un día cubrió
    Autoservicio no suma ese día a las horas de Tradicional. Sin efecto
    si `canal_filtro` está vacío (no tiene sentido "solo días de ese
    canal" sin haber elegido un canal). Se aplica al final, después de
    calcular horas/turnos con el set de filas COMPLETO -- filtrar antes
    rompería la detección de "día con 2 turnos" (_turnos_hoy) para un
    Multicanal con un turno en el canal elegido y otro afuera.

    Devuelve un DataFrame vacío (sin columnas) si no hay filas."""
    session = get_session()
    try:
        query = (
            session.query(
                ClasificacionDiaria.dni, Persona.nombre_completo, ClasificacionDiaria.fecha,
                ClasificacionDiaria.estado, ClasificacionDiaria.comentario_supervisor,
                ClasificacionDiaria.entrada_esperada, ClasificacionDiaria.entrada_real,
                ClasificacionDiaria.salida_esperada, ClasificacionDiaria.salida_real,
                Persona.region, Persona.ciudad, Persona.supervisor_dni,
                ClasificacionDiaria.salida_anticipada_min, ClasificacionDiaria.canal_esperado,
                ClasificacionDiaria.canales_marcados,
            )
            .join(Persona, Persona.dni == ClasificacionDiaria.dni)
            .filter(ClasificacionDiaria.fecha >= desde, ClasificacionDiaria.fecha <= hasta)
        )
        if dni_filtro:
            query = query.filter(ClasificacionDiaria.dni == dni_filtro)
        else:
            cond_scope = condicion_scope(Persona, usuario_actual)
            if cond_scope is not None:
                query = query.filter(cond_scope)
            query = aplicar_filtros_extra(
                query, Persona, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro, canal_filtro,
                estado_filtro=estado_filtro,
            )
        filas = query.all()
        if not filas:
            return pd.DataFrame()

        # Override de supervisor por canal (Davor, 2026-08-29) -- ver
        # scoping.overrides_supervisor_canal(). Se calcula con el
        # usuario_actual real (aun en el caso dni_filtro, donde no se aplica
        # condicion_scope() porque el acceso ya se validó aparte) para que
        # la Ficha de UNA persona también respete el canal de quien mira.
        overrides_sup = overrides_supervisor_canal(session, [f.dni for f in filas], usuario_actual)
        dnis_supervisores = {f.supervisor_dni for f in filas if f.supervisor_dni}
        dnis_supervisores |= set(overrides_sup.values())
        nombre_supervisor = {}
        if dnis_supervisores:
            nombre_supervisor = dict(
                session.query(Persona.dni, Persona.nombre_completo)
                .filter(Persona.dni.in_(dnis_supervisores)).all()
            )

        feriados_set = {f for (f,) in session.query(Feriado.fecha).all()}
        refrigerio_map = cargar_patron_recurrente(session, "refrigerio")
        idx_historial = cargar_historial()
        compensaciones = cargar_compensaciones(session, desde, hasta)
    finally:
        session.close()

    r = pd.DataFrame(filas, columns=[
        "dni", "nombre", "fecha", "estado", "comentario",
        "entrada_esperada", "entrada_real", "salida_esperada", "salida_real",
        "region", "ciudad", "supervisor_dni", "salida_anticipada_min", "canal_esperado",
        "canales_marcados",
    ])
    r["fecha"] = pd.to_datetime(r["fecha"])
    r["estado_base"] = r["estado"].apply(lambda s: s.split(" (")[0])
    r["supervisor_dni_efectivo"] = r["dni"].map(overrides_sup).fillna(r["supervisor_dni"])
    r["supervisor"] = r["supervisor_dni_efectivo"].map(nombre_supervisor)
    r["motivo_falta"] = r.apply(
        lambda row: _motivo_falta(row["estado_base"], row["estado"], row["comentario"]), axis=1
    )

    def _refrigerio_min_para(row):
        dia_norm = WD_NORM.get(row["fecha"].weekday())
        # canal en la clave (Davor, 2026-09-22, 2 turnos/día) -- un
        # Multicanal puede tener un refrigerio distinto por turno, ver
        # cargar_patron_recurrente(). canal_esperado puede venir COMPUESTO
        # ("Autoservicio, Tradicional", turno que cubre varios canales con
        # el MISMO horario -- caso real Maritza) -- valor_patron_para_canal()
        # prueba cada canal del compuesto. Para el 99% de la gente (1 turno,
        # 1 canal) esto es la misma clave de siempre.
        valor_patron = valor_patron_para_canal(refrigerio_map, row["dni"], dia_norm, row["canal_esperado"])
        valor_final = valor_efectivo(idx_historial, row["dni"], "Refrigerio", row["fecha"], valor_patron, canal=canal_para_historial(row["canal_esperado"]))
        return REFRIGERIO_MIN.get(sin_acentos(valor_final), 0) if valor_final else 0

    r["_refrigerio_min"] = r.apply(_refrigerio_min_para, axis=1)
    # Cuántas filas/turnos tiene esta persona ESTE día -- 1 para el 99% de
    # la gente, 2 para un Multicanal con turno AM/PM real. Ver
    # _horas_esperadas_dia() para el porqué.
    r["_turnos_hoy"] = r.groupby(["dni", "fecha"])["dni"].transform("size")
    r["horas_a_trabajar"] = r.apply(lambda row: _horas_esperadas_dia(row, feriados_set), axis=1)

    r["_entrada_esp_td"] = pd.to_timedelta(r["entrada_esperada"].astype(str), errors="coerce")
    r["_entrada_real_td"] = pd.to_timedelta(r["entrada_real"].astype(str), errors="coerce")
    es_cierre_automatico = r["salida_real"].astype(str) == CIERRE_AUTOMATICO_STR
    r["_salida_real_td"] = pd.to_timedelta(r["salida_real"].astype(str), errors="coerce").where(~es_cierre_automatico)
    r["_refrigerio_td"] = pd.to_timedelta(r["_refrigerio_min"], unit="m")

    con_marcacion = r["_entrada_real_td"].notna() & r["_salida_real_td"].notna()
    r["_horas_trab_td"] = (r["_salida_real_td"] - r["_entrada_real_td"] - r["_refrigerio_td"]).where(con_marcacion)
    r["horas_trabajadas"] = r["_horas_trab_td"].apply(
        lambda td: None if pd.isna(td) else round(td.total_seconds() / 3600, 2)
    )

    # Tardanza/salida anticipada + si ese día igual cumplió sus horas (Davor,
    # 2026-09-18: "ranking... dias que llegaron tarde, cuantos de esos dias
    # recuperaron... dias que salieron antes de ruta, si ese dia cumplio sus
    # horas laborales") -- mismo criterio que reportes.py::ficha() usa para
    # "Tardanzas y salidas anticipadas del mes".
    r["es_tardanza"] = r["estado_base"] == "TARDANZA"
    r["es_salida_temprana"] = r["salida_anticipada_min"].fillna(0) > SALIDA_ANTICIPADA_MIN
    r["recupero_dia"] = r["horas_trabajadas"].notna() & (r["horas_trabajadas"] >= r["horas_a_trabajar"])
    # Motivo que compensa las horas (Davor, 2026-11-06) -- ver
    # aplicar_compensaciones().
    r = aplicar_compensaciones(r, compensaciones)

    # Faltas "con sustento" (descanso médico/licencia/feriado regional,
    # Davor 2026-08-23) se tratan como Vacante para las horas: no le "debe"
    # esas horas a nadie, tiene justificación real -- se descuentan de la
    # base "Horas a trabajar", no solo de la variante "sin faltas".
    es_con_sustento = (r["motivo_falta"] == "Falta") & r["comentario"].apply(_tiene_sustento)
    # estado_base == "DESCANSO" (Davor, 2026-10-06: "los descansos médicos
    # deben restar las horas a trabajar de la semana") -- mismo criterio que
    # "con sustento" de arriba, pero para el estado DEDICADO "DESCANSO" que
    # el motor empezó a usar desde el 2026-09-14 (antes un descanso médico
    # SIEMPRE llegaba como FALTA con comentario, que es lo único que
    # es_con_sustento ya cubría; DESCANSO es un estado_base aparte, nunca
    # "Falta", así que es_con_sustento nunca lo agarraba -- "Cumplimiento
    # semanal" contaba 8.5h "debidas" por cada día de descanso médico, caso
    # real: 28% en una semana con 5 días de licencia médica).
    es_descanso = r["estado_base"] == "DESCANSO"
    # DM, no solo D (Davor, 2026-10-06, "esa casilla que diga Falta: 2, DM:
    # 4, Tardanzas: 4") -- mismo criterio que ya usa reportes.py::
    # _estado_corto()/_codigo_tareo() para distinguir Descanso médico de un
    # Descanso regular: sin_acentos(), "médico" con tilde nunca contiene
    # "medic" sin tilde.
    r["es_descanso_medico"] = es_descanso & r["comentario"].apply(
        lambda c: pd.notna(c) and "medic" in sin_acentos(str(c))
    )
    r["_horas_vacante_dia"] = r["horas_a_trabajar"].where(r["motivo_falta"] == "Vacante", 0.0)
    r["_horas_sustento_dia"] = r["horas_a_trabajar"].where(es_con_sustento, 0.0)
    r["_horas_descanso_dia"] = r["horas_a_trabajar"].where(es_descanso, 0.0)

    # "Horas a trabajar sin faltas" (Davor, 2026-08-23): SOLO cuenta los días
    # que la persona realmente vino -- con marcación real (entrada Y salida),
    # sin importar el motivo por el que un día no tiene marcación. Esto
    # incluye Falta/Vacaciones normales, pero también un caso que la
    # clasificación por motivo no cubría: "Asistió (según supervisor, sin
    # marcación app)" -- alguien que el supervisor marcó presente desde
    # "Marcar asistencia" pero todavía nadie cargó la hora real (ver
    # asistencia.py, el aviso "⚠ Confirmar") quedaba contando su jornada
    # completa como si hubiese trabajado, sin ninguna marcación real detrás.
    # Los días ya excluidos de la base (Vacante/con sustento) no se
    # descuentan de nuevo acá -- ya salieron antes.
    ya_excluido_de_base = (r["motivo_falta"] == "Vacante") | es_con_sustento | es_descanso
    r["_horas_sin_marcacion_dia"] = r["horas_a_trabajar"].where(~ya_excluido_de_base & ~con_marcacion, 0.0)
    # "Días activos" (Davor, 2026-10-10: "días que se esta considerando las
    # horas a trabajar sin faltas") -- exactamente los días que SÍ entran en
    # "horas_a_trabajar_sin_faltas" de abajo: ni Vacante/con sustento/
    # Descanso, y con marcación real. Mismo criterio que _horas_sin_marcacion_dia,
    # pero contando días en vez de horas.
    r["_dia_activo"] = ~ya_excluido_de_base & con_marcacion

    if canal_dia_estricto and canal_filtro:
        canales_objetivo = {canonizar_canal(canal_filtro)}
        coincide = r.apply(
            lambda row: canal_del_dia_coincide(row["canal_esperado"], row["canales_marcados"], canales_objetivo),
            axis=1,
        )
        r = r[coincide]

    return r


def cargar_compensaciones(session, desde, hasta):
    """{(dni, fecha): (compensa_tardanza, compensa_salida, motivo)} de los
    días registrados en Ficha > "Tardanzas y salidas anticipadas". Si la tabla
    todavía no se puede leer devuelve {} -- un reporte de horas sin
    compensaciones es mejor que un reporte caído."""
    try:
        asegurar_tabla_compensaciones()
        filas = (
            session.query(
                CompensacionHoras.dni, CompensacionHoras.fecha, CompensacionHoras.compensa_tardanza,
                CompensacionHoras.compensa_salida, CompensacionHoras.motivo,
            )
            .filter(CompensacionHoras.fecha >= desde, CompensacionHoras.fecha <= hasta)
            .all()
        )
    except Exception as e:
        session.rollback()
        print(f"[horas_semanales] compensaciones no disponibles: {e}", file=sys.stderr)
        return {}
    return {(dni, fecha): (bool(tarde), bool(salida), motivo) for dni, fecha, tarde, salida, motivo in filas}


def aplicar_compensaciones(r, compensaciones):
    """Agrega `motivo_compensacion`, `comp_tardanza`, `comp_salida`,
    `compensado` (alguna de las dos) y `_horas_compensadas_dia` a `r` (una
    fila por turno, con las columnas de calcular_detalle_semana()).

    Un analista marca qué cubre el motivo de ese día: la tardanza, la salida
    anticipada o ambas. Si marcó TODO lo que aplica ese día, lo que faltó
    trabajar se descuenta de la base "Horas a trabajar" (mismo tratamiento
    que Vacante/con sustento: no se le "debe" esa parte) y el día cuenta como
    recuperado. Si marcó solo una de las dos, se descuenta únicamente esa
    parte -- los minutos de tardanza (entrada real - programada) o de salida
    anticipada -- y lo demás sigue restando. "Horas trabajadas" nunca se
    toca: sigue siendo lo realmente marcado. Solo aplica a días con
    marcación real: sin horas trabajadas no hay faltante que compensar."""
    claves = list(zip(r["dni"], r["fecha"].dt.date))
    registro = [compensaciones.get(k) for k in claves]
    r["motivo_compensacion"] = pd.Series([x[2] if x else None for x in registro], index=r.index, dtype=object)
    r["comp_tardanza"] = pd.Series([bool(x and x[0]) for x in registro], index=r.index, dtype=bool)
    r["comp_salida"] = pd.Series([bool(x and x[1]) for x in registro], index=r.index, dtype=bool)
    r["compensado"] = r["comp_tardanza"] | r["comp_salida"]

    trabajadas = pd.to_numeric(r["horas_trabajadas"], errors="coerce")
    a_trabajar = pd.to_numeric(r["horas_a_trabajar"], errors="coerce")
    faltante = (a_trabajar - trabajadas).clip(lower=0)

    tarde_h = ((r["_entrada_real_td"] - r["_entrada_esp_td"]).dt.total_seconds() / 3600).clip(lower=0).fillna(0.0)
    salida_h = (pd.to_numeric(r["salida_anticipada_min"], errors="coerce") / 60).clip(lower=0).fillna(0.0)
    cubre_tarde = r["comp_tardanza"] & r["es_tardanza"]
    cubre_salida = r["comp_salida"] & r["es_salida_temprana"]
    completo = (~r["es_tardanza"] | r["comp_tardanza"]) & (~r["es_salida_temprana"] | r["comp_salida"])
    parte_h = tarde_h.where(cubre_tarde, 0.0) + salida_h.where(cubre_salida, 0.0)
    compensada = faltante.where(completo, np.minimum(faltante, parte_h))

    con_horas = r["compensado"] & trabajadas.notna()
    r["_horas_compensadas_dia"] = compensada.where(con_horas, 0.0).fillna(0.0)
    r["recupero_dia"] = r["recupero_dia"] | (con_horas & (r["_horas_compensadas_dia"] >= faltante - 0.005))
    return r


def compensaciones_del_mes(dni, desde, hasta, detalle_mes):
    """Detalle de lo registrado en Ficha > "Tardanzas y salidas anticipadas"
    para `dni` entre `desde` y `hasta` (Davor, 2026-11-06: "una tabla
    inferior de compensaciones con su detalle"), más reciente primero.
    `detalle_mes` es el DataFrame de calcular_detalle_semana() de ese mismo
    rango: de ahí salen lo que pasó ese día (tardanza / salida anticipada),
    cuántas horas faltaron, cuántas quedaron compensadas y cuántas siguen
    pendientes. Si la tabla no se puede leer devuelve [] -- la ficha no se cae."""
    session = get_session()
    try:
        asegurar_tabla_compensaciones()
        registros = (
            session.query(CompensacionHoras)
            .filter(CompensacionHoras.dni == dni, CompensacionHoras.fecha >= desde, CompensacionHoras.fecha <= hasta)
            .order_by(CompensacionHoras.fecha.desc())
            .all()
        )
        filas_db = [
            (r.fecha, r.motivo, bool(r.compensa_tardanza), bool(r.compensa_salida), r.registrado_por, r.updated_at)
            for r in registros
        ]
    except Exception as e:
        print(f"[horas_semanales] detalle de compensaciones no disponible: {e}", file=sys.stderr)
        return []
    finally:
        session.close()

    hay_detalle = detalle_mes is not None and len(detalle_mes) > 0
    fechas = detalle_mes["fecha"].dt.date if hay_detalle else None
    resultado = []
    for fecha, motivo, compensa_tardanza, compensa_salida, registrado_por, registrado_el in filas_db:
        horas_compensadas = horas_pendientes = 0.0
        hubo_tardanza = hubo_salida = False
        if hay_detalle:
            dia = detalle_mes[fechas == fecha]
            if len(dia):
                trabajadas = pd.to_numeric(dia["horas_trabajadas"], errors="coerce")
                a_trabajar = pd.to_numeric(dia["horas_a_trabajar"], errors="coerce")
                faltante = float((a_trabajar - trabajadas).clip(lower=0).fillna(0.0).sum())
                horas_compensadas = float(dia["_horas_compensadas_dia"].sum())
                horas_pendientes = max(faltante - horas_compensadas, 0.0)
                hubo_tardanza = bool(dia["es_tardanza"].any())
                hubo_salida = bool(dia["es_salida_temprana"].any())
        resultado.append({
            "fecha": fecha.strftime("%d/%m"), "fecha_iso": fecha.isoformat(),
            "hubo_tardanza": hubo_tardanza, "hubo_salida": hubo_salida,
            "motivo": motivo, "compensa_tardanza": compensa_tardanza, "compensa_salida": compensa_salida,
            "horas_compensadas": round(horas_compensadas, 2), "horas_pendientes": round(horas_pendientes, 2),
            "registrado_por": registrado_por or "—",
            "registrado_el": registrado_el.strftime("%d/%m/%Y %H:%M") if registrado_el else "—",
        })
    return resultado


def resumen_por_persona(detalle_semana):
    """Agregado semanal por persona -- mismas columnas/regla que
    reporte_semanal.py::agregar_detalle_mercaderista()."""
    if detalle_semana is None or not len(detalle_semana):
        return pd.DataFrame()
    if "compensado" not in detalle_semana.columns:
        detalle_semana = detalle_semana.assign(compensado=False, _horas_compensadas_dia=0.0)

    # Días DISTINTOS, no filas (Davor, 2026-09-22, soporte de 2 turnos/día)
    # -- un Multicanal con 2 turnos tardanza/falta el mismo día no debe
    # contar como 2 días, solo como 1. Se colapsa a 1 fila por (dni,fecha)
    # con "any"/"first" ANTES de contar, separado del agregado de horas de
    # abajo (que sí debe SUMAR los 2 turnos, sin tocar). Para el 99% de la
    # gente (1 sola fila por día) esto da exactamente 1 fila por (dni,fecha)
    # igual que detalle_semana ya tenía -- cero cambio de resultado.
    dias_flags = detalle_semana.groupby(["dni", "fecha"]).agg(
        es_tardanza=("es_tardanza", "any"),
        es_salida_temprana=("es_salida_temprana", "any"),
        es_descanso_medico=("es_descanso_medico", "any"),
        recupero_dia=("recupero_dia", "any"),
        compensado=("compensado", "any"),
        motivo_falta=("motivo_falta", "first"),
        dia_activo=("_dia_activo", "any"),
    ).reset_index()

    g_dias = dias_flags.groupby("dni").agg(
        dias_falta=("motivo_falta", lambda s: int((s == "Falta").sum())),
        dias_vacante=("motivo_falta", lambda s: int((s == "Vacante").sum())),
        dias_descanso_medico=("es_descanso_medico", "sum"),
        dias_tardanza=("es_tardanza", "sum"),
        tardanzas_recuperadas=("es_tardanza", lambda s: int((s & dias_flags.loc[s.index, "recupero_dia"]).sum())),
        dias_salida_temprana=("es_salida_temprana", "sum"),
        salidas_cumplieron=("es_salida_temprana", lambda s: int((s & dias_flags.loc[s.index, "recupero_dia"]).sum())),
        dias_activos=("dia_activo", "sum"),
        dias_compensados=("compensado", "sum"),
    )

    g = detalle_semana.groupby("dni").agg(
        nombre=("nombre", "first"), supervisor=("supervisor", "first"),
        ciudad=("ciudad", "first"), region=("region", "first"),
        horas_trabajadas=("horas_trabajadas", "sum"),
        horas_a_trabajar=("horas_a_trabajar", "sum"),
        _horas_vacante=("_horas_vacante_dia", "sum"),
        _horas_sustento=("_horas_sustento_dia", "sum"),
        _horas_descanso=("_horas_descanso_dia", "sum"),
        _horas_sin_marcacion=("_horas_sin_marcacion_dia", "sum"),
        _horas_compensadas=("_horas_compensadas_dia", "sum"),
    ).join(g_dias).reset_index()

    # DM y Tardanzas sumados al texto (Davor, 2026-10-06: "esa casilla que
    # diga Falta: 2, DM: 4, Tardanzas: 4") -- Vacante se mantiene al final,
    # no se saca nada que ya hubiera, solo se agrega lo pedido.
    g["dias_falta_vacante"] = (
        "Falta: " + g["dias_falta"].astype(str)
        + " / DM: " + g["dias_descanso_medico"].astype(str)
        + " / Tardanzas: " + g["dias_tardanza"].astype(str)
        + " / Vacante: " + g["dias_vacante"].astype(str)
    )
    # 2 decimales (0.01 h = 36 s), no 1 (6 min): las horas se muestran en
    # HH:MM (ver formatear_horas) y con 1 decimal un total de 42:38 salía
    # como 42:30.
    g["horas_trabajadas"] = g["horas_trabajadas"].round(2)
    # Vacante, faltas CON sustento (descanso médico/licencia/feriado
    # regional) y días en estado DESCANSO se descuentan de la base -- a
    # nadie se le puede "deber" horas que tienen justificación real, que
    # nadie estaba para trabajar, o que directamente eran de descanso.
    # _horas_compensadas (Davor, 2026-11-06): lo que faltó en días cuyo
    # motivo un analista marcó como "compensa todas las horas".
    g["horas_a_trabajar"] = (
        g["horas_a_trabajar"] - g["_horas_vacante"] - g["_horas_sustento"] - g["_horas_descanso"] - g["_horas_compensadas"]
    ).round(2)
    # "sin faltas" descuenta además cualquier día sin marcación real
    # (Falta/Vacaciones normales, o un "Asistió" todavía sin hora cargada) --
    # compara solo contra los días que sí tienen una jornada real registrada.
    g["horas_a_trabajar_sin_faltas"] = (g["horas_a_trabajar"] - g["_horas_sin_marcacion"]).round(2)
    g["diferencia_h"] = (g["horas_trabajadas"] - g["horas_a_trabajar_sin_faltas"]).round(2)

    # Division por cero (ej. toda la semana Vacante) -- deja el % en blanco
    # en vez de infinito/NaN feo.
    g["pct_cumplimiento"] = (g["horas_trabajadas"] / g["horas_a_trabajar"].replace(0, np.nan) * 100).round(1)
    g["pct_cumplimiento_sin_faltas"] = (
        g["horas_trabajadas"] / g["horas_a_trabajar_sin_faltas"].replace(0, np.nan) * 100
    ).round(1)

    g = g.drop(columns=["_horas_vacante", "_horas_sustento", "_horas_sin_marcacion"])
    g = g[[
        "dni", "nombre", "supervisor", "ciudad", "region",
        # dias_activos (Davor, 2026-10-10: "colocar como días activos, días
        # que se esta considerando las horas a trabajar sin faltas") -- la
        # cantidad de días que de verdad entran en horas_a_trabajar_sin_faltas
        # (ni Vacante/con sustento/Descanso, con marcación real), para que
        # se entienda sobre cuántos días se está sacando ese cálculo.
        "dias_activos",
        "dias_falta_vacante",
        # dias_falta/dias_descanso_medico/dias_vacante (Davor, 2026-10-09:
        # "dale mayor visibilidad la tabla") -- mismos 3 números que ya
        # estaban adentro del texto de dias_falta_vacante, expuestos acá
        # también sueltos para poder pintarlos como badges de color en vez
        # de un solo string plano en Horas programadas vs trabajadas.
        "dias_falta", "dias_descanso_medico", "dias_vacante",
        "dias_tardanza", "tardanzas_recuperadas", "dias_salida_temprana", "salidas_cumplieron",
        "dias_compensados",
        "horas_trabajadas", "horas_a_trabajar", "horas_a_trabajar_sin_faltas",
        "diferencia_h", "pct_cumplimiento", "pct_cumplimiento_sin_faltas",
    ]]
    return g.sort_values("pct_cumplimiento_sin_faltas", na_position="first")
