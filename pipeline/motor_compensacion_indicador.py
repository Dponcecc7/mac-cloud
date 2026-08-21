# -*- coding: utf-8 -*-
"""
Fase 6: version portable de MAC/motor_compensacion_indicador.py -- misma
logica de negocio (delta diario, ventana de compensacion de 3 dias,
indicador de asistencia), ya migrada a leer Postgres desde la Fase 3 de hoy
(sin cambios ahi). Cambios de portabilidad: Maestro por Graph (graph_client)
en vez de disco local, imports relativos a este archivo en vez de ruta
absoluta de Windows. Visitas/*.xlsx y los 2 Excel de salida
(12_Compensacion.xlsx, 13_Indicador_Asistencia.xlsx) siguen siendo archivos
de trabajo LOCALES al runner -- son solo estado intermedio dentro de la
misma corrida del pipeline (los lee export_dashboard_data.py despues, en el
mismo job), igual que ya funcionaba antes.
"""
import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import Persona, get_session  # noqa: E402
from fact_models import ClasificacionDiaria  # noqa: E402
from graph_client import leer_excel  # noqa: E402

_dfs = []
for f in sorted(glob.glob("Visitas/*.xlsx")):
    _d = pd.read_excel(f)
    _dfs.append(_d)
v = pd.concat(_dfs, ignore_index=True)
v["nro_documento"] = v["nro_documento"].astype(str).str.strip()
v = v.drop_duplicates(subset=["nro_documento", "punto_venta_id", "fecha_inicio", "hora_inicio", "fecha_fin", "hora_fin"], keep="first")
v["fecha_inicio_dt"] = pd.to_datetime(v["fecha_inicio"], format="%d-%m-%Y", errors="coerce")
v["geofence_ok"] = v["distancia_metros_inicio"] <= 200
v_ok = v[v["geofence_ok"]]
n_visitas_por_dia = v_ok.groupby(["nro_documento", "fecha_inicio_dt"]).size().to_dict()


def confianza(dni, fecha):
    n = n_visitas_por_dia.get((dni, fecha), 0)
    if n <= 2:
        return "Baja", n
    if n <= 5:
        return "Media", n
    return "Alta", n


_session = get_session()
try:
    _filas = (
        _session.query(ClasificacionDiaria.dni, Persona.nombre_completo, ClasificacionDiaria.fecha,
                        ClasificacionDiaria.estado, ClasificacionDiaria.entrada_esperada,
                        ClasificacionDiaria.entrada_real, ClasificacionDiaria.salida_esperada,
                        ClasificacionDiaria.salida_real)
        .join(Persona, Persona.dni == ClasificacionDiaria.dni)
        .all()
    )
finally:
    _session.close()

r = pd.DataFrame(_filas, columns=[
    "DNI", "Nombre", "Fecha", "Estado", "Entrada esperada", "Entrada real", "Salida esperada", "Salida real",
])
r["Fecha"] = pd.to_datetime(r["Fecha"])
r["Estado_base"] = r["Estado"].apply(lambda s: s.split(" (")[0])

_m = leer_excel("ASISTENCIA/MAC/1_Maestro_Headcount.xlsx", sheet_name="Maestro Headcount", header=3).dropna(how="all")
_m.columns = [str(c).strip() for c in _m.columns]
_m = _m.rename(columns={_m.columns[0]: "DNI"})
_m = _m[pd.to_numeric(_m["DNI"], errors="coerce").notna()].copy()
_m["DNI"] = _m["DNI"].astype(str).str.strip()
_dnis_mercaderistas = set(_m[_m["Rol"] == "MERCADERISTAS"]["DNI"])
r = r[r["DNI"].isin(_dnis_mercaderistas)].copy()
print(f"Filtrado a Rol=MERCADERISTAS: {r['DNI'].nunique()} personas")

_hoy = pd.Timestamp.today().normalize()
_antes = len(r)
r = r[r["Fecha"] < _hoy].copy()
print(f"Excluyendo el día de hoy ({_hoy.date()}, aún en curso): {_antes - len(r)} filas removidas")


def to_min(t):
    if pd.isna(t) or t in (None, "None"):
        return None
    try:
        td = pd.to_timedelta(str(t))
        return td.total_seconds() / 60
    except Exception:
        return None


