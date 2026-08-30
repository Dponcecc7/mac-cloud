# -*- coding: utf-8 -*-
"""Mitiga inyección de fórmulas en Excel (CSV/Excel Injection, OWASP) --
texto libre que empieza con =, +, -, @ se interpreta como fórmula al
abrirse en Excel (riesgo real: comentarios/nombres los escriben usuarios
vía la web o un Excel de Headcount subido, y esos archivos después los
abre gente en Excel). Anteponer un apóstrofe fuerza texto literal --
Excel lo oculta al mostrar la celda, no cambia el contenido visible.

Sin dependencias a propósito, para poder importarse desde cualquier lado
(mac_cloud/, mac_cloud/pipeline/, scripts sueltos)."""
_CARACTERES_FORMULA = ("=", "+", "-", "@")


def texto_seguro_excel(valor):
    if valor is None:
        return valor
    texto = str(valor)
    if texto.startswith(_CARACTERES_FORMULA):
        return "'" + texto
    return valor


def fila_segura(fila):
    """texto_seguro_excel() aplicado a cada valor de una fila completa --
    para los lugares que vuelcan un DataFrame/lista entera a Excel columna
    por columna en un bucle genérico (ej. reporte_diario_9am.py,
    motor_clasificacion.py), donde no conviene sanear un campo puntual a
    mano y arriesgarse a olvidar el próximo que se agregue."""
    return [texto_seguro_excel(v) for v in fila]
