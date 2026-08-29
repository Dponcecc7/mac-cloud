# -*- coding: utf-8 -*-
"""Filtra qué Personas puede ver cada usuario logueado. Dos niveles:

- **Analista**: acotado a su cliente_id_athena (ver admin.py -- "un
  analista = un cliente fijo"). Sin esto, cualquier analista veía
  nombres/DNI de clientes que no eran el suyo.
- **Supervisor**: acotado a su equipo directo (Persona.supervisor_dni ==
  su propio DNI, via Usuario.dni_asociado). Sin esto, un supervisor veía
  todo el equipo del cliente, no solo el suyo.

- **Analista de canal**: acotado a su canal_asignado (Diego=Farmacia,
  Yeny=Autoservicio, Kevin=Tradicional -- 2026-08-27). Antes todos los
  analistas del mismo cliente_id_athena veían TODO ese grupo sin importar
  canal (por eso Diego veía headcount Tradicional). Ve una Persona si su
  canal principal coincide O si tiene algún día de PatronRecurrente en ese
  canal -- esto último es lo que deja que un mercaderista compartido entre
  canales (ej. canal="MULTICANAL", unos días Farmacia otros Autoservicio)
  aparezca para CADA analista de canal en el suyo, sin duplicar la fila
  (Persona sigue siendo una sola fila por DNI -- admin la ve una sola vez).

Admin ve todo (supervisión cruzada). Un usuario sin nada de esto asignado
todavía también ve todo -- para no romper accesos existentes en silencio;
asignale un cliente_id_athena/dni_asociado en Usuarios para acotarlo."""
from sqlalchemy import func, select

from dimension_models import PatronRecurrente, PersonaSupervisorCanal
from models import Usuario


def condicion_scope(persona_model, usuario_actual):
    """None = sin restricción. Si no es None, es una condición SQLAlchemy
    lista para pasarle a .filter(...) sobre una query que ya tenga
    `persona_model` (dimension_models.Persona) unida/consultada."""
    if usuario_actual.rol == "admin":
        return None
    if usuario_actual.rol == "supervisor" and usuario_actual.dni_asociado:
        # Persona.dni/supervisor_dni salen del ETL vía pd.to_numeric(...).astype(str)
        # -- eso pierde cualquier cero a la izquierda ("09919446" -> "9919446").
        # Si dni_asociado se cargó con el cero (tal como venía en el Excel/DNI
        # real), la comparación exacta nunca matcheaba y el supervisor veía
        # 0 personas en vez de un error visible. Se normaliza acá para que
        # ambos lados comparen igual sin importar cómo se haya tipeado.
        dni_normalizado = usuario_actual.dni_asociado.lstrip("0") or "0"
        # Excluir al propio supervisor de su equipo -- en el Maestro
        # Headcount algunos supervisores quedaron con su propio DNI como
        # supervisor_dni (auto-referenciado), y sin este filtro se veían a
        # sí mismos en su propia lista de mercaderistas (Davor, 2026-08-24:
        # "no tendria sentido").
        return (persona_model.supervisor_dni == dni_normalizado) & (persona_model.dni != dni_normalizado)
    if getattr(usuario_actual, "canal_asignado", None):
        return condicion_canal(persona_model, usuario_actual.canal_asignado)
    if usuario_actual.cliente_id_athena is not None:
        correos = [
            u.email for u in Usuario.query.filter_by(cliente_id_athena=usuario_actual.cliente_id_athena).all()
        ]
        return persona_model.analista_propietario.in_(correos)
    return None


# Canales asignables/filtrables -- mismo vocabulario que admin.py::CANALES_ASIGNABLES
# (canal_asignado de Usuario) y Persona.canal. Compartido acá porque tanto
# scoping como el nuevo filtro de canal para admin (2026-08-29, Davor: "en
# mi caso que soy admin, debo tener un filtro para ver Tradicional,
# Farmacia y AU") necesitan la misma lista.
CANALES_FILTRABLES = ["FARMACIA", "AUTOSERVICIO", "TRADICIONAL"]


