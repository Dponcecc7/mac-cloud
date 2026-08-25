# -*- coding: utf-8 -*-
"""Filtra qué Personas puede ver cada usuario logueado. Dos niveles:

- **Analista**: acotado a su cliente_id_athena (ver admin.py -- "un
  analista = un cliente fijo"). Sin esto, cualquier analista veía
  nombres/DNI de clientes que no eran el suyo.
- **Supervisor**: acotado a su equipo directo (Persona.supervisor_dni ==
  su propio DNI, via Usuario.dni_asociado). Sin esto, un supervisor veía
  todo el equipo del cliente, no solo el suyo.

Admin ve todo (supervisión cruzada). Un usuario sin nada de esto asignado
todavía también ve todo -- para no romper accesos existentes en silencio;
asignale un cliente_id_athena/dni_asociado en Usuarios para acotarlo."""
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
    if usuario_actual.cliente_id_athena is not None:
        correos = [
            u.email for u in Usuario.query.filter_by(cliente_id_athena=usuario_actual.cliente_id_athena).all()
        ]
        return persona_model.analista_propietario.in_(correos)
    return None


def aplicar_filtros_extra(query, persona_model, rol_filtro=None, region_filtro=None, supervisor_filtro=None):
    """Filtros de Rol/Región/Supervisor de Reportes (Davor, 2026-08-24) --
    encima del scope de acceso (condicion_scope), no en vez de. Reportes.py
    solo le pasa valores acá si el usuario es admin ("solo debe haber
    filtros para el perfil admin, para los supervisores no debe haber
    filtros"); para cualquier otro rol estos 3 argumentos quedan en None y
    esta función no hace nada."""
    if rol_filtro:
        query = query.filter(persona_model.rol == rol_filtro)
    if region_filtro:
        query = query.filter(persona_model.region == region_filtro)
    if supervisor_filtro:
        query = query.filter(persona_model.supervisor_dni == supervisor_filtro)
    return query
