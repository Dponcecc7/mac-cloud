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


def descargar(ruta_relativa):
    """Devuelve el contenido del archivo (bytes)."""
    item_id = resolver_item_id(ruta_relativa)
    url = f"{GRAPH}/drives/{SANCELA_DRIVE_ID}/items/{item_id}/content"
    r = requests.get(url, headers=_headers())
    r.raise_for_status()
    return r.content


def leer_excel(ruta_relativa, ruta_local=None, **kwargs_read_excel):
    """Fase 6: version portable de graph_excel.leer_excel() -- misma firma y
    mismo comportamiento (Graph primero, archivo local como respaldo SI
    existe), pero un runner de GitHub Actions nunca tiene ese respaldo local,
    asi que en la practica siempre lee por Graph o falla con
    FileNotFoundError (igual que el original en ese caso)."""
    import io

    import pandas as pd
    try:
        contenido = descargar(ruta_relativa)
        return pd.read_excel(io.BytesIO(contenido), **kwargs_read_excel)
    except Exception as e:
        if ruta_local and os.path.exists(ruta_local):
            print(f"Aviso: fallo la lectura por Graph de '{ruta_relativa}' ({e}) -- usando archivo local de respaldo.")
            return pd.read_excel(ruta_local, **kwargs_read_excel)
        raise FileNotFoundError(f"No se pudo leer '{ruta_relativa}' por Graph ni existe respaldo local en '{ruta_local}': {e}") from e


def subir_creando_si_no_existe(ruta_relativa, wb_o_bytes):
    """Como subir_in_place(), pero si el archivo TODAVIA no existe en
    SharePoint (primera corrida contra una ruta nueva -- ej. el snapshot de
    auditoria de Fase 6) lo crea, en vez de fallar. subir_in_place() no
    puede hacer esto: resuelve el item ID por ruta ANTES de subir, y esa
    resolucion falla con 404 si el archivo no existe todavia. Una vez creado
    una vez, las corridas siguientes ya pueden usar subir_in_place() normal
    (el archivo ya existe, y asi se preserva su ID -- importante para
    archivos conectados a Power Apps; el snapshot de auditoria no lo esta,
    pero el patron es el mismo en todos lados)."""
    if hasattr(wb_o_bytes, "save"):
        buf = io.BytesIO()
        wb_o_bytes.save(buf)
        contenido = buf.getvalue()
    else:
        contenido = wb_o_bytes

    url = f"{GRAPH}/drives/{SANCELA_DRIVE_ID}/root:/{ruta_relativa}:/content"
    headers = dict(_headers())
    headers["Content-Type"] = "application/octet-stream"
    r = requests.put(url, headers=headers, data=contenido)
    r.raise_for_status()
    return r.json()


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
