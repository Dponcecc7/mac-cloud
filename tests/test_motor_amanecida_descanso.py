# -*- coding: utf-8 -*-
""""Amanecida" ya está catalogada como motivo de categoría "Descanso"
(catalogo_motivos, id 16), pero puede llegar a Tabla 3 tipeada como
"Falta - Amanecida" desde la app móvil (texto libre, no pasa por el
desplegable de mac_cloud que ya separa Falta/Descanso por categoría) --
hallazgo real en "Faltas por motivo" del Dashboard (Davor, 2026-09-14:
"amanecida también es como un descanso"). motor_clasificacion.py ahora
reclasifica cualquier comentario "Falta - Amanecida" como DESCANSO, igual
que ya hace con Vacante/Vacaciones dentro de la misma rama."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
from motor_clasificacion import clasificar_dia  # noqa: E402

COL_ENT, COL_SAL, COL_CANAL = "Hora entrada programada", "Hora salida programada", "Canal del día"

VISITAS_VACIA = pd.DataFrame(columns=[
    "nro_documento", "fecha_inicio_dt", "geofence_ok", "hora_inicio_td", "hora_fin_td", "canal_visita",
])


def _pat():
    return {COL_ENT: "08:00:00", COL_SAL: "17:00:00", COL_CANAL: "Tradicional"}


def test_falta_amanecida_se_reclasifica_como_descanso():
    fecha = pd.Timestamp("2026-09-14")
    registro_sup = {"Comentario": "Falta - Amanecida"}
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )
    assert fila["Estado"] == "DESCANSO (comentario supervisor)"


def test_descanso_amanecida_sigue_igual():
    # El comentario que ya se usaba bien ("Descanso - Amanecida") no debe
    # cambiar de comportamiento con este fix.
    fecha = pd.Timestamp("2026-09-14")
    registro_sup = {"Comentario": "Descanso - Amanecida"}
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )
    assert fila["Estado"] == "DESCANSO (comentario supervisor)"


def test_amanecida_sin_prefijo_se_reclasifica_como_descanso():
    # El caso REAL encontrado en producción (DNI 44914722, 2026-09-02):
    # comentario_supervisor literal "Amanecida", sin ningún prefijo Falta/
    # Descanso -- antes del fix quedaba en "FALTA (sin marcación)" (el
    # default inicial, nunca lo tocaba ninguna rama del if/elif).
    fecha = pd.Timestamp("2026-09-14")
    registro_sup = {"Comentario": "Amanecida"}
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )
    assert fila["Estado"] == "DESCANSO (comentario supervisor)"


def test_otras_faltas_no_se_tocan():
    # Cualquier otro motivo de Falta sigue contando como Falta real.
    fecha = pd.Timestamp("2026-09-14")
    registro_sup = {"Comentario": "Falta - Motivo personal"}
    fila = clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )
    assert fila["Estado"] == "FALTA (comentario supervisor)"