for col in ["Entrada esperada", "Entrada real", "Salida esperada", "Salida real"]:
    r[col + " (min)"] = r[col].apply(to_min)


def calc_delta(row):
    if row["Estado_base"].startswith("FALTA") or row["Estado_base"].startswith("ALERTA"):
        return None
    ee, er = row["Entrada esperada (min)"], row["Entrada real (min)"]
    se, sr = row["Salida esperada (min)"], row["Salida real (min)"]
    if None in (ee, er, se, sr):
        return None
    return (ee - er) + (sr - se)


r["delta_min"] = r.apply(calc_delta, axis=1)

resultados = []
for dni, grp in r.sort_values("Fecha").groupby("DNI"):
    grp = grp.reset_index(drop=True)
    for i, row in grp.iterrows():
        if pd.isna(row["delta_min"]) or row["delta_min"] >= 0:
            continue
        fecha = row["Fecha"]
        ventana = grp[(grp["Fecha"] >= fecha) & (grp["Fecha"] <= fecha + pd.Timedelta(days=3))]
        suma_ventana = ventana["delta_min"].dropna().sum()
        compensado = suma_ventana >= 0
        nivel_confianza, n_visitas = confianza(dni, fecha)
        resultados.append({
            "DNI": dni, "Nombre": row["Nombre"], "Fecha": fecha.date(),
            "Déficit del día (min)": round(row["delta_min"], 1),
            "Suma ventana 3 días (min)": round(suma_ventana, 1),
            "Compensado": "SÍ" if compensado else "NO",
            "Confianza": nivel_confianza,
            "N° visitas ese día": n_visitas,
        })

comp = pd.DataFrame(resultados)
print(f"Días con déficit evaluados: {len(comp)}")
if len(comp):
    print(f"Compensados dentro de la ventana de 3 días: {(comp['Compensado']=='SÍ').sum()}")
    print(f"NO compensados -> alerta para escalar al supervisor: {(comp['Compensado']=='NO').sum()}")
    no_comp_all = comp[comp["Compensado"] == "NO"]
    print()
    print("Confianza de los casos NO compensados (Baja = pocas visitas ese día, 'salida real' poco confiable):")
    print(no_comp_all["Confianza"].value_counts().to_string())
    print()
    print("Casos NO compensados de confianza ALTA (top 15 por magnitud del déficit) — los más accionables:")
    no_comp_alta = no_comp_all[no_comp_all["Confianza"] == "Alta"].sort_values("Déficit del día (min)").head(15)
    print(no_comp_alta.to_string(index=False))

comp.to_excel("12_Compensacion.xlsx", index=False)
print()
print("Guardado: 12_Compensacion.xlsx")

print()
print("=" * 70)
print("INDICADOR DE ASISTENCIA POR PERSONA")
print("=" * 70)

if len(comp):
    no_compensados_por_persona = comp[comp["Compensado"] == "NO"].groupby("DNI").size().to_dict()
else:
    no_compensados_por_persona = {}

indicador_rows = []
for dni, grp in r.groupby("DNI"):
    total_dias = len(grp)
    faltas = (grp["Estado_base"].str.startswith("FALTA")).sum()
    tardanzas_no_comp = no_compensados_por_persona.get(dni, 0)
    dias_negativos = faltas + tardanzas_no_comp
    dias_ok = total_dias - dias_negativos
    indicador = round(dias_ok / total_dias * 100, 1) if total_dias else None
    indicador_rows.append({
        "DNI": dni, "Nombre": grp["Nombre"].iloc[0], "Días evaluados": total_dias,
        "Faltas": int(faltas), "Tardanzas no compensadas": tardanzas_no_comp,
        "Indicador de asistencia (%)": indicador,
    })

ind = pd.DataFrame(indicador_rows).sort_values("Indicador de asistencia (%)")
print("Meta de referencia (según mockup inicial): 95%")
print(f"Personas bajo la meta: {(ind['Indicador de asistencia (%)'] < 95).sum()} de {len(ind)}")
print()
print("Los 10 indicadores más bajos:")
print(ind.head(10).to_string(index=False))

ind.to_excel("13_Indicador_Asistencia.xlsx", index=False)
print()
print("Guardado: 13_Indicador_Asistencia.xlsx")
