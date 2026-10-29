# -*- coding: utf-8 -*-
"""Reportes -- Cubrir posiciones (Davor, 2026-10-29: "contemos los días
para saber cuándo ingresa su reemplazo... quiero saber cuánto me demoro en
cubrir una posición").

Dos listas:
- Posiciones YA cubiertas: toda Persona con reemplaza_a_dni (ver
  reemplazos.py::procesar_reemplazo()) -- el "día que empezó la vacante" es
  el ÚLTIMO día con marcación real (entrada_real no nula) de quien se fue,
  y el "día que se cubrió" es el PRIMER día con marcación real de quien
  entró -- a propósito NO se usa fecha_baja/fecha_ingreso (administrativas,
  pueden quedar atrasadas respecto al día real) sino Clasificacion Diaria
  directo, mismo criterio que ya usa el resto de Reportes para "cuándo
  pasó de verdad".
- Posiciones TODAVÍA vacantes (Persona.estado == "Vacante", igual que la
  vista `vacantes` de dimension_models.py) -- días contados hasta HOY,
  sin fecha de cierre todavía.

Si la vacante o el reemplazo nunca tuvieron ninguna marcación real
(ej. reemplazo recién ingresado, aún no empieza), esa fila queda con
dias=None -- la plantilla la muestra como "Sin marcación aún" en vez de un
número engañoso."""
import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import aliased

from dimension_models import Persona, get_session
from fact_models import ClasificacionDiaria
from scoping import aplicar_filtros_extra, condicion_scope


def _ultimo_real_por_dni(session, dnis):
    if not dnis:
        return {}
    filas = (
        session.query(ClasificacionDiaria.dni, func.max(ClasificacionDiaria.fecha))
        .filter(ClasificacionDiaria.dni.in_(dnis), ClasificacionDiaria.entrada_real.isnot(None))
        .group_by(ClasificacionDiaria.dni)
        .all()
    )
    return dict(filas)


def _primer_real_por_dni(session, dnis):
    if not dnis:
        return {}
    filas = (
        session.query(ClasificacionDiaria.dni, func.min(ClasificacionDiaria.fecha))
        .filter(ClasificacionDiaria.dni.in_(dnis), ClasificacionDiaria.entrada_real.isnot(None))
        .group_by(ClasificacionDiaria.dni)
        .all()
    )
    return dict(filas)


def tiempo_cobertura_posiciones(usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None,
                                 ciudad_filtro=None, canal_filtro=None):
    """Devuelve (cubiertas, aun_vacantes), ambas listas de dicts ordenadas
    de más reciente a más antigua."""
    session = get_session()
    try:
        cond_scope = condicion_scope(Persona, usuario_actual)
        Vacante = aliased(Persona)
        Supervisor = aliased(Persona)

        q_cubiertas = (
            session.query(Persona, Vacante, Supervisor.nombre_completo)
            .join(Vacante, Vacante.dni == Persona.reemplaza_a_dni)
            .outerjoin(Supervisor, Supervisor.dni == Persona.supervisor_dni)
            .filter(Persona.reemplaza_a_dni.isnot(None))
        )
        if cond_scope is not None:
            q_cubiertas = q_cubiertas.filter(cond_scope)
        q_cubiertas = aplicar_filtros_extra(
            q_cubiertas, Persona, rol_filtro=rol_filtro, region_filtro=region_filtro,
            supervisor_filtro=supervisor_filtro, ciudad_filtro=ciudad_filtro, canal_filtro=canal_filtro,
        )
        pares = q_cubiertas.all()

        ultimo_real = _ultimo_real_por_dni(session, {vacante.dni for _, vacante, _ in pares})
        primer_real = _primer_real_por_dni(session, {nuevo.dni for nuevo, _, _ in pares})

        cubiertas = []
        for nuevo, vacante, nombre_supervisor in pares:
            ult = ultimo_real.get(vacante.dni)
            prim = primer_real.get(nuevo.dni)
            cubiertas.append({
                "dni_vacante": vacante.dni, "nombre_vacante": vacante.nombre_completo,
                "dni_nuevo": nuevo.dni, "nombre_nuevo": nuevo.nombre_completo,
                "rol": nuevo.rol, "canal": nuevo.canal, "region": nuevo.region, "ciudad": nuevo.ciudad,
                "zona": nuevo.zona, "supervisor": nombre_supervisor,
                "ultimo_dia_real": ult, "primer_dia_real": prim,
                "dias_para_cubrir": (prim - ult).days if ult and prim else None,
            })
        cubiertas.sort(key=lambda f: f["primer_dia_real"] or dt.date.min, reverse=True)

        q_vacantes = session.query(Persona).filter(Persona.estado == "Vacante")
        if cond_scope is not None:
            q_vacantes = q_vacantes.filter(cond_scope)
        q_vacantes = aplicar_filtros_extra(
            q_vacantes, Persona, rol_filtro=rol_filtro, region_filtro=region_filtro,
            supervisor_filtro=supervisor_filtro, ciudad_filtro=ciudad_filtro, canal_filtro=canal_filtro,
        )
        abiertas = q_vacantes.all()
        ultimo_real_abiertas = _ultimo_real_por_dni(session, {p.dni for p in abiertas})

        supervisores_dni = {p.supervisor_dni for p in abiertas if p.supervisor_dni}
        nombres_supervisores = dict(
            session.query(Persona.dni, Persona.nombre_completo).filter(Persona.dni.in_(supervisores_dni)).all()
        ) if supervisores_dni else {}

        hoy = dt.date.today()
        aun_vacantes = []
        for p in abiertas:
            ult = ultimo_real_abiertas.get(p.dni)
            aun_vacantes.append({
                "dni": p.dni, "nombre": p.nombre_completo,
                "rol": p.rol, "canal": p.canal, "region": p.region, "ciudad": p.ciudad, "zona": p.zona,
                "supervisor": nombres_supervisores.get(p.supervisor_dni),
                "ultimo_dia_real": ult,
                "dias_vacante": (hoy - ult).days if ult else None,
            })
        aun_vacantes.sort(key=lambda f: f["dias_vacante"] if f["dias_vacante"] is not None else -1, reverse=True)

        return cubiertas, aun_vacantes
    finally:
        session.close()
