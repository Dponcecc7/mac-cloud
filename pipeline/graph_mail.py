# -*- coding: utf-8 -*-
"""
Fase 6: version portable de graph_mail.py -- misma logica de envio (Graph
sendMail), importa graph_client en vez de graph_excel. Sigue bloqueado por el
mismo permiso Mail.Send que la version local hasta que un admin de Entra ID
lo otorgue.
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graph_client import GRAPH, _headers  # noqa: E402


def enviar_correo(remitente, destinatario, asunto, html_body, cc=None):
    destinatarios = [{"emailAddress": {"address": destinatario}}]
    cc_list = [{"emailAddress": {"address": cc}}] if cc else []

    body = {
        "message": {
            "subject": asunto,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": destinatarios,
            "ccRecipients": cc_list,
        },
        "saveToSentItems": True,
    }

    url = f"{GRAPH}/users/{remitente}/sendMail"
    r = requests.post(url, headers={**_headers(), "Content-Type": "application/json"}, json=body)
    r.raise_for_status()
