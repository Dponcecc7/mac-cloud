# -*- coding: utf-8 -*-
"""
Historial de cambios -- reemplaza el 8_Historial_Cambios.xlsx que se editaba
a mano para forzar un valor distinto al Maestro/Patrón (hora de entrada/
salida, canal, supervisor, zona, refrigerio) para un DNI en un rango de
fechas. La lectura ya vive en Postgres desde Fase 6 (ver
pipeline/historial_cambios.py::cargar_historial()); lo que faltaba era la
pantalla para poder cargar/editar/borrar esas filas sin tocar la base a
mano -- Davor, 2026-08-25 ("teniamos un excel historial_cambios... eso no
hemos migrado a la plataforma").
"""
import datetime as dt
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from dimension_models import HistorialCambio, Persona, get_session
from scoping import condicion_scope

bp = Blueprint("historial", __name__, url_prefix="/historial")

# Mismo vocabulario que documentaba MAC/historial_cambios.py -- son los
# únicos "Campo" que algún módulo del pipeline realmente lee vía
# valor_efectivo() (ver motor_clasificacion.py, reporte_diario_9am.py,
# horas_semanales.py, export_sharepoint.py, cobertura.py). "Rol" estaba
# documentado en el Excel original pero ningún módulo lo consulta -- no se
# incluye acá para no ofrecer una opción que no hace nada en silencio.
CAMPOS_VALIDOS = [
    "Hora entrada programada", "Hora salida programada",
    "Canal", "Canal del día", "Supervisor", "Zona", "Refrigerio",
]
DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _analista_requerido(f):
    """Mismo criterio de acceso que "Cargar Headcount" (cargas.py) -- un
    override de Historial es tan sensible como subir Headcount nuevo, así
    que se abre a analista+admin, no solo admin."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.rol not in ("analista", "admin"):
            flash("Esta sección es solo para analistas.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper


def _persona_en_scope(session, dni):
    """Busca la Persona y confirma que está dentro del scope de
    current_user (mismo criterio que reportes.py::ficha()) -- evita que un
    analista de un cliente cree/edite/borre un cambio sobre gente de otro
    cliente escribiendo el DNI a mano en el formulario."""
    query = session.query(Persona).filter(Persona.dni == dni)
    cond_scope = condicion_scope(Persona, current_user)
    if cond_scope is not None:
        query = query.filter(cond_scope)
    return query.first()


def _leer_form_cambio(form):
    dni = form.get("dni", "").strip()
    campo = form.get("campo", "").strip()
    valor_nuevo = form.get("valor_nuevo", "").strip()
    fecha_desde = form.get("fecha_desde", "").strip()
    fecha_hasta = form.get("fecha_hasta", "").strip() or None
    dia_semana = form.get("dia_semana", "").strip() or None
    comentario = form.get("comentario", "").strip() or None

    if not dni or campo not in CAMPOS_VALIDOS or not valor_nuevo or not fecha_desde:
        return None, "Completá DNI, Campo, Valor nuevo y Fecha desde."
    if dia_semana and dia_semana not in DIAS_SEMANA:
        return None, "Día de la semana inválido."
    try:
        fecha_desde = dt.date.fromisoformat(fecha_desde)
        fecha_hasta = dt.date.fromisoformat(fecha_hasta) if fecha_hasta else None
    except ValueError:
        return None, "Fecha inválida."
    if fecha_hasta and fecha_hasta < fecha_desde:
        return None, "La fecha hasta no puede ser anterior a la fecha desde."
    return {
        "dni": dni, "campo": campo, "valor_nuevo": valor_nuevo,
        "fecha_desde": fecha_desde, "fecha_hasta": fecha_hasta,
        "dia_semana": dia_semana, "comentario": comentario,
    }, None


@bp.route("/", methods=["GET"])
@_analista_requerido
def listar():
    buscar = request.args.get("buscar", "").strip()
    campo_filtro = request.args.get("campo", "").strip()

    session = get_session()
    try:
        query = session.query(HistorialCambio, Persona.nombre_completo).join(
            Persona, Persona.dni == HistorialCambio.dni
        )
        cond_scope = condicion_scope(Persona, current_user)
        if cond_scope is not None:
            query = query.filter(cond_scope)
        if buscar:
            like = f"%{buscar}%"
            query = query.filter((HistorialCambio.dni.ilike(like)) | (Persona.nombre_completo.ilike(like)))
        if campo_filtro:
            query = query.filter(HistorialCambio.campo == campo_filtro)
        filas = query.order_by(HistorialCambio.fecha_desde.desc()).all()
    finally:
        session.close()

    cambios = [{"c": c, "nombre": nombre} for c, nombre in filas]
    return render_template(
        "historial.html", usuario=current_user, cambios=cambios,
        campos=CAMPOS_VALIDOS, dias_semana=DIAS_SEMANA,
        buscar=buscar, campo_filtro=campo_filtro,
    )


@bp.route("/crear", methods=["POST"])
@_analista_requerido
def crear():
    volver = request.form.get("volver") or url_for("historial.listar")
    datos, error = _leer_form_cambio(request.form)
    if error:
        flash(error, "error")
        return redirect(volver)

    session = get_session()
    try:
        persona = _persona_en_scope(session, datos["dni"])
        if not persona:
            flash(f"No se encontró a nadie con DNI {datos['dni']} en tu alcance.", "error")
            return redirect(volver)
        session.add(HistorialCambio(**datos))
        session.commit()
        flash(f"Cambio registrado para {persona.nombre_completo.title()}.", "ok")
    finally:
        session.close()
    return redirect(volver)


@bp.route("/<int:cambio_id>/editar", methods=["GET", "POST"])
@_analista_requerido
def editar(cambio_id):
    session = get_session()
    try:
        cambio = session.query(HistorialCambio).filter(HistorialCambio.id == cambio_id).first()
        if not cambio or not _persona_en_scope(session, cambio.dni):
            flash("No se encontró ese cambio o no tenés acceso a él.", "error")
            return redirect(url_for("historial.listar"))

        if request.method == "POST":
            datos, error = _leer_form_cambio(request.form)
            if error:
                flash(error, "error")
                return redirect(url_for("historial.editar", cambio_id=cambio_id))
            if datos["dni"] != cambio.dni and not _persona_en_scope(session, datos["dni"]):
                flash(f"No se encontró a nadie con DNI {datos['dni']} en tu alcance.", "error")
                return redirect(url_for("historial.editar", cambio_id=cambio_id))
            for campo, valor in datos.items():
                setattr(cambio, campo, valor)
            session.commit()
            flash("Cambio actualizado.", "ok")
            return redirect(url_for("historial.listar"))

        persona = session.query(Persona).filter(Persona.dni == cambio.dni).first()
        return render_template(
            "historial_editar.html", usuario=current_user, cambio=cambio,
            nombre=persona.nombre_completo if persona else cambio.dni,
            campos=CAMPOS_VALIDOS, dias_semana=DIAS_SEMANA,
        )
    finally:
        session.close()


@bp.route("/<int:cambio_id>/eliminar", methods=["POST"])
@_analista_requerido
def eliminar(cambio_id):
    volver = request.form.get("volver") or url_for("historial.listar")
    session = get_session()
    try:
        cambio = session.query(HistorialCambio).filter(HistorialCambio.id == cambio_id).first()
        if not cambio or not _persona_en_scope(session, cambio.dni):
            flash("No se encontró ese cambio o no tenés acceso a él.", "error")
        else:
            session.delete(cambio)
            session.commit()
            flash("Cambio eliminado.", "ok")
    finally:
        session.close()
    return redirect(volver)
