# -*- coding: utf-8 -*-
"""Entrypoint que espera PythonAnywhere (y cualquier servidor WSGI estándar) --
la configuración de la Web App en PythonAnywhere apunta a `application` en
este archivo. Ver DEPLOY.md."""
from app import app as application
