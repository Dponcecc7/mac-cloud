# -*- coding: utf-8 -*-
"""Reportes -- Recomendaciones e insights (#3) y Alertas predictivas (#5) de
la lista de Davor, 2026-08-22. UNA sola fuente para ambas ideas: reglas de
umbral explícitas sobre los mismos datos ya validados de Horas semanales
(horas_semanales.py) y Alertas (alertas.py) -- NO es un modelo predictivo
real (eso necesitaría mucho más historial); son señales tempranas honestas,
etiquetadas como tal en la interfaz."""
import datetime as dt

import pandas as pd

from alertas import SALIDA_ANTICIPADA_MIN, UMBRAL, _tiene_sustento
from dimension_models import Persona, get_session
from fact_models import ClasificacionDiaria
from horas_semanales import semana_iso, calcular_detalle_semana, resumen_por_persona
from scoping import aplicar_filtros_extra, condicion_scope

UMBRAL_CAIDA_PCT = 15  # puntos porcentuales de caída en % Cumplimiento sin faltas (B)
UMBRAL_RIESGO_SEMANA_PCT = 80  # (D) -- mismo umbral "amarillo" que ya usa reporte_semanal.py

# Resumen de perfil / puntaje del mes (Davor, 2026-08-24, pesos ajustados
# 2026-08-24) -- 4 métricas del mes en curso, cada una normalizada a 0-100
# (más alto = mejor), combinadas con estos pesos. Bandas de la carita final
# sobre el promedio ponderado de las 4.
PESO_CUMPLIMIENTO = 0.35
PESO_FALTA = 0.35
PESO_TARDANZA = 0.15
PESO_SALIDA = 0.15
BANDA_EXCELENTE = 95
BANDA_SEGUIMIENTO = 85


