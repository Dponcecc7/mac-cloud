# -*- coding: utf-8 -*-
"""
Fase 6: version portable de MAC/export_dashboard_data.py -- la clasificacion
ya se migro a Postgres en Fase 3 de hoy (sin cambios ahi). Cambios de
portabilidad: Maestro por Graph, import relativo. 10_Alerta_PDV_Recurrente.xlsx
viene de alerta_pdv_recurrente.py, que NO es parte del pipeline automatico
ni siquiera en la laptop (herramienta manual/ocasional) -- si no existe, esa
seccion del dashboard queda en 0 en vez de tumbar todo el export (igual de
"stale/ausente" que ya podia estar en produccion si nadie lo corrio a mano).
"""
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import Persona, get_session  # noqa: E402
from fact_models import ClasificacionDiaria  # noqa: E402
from graph_client import leer_excel  # noqa: E402

_session = get_session()
try:
    _filas = (
        _session.query(ClasificacionDiaria.dni, Persona.nombre_completo, ClasificacionDiaria.fecha,
                        ClasificacionDiaria.estado, ClasificacionDiaria.canal_esperado,
                        ClasificacionDiaria.trabajo_otro_canal, ClasificacionDiaria.alerta_analista)
        .join(Persona, Persona.dni == ClasificacionDiaria.dni)
        .all()
    )
finally:
    _session.close()

r = pd.DataFrame(_filas, columns=[
    "DNI", "Nombre", "Fecha", "Estado", "Canal esperado (Patrón)",
    "Trabajó para otro canal", "Alerta para analista",
])
r["Trabajó para otro canal"] = r["Trabajó para otro canal"].map({True: "SÍ", False: "NO"})
r["Alerta para analista"] = r["Alerta para analista"].map({True: "SÍ", False: "NO"})
r["Estado_base"] = r["Estado"].apply(lambda s: s.split(" (")[0])
r["Fecha"] = pd.to_datetime(r["Fecha"])

m_reg = leer_excel("ASISTENCIA/MAC/1_Maestro_Headcount.xlsx", sheet_name="Maestro Headcount", header=3).dropna(how="all")
m_reg.columns = [str(c).strip() for c in m_reg.columns]
m_reg = m_reg.rename(columns={m_reg.columns[0]: "DNI"})
m_reg = m_reg[pd.to_numeric(m_reg["DNI"], errors="coerce").notna()].copy()
m_reg["DNI"] = m_reg["DNI"].astype(str).str.strip()
region_map = m_reg.set_index("DNI")["Región"].to_dict()
r["Región"] = r["DNI"].map(region_map).fillna("Sin región")

tendencia = r.groupby("Fecha")["Estado_base"].value_counts().unstack(fill_value=0)
for col in ["ASISTIÓ A TIEMPO", "TARDANZA", "FALTA", "ALERTA"]:
    if col not in tendencia.columns:
        tendencia[col] = 0
tendencia = tendencia.sort_index()
serie_dias = [d.strftime("%d/%m") for d in tendencia.index]
serie_falta = tendencia["FALTA"].tolist()
serie_tardanza = tendencia["TARDANZA"].tolist()

total = len(r)
n_asistio = (r["Estado_base"] == "ASISTIÓ A TIEMPO").sum()
n_tardanza = (r["Estado_base"] == "TARDANZA").sum()
n_falta = (r["Estado_base"] == "FALTA").sum()
n_geofence = (r["Estado_base"].str.startswith("ALERTA")).sum()
pct_efectividad = round((n_asistio + n_tardanza) / total * 100, 1)

por_canal = r.groupby("Canal esperado (Patrón)")["Estado_base"].apply(
    lambda s: round((s.isin(["ASISTIÓ A TIEMPO", "TARDANZA"])).sum() / len(s) * 100, 1)
).sort_values(ascending=False)

n_otro_canal = (r["Trabajó para otro canal"] == "SÍ").sum()
n_alerta_analista = (r["Alerta para analista"] == "SÍ").sum()

try:
    pdv = pd.read_excel("10_Alerta_PDV_Recurrente.xlsx", sheet_name="PDV recurrente", header=3)
    n_pdv_concentrado = len(pdv)
except FileNotFoundError:
    n_pdv_concentrado = 0

