# -*- coding: utf-8 -*-
"""DNI real que empieza en 0 (ej. "09917175") -- pd.to_numeric(...).astype
("int64").astype(str) (necesario para el gotcha de "18074336.0" cuando una
fila viene vacía, ver comentario en parseo_headcount.py) se come ese cero
de encabezado como efecto secundario, dejando "9917175" -- que después no
matchea contra el DNI real guardado en personas (FK), y esa persona queda
"huérfana" en cada corrida de motor_clasificacion.py (hallazgo real, Davor
2026-09-21: Felix Leon Karina Elizabeth, DNI 09917175, nunca aparecía en
ningún reporte)."""
import io

import openpyxl

from parseo_headcount import parsear_maestro, parsear_patron


def _wb_maestro(dni_valor):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Maestro Headcount"
    ws.append(["fila vacía de encabezado"])
    ws.append([])
    ws.append([])
    ws.append(["DNI", "Nombre completo", "Estado"])
    ws.append([dni_valor, "Persona de prueba", "Activo"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _wb_patron(dni_valor):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Patrón recurrente"
    ws.append(["fila vacía de encabezado"])
    ws.append([])
    ws.append([])
    ws.append(["DNI", "Día de la semana"])
    ws.append([dni_valor, "Lunes"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_parsear_maestro_no_pierde_el_cero_a_la_izquierda():
    # openpyxl guarda un int de Python como celda numérica -- exactamente
    # el mismo caso que un DNI tipeado sin comillas en Excel.
    m = parsear_maestro(_wb_maestro(9917175))
    assert m["DNI"].tolist() == ["09917175"]


def test_parsear_maestro_no_afecta_un_dni_de_8_digitos_normal():
    m = parsear_maestro(_wb_maestro(45694774))
    assert m["DNI"].tolist() == ["45694774"]


def test_parsear_patron_no_pierde_el_cero_a_la_izquierda():
    p = parsear_patron(_wb_patron(9917175))
    assert p["DNI"].tolist() == ["09917175"]
