# -*- coding: utf-8 -*-
"""Reportes -- Recomendaciones e insights (#3) y Alertas predictivas (#5) de
la lista de Davor, 2026-08-22. UNA sola fuente para ambas ideas: reglas de
umbral explícitas sobre los mismos datos ya validados de Horas semanales
(horas_semanales.py) y Alertas (alertas.py) -- NO es un modelo predictivo
real (eso necesitaría mucho más historial); son señales tempranas honestas,
etiquetadas como tal en la interfaz."""
import datetime as dt

import pandas as pd

from alertas import UMBRAL
from horas_semanales import semana_iso, calcular_detalle_semana, resumen_por_persona

UMBRAL_CAIDA_PCT = 15  # puntos porcentuales de caída en % Cumplimiento sin faltas (B)
UMBRAL_RIESGO_SEMANA_PCT = 80  # (D) -- mismo umbral "amarillo" que ya usa reporte_semanal.py


def insights_equipo(usuario_actual, dni_filtro=None, hoy=None):
    """Devuelve la lista de insights/alertas predictivas para el equipo
    visible de `usuario_actual` -- o para un solo `dni_filtro` (ficha
    individual, el acceso ya se valida aparte)."""
    hoy = hoy or dt.date.today()
    anio_actual, num_actual, _ = hoy.isocalendar()
    inicio_actual, _ = semana_iso(anio_actual, num_actual)
    desde = inicio_actual - dt.timedelta(weeks=4)  # 4 semanas cerradas + la actual

    detalle = calcular_detalle_semana(desde, hoy, usuario_actual, dni_filtro=dni_filtro)
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

    mes_desde = hoy.replace(day=1)
    detalle_mes = detalle[detalle["fecha"] >= pd.Timestamp(mes_desde)]
    tardanzas_mes = detalle_mes[detalle_mes["estado_base"] == "TARDANZA"].groupby("dni").size()
    faltas_mes = detalle_mes[detalle_mes["estado_base"] == "FALTA"].groupby("dni").size()

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
                insights.append({
                    "dni": dni, "nombre": nombre, "tipo": "tardanza_creciente", "icono": "📈", "severidad": "media",
                    "mensaje": f"Patrón de tardanza creciente — {actual_t} esta semana vs {prom_ant:.1f} en promedio antes.",
                })

        # B) Cumplimiento de horas en baja -- compara el promedio de las 2
        # últimas semanas CERRADAS contra las 2 anteriores a esas (la semana
        # en curso no cuenta, todavía no terminó).
        pcts_cerrados = [
            resumenes_semana[s].loc[dni, "pct_cumplimiento_sin_faltas"]
            for s in semanas_cerradas if dni in resumenes_semana[s].index
        ]
        pcts_cerrados = [p for p in pcts_cerrados if pd.notna(p)]
        if len(pcts_cerrados) >= 4:
            recientes, previas = pcts_cerrados[-2:], pcts_cerrados[-4:-2]
            caida = (sum(previas) / len(previas)) - (sum(recientes) / len(recientes))
            if caida >= UMBRAL_CAIDA_PCT:
                insights.append({
                    "dni": dni, "nombre": nombre, "tipo": "cumplimiento_bajando", "icono": "📉", "severidad": "media",
                    "mensaje": f"Cumplimiento de horas en baja — cayó {caida:.0f} puntos vs. 2 semanas atrás.",
                })

        # C) A un paso de la alerta formal (ver alertas.py, UMBRAL=3) --
        # aviso preventivo antes de que se dispare la alerta de verdad.
        t_mes, f_mes = int(tardanzas_mes.get(dni, 0)), int(faltas_mes.get(dni, 0))
        if t_mes == UMBRAL - 1:
            insights.append({
                "dni": dni, "nombre": nombre, "tipo": "cerca_alerta", "icono": "🔶", "severidad": "baja",
                "mensaje": f"A una tardanza de activar la alerta formal de memorándum ({t_mes} este mes).",
            })
        if f_mes == UMBRAL - 1:
            insights.append({
                "dni": dni, "nombre": nombre, "tipo": "cerca_alerta", "icono": "🔶", "severidad": "baja",
                "mensaje": f"A una falta de activar la alerta formal de observación ({f_mes} este mes).",
            })

        # D) Riesgo de no llegar al objetivo de la semana en curso -- compara
        # horas trabajadas vs a trabajar SOLO de los días ya transcurridos
        # (no toda la semana, que penalizaría siempre hasta el sábado).
        avance = detalle[(detalle["dni"] == dni) & (detalle["semana_iso"] == num_actual) & (detalle["fecha"] <= pd.Timestamp(hoy))]
        dias_habiles = int((avance["horas_a_trabajar"] > 0).sum())
        if dias_habiles >= 2:
            resumen_avance = resumen_por_persona(avance)
            if len(resumen_avance):
                pct_avance = resumen_avance.iloc[0]["pct_cumplimiento_sin_faltas"]
                if pd.notna(pct_avance) and pct_avance < UMBRAL_RIESGO_SEMANA_PCT:
                    insights.append({
                        "dni": dni, "nombre": nombre, "tipo": "riesgo_semana", "icono": "⏳", "severidad": "media",
                        "mensaje": f"Va al {pct_avance:.0f}% de ritmo esta semana — en riesgo de no llegar al objetivo si sigue así.",
                    })

    orden_severidad = {"alta": 0, "media": 1, "baja": 2}
    insights.sort(key=lambda i: (orden_severidad.get(i["severidad"], 9), i["nombre"]))
    return insights
