# -*- coding: utf-8 -*-
"""Lectura/normalización de PatronRecurrente compartida por
horas_semanales.py, alertas.py y cobertura.py -- antes cada uno tenía su
propia copia idéntica de WD_NORM/_sin_acentos() y del query +
dict-comprehension para leer el patrón (hallazgo de revisión de código,
2026-08-24: "parseo de Patrón Recurrente duplicado en 3 archivos").

WD_NORM SÍ incluye domingo (6) desde 2026-09-14 (Davor: el equipo de
Autoservicio trabaja domingo, y debe contar "en todos los análisis, si el
analista lo sube en su patrón recurrente") -- guiado 100% por los datos:
alguien sin fila de domingo en su propio Patrón Recurrente simplemente no
tiene nada que este mapeo pueda resolver para ese día, así que agregar la
clave acá no cambia nada para quien no lo tenga cargado."""
from dimension_models import PatronRecurrente

WD_NORM = {0: "lunes", 1: "martes", 2: "miercoles", 3: "jueves", 4: "viernes", 5: "sabado", 6: "domingo"}


def sin_acentos(s):
    """Normaliza "Miércoles"/"Sábado" -> "miercoles"/"sabado" -- el patrón
    recurrente guarda el día tal cual venía del Excel, y no vale la pena
    arriesgarse a un mismatch de encoding/acento entre el dato guardado y
    weekday()."""
    if not s:
        return ""
    return (
        s.strip().lower()
        .replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    )


def cargar_patron_recurrente(session, atributo):
    """{(dni, dia_normalizado): valor} para `atributo` de PatronRecurrente
    (ej. "refrigerio", "hora_salida_prog") -- una sola query, reusada donde
    antes horas_semanales.py/alertas.py/cobertura.py repetían la misma."""
    return {
        (p.dni, sin_acentos(p.dia_semana)): getattr(p, atributo)
        for p in session.query(PatronRecurrente).all()
    }
