# -*- coding: utf-8 -*-
"""Davor, 2026-09-17: "no deberían aparecer en ese panel inferior ya que
parece que si se les puso motivo, solo debe estar en la parte superior
'Pendientes de marcar'" -- alguien todavía sin ningún comentario/motivo
vivía en LOS DOS paneles a la vez (Pendientes de marcar arriba, Faltas/
Vacaciones/Vacantes de hoy abajo), lo que hacía pensar que ya tenía un
motivo cargado cuando en realidad seguía pendiente.
asistencia.py::_faltas_vacaciones_vacantes() ahora excluye a quien
_es_pendiente() ya selecciona para el otro panel."""
from asistencia import _faltas_vacaciones_vacantes, _pendientes_de_marcar


def _fila(estado, comentario_entrada="", marcado_web=False, mercaderista="X"):
    return {"estado": estado, "comentario_entrada": comentario_entrada, "marcado_web": marcado_web, "mercaderista": mercaderista}


def test_falta_sin_comentario_solo_en_pendientes():
    filas = [_fila("FALTA (sin marcación)")]
    assert len(_pendientes_de_marcar(filas)) == 1
    assert len(_faltas_vacaciones_vacantes(filas)) == 0


def test_falta_con_comentario_solo_en_faltas_vacaciones_vacantes():
    filas = [_fila("FALTA (sin marcación)", comentario_entrada="Falta - Injustificada")]
    assert len(_pendientes_de_marcar(filas)) == 0
    resultado = _faltas_vacaciones_vacantes(filas)
    assert len(resultado) == 1
    assert resultado[0]["estado_efectivo"] == "FALTA"


def test_vacante_sin_comentario_ni_marcado_web_sigue_pendiente():
    # Documenta el criterio real de _es_pendiente(): un VACANTE sin
    # comentario_entrada Y sin marcado_web sigue siendo "pendiente" (vive
    # solo arriba), aunque el estado ya no sea el default "sin marcación".
    filas = [_fila("VACANTE (comentario supervisor)", marcado_web=False, comentario_entrada="")]
    assert len(_pendientes_de_marcar(filas)) == 1
    assert len(_faltas_vacaciones_vacantes(filas)) == 0


def test_vacante_marcado_via_app_ya_no_es_pendiente():
    filas = [_fila("VACANTE (comentario supervisor)", marcado_web=True, comentario_entrada="")]
    assert len(_pendientes_de_marcar(filas)) == 0
    resultado = _faltas_vacaciones_vacantes(filas)
    assert len(resultado) == 1
    assert resultado[0]["estado_efectivo"] == "VACANTE"


def test_vacaciones_siempre_en_faltas_vacaciones_vacantes():
    # VACACIONES nunca es "pendiente" en el otro panel (ese solo mira
    # FALTA/VACANTE) -- siempre debe aparecer acá.
    filas = [_fila("VACACIONES (comentario supervisor)", comentario_entrada="Falta - Vacaciones")]
    assert len(_pendientes_de_marcar(filas)) == 0
    assert len(_faltas_vacaciones_vacantes(filas)) == 1
