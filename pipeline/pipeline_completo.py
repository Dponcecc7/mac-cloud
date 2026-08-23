# -*- coding: utf-8 -*-
"""
Fase 6: orquestador del pipeline completo en la nube -- equivalente a
MAC/pipeline_completo.py, pero corriendo los scripts portados de
pipeline/. Adquiere el candado distribuido (pipeline/db_lock.py, tabla
Postgres `pipeline_lock`) una sola vez para toda la corrida -- necesario
porque, a diferencia del candado de archivo local que ya usa la version de
la laptop, este pipeline puede correr en paralelo con el de la laptop
durante la validacion (ver plan Fase 6).

Uso: python pipeline_completo.py [YYYY-MM-DD]
     (sin argumento, usa la ventana movil de 400 dias -- ver traer_visitas_historico.py)
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_lock import adquirir_lock, liberar_lock  # noqa: E402

NOMBRE_LOCK = "pipeline_completo"
LOCK_MAX_MINUTOS = 25

AQUI = os.path.dirname(os.path.abspath(__file__))

PASOS = [
    ("1/8 Trayendo visitas desde Athena", ["traer_visitas_historico.py"] + (sys.argv[1:2] if len(sys.argv) > 1 else [])),
    # Sincroniza SOLO los últimos 10 días a Postgres (tabla `visitas`) --
    # las visitas más viejas ya quedaron guardadas en corridas anteriores y
    # no cambian (ver sync_visitas_postgres.py). Antes de esto, el
    # historial completo que trae el paso 1 se descartaba al terminar cada
    # corrida (disco efímero del runner) -- ahora alimenta Cobertura Diaria
    # y las alertas de Visita larga/Punto Censo en mac_cloud.
    ("1b/8 Sincronizando visitas a Postgres (ventana de 10 días)", ["sync_visitas_postgres.py"]),
    ("2/8 Corriendo motor de clasificación (nube)", ["motor_clasificacion.py"]),
    ("3/8 Regenerando resumen por persona", ["resumen_por_persona.py"]),
    ("4/8 Corriendo compensación e indicador", ["motor_compensacion_indicador.py"]),
    ("5/8 Corriendo alerta de visita larga / Punto Censo", ["alerta_visita_larga.py"]),
    ("6/8 Exportando data para el dashboard", ["export_dashboard_data.py"]),
    ("7/8 Publicando snapshot de auditoría (Power Apps, modo sombra)", ["export_sharepoint.py"]),
]


def main():
    for titulo, script_y_args in PASOS:
        script = script_y_args[0]
        args = script_y_args[1:]
        print(f"\n{'='*70}\n{titulo}\n{'='*70}")
        t0 = time.time()
        resultado = subprocess.run(
            [sys.executable, os.path.join(AQUI, script)] + args,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        print(resultado.stdout)
        if resultado.returncode != 0:
            print(resultado.stderr)
            print(f"\nFALLÓ en el paso: {titulo}")
            sys.exit(1)
        print(f"(listo en {time.time() - t0:.1f}s)")

    print(f"\n{'='*70}\nPipeline (nube) completo.\n{'='*70}")


if __name__ == "__main__":
    ok, motivo = adquirir_lock(NOMBRE_LOCK, "github-actions", max_minutos=LOCK_MAX_MINUTOS)
    if not ok:
        print(f"Pipeline no iniciado: {motivo}")
        sys.exit(0)
    try:
        main()
    finally:
        liberar_lock(NOMBRE_LOCK)
