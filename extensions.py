# -*- coding: utf-8 -*-
"""Instancias compartidas de Flask-SQLAlchemy, Flask-Login, CSRFProtect y
Flask-Limiter -- separadas en su propio módulo (en vez de crearlas dentro de
app.py) para que auth.py/admin.py puedan importarlas sin generar un import
circular con la application factory."""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Inicia sesión para continuar."
login_manager.login_message_category = "error"
csrf = CSRFProtect()
# Almacenamiento en memoria -- suficiente para una sola instancia de Render
# (free tier, sin Redis). Si algun dia hay mas de un worker/instancia, los
# limites dejan de ser globales entre ellos (cada uno cuenta por su lado) --
# no es un problema de seguridad, en el peor caso se permiten mas intentos
# de los pensados, nunca menos.
limiter = Limiter(key_func=get_remote_address, default_limits=[])