def insights_equipo(usuario_actual, dni_filtro=None, desde=None, hasta=None,
                     rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None,
                     detalle=None):
    """Devuelve la lista de insights/alertas predictivas para el equipo
    visible de `usuario_actual` -- o para un solo `dni_filtro` (ficha
    individual, el acceso ya se valida aparte). `desde` acota el bloque C
    ("a un paso de la alerta formal", que cuenta sobre ese rango en vez de
    forzar el mes calendario completo -- Davor, 2026-08-26: quería poder
    filtrar Desempeño por días sueltos, no solo mes completo); default
    `hasta.replace(day=1)` si no se pasa, igual que antes. `hasta` es la
    fecha de referencia para "esta semana"/"días transcurridos" (default
    hoy real). `rol_filtro`/`region_filtro`/`supervisor_filtro`/`ciudad_filtro`:
    filtros extra de Reportes (solo admin/analista, ver scoping.aplicar_filtros_extra).

    `detalle` (Davor, 2026-09-01, 503 en Render por timeout): si ya se trajo
    calcular_detalle_semana() afuera (ver reportes.py::recomendaciones(),
    que reusa el mismo detalle para esta función y resumen_perfil_equipo()
    en vez de traerlo 2 veces -- lo mas caro del reporte), se reusa en vez
    de volver a consultar. Debe cubrir al menos desde `desde_semanas` (4
    semanas antes del isocalendar de `hasta`) hasta `hasta`."""
    hasta = hasta or dt.date.today()
    anio_actual, num_actual, _ = hasta.isocalendar()
    inicio_actual, _ = semana_iso(anio_actual, num_actual)
    desde_semanas = inicio_actual - dt.timedelta(weeks=4)  # 4 semanas cerradas + la actual

    if detalle is None:
        detalle = calcular_detalle_semana(
            desde_semanas, hasta, usuario_actual, dni_filtro=dni_filtro,
            rol_filtro=rol_filtro, region_filtro=region_filtro, supervisor_filtro=supervisor_filtro,
            ciudad_filtro=ciudad_filtro, canal_filtro=canal_filtro,
        )
    if not len(detalle):
        return []
    detalle = detalle.copy()
    detalle["semana_iso"] = detalle["fecha"].apply(lambda f: f.isocalendar()[1])
    semanas = sorted(detalle["semana_iso"].unique())
    semanas_cerradas = [s for s in semanas if s != num_actual]

    resumenes_semana = {}
    for num_sem, grupo in detalle.groupby("semana_iso"):
        resumenes_semana[num_sem] = resumen_por_persona(grupo).set_index("dni")

    tardanzas_semana = detalle[detalle["estado_base"] == "TARDANZA"].groupby(["dni", "semana_iso"]).size()

    mes_desde = desde or hasta.replace(day=1)
    detalle_mes = detalle[detalle["fecha"] >= pd.Timestamp(mes_desde)]
    tardanzas_mes = detalle_mes[detalle_mes["estado_base"] == "TARDANZA"].groupby("dni").size()
    # Mismo criterio que alertas.py: descanso médico/licencia/feriado
    # regional tienen sustento, no cuentan para "a un paso de la alerta
    # formal" (esa regla espeja directamente el umbral de alertas.py).
    # Ojo: .apply() sobre una columna de 0 filas puede devolver un resultado
    # con el índice roto (mismo bug ya encontrado en alertas.py) -- solo se
    # filtra si hay algo que filtrar.
    faltas_solo = detalle_mes[detalle_mes["estado_base"] == "FALTA"]
    if len(faltas_solo):
        faltas_sin_sustento = faltas_solo[~faltas_solo["comentario"].apply(_tiene_sustento)]
    else:
        faltas_sin_sustento = faltas_solo
    faltas_mes = faltas_sin_sustento.groupby("dni").size()

    insights = []
    for dni in detalle["dni"].unique():
        nombre = detalle[detalle["dni"] == dni]["nombre"].iloc[0]

        # A) Patrón de tardanza creciente -- esta semana ya supera el
        # promedio de las semanas anteriores (y no es solo 1 tardanza suelta).
        serie_t = [int(tardanzas_semana.get((dni, s), 0)) for s in semanas]
        if len(serie_t) >= 2:
            actual_t, anteriores_t = serie_t[-1], serie_t[:-1]
            prom_ant = sum(anteriores_t) / len(anteriores_t) if anteriores_t else 0
            if actual_t >= 2 and actual_t > prom_ant:
                fechas_t = sorted(
                    detalle[(detalle["dni"] == dni) & (detalle["semana_iso"] == num_actual) & (detalle["estado_base"] == "TARDANZA")]["fecha"]
                )
                insights.append({
                    "dni": dni, "nombre": nombre, "tipo": "tardanza_creciente", "icono": "📈", "severidad": "media",
                    "mensaje": f"Patrón de tardanza creciente — {actual_t} esta semana vs {prom_ant:.1f} en promedio antes.",
                    "detalle_fechas": [f.strftime("%d/%m") for f in fechas_t],
                })

        # B) Cumplimiento de horas en baja -- compara el promedio de las 2
        # últimas semanas CERRADAS contra las 2 anteriores a esas (la semana
        # en curso no cuenta, todavía no terminó).
        semana_pct_cerradas = [
            (s, resumenes_semana[s].loc[dni, "pct_cumplimiento_sin_faltas"])
            for s in semanas_cerradas if dni in resumenes_semana[s].index
        ]
        semana_pct_cerradas = [(s, p) for s, p in semana_pct_cerradas if pd.notna(p)]
        if len(semana_pct_cerradas) >= 4:
            recientes, previas = semana_pct_cerradas[-2:], semana_pct_cerradas[-4:-2]
            prom_recientes = sum(p for _, p in recientes) / len(recientes)
            prom_previas = sum(p for _, p in previas) / len(previas)
            caida = prom_previas - prom_recientes
            if caida >= UMBRAL_CAIDA_PCT:
                insights.append({
                    "dni": dni, "nombre": nombre, "tipo": "cumplimiento_bajando", "icono": "📉", "severidad": "media",
                    "mensaje": f"Cumplimiento de horas en baja — cayó {caida:.0f} puntos vs. 2 semanas atrás.",
                    "detalle_semanas": [{"semana": f"S{s}", "pct": round(p, 1)} for s, p in (previas + recientes)],
                })

        # C) A un paso de la alerta formal (ver alertas.py, UMBRAL=3) --
        # aviso preventivo antes de que se dispare la alerta de verdad.
        t_mes, f_mes = int(tardanzas_mes.get(dni, 0)), int(faltas_mes.get(dni, 0))
        if t_mes == UMBRAL - 1:
            fechas_t_mes = sorted(detalle_mes[(detalle_mes["dni"] == dni) & (detalle_mes["estado_base"] == "TARDANZA")]["fecha"])
            insights.append({
                "dni": dni, "nombre": nombre, "tipo": "cerca_alerta_tardanza", "icono": "⚠️", "severidad": "baja",
                "mensaje": f"A una tardanza de activar la alerta formal de memorándum ({t_mes} este mes).",
                "detalle_fechas": [f.strftime("%d/%m") for f in fechas_t_mes],
            })
        if f_mes == UMBRAL - 1:
            fechas_f_mes = sorted(faltas_sin_sustento[faltas_sin_sustento["dni"] == dni]["fecha"])
            insights.append({
                "dni": dni, "nombre": nombre, "tipo": "cerca_alerta_falta", "icono": "🛑", "severidad": "baja",
                "mensaje": f"A una falta de activar la alerta formal de observación ({f_mes} este mes).",
                "detalle_fechas": [f.strftime("%d/%m") for f in fechas_f_mes],
            })

        # D) Riesgo de no llegar al objetivo de la semana en curso -- compara
        # horas trabajadas vs a trabajar SOLO de los días ya transcurridos
        # (no toda la semana, que penalizaría siempre hasta el sábado).
        avance = detalle[(detalle["dni"] == dni) & (detalle["semana_iso"] == num_actual) & (detalle["fecha"] <= pd.Timestamp(hasta))]
        dias_habiles = int((avance["horas_a_trabajar"] > 0).sum())
        if dias_habiles >= 2:
            resumen_avance = resumen_por_persona(avance)
            if len(resumen_avance):
                pct_avance = resumen_avance.iloc[0]["pct_cumplimiento_sin_faltas"]
                if pd.notna(pct_avance) and pct_avance < UMBRAL_RIESGO_SEMANA_PCT:
                    detalle_dias = [
                        {
                            "fecha": row["fecha"].strftime("%d/%m"),
                            "horas_trabajadas": row["horas_trabajadas"] if pd.notna(row["horas_trabajadas"]) else 0,
                            "horas_a_trabajar": row["horas_a_trabajar"],
                        }
                        for _, row in avance[avance["horas_a_trabajar"] > 0].sort_values("fecha").iterrows()
                    ]
                    insights.append({
                        "dni": dni, "nombre": nombre, "tipo": "riesgo_semana", "icono": "⏳", "severidad": "media",
                        "mensaje": f"Va al {pct_avance:.0f}% de ritmo esta semana — en riesgo de no llegar al objetivo si sigue así.",
                        "detalle_dias": detalle_dias,
                    })

    orden_severidad = {"alta": 0, "media": 1, "baja": 2}
    insights.sort(key=lambda i: (orden_severidad.get(i["severidad"], 9), i["nombre"]))
    return insights


