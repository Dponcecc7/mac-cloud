# -*- coding: utf-8 -*-
"""
Fase 6: version portable de graph_lists.py -- misma logica (Listas de
SharePoint que alimentan la Power App de supervisores), pero importa
graph_client (credenciales por env var) en vez de graph_excel (archivo
local). Ni una linea de la logica de sincronizacion cambia.
"""
import math
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import graph_client as ge  # noqa: E402

LISTAS = {
    "ClasificacionDiaria": "f4006cf3-2de5-40b7-84d9-1e0d04728a73",
    "RegistroSupervisor": "a8cb7da1-ada2-4c4d-a4ac-b59533bb4678",
}

COLUMNAS_NUMERICAS = {"SalidaAnticipadaMin"}
COLUMNAS_FECHA = {"Fecha", "FechaRegistro"}


def _limpiar_valor(nombre_col, valor):
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    if nombre_col in COLUMNAS_FECHA:
        import pandas as pd
        ts = pd.Timestamp(valor)
        return ts.strftime("%Y-%m-%dT00:00:00Z")
    if nombre_col in COLUMNAS_NUMERICAS:
        try:
            return float(valor)
        except (TypeError, ValueError):
            return None
    return str(valor)


def listar_items(nombre_lista):
    list_id = LISTAS[nombre_lista]
    items = []
    url = f"{ge.GRAPH}/sites/{ge.SITE_ID}/lists/{list_id}/items?expand=fields&$top=200"
    while url:
        r = requests.get(url, headers=ge._headers())
        r.raise_for_status()
        data = r.json()
        for it in data.get("value", []):
            fields = dict(it["fields"])
            fields["_item_id"] = it["id"]
            items.append(fields)
        url = data.get("@odata.nextLink")
    return items


def _con_reintentos(hacer_request, max_reintentos=3):
    error = None
    for intento in range(max_reintentos):
        r = hacer_request()
        if r.ok:
            return True, r, None
        error = f"{r.status_code} {r.text[:300]}"
        transitorio = r.status_code == 429 or r.status_code >= 500
        if not transitorio or intento == max_reintentos - 1:
            return False, r, error
        time.sleep(float(r.headers.get("Retry-After", 2 ** intento)))
    return False, None, error


def agregar_items(nombre_lista, filas):
    list_id = LISTAS[nombre_lista]
    url = f"{ge.GRAPH}/sites/{ge.SITE_ID}/lists/{list_id}/items"
    creados = 0
    fallidos = []
    for i, fila in enumerate(filas):
        campos = {k: _limpiar_valor(k, v) for k, v in fila.items() if k != "_item_id"}
        campos = {k: v for k, v in campos.items() if v is not None}
        body = {"fields": campos}
        ok, _, error = _con_reintentos(
            lambda: requests.post(url, headers={**ge._headers(), "Content-Type": "application/json"}, json=body)
        )
        if ok:
            creados += 1
        else:
            fallidos.append({"fila": i, "dni": fila.get("DNI"), "error": error})
    return creados, fallidos


def borrar_todos_los_items(nombre_lista):
    list_id = LISTAS[nombre_lista]
    items = listar_items(nombre_lista)
    fallidos = []
    for it in items:
        url = f"{ge.GRAPH}/sites/{ge.SITE_ID}/lists/{list_id}/items/{it['_item_id']}"
        ok, r, error = _con_reintentos(lambda: requests.delete(url, headers=ge._headers()))
        if not ok and r is not None and r.status_code == 404:
            ok = True
        if not ok:
            fallidos.append({"item_id": it["_item_id"], "error": error})
    if fallidos:
        detalle = "; ".join(f"{f['item_id']}: {f['error']}" for f in fallidos)
        raise RuntimeError(
            f"{len(fallidos)} de {len(items)} items no se pudieron borrar de '{nombre_lista}' -- {detalle}"
        )
    return len(items)


def reemplazar_todos_los_items(nombre_lista, filas, clave="DNI"):
    list_id = LISTAS[nombre_lista]
    actuales = {str(it[clave]): it for it in listar_items(nombre_lista) if it.get(clave) is not None}
    nuevas = {str(fila.get(clave)): fila for fila in filas}

    creados = actualizados = borrados = 0
    fallidos = []

    for k, fila in nuevas.items():
        campos = {c: _limpiar_valor(c, v) for c, v in fila.items() if c != "_item_id"}
        campos = {c: v for c, v in campos.items() if v is not None}
        existente = actuales.get(k)
        if existente:
            url = f"{ge.GRAPH}/sites/{ge.SITE_ID}/lists/{list_id}/items/{existente['_item_id']}/fields"
            ok, _, error = _con_reintentos(
                lambda campos=campos, url=url: requests.patch(
                    url, headers={**ge._headers(), "Content-Type": "application/json"}, json=campos)
            )
            if ok:
                actualizados += 1
            else:
                fallidos.append({clave.lower(): k, "accion": "actualizar", "error": error})
        else:
            url = f"{ge.GRAPH}/sites/{ge.SITE_ID}/lists/{list_id}/items"
            body = {"fields": campos}
            ok, _, error = _con_reintentos(
                lambda body=body, url=url: requests.post(
                    url, headers={**ge._headers(), "Content-Type": "application/json"}, json=body)
            )
            if ok:
                creados += 1
            else:
                fallidos.append({clave.lower(): k, "accion": "crear", "error": error})

    for k, it in actuales.items():
        if k in nuevas:
            continue
        url = f"{ge.GRAPH}/sites/{ge.SITE_ID}/lists/{list_id}/items/{it['_item_id']}"
        ok, r, error = _con_reintentos(lambda url=url: requests.delete(url, headers=ge._headers()))
        if not ok and r is not None and r.status_code == 404:
            ok = True
        if ok:
            borrados += 1
        else:
            fallidos.append({clave.lower(): k, "accion": "borrar", "error": error})

    return {"creados": creados, "actualizados": actualizados, "borrados": borrados, "fallidos": fallidos}
