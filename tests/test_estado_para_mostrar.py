# -*- coding: utf-8 -*-
"""Bug real reportado por Yeny (2026-09-17): en "Faltas / Vacaciones /
Vacantes de hoy" alguien a quien ya se le marcó "✓ Asistió" seguía
mostrando el badge "Falta" -- el badge se armaba del estado crudo del
motor (ClasificacionDiaria.estado), que recién se actualiza en la
siguiente corrida del pipeline, ignorando que ya había una corrección
"Asistió" cargada hoy mismo (comentario_entrada). asistencia.py::
_estado_para_mostrar() prioriza la corrección ya cargada sobre el estado
viejo del motor."""
from asistencia import _estado_para_mostrar


def test_correccion_asistio_gana_sobre_falta_del_motor():
    assert _estado_para_mostrar("FALTA (sin marcación)", "Asistió") == "ASISTIÓ"


def test_correccion_descanso_gana_sobre_falta_del_motor():
    assert _estado_para_mostrar("FALTA (sin marcación)", "Descanso - Amanecida") == "DESCANSO"


def test_correccion_vacante_gana_sobre_falta_del_motor():
    assert _estado_para_mostrar("FALTA (sin marcación)", "Vacante") == "VACANTE"


def test_correccion_falta_con_motivo_sigue_siendo_falta():
    assert _estado_para_mostrar("FALTA (sin marcación)", "Falta - Motivo personal") == "FALTA"


def test_sin_correccion_usa_el_estado_del_motor():
    assert _estado_para_mostrar("VACANTE (comentario supervisor)", None) == "VACANTE"
    assert _estado_para_mostrar("FALTA (sin marcación)", "") == "FALTA"
