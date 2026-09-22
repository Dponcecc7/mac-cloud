# -*- coding: utf-8 -*-
"""
Copia de MAC/ventana_reemplazos.py::procesar_reemplazo() -- ya era 100%
Postgres-nativo desde la Fase 2 (no toca Excel/Graph para nada), asi que se
puede llamar DIRECTO desde el blueprint de asistencia.py (import, no
subprocess) sin ningun riesgo nuevo. Logica identica, cero cambios --
MAC/ventana_reemplazos.py sigue siendo el original para lo que corre local
via agregar_reemplazo.py.
"""
import datetime as dt

from dimension_models import Persona, PatronRecurrente, PersonaSupervisorCanal, get_session
from patron_recurrente import canonizar_canal_dia, sin_acentos

# "analista_propietario" agregado 2026-08-27 -- sin esto, un reemplazo
# quedaba con esa columna en NULL (nunca se seteaba en ningún lado para la
# persona nueva), y en SQL "columna IN (...)"/"NOT IN (...)" con NULL no
# matchea NINGUNA de las dos -- la persona desaparecía tanto de la lista de
# "visibles" de condicion_scope() (scoping.py) como de cualquier intento de
# encontrar "a quién le falta el dato". Invisible para cualquier analista
# (admin no tiene ese filtro, por eso Davor SÍ los veía) hasta que alguien
# comparara el total a mano (Kevin: 73 vs Davor: 75).
CAMPOS_HEREDADOS = ["rol", "canal", "region", "ciudad", "zona", "supervisor_dni", "analista_propietario"]


def procesar_reemplazo(dni_vacante, dni_nuevo, nombre_nuevo, fecha_ingreso, dry_run=False, motivo_baja=None):
    dni_vacante, dni_nuevo = str(dni_vacante).strip(), str(dni_nuevo).strip()
    if dni_nuevo == dni_vacante:
        # Sin este guard, session.get(Persona, dni_nuevo) devuelve el MISMO
        # objeto que persona_vacante (identity map de SQLAlchemy por PK) --
        # los 2 ".estado = ..." de abajo pisan la misma fila, y como
        # "Inactivo" corre después, la persona queda inactiva en vez de
        # reactivada.
        raise ValueError(f"El DNI nuevo ({dni_nuevo}) no puede ser igual al DNI de la vacante que se está cubriendo.")
    log = []

    session = get_session()
    try:
        persona_vacante = session.get(Persona, dni_vacante)
        if persona_vacante is None:
            raise ValueError(f"No se encontro el DNI {dni_vacante} en personas")
        if persona_vacante.estado not in ("Vacante", "Activo"):
            raise ValueError(f"DNI {dni_vacante} no esta en estado Vacante ni Activo (esta en '{persona_vacante.estado}')")

        viene_de_activo = persona_vacante.estado == "Activo"
        if viene_de_activo:
            log.append(f"DNI {dni_vacante} estaba Activo -- se da de baja hoy con fecha {fecha_ingreso} (motivo: {motivo_baja or 'no especificado'})")

        heredado = {campo: getattr(persona_vacante, campo) for campo in CAMPOS_HEREDADOS}
        log.append(f"Heredado de DNI {dni_vacante}: {heredado}")

        persona_reemplazo = session.get(Persona, dni_nuevo)
        es_reingreso = persona_reemplazo is not None
        log.append(f"Es reingreso: {'SÍ' if es_reingreso else 'NO'}")

        if not dry_run:
            if persona_reemplazo is None:
                persona_reemplazo = Persona(dni=dni_nuevo)
                session.add(persona_reemplazo)

            persona_reemplazo.nombre_completo = nombre_nuevo
            persona_reemplazo.fecha_ingreso = fecha_ingreso
            persona_reemplazo.fecha_baja = None
            persona_reemplazo.estado = "Activo"
            persona_reemplazo.reemplaza_a_dni = dni_vacante
            persona_reemplazo.es_reingreso = es_reingreso
            for campo, valor in heredado.items():
                setattr(persona_reemplazo, campo, valor)
            persona_reemplazo.motivo_baja = None
            persona_reemplazo.registrado_por = "Analista MAC"
            persona_reemplazo.fecha_registro = dt.date.today()

            if viene_de_activo:
                persona_vacante.fecha_baja = fecha_ingreso - dt.timedelta(days=1)
                persona_vacante.motivo_baja = motivo_baja
            persona_vacante.estado = "Inactivo"

        filas_patron_vacante = session.query(PatronRecurrente).filter_by(dni=dni_vacante).all()
        log.append(f"Filas de patrón a copiar: {len(filas_patron_vacante)}")

        if not dry_run:
            session.query(PatronRecurrente).filter_by(dni=dni_nuevo).delete()
            session.flush()
            for fila in filas_patron_vacante:
                # sin_acentos()/canonizar_canal_dia() acá también (Davor,
                # 2026-09-14) -- copiar el patrón de la vacante no debería
                # propagar una capitalización inconsistente si la fila
                # original venía de antes de esta corrección.
                session.add(PatronRecurrente(
                    dni=dni_nuevo, dia_semana=sin_acentos(fila.dia_semana),
                    hora_entrada_prog=fila.hora_entrada_prog, hora_salida_prog=fila.hora_salida_prog,
                    canal_dia=canonizar_canal_dia(fila.canal_dia), refrigerio=fila.refrigerio,
                ))

        # Si dni_vacante era supervisor de otras personas, esas personas
        # deben pasar a reportarle al reemplazo, no seguir apuntando a
        # alguien ya Inactivo (Davor, 2026-09-04: "si ingreso el reemplazo...
        # la columna supervisor de los mercaderistas antiguos debe cambiar a
        # su nuevo super, no mantenerse el antiguo"). Se repunta tanto
        # Persona.supervisor_dni como los overrides de PersonaSupervisorCanal
        # (Multicanal con supervisor distinto por Farmacia/AU vs Tradicional).
        n_reportes = session.query(Persona).filter(Persona.supervisor_dni == dni_vacante).count()
        n_overrides_sup = session.query(PersonaSupervisorCanal).filter(PersonaSupervisorCanal.supervisor_dni == dni_vacante).count()
        if n_reportes or n_overrides_sup:
            log.append(f"{dni_vacante} era supervisor de {n_reportes} persona(s) ({n_overrides_sup} override(s) de canal) -- se repuntan a {dni_nuevo}.")
        if not dry_run:
            if n_reportes:
                session.query(Persona).filter(Persona.supervisor_dni == dni_vacante).update(
                    {Persona.supervisor_dni: dni_nuevo}, synchronize_session=False,
                )
            if n_overrides_sup:
                session.query(PersonaSupervisorCanal).filter(PersonaSupervisorCanal.supervisor_dni == dni_vacante).update(
                    {PersonaSupervisorCanal.supervisor_dni: dni_nuevo}, synchronize_session=False,
                )

        if not dry_run:
            session.commit()
            log.append("Guardado en Postgres -- personas y patron_recurrente actualizados.")
            log.append("La vacante ya no aparece en la vista `vacantes` (estado -> Inactivo).")
        else:
            session.rollback()
    finally:
        session.close()

    return log


