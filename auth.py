# -*- coding: utf-8 -*-
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from extensions import db, limiter
from models import Usuario

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        usuario = Usuario.query.filter_by(email=email).first()

        if usuario is None or not usuario.activo or not check_password_hash(usuario.password_hash, password):
            flash("Correo o contraseña incorrectos, o usuario inactivo.", "error")
            return render_template("login.html")

        login_user(usuario)
        usuario.last_login = datetime.utcnow()
        db.session.commit()
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
