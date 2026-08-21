# -*- coding: utf-8 -*-
"""
Fase 6: version portable de MAC/ciclo_reporte_9am.py -- mismo orden (aplicar
correcciones pendientes ANTES de regenerar el reporte, para no perderlas si
el analista edito justo antes de esta corrida), candado distribuido propio
(nombre distinto al del pipeline principal, corren en paralelo sin
molestarse).

Uso: python ciclo_reporte_9am.py
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_lock import adquirir_lock, liberar_lock  # noqa: E402

NOMBRE_LOCK = "reporte_9am"
LOCK_MAX_MINUTOS = 15

AQUI = os.path.dirname(os.path.abspath(__file__))


def _correr_paso(titulo, script, args=None):
    print(f"\n{'='*70}\n{titulo}\n{'='*70}")
    t0 = time.time()
    resultado = subprocess.run(
        [sys.executable, os.path.join(AQUI, script)] + (args or []),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(resultado.stdout)
    if resultado.returncode != 0:
        print(resultado.stderr)
        print(f"\nFALLÓ en el paso: {titulo}")
        return False
    print(f"(listo en {time.time() - t0:.1f}s)")
    return True


def main():
    _correr_paso("1/2 Aplicando correcciones pendientes del reporte de hoy", "aplicar_correcciones.py")
    ok = _correr_paso("2/2 Regenerando el reporte diario de hoy", "reporte_diario_9am.py")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    ok, motivo = adquirir_lock(NOMBRE_LOCK, "github-actions", max_minutos=LOCK_MAX_MINUTOS)
    if not ok:
        print(f"No se ejecuta: {motivo}")
        sys.exit(0)
    try:
        main()
    finally:
        liberar_lock(NOMBRE_LOCK)
