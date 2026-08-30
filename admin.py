# -*- coding: utf-8 -*-
import json
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.security import generate_password_hash

from extensions import db
from models import Usuario
from permisos import PAGINAS_REPORTES, PAGINAS_TOP, TODAS_LAS_CLAVES, paginas_de

bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(f):
    """Igual que @login_required, pero además exige rol admin -- separado de
    login_required (en vez de un solo decorator con parámetro) para que quede
    explícito en cada ruta cuál protección aplica."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.rol != "admin":
            flash("No tienes permiso para ver esta página.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper


@bp.route("/usuarios", methods=["GET", "POST"])
@admin_required
def usuarios():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        rol = request.form.get("rol", "supervisor")
        dni_asociado = request.form.get("dni_asociado", "").strip() or None
        # Persona.dni/supervisor_dni nunca tienen cero a la izquierda (vienen
        # de pd.to_numeric(...).astype(str) en el ETL) -- si acá se guarda
        # "09919446" tal cual viene del DNI real, la comparación en
        # scoping.py nunca matchea y el supervisor ve 0 personas.
        if dni_asociado:
            dni_asociado = dni_asociado.lstrip("0") or "0"
        cliente_id_athena = request.form.get("cliente_id_athena", "").strip() or None

        if not email or not password:
            flash("Correo y contraseña son obligatorios.", "error")
        elif len(password) < 8:
            flash("La contraseña debe tener al menos 8 caracteres.", "error")
        elif Usuario.query.filter_by(email=email).first():
            flash(f"Ya existe un usuario con el correo {email}.", "error")
        elif cliente_id_athena and not cliente_id_athena.isdigit():
            flash("El cliente ID de Athena tiene que ser un número.", "error")
        else:
            nuevo = Usuario(
                email=email,
                password_hash=generate_password_hash(password),
                rol=rol,
                dni_asociado=dni_asociado,
                cliente_id_athena=int(cliente_id_athena) if cliente_id_athena else None,
            )
            db.session.add(nuevo)
            db.session.commit()
            flash(f"Usuario {email} creado.", "ok")
        return redirect(url_for("admin.usuarios"))

    todos = Usuario.query.order_by(Usuario.created_at.desc()).all()
    return render_template(
        "admin_usuarios.html", usuario=current_user, usuarios=todos,
        paginas_top=PAGINAS_TOP, paginas_reportes=PAGINAS_REPORTES, permisos_de=paginas_de,
    )


@bp.route("/usuarios/<int:usuario_id>/toggle", methods=["POST"])
@admin_required
def toggle_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
    if usuario.id == current_user.id:
        flash("No puedes desactivarte a ti mismo.", "error")
    else:
        usuario.activo = not usuario.activo
        db.session.commit()
        flash(f"Usuario {usuario.email} {'activado' if usuario.activo else 'desactivado'}.", "ok")
    return redirect(url_for("admin.usuarios"))


CANALES_ASIGNABLES = ["FARMACIA", "AUTOSERVICIO", "TRADICIONAL"]


@bp.route("/usuarios/<int:usuario_id>/acceso", methods=["POST"])
@admin_required
def set_acceso_usuario(usuario_id):
    """Un solo formulario para los 3 campos que controlan qué Personas ve
    cada usuario (Davor, 2026-08-28: "puedo seleccionar cada campo que
    quiero darle acceso... ahora lo veo muy general" -- antes solo se podía
    tocar Cliente ID desde acá, canal_asignado (ver scoping.py) y DNI
    asociado no tenían forma de editarse sin entrar a la base a mano)."""
    usuario = Usuario.query.get_or_404(usuario_id)

    cliente_id_athena = request.form.get("cliente_id_athena", "").strip()
    canal_asignado = request.form.get("canal_asignado", "").strip().upper() or None
    dni_asociado = request.form.get("dni_asociado", "").strip() or None
    if dni_asociado:
        # Mismo motivo que en el alta (arriba): sin cero a la izquierda, para
        # que matchee Persona.supervisor_dni en scoping.py.
        dni_asociado = dni_asociado.lstrip("0") or "0"

    if cliente_id_athena and not cliente_id_athena.isdigit():
        flash("El cliente ID de Athena tiene que ser un número.", "error")
    elif canal_asignado and canal_asignado not in CANALES_ASIGNABLES:
        flash(f"Canal inválido -- tiene que ser uno de: {', '.join(CANALES_ASIGNABLES)}.", "error")
    else:
        usuario.cliente_id_athena = int(cliente_id_athena) if cliente_id_athena else None
        usuario.canal_asignado = canal_asignado
        usuario.dni_asociado = dni_asociado
        db.session.commit()
        flash(f"Acceso de {usuario.email} actualizado.", "ok")
    return redirect(url_for("admin.usuarios"))


@bp.route("/usuarios/<int:usuario_id>/permisos", methods=["POST"])
@admin_required
def guardar_permisos(usuario_id):
    """Qué pestañas/subpestañas ve este usuario (Davor, 2026-08-30: "yo
    como admin debo seleccionar que accesos doy") -- ver permisos.py.
    Guarda la selección explícita aunque venga vacía (== "sacale todo",
    distinto de nunca haber tocado esta pantalla, que sigue usando el
    default de su rol)."""
    usuario = Usuario.query.get_or_404(usuario_id)
    seleccion = [c for c in request.form.getlist("paginas") if c in TODAS_LAS_CLAVES]
    # tiene_acceso() exige "reportes" (el top-level) para CUALQUIER
    # subpágina "reportes_*" -- sin esto, tildar una subpágina puntual sin
    # acordarse de tildar también "Reportes" arriba la dejaba bloqueada en
    # silencio pese a aparecer marcada.
    if any(c.startswith("reportes_") for c in seleccion) and "reportes" not in seleccion:
        seleccion.append("reportes")
    usuario.paginas_permitidas = json.dumps(seleccion)
    db.session.commit()
    flash(f"Permisos de {usuario.email} actualizados.", "ok")
    return redirect(url_for("admin.usuarios"))
