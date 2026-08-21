# -*- coding: utf-8 -*-
"""
Fase 6: pseudonimizacion para logs de workflows publicos (repo publico --
ver plan Fase 6, GitHub Actions expone los logs a cualquiera). Un hash corto
y estable por DNI: mismo DNI siempre da el mismo pseudonimo, asi que dos
lineas de log (o corridas distintas) sobre la misma persona se pueden
correlacionar sin mostrar el DNI real ni el nombre. Para saber a quien
corresponde un pseudonimo puntual, hashear los DNI reales (Maestro/Postgres)
con esta misma funcion y buscar el que matchea -- deliberado: no hay tabla
de "reversa" en ningun lado publico.
"""
import hashlib


def pseudonimo(dni):
    return hashlib.sha256(str(dni).encode("utf-8")).hexdigest()[:8]
