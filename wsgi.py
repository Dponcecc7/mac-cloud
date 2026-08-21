# -*- coding: utf-8 -*-
"""Entrypoint que espera gunicorn (y cualquier servidor WSGI estándar) -- el
Start Command de Render (`gunicorn wsgi:application`) apunta a `application`
en este archivo. Ver DEPLOY.md."""
from app import app as application
