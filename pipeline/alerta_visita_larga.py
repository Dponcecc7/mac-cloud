# -*- coding: utf-8 -*-
"""
Fase 6: version portable de MAC/alerta_visita_larga.py -- misma logica,
Maestro/Patron por Graph (graph_client) en vez de disco local. Visitas/*.xlsx
y los 2 Excel de salida siguen siendo archivos de trabajo locales al runner.
"""
import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graph_client import leer_excel  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from anon import pseudonimo  # noqa: E402


def fmt_hora(td):
    if pd.isna(td):
        return None
    total_seg = int(td.total_seconds())
    h, resto = divmod(total_seg, 3600)
    m, s = divmod(resto, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


UMBRAL_MIN = 60

dfs = []
for f in sorted(glob.glob("Visitas/*.xlsx")):
    d = pd.read_excel(f)
    dfs.append(d)
v = pd.concat(dfs, ignore_index=True)
v["nro_documento"] = v["nro_documento"].astype(str).str.strip()
v = v.drop_duplicates(subset=["nro_documento", "punto_venta_id", "fecha_inicio", "hora_inicio", "fecha_fin", "hora_fin"], keep="first")
v["fecha_inicio_dt"] = pd.to_datetime(v["fecha_inicio"], format="%d-%m-%Y", errors="coerce")
v["hora_inicio_td"] = pd.to_timedelta(v["hora_inicio"].astype(str), errors="coerce")
v["hora_fin_td"] = pd.to_timedelta(v["hora_fin"].astype(str), errors="coerce")
v["geofence_ok"] = v["distancia_metros_inicio"] <= 200
v["duracion_min"] = (v["hora_fin_td"] - v["hora_inicio_td"]).dt.total_seconds() / 60

CIERRE_AUTOMATICO = pd.Timedelta(hours=23, minutes=30, seconds=0)
v_ok = v[v["geofence_ok"] & (v["hora_fin_td"] != CIERRE_AUTOMATICO)].copy()

PUNTOS_EXCLUIDOS = ["AMOF", "OVERALL", "PUNTO CENSO"]
patron_excluido = "|".join(PUNTOS_EXCLUIDOS)
v_ok = v_ok[~v_ok["punto_venta"].astype(str).str.upper().str.contains(patron_excluido, na=False)].copy()

p = leer_excel("ASISTENCIA/MAC/2A_Patron_Recurrente.xlsx", sheet_name="Patrón recurrente", header=3).dropna(how="all")
p.columns = [str(c).strip() for c in p.columns]
p = p.rename(columns={p.columns[0]: "DNI", p.columns[1]: "Dia"})
p["DNI"] = p["DNI"].astype(str).str.strip()
p["Dia"] = p["Dia"].astype(str).str.strip().str.lower()
dia_map = {"lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2, "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5}
p["weekday"] = p["Dia"].map(dia_map)
col_sal = [c for c in p.columns if "salida" in c.lower()][0]
p_idx = p.set_index(["DNI", "weekday"])

m = leer_excel("ASISTENCIA/MAC/1_Maestro_Headcount.xlsx", sheet_name="Maestro Headcount", header=3).dropna(how="all")
m.columns = [str(c).strip() for c in m.columns]
m = m.rename(columns={m.columns[0]: "DNI"})
# Mismo gotcha que parseo_headcount.py -- si ALGUNA fila de DNI viene
# vacía, pandas sube toda la columna a float64 y sobrevive al filtro,
# dejando "18074336.0" en vez de "18074336" para todos los DNIs válidos.
dni_num = pd.to_numeric(m["DNI"], errors="coerce")
m = m[dni_num.notna()].copy()
m["DNI"] = dni_num[dni_num.notna()].astype("int64").astype(str)
nombre_map = m.set_index("DNI")["Nombre completo"].to_dict()

dnis_mercaderistas = set(m[m["Rol"] == "MERCADERISTAS"]["DNI"])
v_ok = v_ok[v_ok["nro_documento"].isin(dnis_mercaderistas)].copy()
print(f"Filtrado a Rol=MERCADERISTAS: {v_ok['nro_documento'].nunique()} personas")

idx_ultima = v_ok.groupby(["nro_documento", "fecha_inicio_dt"])["hora_inicio_td"].idxmax()
ultimas = v_ok.loc[idx_ultima].copy()

filas = []
for _, row in ultimas.iterrows():
    dni = row["nro_documento"]
    weekday = row["fecha_inicio_dt"].weekday()
    key = (dni, weekday)
    if key not in p_idx.index:
        continue
    pat = p_idx.loc[key]
    if isinstance(pat, pd.DataFrame):
        pat = pat.iloc[0]
    salida_esp_td = pd.to_timedelta(str(pat[col_sal]))
    if row["duracion_min"] > UMBRAL_MIN and row["hora_inicio_td"] < salida_esp_td:
        filas.append({
            "DNI": dni,
            "Nombre": nombre_map.get(dni, ""),
            "Fecha": row["fecha_inicio_dt"].date(),
            "Punto de venta": row["punto_venta"],
            "Hora inicio última visita": fmt_hora(row["hora_inicio_td"]),
            "Hora fin última visita": fmt_hora(row["hora_fin_td"]),
            "Duración (min)": round(row["duracion_min"], 1),
            "Hora salida programada": fmt_hora(salida_esp_td),
        })

res = pd.DataFrame(filas).sort_values("Duración (min)", ascending=False)
total_dias_evaluados = len(ultimas)
print(f"Total día-persona con última visita evaluada: {total_dias_evaluados}")
print(f"Casos con última visita > {UMBRAL_MIN} min Y antes de la hora de salida programada: {len(res)} ({len(res)/total_dias_evaluados*100:.1f}%)")

canal_por_dni = m.set_index("DNI")["Canal"].to_dict()
res["Canal (Maestro)"] = res["DNI"].map(canal_por_dni)
print()
print("Desglose por canal:")
print(res["Canal (Maestro)"].value_counts().to_string())

res_tradicional = res[res["Canal (Maestro)"] == "TRADICIONAL"]
print()
print(f"Casos en canal TRADICIONAL (el patrón sospechoso que preguntas): {len(res_tradicional)}")
# DNI pseudonimizado, sin Nombre (Fase 6, repo público -- ver
# pipeline/anon.py). El detalle real completo queda en
# 14_Alerta_Visita_Larga.xlsx (SharePoint, no público).
print()
print("Top 20 casos TRADICIONAL (por duración):")
for _, row in res_tradicional.head(20).iterrows():
    print(f"  {pseudonimo(row['DNI'])} {row['Fecha']}: {row['Duración (min)']} min "
          f"(hora inicio {row['Hora inicio última visita']}, salida programada {row['Hora salida programada']})")

res.to_excel("14_Alerta_Visita_Larga.xlsx", index=False)
print()
print("Guardado: 14_Alerta_Visita_Larga.xlsx")

UMBRAL_DIAS_CENSO = 3
UMBRAL_HORAS_CENSO_MIN = 120

censo = v[v["geofence_ok"] & v["nro_documento"].isin(dnis_mercaderistas) &
          v["punto_venta"].astype(str).str.upper().str.contains("PUNTO CENSO", na=False)].copy()

print()
print("=" * 70)
print("CHEQUEO PUNTO CENSO (debe ser esporádico)")
print("=" * 70)
if censo.empty:
    print("Ningún mercaderista registró visitas a Punto Censo en el período.")
else:
    resumen_censo = censo.groupby("nro_documento").agg(
        dias_distintos=("fecha_inicio_dt", "nunique"),
        duracion_total_min=("duracion_min", "sum"),
        duracion_max_min=("duracion_min", "max"),
    ).reset_index().rename(columns={"nro_documento": "DNI"})
    resumen_censo["Nombre"] = resumen_censo["DNI"].map(nombre_map)
    resumen_censo["Alerta"] = (
        (resumen_censo["dias_distintos"] > UMBRAL_DIAS_CENSO) |
        (resumen_censo["duracion_max_min"] > UMBRAL_HORAS_CENSO_MIN)
    )
    resumen_censo = resumen_censo.sort_values("dias_distintos", ascending=False)
    print(f"Mercaderistas con alguna visita a Punto Censo: {len(resumen_censo)}")
    print(f"Alertados (>{UMBRAL_DIAS_CENSO} días distintos, o >{UMBRAL_HORAS_CENSO_MIN} min seguidos): {resumen_censo['Alerta'].sum()}")
    # DNI pseudonimizado, sin Nombre (Fase 6, repo público) -- el detalle
    # real completo queda en 15_Alerta_Punto_Censo.xlsx (SharePoint).
    for _, row in resumen_censo.iterrows():
        print(f"  {pseudonimo(row['DNI'])}: {row['dias_distintos']} días distintos, "
              f"{row['duracion_total_min']} min total, {row['duracion_max_min']} min máx, alerta={row['Alerta']}")
    resumen_censo.to_excel("15_Alerta_Punto_Censo.xlsx", index=False)
    print()
    print("Guardado: 15_Alerta_Punto_Censo.xlsx")
