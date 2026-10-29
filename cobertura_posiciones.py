# -*- coding: utf-8 -*-
"""Reportes -- Cubrir posiciones (Davor, 2026-10-29: "contemos los días
para saber cuándo ingresa su reemplazo... quiero saber cuánto me demoro en
cubrir una posición").

Tres listas:
- Personal activo (roster completo, Davor 2026-10-29: "que aparezca todo
  el personal activo y si hay alguna vacante, que sí aparezca ese detalle
  con los días") -- cada Activo que matchea los filtros, con el dato de
  "cubiertas" pegado si esa persona reemplazó una vacante.
- Posiciones YA cubiertas: toda Persona con reemplaza_a_dni (ver
  reemplazos.py::procesar_reemplazo()) -- el "día que empezó la vacante"
  (para contar "días para cubrir") es el día siguiente al ÚLTIMO día con
  marcación real (entrada_real no nula) de quien se fue, SALVO que el
  analista ya haya marcado ese día siguiente como Vacante -- en ese caso
  cuenta desde ahí mismo, sin el día de gracia (ver
  _inicio_vacante_por_dni(), Davor 2026-10-29: "consider 1 día más como
  Falta y ya desde ahí le pones como Vacante, pero si el analista desde el
  primer día puso vacante, consideralo desde ahí"). El "día que se cubrió"
  es el PRIMER día con marcación real de quien entró -- a propósito NO se
  usa fecha_baja/fecha_ingreso (administrativas, pueden quedar atrasadas
  respecto al día real) sino Clasificacion Diaria directo.
- Posiciones TODAVÍA vacantes (Persona.estado == "Vacante", igual que la
  vista `vacantes` de dimension_models.py) -- mismo criterio de inicio de
  arriba, días contados hasta HOY, sin fecha de cierre todavía.

Si la vacante o el reemplazo nunca tuvieron ninguna marcación real
(ej. reemplazo recién ingresado, aún no empieza), esa fila queda con
dias=None -- la plantilla la muestra como "Sin marcación aún" en vez de un
número engañoso."""
import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import aliased

from dimension_models import Persona, get_session
from fact_models import ClasificacionDiaria
from patron_recurrente import sin_acentos
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


def _inicio_vacante_por_dni(session, ultimo_real):
    """Davor, 2026-10-29: "la última vez que tiene como asistencia, consider
    1 día más como Falta y ya desde ahí le pones como Vacante, pero si el
    analista desde el primer día puso vacante, consideralo desde ahí" -- el
    día siguiente al último real cuenta como gracia (podría ser cualquier
    motivo puntual), la vacante arranca recién al segundo día sin marcación,
    EXCEPTO si el analista ya marcó ese primer día como Vacante -- ahí se
    cuenta desde ese mismo día, sin gracia. `ultimo_real`: {dni: fecha}."""
    if not ultimo_real:
        return {}
    siguiente_por_dni = {dni: fecha + dt.timedelta(days=1) for dni, fecha in ultimo_real.items()}
    filas = (
        session.query(ClasificacionDiaria.dni, ClasificacionDiaria.fecha, ClasificacionDiaria.estado)
        .filter(ClasificacionDiaria.dni.in_(list(siguiente_por_dni)))
        .all()
    )
    estado_dia_siguiente = {
        dni: estado for dni, fecha, estado in filas if siguiente_por_dni.get(dni) == fecha
    }
    inicio = {}
    for dni, fecha_siguiente in siguiente_por_dni.items():
        estado = estado_dia_siguiente.get(dni)
        if estado and estado.upper().startswith("VACANTE"):
            inicio[dni] = fecha_siguiente
        else:
            inicio[dni] = fecha_siguiente + dt.timedelta(days=1)
    return inicio


