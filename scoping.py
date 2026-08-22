# -*- coding: utf-8 -*-
"""Filtra qué Personas puede ver cada usuario logueado, según el
cliente_id_athena del analista (ver admin.py -- "un analista = un cliente
fijo"). Sin esto, cualquier usuario logueado (aunque sea de otro cliente)
veía el nombre/DNI de TODOS los clientes en Personal/Dashboard/Asistencia.

Admin ve todo (supervisión cruzada). Un usuario sin cliente_id_athena
asignado todavía (ej. una cuenta de supervisor vieja, previa a esto) también
ve todo -- para no romper accesos existentes en silencio; asignale un
cliente_id_athena en Usuarios para acotarlo."""
from models import Usuario


def correos_visibles(usuario_actual):
    """None = sin restricción (admin, o usuario sin cliente_id_athena
    asignado todavía -- para no romper accesos existentes en silencio).
    Si no es None, es la lista de correos de analista cuyas Personas puede
    ver `usuario_actual` (mismo cliente_id_athena)."""
    if usuario_actual.rol == "admin" or not usuario_actual.cliente_id_athena:
        return None
    return [
        u.email for u in Usuario.query.filter_by(cliente_id_athena=usuario_actual.cliente_id_athena).all()
    ]


def personas_visibles(session, persona_model, usuario_actual):
    """Devuelve un Query de `persona_model` (dimension_models.Persona)
    acotado al mismo cliente_id_athena que `usuario_actual`, usando la
    sesión standalone `session` (no la de Flask-SQLAlchemy)."""
    q = session.query(persona_model)
    correos = correos_visibles(usuario_actual)
    if correos is None:
        return q
    return q.filter(persona_model.analista_propietario.in_(correos))
