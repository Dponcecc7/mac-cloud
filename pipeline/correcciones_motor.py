# -*- coding: utf-8 -*-
"""
Reemplazo de la lectura de 3_Registro_Diario_Supervisor.xlsx (Tabla 3) para
motor_clasificacion.py y reporte_diario_9am.py -- Davor, 2026-10-27: "no
deben haber excel", CorreccionWeb (Postgres) ya recibe exactamente la misma
informacion que Tabla 3 desde cada call-site de asistencia.py (ver
dimension_models.py::CorreccionWeb), asi que deja de hacer falta la
SharePoint Excel como fuente para el motor.

cargar_correcciones_web() devuelve la MISMA forma que el codigo viejo
armaba leyendo el Excel: un dict {(DNI, fecha): pandas.Series} con las
claves "Comentario"/"Hora entrada corregida"/"Hora salida corregida"/
"Estado reportado", fusionado con el MISMO algoritmo por columna (ultimo
valor no-nulo gana, agrupado por DNI+fecha) -- asi clasificar_dia() (la
funcion que de verdad clasifica, NO SE TOCA) no necesita saber de donde
vienen los datos.

Mapeo CorreccionWeb -> columnas de Tabla 3: cada fila de CorreccionWeb ya
es "pura" por diseno (asistencia.py nunca llena comentario_entrada Y
comentario_salida en la misma fila, ver cada call-site) -- "Comentario" sale
de comentario_entrada si existe, si no de comentario_salida (mismo criterio
que _agregar_fila_tabla3(): un solo argumento `comentario` por llamada,
nunca los dos tipos a la vez).
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import CorreccionWeb, get_session  # noqa: E402


def cargar_correcciones_web():
    session = get_session()
    try:
        filas = session.query(CorreccionWeb).order_by(CorreccionWeb.fecha_registro).all()
    finally:
        session.close()

    if not filas:
        return {}

    registros = []
    for f in filas:
        # .zfill(8) -- mismo resguardo que ya aplica motor_clasificacion.py
        # a los DNIs del Maestro Headcount/Patrón recurrente (ver
        # comentarios ahí); acá el DNI nunca pasó por un redondeo de pandas
        # a numérico (viene de un <input>/<select> del form web), pero se
        # normaliza igual para que la clave matchee sin dudas contra el
        # resto del motor.
        registros.append({
            "DNI": str(f.dni).strip().zfill(8),
            "Fecha_dt": f.fecha,
            "Comentario": f.comentario_entrada if f.comentario_entrada is not None else f.comentario_salida,
            "Hora entrada corregida": f.hora_entrada_corregida,
            "Hora salida corregida": f.hora_salida_corregida,
            "Estado reportado": f.estado_reportado,
        })
    df = pd.DataFrame(registros)

    # Mismo algoritmo exacto que la version Excel de motor_clasificacion.py
    # (sup_idx) -- no simplificar/"mejorar": el orden de fusión (último
    # valor no-nulo por columna, agrupado por DNI+fecha) es lo que hace
    # funcionar MARCADOR_BORRADO y el caso "hora corregida de un envío
    # anterior que sobrevive a una corrección posterior sin hora" (ver
    # motor_clasificacion.py líneas ~260-270).
    sup_idx = {}
    for (dni_sup, fecha_sup), grupo in df.groupby(["DNI", "Fecha_dt"]):
        if len(grupo) == 1:
            sup_idx[(dni_sup, fecha_sup)] = grupo.iloc[0]
            continue
        fusion = grupo.iloc[-1].copy()
        for col in grupo.columns:
            no_nulos = grupo[col].dropna()
            if len(no_nulos):
                fusion[col] = no_nulos.iloc[-1]
        sup_idx[(dni_sup, fecha_sup)] = fusion
    return sup_idx