def condicion_canal(persona_model, canal):
    """Misma condición que el branch de canal_asignado en condicion_scope(),
    factorizada para reusar en el filtro de canal de admin (aplicar_filtros_extra) --
    ve una Persona si su canal principal coincide O si tiene algún día de
    PatronRecurrente en ese canal (mercaderistas compartidos entre canales)."""
    canal_norm = canal.strip().upper()
    dnis_con_ese_canal = select(PatronRecurrente.dni).where(func.upper(PatronRecurrente.canal_dia) == canal_norm)
    return (func.upper(persona_model.canal) == canal_norm) | persona_model.dni.in_(dnis_con_ese_canal)


def overrides_supervisor_canal(session, dnis, usuario_actual):
    """{dni: supervisor_dni} para las Personas de `dnis` que tengan un
    supervisor DISTINTO para el canal_asignado de `usuario_actual` -- caso
    puntual (Davor, 2026-08-29): un mercaderista compartido entre canales
    puede tener un supervisor real distinto por Tradicional que por
    Farmacia/AU, fijo por canal (no varía según qué canal le toque
    trabajar ese día puntual, por eso no es PatronRecurrente). {} si el
    usuario no tiene canal_asignado o no hay `dnis` que consultar -- la
    inmensa mayoría de personas nunca tiene fila en persona_supervisor_canal,
    así que en ese caso simplemente seguí usando Persona.supervisor_dni."""
    canal_asignado = getattr(usuario_actual, "canal_asignado", None) if usuario_actual else None
    if not canal_asignado or not dnis:
        return {}
    filas = (
        session.query(PersonaSupervisorCanal.dni, PersonaSupervisorCanal.supervisor_dni)
        .filter(
            PersonaSupervisorCanal.dni.in_(dnis),
            func.upper(PersonaSupervisorCanal.canal) == canal_asignado.strip().upper(),
        )
        .all()
    )
    return dict(filas)


def todos_overrides_supervisor_canal(session, dnis):
    """{dni: [(canal, supervisor_dni), ...]} SIN filtrar por el canal del
    usuario que mira -- a diferencia de overrides_supervisor_canal() (pensada
    para pintar UN reporte ya acotado a un canal), esto es para la pantalla
    Personal (Davor, 2026-08-29: "que aparezca el nombre y también si hay 2
    supers para ese mercaderista"), donde admin/analista necesitan ver TODOS
    los supervisores por canal de un compartido de un vistazo, no solo el
    que aplicaría a su propio canal_asignado."""
    if not dnis:
        return {}
    filas = (
        session.query(PersonaSupervisorCanal.dni, PersonaSupervisorCanal.canal, PersonaSupervisorCanal.supervisor_dni)
        .filter(PersonaSupervisorCanal.dni.in_(dnis))
        .all()
    )
    resultado = {}
    for dni, canal, supervisor_dni in filas:
        resultado.setdefault(dni, []).append((canal, supervisor_dni))
    return resultado


def aplicar_filtros_extra(query, persona_model, rol_filtro=None, region_filtro=None, supervisor_filtro=None, ciudad_filtro=None, canal_filtro=None, subcanal_filtro=None):
    """Filtros de Rol/Región/Supervisor/Ciudad de Reportes y Marcar
    asistencia (Davor, 2026-08-24/25) -- encima del scope de acceso
    (condicion_scope), no en vez de. Solo admin/analista le pasan valores
    acá ("para supervisor no debería aparecer filtros"); para supervisor
    estos argumentos quedan en None y esta función no hace nada.

    `canal_filtro` (Davor, 2026-08-29) -- SOLO para admin: un analista de
    canal ya está acotado a su canal_asignado por condicion_scope(), no
    necesita elegir; el admin ve todo por defecto y con esto puede acotarse
    a un canal puntual para revisar, igual que ya podía por Rol/Región/etc."""
    if rol_filtro:
        query = query.filter(persona_model.rol == rol_filtro)
    if region_filtro:
        query = query.filter(persona_model.region == region_filtro)
    if supervisor_filtro:
        query = query.filter(persona_model.supervisor_dni == supervisor_filtro)
    if ciudad_filtro:
        query = query.filter(persona_model.ciudad == ciudad_filtro)
    if canal_filtro:
        query = query.filter(condicion_canal(persona_model, canal_filtro))
    if subcanal_filtro:
        query = query.filter(persona_model.subcanal == subcanal_filtro)
    return query
