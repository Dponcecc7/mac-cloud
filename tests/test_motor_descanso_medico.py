# -*- coding: utf-8 -*-
""""Descanso médico" está catalogado bajo categoría "Salud" (catalogo_motivos,
id 1), NO "Descanso" -- asistencia.py arma el desplegable de motivos de
Falta con todo lo que NO sea categoría "Descanso", así que "Descanso
médico" aparece en el desplegable de FALTA, no en el de Descanso. El flujo
normal de carga (botón "Falta" + motivo "Descanso médico") manda
"Falta - Descanso médico" a Tabla 3 -- hallazgo real (Davor, 2026-10-06,
"validemos que no estemos haciendo mal el registro"): sin este fix, ese
comentario caía en el catch-all de FALTA genérica, y un mercaderista con
licencia médica real quedaba contando como ausencia injustificada en el
Indicador de Asistencia y en "Horas a trabajar" -- exactamente lo que el
estado DESCANSO existe para evitar. Mismo patrón que ya existía para
"Falta - Amanecida" (ver test_motor_amanecida_descanso.py)."""
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


def _clasificar(comentario):
    fecha = pd.Timestamp("2026-09-14")
    registro_sup = {"Comentario": comentario}
    return clasificar_dia(
        "12345678", "Prueba", fecha, fecha.weekday(), _pat(),
        COL_ENT, COL_SAL, COL_CANAL, VISITAS_VACIA, registro_sup, {},
    )


def test_falta_descanso_medico_se_reclasifica_como_descanso():
    # El caso real: flujo normal de carga (Falta + motivo "Descanso médico"
    # del catálogo, categoría Salud) -- con tilde, como realmente llega.
    assert _clasificar("Falta - Descanso médico")["Estado"] == "DESCANSO (comentario supervisor)"


def test_falta_descanso_medico_sin_tilde_tambien_se_reclasifica():
    # "médico" sin tilde (typo real/posible) -- sin_acentos() de ambos
    # lados tiene que matchear igual.
    assert _clasificar("Falta - Descanso medico")["Estado"] == "DESCANSO (comentario supervisor)"


def test_descanso_medico_sin_prefijo_sigue_igual():
    # Ya funcionaba antes de este fix (comienza con "DESCANSO" directo) --
    # no debe cambiar de comportamiento.
    assert _clasificar("Descanso médico")["Estado"] == "DESCANSO (comentario supervisor)"


def test_problema_de_salud_sin_sustento_no_se_toca():
    # Distinto de "Descanso médico" a propósito (ver alertas.py::
    # MOTIVOS_CON_SUSTENTO) -- sin sustento documentado todavía, sigue
    # contando como Falta real hasta que se confirme.
    assert _clasificar("Falta - Problema de salud (sin sustento aún)")["Estado"] == "FALTA (comentario supervisor)"


def test_otras_faltas_no_se_tocan():
    assert _clasificar("Falta - Motivo personal")["Estado"] == "FALTA (comentario supervisor)"
