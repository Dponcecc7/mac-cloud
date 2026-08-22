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
        return persona_model.supervisor_dni == usuario_actual.dni_asociado
    if usuario_actual.cliente_id_athena:
        correos = [
            u.email for u in Usuario.query.filter_by(cliente_id_athena=usuario_actual.cliente_id_athena).all()
        ]
        return persona_model.analista_propietario.in_(correos)
    return None
