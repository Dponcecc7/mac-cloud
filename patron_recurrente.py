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


CANAL_DIA_CANONICO = {
    "tradicional": "Tradicional",
    "autoservicio": "Autoservicio", "autoservicios": "Autoservicio", "autorsevicios": "Autoservicio",
    "farmacia": "Farmacia",
}


def canonizar_canal_dia(valor):
    """Normaliza "Canal del día" (PatronRecurrente.canal_dia) a la forma
    EXACTA que usa motor_clasificacion.py::TIPO_NEGOCIO_A_CANAL para el
    canal real de una visita ("Tradicional"/"Autoservicio"/"Farmacia",
    Title case) -- la comparación en el motor (canal_esp_norm not in
    canales_marcados) es de texto EXACTO, sensible a mayúsculas, sin
    tolerancia a errores de tipeo. Hallazgo real, 2026-09-14: "FARMACIA"/
    "AUTOSERVICIO" (mayúsculas) y el typo "Autorsevicios" NUNCA coincidían
    con una visita real -- 97% de las 4064 marcas "trabajó en canal
    distinto al asignado" en toda la base eran este bug de formato, no
    cambios de canal reales (534 de 978 filas de gente activa mal
    formateadas). OJO: esto es un vocabulario DISTINTO al de
    scoping.canonizar_canal() (Persona.canal, en MAYÚSCULAS) -- Title
    case acá a propósito, para calzar con lo que espera el motor."""
    if not valor:
        return valor
    return CANAL_DIA_CANONICO.get(valor.strip().lower(), valor.strip())


def dnis_con_domingo(session):
    """{dni, ...} de quienes tienen fila de domingo en su Patrón Recurrente
    -- usado para decidir, persona por persona, si "el día hábil anterior"
    de un lunes es domingo (Autoservicio) o sábado (todos los demás, ver
    asistencia.py::_dia_habil_anterior()). Reusado también por
    proyecciones.py::ranking_proxima_falta()."""
    return {
        dni for dni, dia in session.query(PatronRecurrente.dni, PatronRecurrente.dia_semana).all()
        if sin_acentos(dia) == "domingo"
    }
