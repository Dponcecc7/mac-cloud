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
    # Davor, 2026-10-27: "¿no puede ser directa a Postgres?" -- hasta hoy
    # este paso escribía Visitas/*.xlsx y un paso aparte (sync_visitas_
    # postgres.py, retirado) lo releía para upsertear a Postgres. Confirmado
    # que Postgres ya tenía el histórico completo (138,263 filas desde
    # 2026-07-01) -- el Excel intermedio era 100% redundante. Ahora
    # traer_visitas_historico.py hace el upsert directo (ventana de los
    # últimos 10 días -- las visitas más viejas que trae Athena en esta
    # misma corrida ya están sincronizadas y son inmutables, ver ese
    # archivo), y motor_clasificacion.py / alerta_visita_larga.py / motor_
    # compensacion_indicador.py leen la tabla `visitas` directo (pipeline/
    # visitas_motor.py), no un archivo local.
    ("1/6 Trayendo visitas desde Athena y sincronizando a Postgres", ["traer_visitas_historico.py"] + (sys.argv[1:2] if len(sys.argv) > 1 else [])),
    ("2/6 Corriendo motor de clasificación (nube)", ["motor_clasificacion.py"]),
    ("3/6 Regenerando resumen por persona", ["resumen_por_persona.py"]),
    ("4/6 Corriendo compensación e indicador", ["motor_compensacion_indicador.py"]),
    ("5/6 Corriendo alerta de visita larga / Punto Censo", ["alerta_visita_larga.py"]),
    ("6/6 Exportando data para el dashboard", ["export_dashboard_data.py"]),
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