def resolver_cadena_vigente(dnis):
    """{dni_original: {"dni", "nombre", "cambio"}} -- a quién le corresponde
    HOY cada `dni` de entrada, caminando hacia adelante la cadena de
    reemplazos (Persona.reemplaza_a_dni apunta del DNI NUEVO hacia el DNI
    que reemplazó) hasta llegar a alguien que todavía nadie más reemplazó.
    `cambio=False` y mismo dni/nombre=None si no hubo ningún reemplazo
    registrado para ese DNI.

    A diferencia del resto de este archivo, esto NO es una copia de
    MAC/ventana_reemplazos.py -- es funcionalidad nueva, solo para la nube
    (Davor, 2026-09-22: "que también automáticamente se cambiara la persona
    según entraba su reemplazo"). Usado por planning.py: PlanningPdv.dni_asignado
    es un snapshot del DNI que estaba en el Excel de Planning al momento de
    subirlo, sin ninguna relación con Persona -- esto lo resuelve recién al
    MOSTRAR el Planning, para no tener que volver a subir el Excel cada vez
    que alguien renuncia y entra su reemplazo."""
    dnis = {d for d in dnis if d}
    if not dnis:
        return {}

    session = get_session()
    try:
        # Un solo query trae TODA la tabla de sucesores (reemplaza_a_dni no
        # nulo) -- la tabla personas es chica (cientos de filas, no miles
        # como el Planning), así que resolver la cadena en memoria es más
        # simple y muchísimo más rápido que 1 consulta por cada DNI del
        # Planning (miles de filas -- ver _consolidar_por_pdv()).
        sucesor_de = {
            reemplaza_a: (dni, nombre)
            for reemplaza_a, dni, nombre in session.query(
                Persona.reemplaza_a_dni, Persona.dni, Persona.nombre_completo,
            ).filter(Persona.reemplaza_a_dni.isnot(None)).all()
        }
    finally:
        session.close()

    resultado = {}
    for dni_original in dnis:
        dni_actual, nombre_actual = dni_original, None
        vistos = {dni_original}
        while dni_actual in sucesor_de:
            dni_siguiente, nombre_siguiente = sucesor_de[dni_actual]
            if dni_siguiente in vistos:
                break  # corte de seguridad ante un ciclo de datos corrupto -- no debería pasar nunca
            dni_actual, nombre_actual = dni_siguiente, nombre_siguiente
            vistos.add(dni_actual)
        resultado[dni_original] = {"dni": dni_actual, "nombre": nombre_actual, "cambio": dni_actual != dni_original}
    return resultado