def _clip(x):
    return max(0.0, min(100.0, x))


def resumen_perfil_equipo(usuario_actual, dni_filtro=None, desde=None, hasta=None,
                           rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None,
                           detalle=None):
    """Resumen de perfil del periodo elegido (default: mes en curso) -- 4
    métricas normalizadas a 0-100 (más alto = mejor), combinadas con
    PESO_CUMPLIMIENTO/PESO_FALTA/PESO_TARDANZA/PESO_SALIDA: Cumplimiento de
    horas (mismo % sin faltas ya validado en Horas semanales), Tardanzas,
    Faltas SIN sustento (mismo criterio que alertas.py) y Salidas
    anticipadas (mismo umbral de 10 min que ya usa alertas.py), todas sobre
    los días hábiles ya transcurridos del periodo. Para TODO el equipo
    visible de `usuario_actual` (o un solo `dni_filtro`), no solo quienes
    ya tienen una señal arriba. `desde`/`hasta` (default mes calendario en
    curso) -- Davor, 2026-08-26: quería poder filtrar hasta un día
    puntual, no solo el mes completo. `rol_filtro`/`region_filtro`/
    `supervisor_filtro`/`ciudad_filtro`: filtros extra de Reportes (solo
    admin/analista, ver scoping.aplicar_filtros_extra).

    `detalle` -- mismo criterio que insights_equipo(): si ya se trajo
    calcular_detalle_semana() afuera (cubriendo al menos desde/hasta), se
    reusa y solo se recorta al rango pedido, en vez de volver a consultar
    (evita traer 2 veces lo mismo cuando reportes.py::recomendaciones()
    pide insights_equipo() y esto en la misma request)."""
    hasta = hasta or dt.date.today()
    desde = desde or hasta.replace(day=1)

    if detalle is None:
        detalle_mes = calcular_detalle_semana(
            desde, hasta, usuario_actual, dni_filtro=dni_filtro,
            rol_filtro=rol_filtro, region_filtro=region_filtro, supervisor_filtro=supervisor_filtro,
            ciudad_filtro=ciudad_filtro, canal_filtro=canal_filtro,
        )
    elif not len(detalle):
        # calcular_detalle_semana() devuelve un DataFrame SIN columnas si no
        # hay filas -- detalle["fecha"] tiraria KeyError sobre ese vacio.
        detalle_mes = detalle
    else:
        detalle_mes = detalle[(detalle["fecha"] >= pd.Timestamp(desde)) & (detalle["fecha"] <= pd.Timestamp(hasta))]
    if not len(detalle_mes):
        return []

    resumen_mes = resumen_por_persona(detalle_mes).set_index("dni")
    dias_habiles = detalle_mes[detalle_mes["horas_a_trabajar"] > 0].groupby("dni").size()
    tardanzas = detalle_mes[detalle_mes["estado_base"] == "TARDANZA"].groupby("dni").size()

    faltas_solo = detalle_mes[detalle_mes["estado_base"] == "FALTA"]
    faltas_sin_sustento = faltas_solo[~faltas_solo["comentario"].apply(_tiene_sustento)] if len(faltas_solo) else faltas_solo
    faltas = faltas_sin_sustento.groupby("dni").size()

    # Salidas anticipadas -- calcular_detalle_semana() no trae
    # salida_anticipada_min, se consulta aparte (mismo umbral/columna que ya
    # usa alertas.py para su propia alerta "salida_temprana").
    session = get_session()
    try:
        q = (
            session.query(ClasificacionDiaria.dni, ClasificacionDiaria.salida_anticipada_min)
            .join(Persona, Persona.dni == ClasificacionDiaria.dni)
            .filter(ClasificacionDiaria.fecha >= desde, ClasificacionDiaria.fecha <= hasta)
        )
        if dni_filtro:
            q = q.filter(ClasificacionDiaria.dni == dni_filtro)
        else:
            cond_scope = condicion_scope(Persona, usuario_actual)
            if cond_scope is not None:
                q = q.filter(cond_scope)
            q = aplicar_filtros_extra(q, Persona, rol_filtro, region_filtro, supervisor_filtro, ciudad_filtro)
        filas_salida = q.all()
    finally:
        session.close()
    salidas = pd.DataFrame(filas_salida, columns=["dni", "salida_anticipada_min"])
    if len(salidas):
        salidas_tempranas = salidas[salidas["salida_anticipada_min"].fillna(0) > SALIDA_ANTICIPADA_MIN].groupby("dni").size()
    else:
        salidas_tempranas = pd.Series(dtype=int)

    # Solo gente ACTUALMENTE activa -- alguien dado de baja durante el
    # periodo (ej. renunció el 21/08) sigue teniendo días hábiles y puede
    # salir con un puntaje "Excelente" perfecto de sus pocos días
    # trabajados, pero ya no es parte del equipo y ensucia el ranking
    # (Davor, 2026-08-26: "Fernando... no debería aparecer en el reporte").
    session = get_session()
    try:
        estados = dict(
            session.query(Persona.dni, Persona.estado)
            .filter(Persona.dni.in_(detalle_mes["dni"].unique().tolist())).all()
        )
    finally:
        session.close()

    resumen = []
    for dni in detalle_mes["dni"].unique():
        if estados.get(dni) != "Activo":
            continue
        dh = int(dias_habiles.get(dni, 0))
        if dh == 0:
            continue
        nombre = detalle_mes[detalle_mes["dni"] == dni]["nombre"].iloc[0]

        pct_cumpl = resumen_mes.loc[dni, "pct_cumplimiento_sin_faltas"] if dni in resumen_mes.index else None
        comp_cumplimiento = _clip(float(pct_cumpl)) if pct_cumpl is not None and pd.notna(pct_cumpl) else None
        tardanza_pct = round(int(tardanzas.get(dni, 0)) / dh * 100, 1)
        falta_pct = round(int(faltas.get(dni, 0)) / dh * 100, 1)
        salida_pct = round(int(salidas_tempranas.get(dni, 0)) / dh * 100, 1)
        comp_tardanza = _clip(100.0 - tardanza_pct)
        comp_falta = _clip(100.0 - falta_pct)
        comp_salida = _clip(100.0 - salida_pct)

        # Ponderado (35/35/15/15), no promedio simple -- si falta un
        # componente (solo Cumplimiento puede venir None, sin datos de
        # Horas semanales ese mes), se renormaliza sobre el peso de los que
        # sí hay en vez de tratarlo como 0.
        componentes = [
            (comp_cumplimiento, PESO_CUMPLIMIENTO), (comp_tardanza, PESO_TARDANZA),
            (comp_falta, PESO_FALTA), (comp_salida, PESO_SALIDA),
        ]
        componentes = [(v, w) for v, w in componentes if v is not None]
        if not componentes:
            continue
        peso_total = sum(w for _, w in componentes)
        puntaje = sum(v * w for v, w in componentes) / peso_total

        if puntaje >= BANDA_EXCELENTE:
            carita, etiqueta = "😊", "Excelente"
        elif puntaje >= BANDA_SEGUIMIENTO:
            carita, etiqueta = "😐", "Seguimiento"
        else:
            carita, etiqueta = "😟", "Evaluar"

        resumen.append({
            "dni": dni, "nombre": nombre,
            "cumplimiento": round(comp_cumplimiento, 1) if comp_cumplimiento is not None else None,
            "tardanza_pct": tardanza_pct, "falta_pct": falta_pct, "salida_pct": salida_pct,
            "puntaje": round(puntaje, 1), "carita": carita, "etiqueta": etiqueta,
        })

    resumen.sort(key=lambda r: r["puntaje"])
    return resumen
