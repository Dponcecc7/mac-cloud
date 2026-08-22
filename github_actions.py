# -*- coding: utf-8 -*-
"""
Dispara un workflow de GitHub Actions (workflow_dispatch) por API en vez de
correr Athena/el pipeline sincronico dentro de un request de Flask -- Render
(free tier) no tiene margen para una operacion de varios minutos bloqueando
el proceso web para todos los usuarios a la vez, y ya existe la
infraestructura del pipeline en la nube (Fase 6) para hacer el trabajo real.

Necesita GITHUB_PAT (variable de entorno) -- un Personal Access Token
fine-grained, scope solo el repo mac-cloud, permiso "Actions: write". Sin
esto, disparar_workflow() devuelve (False, mensaje) en vez de lanzar --
la pantalla debe mostrar el aviso, no romper la app.
"""
import os

import requests

REPO = "Dponcecc7/mac-cloud"
GITHUB_API = "https://api.github.com"


def disparar_workflow(nombre_archivo_yml, ref="main"):
    """Devuelve (ok, mensaje)."""
    token = os.environ.get("GITHUB_PAT")
    if not token:
        return False, "GITHUB_PAT no configurado -- no se puede disparar el workflow desde acá."

    url = f"{GITHUB_API}/repos/{REPO}/actions/workflows/{nombre_archivo_yml}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        r = requests.post(url, headers=headers, json={"ref": ref}, timeout=15)
        r.raise_for_status()
        return True, f"Disparado: {nombre_archivo_yml} -- va a tardar unos minutos, revisá github.com/{REPO}/actions o refrescá esta página en un rato."
    except requests.RequestException as e:
        return False, f"No se pudo disparar {nombre_archivo_yml}: {e}"


def estado_ultima_corrida(nombre_archivo_yml):
    """Devuelve {status, conclusion, html_url} de la corrida MAS RECIENTE de
    ese workflow, o None si no se puede consultar. Usado para hacer polling
    desde asistencia_resultado.html despues de disparar_workflow() -- no hay
    forma de obtener el run_id exacto desde la respuesta de /dispatches (la
    API de GitHub no lo devuelve), asi que se asume que la corrida mas
    reciente es la que se acaba de disparar (valido en la practica: nadie
    mas dispara este workflow manualmente al mismo tiempo)."""
    token = os.environ.get("GITHUB_PAT")
    if not token:
        return None
    url = f"{GITHUB_API}/repos/{REPO}/actions/workflows/{nombre_archivo_yml}/runs"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        r = requests.get(url, headers=headers, params={"per_page": 1}, timeout=15)
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])
        if not runs:
            return None
        run = runs[0]
        return {"status": run["status"], "conclusion": run["conclusion"], "html_url": run["html_url"]}
    except requests.RequestException:
        return None