resumen = pd.read_excel("9_Resumen_Por_Persona.xlsx", sheet_name="Resumen por persona", header=3)
top_falta = resumen.sort_values("Falta", ascending=False).head(6)[["Nombre", "Falta", "Tardanza", "% Falta"]].to_dict("records")

n_perfectas = int(((resumen["Falta"] == 0) & (resumen["Tardanza"] == 0)).sum())
n_personas = len(resumen)

ind = pd.read_excel("13_Indicador_Asistencia.xlsx")
META_INDICADOR = 95
pct_bajo_meta = round((ind["Indicador de asistencia (%)"] < META_INDICADOR).sum() / len(ind) * 100, 1)
indicador_promedio = round(ind["Indicador de asistencia (%)"].mean(), 1)
top_indicador_bajo = ind.sort_values("Indicador de asistencia (%)").head(6)[
    ["Nombre", "Días evaluados", "Faltas", "Tardanzas no compensadas", "Indicador de asistencia (%)"]
].to_dict("records")

comp = pd.read_excel("12_Compensacion.xlsx")
n_no_compensados = int((comp["Compensado"] == "NO").sum())
confianza_breakdown = comp[comp["Compensado"] == "NO"]["Confianza"].value_counts().to_dict()

visita_larga = pd.read_excel("14_Alerta_Visita_Larga.xlsx")
n_visita_larga = len(visita_larga)
n_visita_larga_tradicional = int((visita_larga["Canal (Maestro)"] == "TRADICIONAL").sum())

try:
    censo = pd.read_excel("15_Alerta_Punto_Censo.xlsx")
    n_censo_alertados = int(censo["Alerta"].sum()) if len(censo) else 0
except FileNotFoundError:
    n_censo_alertados = 0

registros = r[["DNI", "Nombre", "Fecha", "Región", "Canal esperado (Patrón)", "Estado_base", "Trabajó para otro canal", "Alerta para analista"]].copy()
registros["Fecha"] = registros["Fecha"].dt.strftime("%Y-%m-%d")
registros = registros.rename(columns={
    "Canal esperado (Patrón)": "Canal", "Estado_base": "Estado",
    "Trabajó para otro canal": "OtroCanal", "Alerta para analista": "AlertaAnalista",
})
registros_list = registros.to_dict("records")

data = {
    "periodo": {"desde": r["Fecha"].min().strftime("%d/%m/%Y"), "hasta": r["Fecha"].max().strftime("%d/%m/%Y")},
    "regiones": sorted(r["Región"].unique().tolist()),
    "canales": sorted(r["Canal esperado (Patrón)"].dropna().unique().tolist()),
    "registros": registros_list,
    "resumen": {
        "total_dias_persona": int(total),
        "asistio": int(n_asistio),
        "tardanza": int(n_tardanza),
        "falta": int(n_falta),
        "geofence": int(n_geofence),
        "pct_efectividad": pct_efectividad,
    },
    "tendencia": {"dias": serie_dias, "falta": [int(x) for x in serie_falta], "tardanza": [int(x) for x in serie_tardanza]},
    "efectividad_por_canal": {k: v for k, v in por_canal.items()},
    "alertas": {
        "otro_canal": int(n_otro_canal),
        "alerta_analista": int(n_alerta_analista),
        "pdv_concentrado": int(n_pdv_concentrado),
        "geofence": int(n_geofence),
    },
    "top_falta": top_falta,
    "asistencia_perfecta": {"n": n_perfectas, "total": n_personas},
    "fase3": {
        "indicador_promedio": indicador_promedio,
        "meta_indicador": META_INDICADOR,
        "pct_bajo_meta": pct_bajo_meta,
        "n_bajo_meta": int((ind["Indicador de asistencia (%)"] < META_INDICADOR).sum()),
        "n_personas_indicador": len(ind),
        "top_indicador_bajo": top_indicador_bajo,
        "no_compensados": n_no_compensados,
        "confianza": {str(k): int(v) for k, v in confianza_breakdown.items()},
        "visita_larga_total": n_visita_larga,
        "visita_larga_tradicional": n_visita_larga_tradicional,
        "censo_alertados": n_censo_alertados,
    },
}

with open("dashboard_data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(json.dumps(data, ensure_ascii=False, indent=2))
