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
    # Ojo: si ALGUNA fila de la columna DNI viene vacía (fila suelta, nota,
    # plantilla sin borrar), pandas sube toda la columna a float64 -- y eso
    # sobrevive al filtro de abajo (filtrar filas no cambia el dtype), así
    # que ".astype(str)" sobre la columna ORIGINAL deja "18074336.0" en vez
    # de "18074336" para TODOS los DNIs válidos. dni_num solo sirve para
    # FILTRAR filas no numéricas -- el valor final se arma desde el string
    # ORIGINAL (regex le saca el ".0" de sobra), nunca redondeando a int64:
    # eso se comía cualquier cero a la izquierda de un DNI real de 8 dígitos
    # (ej. "09917175" -> "9917175", Davor 2026-09-21, Felix Leon Karina
    # Elizabeth) y, peor aún, DOS ceros de un carnet de extranjería de 9
    # dígitos (ej. "007845810" -> "7845810"), que el .zfill(8) de respaldo
    # solo alcanzaba a reponer a medias (Davor, 2026-11-20, Delgado X Jenny
    # Carolina).
    dni_num = pd.to_numeric(m["DNI"], errors="coerce")
    m = m[dni_num.notna()].copy()
    m["DNI"] = m["DNI"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(8)
    return m


def parsear_patron(fuente):
    p = pd.read_excel(fuente, sheet_name="Patrón recurrente", header=3).dropna(how="all")
    p.columns = [str(c).strip() for c in p.columns]
    p = p.rename(columns={p.columns[0]: "DNI"})
    # Mismo gotcha que parsear_maestro() -- ver comentario ahí: el valor
    # final se arma desde el string original, nunca redondeando a int64, para
    # no perder ceros a la izquierda de un DNI/carnet de extranjería real.
    dni_num = pd.to_numeric(p["DNI"], errors="coerce")
    p = p[dni_num.notna()].copy()
    p["DNI"] = p["DNI"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(8)
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
