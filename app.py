# -*- coding: utf-8 -*-
"""
Esqueleto en la nube del Proyecto MAC -- Fase 0 + Fase 1 del plan de
migración (ver DEPLOY.md). Este es un proyecto NUEVO e independiente del
app.py local que sigue corriendo en la laptop; no toca ni reemplaza nada de
lo existente todavía. Por ahora solo resuelve login/roles/multiusuario --
el reporte diario real llega en fases futuras (2 y 3 del roadmap).
"""
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template
from flask_login import current_user, login_required
from sqlalchemy import text

from extensions import db, login_manager
from models import Usuario
from dimension_models import Persona
from dimension_models import get_session as get_dim_session

load_dotenv()  # lee .env en local; en PythonAnywhere las variables se cargan desde su panel, no de este archivo


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-no-usar-en-produccion")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///dev.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Usuario, int(user_id))

    from auth import bp as auth_bp
    from admin import bp as admin_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)

    @app.get("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify({"status": "ok", "db": True}), 200
        except Exception as e:
            return jsonify({"status": "error", "db": False, "detalle": str(e)}), 500

    @app.get("/")
    @login_required
    def dashboard():
        return render_template("dashboard.html", usuario=current_user)

    @app.get("/personal")
    @login_required
    def personal():
        # Fase 2: primera pantalla que lee directo de Postgres (dimension_models,
        # las mismas tablas que migrar_dimensiones_a_postgres.py pobló) --
        # solo lectura por ahora, el alta/baja/reemplazo sigue siendo
        # agregar_reemplazo.py (CLI local) hasta que haya un formulario acá.
        dim_session = get_dim_session()
        try:
            personas = dim_session.query(Persona).order_by(Persona.estado, Persona.nombre_completo).all()
        finally:
            dim_session.close()
        return render_template("personal.html", usuario=current_user, personas=personas)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
