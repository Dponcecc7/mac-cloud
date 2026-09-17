# -*- coding: utf-8 -*-
"""Hallazgo real (Davor, 2026-09-17): "Faltas del mes" en Perfil
mercaderista mostraba el motivo como el string literal "Nan" en vez de
"Sin motivo". Causa: un comentario NULL que llega de un DataFrame de
pandas es float('nan'), que es "truthy" para "if not texto" (no lo
atajaba) -- asistencia.py::_homologar_motivo() se colaba hasta el final
y devolvía "Nan" (str(nan).title()). Clásico chequeo sin pandas:
"texto != texto" solo es True para NaN."""
import math

from asistencia import _homologar_motivo


def test_nan_devuelve_none_no_el_string_nan():
    assert _homologar_motivo(math.nan) is None


def test_reportes_faltas_del_mes_usa_sin_motivo_con_nan():
    # Mismo patrón que reportes.py::ficha() -- "_homologar_motivo(x) or
    # 'Sin motivo'" -- con el bug, math.nan pasaba de largo y el `or`
    # nunca se activaba (un string "Nan" es truthy).
    assert (_homologar_motivo(math.nan) or "Sin motivo") == "Sin motivo"


def test_none_y_vacio_siguen_devolviendo_falsy():
    assert not _homologar_motivo(None)
    assert not _homologar_motivo("")


def test_texto_real_sigue_funcionando():
    assert _homologar_motivo("Falta - Motivo personal") == "Motivo personal"
