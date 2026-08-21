# -*- coding: utf-8 -*-
"""
Fase 5: versión portable de graph_excel.py -- mismo flujo de autenticación
(MSAL, client-credentials, App Registration "MAC-Pipeline") y la misma
`subir_in_place()`, pero lee las credenciales de VARIABLES DE ENTORNO
(TENANT_ID, CLIENT_ID, CLIENT_SECRET) en vez de un archivo
graph_credentials.local.env -- ese archivo no existe en un runner de GitHub
Actions. Mismo patrón que ya usa app.py con DATABASE_URL/SECRET_KEY.

graph_excel.py (el original, en asistencia_app/) sigue existiendo tal cual
para los scripts que corren local -- este módulo es solo para lo que corre
en GitHub Actions.
"""
import io
import os

import msal
import requests
from dotenv import load_dotenv

load_dotenv()

SITE_ID = "overallperu.sharepoint.com,4d8e6292-c8ef-46a2-866a-6e46b73b6cea,51de1931-66bb-48b6-830c-660773b68abe"
SANCELA_DRIVE_ID = "b!kmKOTe_IokaGam5Gtzts6jEZ3lG7ZrZIgwxmB3O2ir7XCIzlEMUUTZoHFyI59nAJ"

GRAPH = "https://graph.microsoft.com/v1.0"

_token_cache = {"token": None}


def _token():
    if _token_cache["token"] is not None:
        return _token_cache["token"]
    authority = f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}"
    app = msal.ConfidentialClientApplication(
        os.environ["CLIENT_ID"], authority=authority, client_credential=os.environ["CLIENT_SECRET"],
    )
    resultado = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in resultado:
        raise RuntimeError(f"No se pudo autenticar contra Graph: {resultado.get('error_description')}")
    _token_cache["token"] = resultado["access_token"]
    return resultado["access_token"]


def _headers():
    return {"Authorization": f"Bearer {_token()}"}


def resolver_item_id(ruta_relativa):
    url = f"{GRAPH}/drives/{SANCELA_DRIVE_ID}/root:/{ruta_relativa}"
    r = requests.get(url, headers=_headers())
    r.raise_for_status()
    return r.json()["id"]


def subir_in_place(ruta_relativa, wb_o_bytes):
    """Sobreescribe el CONTENIDO del mismo driveItem (PUT), preservando su
    ID -- igual que graph_excel.subir_in_place()."""
    item_id = resolver_item_id(ruta_relativa)
    if hasattr(wb_o_bytes, "save"):
        buf = io.BytesIO()
        wb_o_bytes.save(buf)
        contenido = buf.getvalue()
    else:
        contenido = wb_o_bytes

    url = f"{GRAPH}/drives/{SANCELA_DRIVE_ID}/items/{item_id}/content"
    headers = dict(_headers())
    headers["Content-Type"] = "application/octet-stream"
    r = requests.put(url, headers=headers, data=contenido)
    r.raise_for_status()
    return r.json()
