# -*- coding: utf-8 -*-
"""
Parseo compartido de Maestro Headcount / Patrón Recurrente -- mismo código
que usaba migrar_dimensiones_a_postgres.py (Fase 2), factorizado acá para
que también lo use la carga web de otros analistas (mac_cloud/cargas.py,
Fase 4) sin duplicar la lógica de columnas/limpieza.

`fuente` en ambas funciones puede ser una ruta de archivo (string) o un
file-like (BytesIO de un archivo subido por HTTP) -- pandas.read_excel()
acepta las dos formas indistintamente.
"""
import pandas as pd


def parsear_maestro(fuente):
    m = pd.read_excel(fuente, sheet_name="Maestro Headcount", header=3).dropna(how="all")
    m.columns = [str(c).strip() for c in m.columns]
    m = m.rename(columns={m.columns[0]: "DNI"})
    m = m[pd.to_numeric(m["DNI"], errors="coerce").notna()].copy()
    m["DNI"] = m["DNI"].astype(str).str.strip()
    return m


def parsear_patron(fuente):
    p = pd.read_excel(fuente, sheet_name="Patrón recurrente", header=3).dropna(how="all")
    p.columns = [str(c).strip() for c in p.columns]
    p = p.rename(columns={p.columns[0]: "DNI"})
    p = p[pd.to_numeric(p["DNI"], errors="coerce").notna()].copy()
    p["DNI"] = p["DNI"].astype(str).str.strip()
    return p


COLUMNAS_MAESTRO_ESPERADAS = [
    "DNI", "Nombre completo", "Fecha de ingreso", "Fecha de baja", "Estado",
    "Reemplaza_a (DNI)", "Es reingreso (Sí/No)", "Rol", "Canal", "Región",
    "Ciudad / Mercado", "Zona / Ruta asignada", "Supervisor asignado",
    "Correo corporativo", "Motivo de baja", "Registrado por", "Fecha de registro",
]
COLUMNAS_PATRON_ESPERADAS = [
    "DNI", "Día de la semana", "Hora entrada programada", "Hora salida programada",
    "Canal del día", "Refrigerio",
]


def validar_columnas(df, esperadas, nombre_archivo):
    faltantes = [c for c in esperadas if c not in df.columns]
    if faltantes:
        raise ValueError(
            f"'{nombre_archivo}' no tiene el formato esperado -- faltan las columnas: {', '.join(faltantes)}. "
            f"Usa la misma plantilla que ya usa el sistema (mismos encabezados, en la fila 4)."
        )
