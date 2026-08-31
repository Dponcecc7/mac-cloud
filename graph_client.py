# -*- coding: utf-8 -*-
"""
Fase 5: versión portable de graph_excel.py -- mismo flujo de autenticación
(MSAL, client-credentials, App Registration "MAC-Pipeline") y la misma
`subir_in_place()`, pero lee las credenciales de VARIABLES DE ENTORNO
(TENANT_ID, CLIENT_ID, CLIENT_SECRET) en vez de un archivo
graph_credentials.local.env -- ese archivo no existe en un runner de GitHub
Actions. Mismo patrón que ya usa app.py con DATABASE_URL/SECRET_KEY.

graph_excel.py (el original, en asistencia_app/) sigue existiendo tal cual
para los scripts que corren local.

CORRECCIÓN 2026-08-27: a pesar de lo que decía acá antes, este módulo NO es
solo para GitHub Actions -- asistencia.py (el proceso web de Render) también
lo importa. Eso importa porque un runner de GH Actions vive un par de
minutos (el cache de token de acá abajo nunca alcanza a vencer en la
práctica), pero el proceso de Render vive horas -- el cache SIN control de
vencimiento causó un apagón real (401 Unauthorized contra Graph, todo lo
que toca Tabla 3 roto hasta el próximo reinicio del proceso). Ver _token().
"""
import io
import os
import time

import msal
import requests
from dotenv import load_dotenv

load_dotenv()

SITE_ID = "overallperu.sharepoint.com,4d8e6292-c8ef-46a2-866a-6e46b73b6cea,51de1931-66bb-48b6-830c-660773b68abe"
SANCELA_DRIVE_ID = "b!kmKOTe_IokaGam5Gtzts6jEZ3lG7ZrZIgwxmB3O2ir7XCIzlEMUUTZoHFyI59nAJ"

GRAPH = "https://graph.microsoft.com/v1.0"

# (conexion, lectura) en segundos -- sin esto, requests espera EL PEDIDO
# ENTERO para siempre si Graph se queda sin responder (ni error ni timeout
# del lado de Python), lo que colgaba el boton "Falta"/Guardar de Marcar
# asistencia de forma indefinida (Davor, 2026-08-31: "sale cargando nomas
# y no se guarda", confirmado que nunca terminaba ni esperando minutos).
TIMEOUT = (10, 60)

# Apagón real 2026-08-27: este módulo, a pesar del docstring de arriba
# ("solo para lo que corre en GitHub Actions"), también lo usa asistencia.py
# en el proceso web de Render -- que es LARGO VIVIENDO (a diferencia de un
# runner de GH Actions, que muere apenas termina). El cache de token de
# abajo nunca revisaba vencimiento, solo "¿ya tengo uno?" -- una vez que el
# token de Microsoft vencía (~60-90 min), CUALQUIER cosa que tocara Tabla 3
# vía Graph (marcar, guardar, headcount, reemplazo...) empezaba a tirar 401
# hasta el próximo reinicio del proceso (un deploy, o el ciclo de sueño de
# Render free tier). Ahora se guarda cuándo vence de verdad (expires_in de
# MSAL, con margen de 5 min) y se renueva solo antes de esa hora.
_token_cache = {"token": None, "expira_en": 0}
_MARGEN_RENOVACION_SEG = 300


def _token(forzar_nuevo=False):
    if not forzar_nuevo and _token_cache["token"] is not None and time.time() < _token_cache["expira_en"]:
        return _token_cache["token"]
    authority = f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}"
    app = msal.ConfidentialClientApplication(
        os.environ["CLIENT_ID"], authority=authority, client_credential=os.environ["CLIENT_SECRET"],
        # Mismo motivo que TIMEOUT en las llamadas a Graph de mas abajo --
        # sin esto, si login.microsoftonline.com no responde, msal se queda
        # esperando el token PARA SIEMPRE, y como esto corre ANTES de
        # cualquier requests.get/put con timeout, el arreglo de esas
        # llamadas no alcanza a aplicarse (Davor, 2026-08-31: seguia
        # colgado despues de agregar timeout solo a Graph).
        timeout=TIMEOUT,
    )
    resultado = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in resultado:
        raise RuntimeError(f"No se pudo autenticar contra Graph: {resultado.get('error_description')}")
    _token_cache["token"] = resultado["access_token"]
    _token_cache["expira_en"] = time.time() + resultado.get("expires_in", 3600) - _MARGEN_RENOVACION_SEG
    return resultado["access_token"]


def _con_reintento_401(hacer_pedido):
    """Ejecuta `hacer_pedido()` (una llamada a requests.get/put que usa
    _headers() adentro) y, si igual devuelve 401 por algún motivo que
    _token() no anticipó (revocado, permisos cambiados, reloj desincronizado),
    fuerza un token nuevo y reintenta UNA vez -- red de seguridad además del
    control de vencimiento de arriba, no un reemplazo."""
    r = hacer_pedido()
    if r.status_code == 401:
        _token(forzar_nuevo=True)
        r = hacer_pedido()
    return r


def _headers():
    return {"Authorization": f"Bearer {_token()}"}


def resolver_item_id(ruta_relativa):
    url = f"{GRAPH}/drives/{SANCELA_DRIVE_ID}/root:/{ruta_relativa}"
    r = _con_reintento_401(lambda: requests.get(url, headers=_headers(), timeout=TIMEOUT))
    r.raise_for_status()
    return r.json()["id"]


def descargar(ruta_relativa):
    """Devuelve el contenido del archivo (bytes)."""
    item_id = resolver_item_id(ruta_relativa)
    url = f"{GRAPH}/drives/{SANCELA_DRIVE_ID}/items/{item_id}/content"
    r = _con_reintento_401(lambda: requests.get(url, headers=_headers(), timeout=TIMEOUT))
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

    def _pedido():
        headers = dict(_headers())
        headers["Content-Type"] = "application/octet-stream"
        return requests.put(url, headers=headers, data=contenido, timeout=TIMEOUT)

    r = _con_reintento_401(_pedido)
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

    def _pedido():
        headers = dict(_headers())
        headers["Content-Type"] = "application/octet-stream"
        return requests.put(url, headers=headers, data=contenido, timeout=TIMEOUT)

    r = _con_reintento_401(_pedido)
    r.raise_for_status()
    return r.json()
