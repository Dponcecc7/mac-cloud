# -*- coding: utf-8 -*-
"""
Fase 6: candado distribuido en Postgres (tabla `pipeline_lock`, ver
dimension_models.PipelineLock) -- reemplaza al candado de archivo local
(logs/*.lock) para cuando el mismo pipeline puede estar corriendo a la vez
en la laptop Y en un runner de GitHub Actions durante la validacion en
paralelo (ver plan Fase 6). La edad del candado se calcula EN Postgres
(`now() - adquirido_en`) para no depender de que el reloj/zona horaria del
cliente (laptop vs runner) coincida con el del servidor.

Uso:
    ok, motivo = adquirir_lock("pipeline_completo", "github-actions", max_minutos=25)
    if not ok:
        print(f"No se ejecuta: {motivo}")
        sys.exit(0)
    try:
        ...
    finally:
        liberar_lock("pipeline_completo")
"""
import os
import sys

# Ruta relativa a este archivo (no absoluta de Windows) -- este modulo corre
# tanto en la laptop como en un runner de GitHub Actions (checkout en una
# ruta Linux totalmente distinta), asi que no puede depender de un path
# fijo como el resto de MAC/ (ver plan Fase 6).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import get_session  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402


def adquirir_lock(nombre, quien, max_minutos=15):
    session = get_session()
    try:
        fila = session.execute(
            text("SELECT quien, EXTRACT(EPOCH FROM (now() - adquirido_en)) / 60 AS edad_min "
                 "FROM pipeline_lock WHERE nombre = :nombre"),
            {"nombre": nombre},
        ).first()
        if fila is not None and fila.edad_min < max_minutos:
            return False, f"Ya hay otra corrida activa (lock de {fila.edad_min:.1f} min, iniciada por: {fila.quien})"
        if fila is not None:
            session.execute(text("DELETE FROM pipeline_lock WHERE nombre = :nombre"), {"nombre": nombre})
            session.commit()
        try:
            session.execute(
                text("INSERT INTO pipeline_lock (nombre, quien, adquirido_en) VALUES (:nombre, :quien, now())"),
                {"nombre": nombre, "quien": quien},
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            return False, "Otra corrida adquirió el candado justo antes (colisión de carrera) -- no se ejecuta."
        return True, None
    finally:
        session.close()


def liberar_lock(nombre):
    session = get_session()
    try:
        session.execute(text("DELETE FROM pipeline_lock WHERE nombre = :nombre"), {"nombre": nombre})
        session.commit()
    finally:
        session.close()
