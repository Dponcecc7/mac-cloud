# -*- coding: utf-8 -*-
"""
Fase 6: primer paso del pipeline en la nube -- trae el historico de Athena
(misma ventana movil de 400 dias que ya usa MAC/pipeline_completo.py) y lo
deja en Visitas/*.xlsx, mismo formato que usan motor_clasificacion.py y el
resto. A diferencia del original (que escribe a Visitas_Athena/ y despues
copia/limpia hacia Visitas/ -- necesario en un disco que persiste entre
corridas, para no acumular un archivo por dia para siempre), este escribe
DIRECTO a Visitas/: un runner de GitHub Actions arranca vacio en cada
corrida, no hay nada que acumular ni limpiar.

Uso: python traer_visitas_historico.py [YYYY-MM-DD]
     (sin argumento, usa la ventana movil de 400 dias)
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from athena_client import traer_visitas  # noqa: E402

INICIO_SISTEMA = dt.date(2026, 7, 1)
VENTANA_HISTORICO_DIAS = 400


def _desde_por_defecto():
    ventana = dt.date.today() - dt.timedelta(days=VENTANA_HISTORICO_DIAS)
    return max(INICIO_SISTEMA, ventana).isoformat()


def main():
    desde = sys.argv[1] if len(sys.argv) > 1 else _desde_por_defecto()
    filtro = f"AND date_parse(v.fecha_inicio, '%d-%m-%Y') >= date '{desde}'"
    out = traer_visitas("dim_lf_general_visitas", filtro)
    os.makedirs("Visitas", exist_ok=True)
    destino = f"Visitas/historico_desde_{desde}.xlsx"
    out.to_excel(destino, index=False)
    print(f"Guardado: {destino} ({len(out)} filas)")


if __name__ == "__main__":
    main()
