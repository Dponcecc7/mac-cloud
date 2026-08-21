# -*- coding: utf-8 -*-
"""
Fase 6: comparar_clasificacion.py NO es un port directo del original de
MAC/ -- ese comparaba 7_Clasificacion_Diaria.xlsx (Excel de produccion)
contra Postgres (motor sombra). En la nube esa comparacion ya no tendria
sentido: motor_clasificacion.py (nube) escribe a la MISMA tabla Postgres que
motor_clasificacion_diaria.py (local) -- durante la validacion en paralelo,
Postgres refleja lo ultimo que haya escrito CUALQUIERA de los dos motores
(protegidos por el candado, ver pipeline/db_lock.py), no especificamente al
motor de la nube. Comparar "Postgres vs Excel" en ese escenario compararia
contra si mismo la mitad del tiempo.

La comparacion que SI tiene sentido durante la validacion: el snapshot de
auditoria que sube motor_clasificacion.py (nube) vs el Excel real de
produccion que sigue escribiendo el motor local -- ambos por Graph (SharePoint
es accesible desde cualquier lado, no hace falta pasar por Postgres para
esto). Mismas 4 columnas comparadas que el original.

Uso: python comparar_clasificacion.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graph_client import leer_excel  # noqa: E402

RUTA_GRAPH_MAC = "ASISTENCIA/MAC/"
ARCHIVO_PRODUCCION = "7_Clasificacion_Diaria.xlsx"
ARCHIVO_AUDITORIA_NUBE = "_nube_7_Clasificacion_Diaria.xlsx"

COLUMNAS_A_COMPARAR = ["Estado", "Entrada real", "Salida real", "Salida anticipada (min)"]


def _cargar(nombre_archivo):
    df = leer_excel(RUTA_GRAPH_MAC + nombre_archivo)
    df["DNI"] = df["DNI"].astype(str)
    df["Fecha"] = pd.to_datetime(df["Fecha"]).dt.date
    return df.set_index(["DNI", "Fecha"])


def main():
    prod = _cargar(ARCHIVO_PRODUCCION)
    nube = _cargar(ARCHIVO_AUDITORIA_NUBE)

    claves_prod = set(prod.index)
    claves_nube = set(nube.index)
    comunes = claves_prod & claves_nube
    solo_prod = claves_prod - claves_nube
    solo_nube = claves_nube - claves_prod

    print("=" * 90)
    print("COMPARACIÓN (Fase 6): 7_Clasificacion_Diaria.xlsx (motor local) vs _nube_7_Clasificacion_Diaria.xlsx (motor nube)")
    print("=" * 90)
    print(f"Filas en motor local: {len(claves_prod)}")
    print(f"Filas en motor nube: {len(claves_nube)}")
    print(f"Filas en ambos (comparables): {len(comunes)}")
    print(f"Solo en motor local: {len(solo_prod)}")
    print(f"Solo en motor nube: {len(solo_nube)}")
    print()

    discrepancias_por_campo = {c: 0 for c in COLUMNAS_A_COMPARAR}
    filas_con_discrepancia = 0
    detalle = []

    for clave in sorted(comunes):
        fila_prod = prod.loc[clave]
        fila_nube = nube.loc[clave]
        tiene_discrepancia = False
        detalle_fila = {"DNI": clave[0], "Fecha": clave[1]}
        for col in COLUMNAS_A_COMPARAR:
            v_prod = fila_prod[col]
            v_nube = fila_nube[col]
            v_prod_norm = None if pd.isna(v_prod) else v_prod
            v_nube_norm = None if pd.isna(v_nube) else v_nube
            if v_prod_norm != v_nube_norm:
                discrepancias_por_campo[col] += 1
                detalle_fila[col] = (v_prod_norm, v_nube_norm)
                tiene_discrepancia = True
        if tiene_discrepancia:
            filas_con_discrepancia += 1
            detalle.append(detalle_fila)

    pct_identicas = 100 * (len(comunes) - filas_con_discrepancia) / len(comunes) if comunes else 0
    print(f"Filas idénticas (en las {len(COLUMNAS_A_COMPARAR)} columnas comparadas): {len(comunes) - filas_con_discrepancia} / {len(comunes)} ({pct_identicas:.1f}%)")
    print(f"Filas con al menos 1 discrepancia: {filas_con_discrepancia}")
    print()
    print("Discrepancias por columna:")
    for col, n in discrepancias_por_campo.items():
        print(f"  {col}: {n}")

    if detalle:
        print()
        print(f"Detalle de las primeras {min(30, len(detalle))} discrepancias:")
        for d in detalle[:30]:
            partes = [f"{k}: local={v[0]!r} vs nube={v[1]!r}" for k, v in d.items() if k not in ("DNI", "Fecha")]
            print(f"  DNI {d['DNI']} {d['Fecha']}: " + " | ".join(partes))

    if solo_prod:
        print()
        print(f"Ejemplos solo en motor local (primeras 10 de {len(solo_prod)}): {sorted(solo_prod)[:10]}")
    if solo_nube:
        print()
        print(f"Ejemplos solo en motor nube (primeras 10 de {len(solo_nube)}): {sorted(solo_nube)[:10]}")


if __name__ == "__main__":
    main()