def tiempo_cobertura_posiciones(usuario_actual, rol_filtro=None, region_filtro=None, supervisor_filtro=None,
                                 ciudad_filtro=None, canal_filtro=None):
    """Devuelve (cubiertas, aun_vacantes, activos_roster)."""
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
        inicio_vacante = _inicio_vacante_por_dni(session, ultimo_real)

        cubiertas = []
        for nuevo, vacante, nombre_supervisor in pares:
            ult = ultimo_real.get(vacante.dni)
            prim = primer_real.get(nuevo.dni)
            inicio = inicio_vacante.get(vacante.dni)
            cubiertas.append({
                "dni_vacante": vacante.dni, "nombre_vacante": vacante.nombre_completo,
                "dni_nuevo": nuevo.dni, "nombre_nuevo": nuevo.nombre_completo,
                "rol": nuevo.rol, "canal": nuevo.canal, "region": nuevo.region, "ciudad": nuevo.ciudad,
                "zona": nuevo.zona, "supervisor": nombre_supervisor,
                "ultimo_dia_real": ult, "primer_dia_real": prim,
                # max(0, ...) -- si el reemplazo arrancó DENTRO del día de
                # gracia (antes de que la vacante "empezara" oficialmente),
                # no tiene sentido un número negativo: se cubrió al toque.
                "dias_para_cubrir": max(0, (prim - inicio).days) if inicio and prim else None,
            })
        cubiertas.sort(key=lambda f: f["primer_dia_real"] or dt.date.min, reverse=True)

        # Davor, 2026-10-29 (caso real Alexandra Sayuri Malasquez): no basta
        # Persona.estado == "Vacante" -- eso es el paso ADMINISTRATIVO ("Dar
        # de baja" procesado), que puede quedar atrasado varios días
        # respecto a lo que el supervisor ya reportó día a día en Marcar
        # asistencia. Si el ÚLTIMO día clasificado de un Activo ya salió
        # "VACANTE..." (botón Vacante de Marcar asistencia, sin marcación
        # real), esa posición YA está operativamente vacante aunque nadie
        # haya tocado "Dar de baja" todavía -- se suma a la lista con el
        # mismo criterio. Validado: solo 2 casos así en todo el sistema al
        # momento de escribir esto, no un falso-positivo masivo.
        q_activos = session.query(Persona).filter(Persona.estado == "Activo")
        if cond_scope is not None:
            q_activos = q_activos.filter(cond_scope)
        q_activos = aplicar_filtros_extra(
            q_activos, Persona, rol_filtro=rol_filtro, region_filtro=region_filtro,
            supervisor_filtro=supervisor_filtro, ciudad_filtro=ciudad_filtro, canal_filtro=canal_filtro,
        )
        activos = q_activos.all()
        ultima_fecha_clasif = dict(
            session.query(ClasificacionDiaria.dni, func.max(ClasificacionDiaria.fecha))
            .filter(ClasificacionDiaria.dni.in_([p.dni for p in activos]))
            .group_by(ClasificacionDiaria.dni).all()
        ) if activos else {}
        pares_fecha_dni = [(dni, fecha) for dni, fecha in ultima_fecha_clasif.items()]
        estados_ultima_fecha = {}
        # Davor, 2026-10-29: "tengo actualmente también 2 personas con DM
        # [Descanso médico], colocame también eso como detalle" -- mismo
        # criterio que reportes.py::_codigo_tareo() (estado "DESCANSO" +
        # "medic" en el comentario, sin_acentos() porque "médico" con tilde
        # nunca contiene la subcadena sin tilde).
        dnis_descanso_medico = {}
        if pares_fecha_dni:
            filas_estado = (
                session.query(
                    ClasificacionDiaria.dni, ClasificacionDiaria.fecha,
                    ClasificacionDiaria.estado, ClasificacionDiaria.comentario_supervisor,
                )
                .filter(ClasificacionDiaria.dni.in_([d for d, _ in pares_fecha_dni]))
                .all()
            )
            for dni, fecha, estado, comentario in filas_estado:
                if ultima_fecha_clasif.get(dni) != fecha:
                    continue
                if estado.upper().startswith("VACANTE"):
                    estados_ultima_fecha[dni] = estado
                elif estado.upper().startswith("DESCANSO") and "medic" in sin_acentos(str(comentario or "")):
                    dnis_descanso_medico[dni] = estado
        activos_reportados_vacante = [p for p in activos if p.dni in estados_ultima_fecha]
        ultimo_real_dm = _ultimo_real_por_dni(session, set(dnis_descanso_medico))

        q_vacantes = session.query(Persona).filter(Persona.estado == "Vacante")
        if cond_scope is not None:
            q_vacantes = q_vacantes.filter(cond_scope)
        q_vacantes = aplicar_filtros_extra(
            q_vacantes, Persona, rol_filtro=rol_filtro, region_filtro=region_filtro,
            supervisor_filtro=supervisor_filtro, ciudad_filtro=ciudad_filtro, canal_filtro=canal_filtro,
        )
        abiertas = q_vacantes.all() + activos_reportados_vacante
        ultimo_real_abiertas = _ultimo_real_por_dni(session, {p.dni for p in abiertas})
        inicio_vacante_abiertas = _inicio_vacante_por_dni(session, ultimo_real_abiertas)

        supervisores_dni = {p.supervisor_dni for p in abiertas + activos if p.supervisor_dni}
        nombres_supervisores = dict(
            session.query(Persona.dni, Persona.nombre_completo).filter(Persona.dni.in_(supervisores_dni)).all()
        ) if supervisores_dni else {}

        hoy = dt.date.today()
        aun_vacantes = []
        for p in abiertas:
            ult = ultimo_real_abiertas.get(p.dni)
            inicio = inicio_vacante_abiertas.get(p.dni)
            dias = max(0, (hoy - inicio).days) if inicio else None
            aun_vacantes.append({
                "dni": p.dni, "nombre": p.nombre_completo,
                "rol": p.rol, "canal": p.canal, "region": p.region, "ciudad": p.ciudad, "zona": p.zona,
                "supervisor": nombres_supervisores.get(p.supervisor_dni),
                "ultimo_dia_real": ult,
                "dias_vacante": dias,
                "pendiente_dar_de_baja": p.dni in estados_ultima_fecha,
            })
        aun_vacantes.sort(key=lambda f: f["dias_vacante"] if f["dias_vacante"] is not None else -1, reverse=True)

        # Davor, 2026-10-29: "que aparezca todo el personal activo y si hay
        # alguna vacante, que sí aparezca ese detalle con los días" -- el
        # roster completo (todos los Activos que matchean los filtros), no
        # solo las posiciones que tuvieron movimiento. Si la persona cubrió
        # una vacante (reemplaza_a_dni), se le pega el mismo dato de
        # "cubiertas" de arriba en vez de recalcularlo.
        cubiertas_por_dni_nuevo = {f["dni_nuevo"]: f for f in cubiertas}
        # Davor, 2026-10-29 (caso real Alexandra Sayuri Malasquez): el
        # roster tiene que mostrar en la MISMA fila si esa persona, aunque
        # siga "Activo" en el sistema, ya está reportada como vacante ahora
        # -- no solo si alguna vez cubrió una vacante ajena.
        vacante_por_dni = {f["dni"]: f for f in aun_vacantes}
        hoy_dm = dt.date.today()
        activos_roster = []
        for p in activos:
            info = cubiertas_por_dni_nuevo.get(p.dni)
            vac = vacante_por_dni.get(p.dni)
            ult_dm = ultimo_real_dm.get(p.dni)
            activos_roster.append({
                "dni": p.dni, "nombre": p.nombre_completo,
                "rol": p.rol, "canal": p.canal, "region": p.region, "ciudad": p.ciudad, "zona": p.zona,
                "supervisor": nombres_supervisores.get(p.supervisor_dni),
                "fecha_ingreso": p.fecha_ingreso,
                "reemplazo_de": info["nombre_vacante"] if info else None,
                "dias_para_cubrir": info["dias_para_cubrir"] if info else None,
                "vacante_ahora": vac is not None,
                "dias_vacante_ahora": vac["dias_vacante"] if vac else None,
                "en_descanso_medico": p.dni in dnis_descanso_medico,
                "dias_descanso_medico": (hoy_dm - ult_dm).days if ult_dm else None,
            })
        activos_roster.sort(key=lambda f: (f["nombre"] or "").upper())

        return cubiertas, aun_vacantes, activos_roster
    finally:
        session.close()
