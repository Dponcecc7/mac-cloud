# -*- coding: utf-8 -*-
"""Instancias compartidas de Flask-SQLAlchemy, Flask-Login y CSRFProtect --
separadas en su propio módulo (en vez de crearlas dentro de app.py) para que
auth.py/admin.py puedan importarlas sin generar un import circular con la
application factory."""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Inicia sesión para continuar."
login_manager.login_message_category = "error"
csrf = CSRFProtect()
