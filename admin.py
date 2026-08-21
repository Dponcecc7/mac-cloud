# -*- coding: utf-8 -*-
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.security import generate_password_hash

from extensions import db
from models import Usuario

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

        if not email or not password:
            flash("Correo y contraseña son obligatorios.", "error")
        elif Usuario.query.filter_by(email=email).first():
            flash(f"Ya existe un usuario con el correo {email}.", "error")
        else:
            nuevo = Usuario(
                email=email,
                password_hash=generate_password_hash(password),
                rol=rol,
                dni_asociado=dni_asociado,
            )
            db.session.add(nuevo)
            db.session.commit()
            flash(f"Usuario {email} creado.", "ok")
        return redirect(url_for("admin.usuarios"))

    todos = Usuario.query.order_by(Usuario.created_at.desc()).all()
    return render_template("admin_usuarios.html", usuarios=todos)


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
