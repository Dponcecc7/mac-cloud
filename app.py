# -*- coding: utf-8 -*-
"""
Esqueleto en la nube del Proyecto MAC -- Fase 0 + Fase 1 del plan de
migración (ver DEPLOY.md). Este es un proyecto NUEVO e independiente del
app.py local que sigue corriendo en la laptop; no toca ni reemplaza nada de
lo existente todavía. Por ahora solo resuelve login/roles/multiusuario --
el reporte diario real llega en fases futuras (2 y 3 del roadmap).
"""
import datetime as dt
import os
import re
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import text

PERU_TZ = ZoneInfo("America/Lima")  # sin horario de verano -- offset fijo UTC-5

from extensions import csrf, db, limiter, login_manager
from models import Usuario
from dimension_models import Feriado, Persona
from dimension_models import get_session as get_dim_session
from fact_models import ClasificacionDiaria
from scoping import condicion_scope

load_dotenv()  # lee .env en local; en PythonAnywhere las variables se cargan desde su panel, no de este archivo

_RE_PREFIJO_FALTA = re.compile(r"^falta\s*[-–]?\s*", re.IGNORECASE)


def _motivo_limpio(comentario):
    """Version chica de asistencia.py::_homologar_motivo() -- separada a
    proposito (evita acoplar app.py al modulo del blueprint) para agrupar
    "Faltas por motivo" del dashboard.

    pd.isna() en vez de "not comentario": una fila FALTA sin comentario del
    supervisor todavia llega como NaN de pandas (no None), y "not NaN" da
    False -- sin este chequeo, str(NaN) = "nan" terminaba mostrandose
    literal como motivo "Nan" en vez de agruparse en "Sin motivo"."""
    if pd.isna(comentario) or not str(comentario).strip():
        return "Sin motivo"
    texto = str(comentario).strip()
    while True:
        m = _RE_PREFIJO_FALTA.match(texto)
        if not m or m.end() == 0:
            break
        resto = texto[m.end():].strip()
        if resto == texto:
            break
        texto = resto
    texto = re.sub(r"\s*\([^)]*\)\s*$", "", texto).strip()
    return (texto[0].upper() + texto[1:]) if texto else "Sin motivo"


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-no-usar-en-produccion")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///dev.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # Cookie de sesion solo por HTTPS -- Render sirve todo por HTTPS, asi
    # que esto no rompe produccion. SESSION_COOKIE_SECURE=false permite
    # override para correr local por http (python app.py en localhost).
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # 10 MB -- de sobra para un Excel de Headcount, evita que alguien suba
    # un archivo gigante y cuelgue el worker (Render free tier, un solo
    # proceso web para todos los usuarios).
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    @app.after_request
    def _headers_seguridad(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # 'unsafe-inline' en script/style porque las plantillas usan <style>
        # y <script> embebidos (sin build step de assets) -- igual bloquea
        # cargar recursos desde dominios externos no autorizados, que es el
        # riesgo principal que mitiga un CSP.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "frame-ancestors 'none'"
        )
        return response

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Usuario, int(user_id))

    from auth import bp as auth_bp
    from admin import bp as admin_bp
    from cargas import bp as cargas_bp
    from asistencia import bp as asistencia_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(cargas_bp)
    app.register_blueprint(asistencia_bp)

    @app.get("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify({"status": "ok", "db": True}), 200
        except Exception as e:
            return jsonify({"status": "error", "db": False, "detalle": str(e)}), 500

    @app.get("/")
    @login_required
    def dashboard():
        # Fase 6 (2026-08-21): dashboard real -- consulta Postgres en vivo en
        # cada visita, no depende de dashboard_data.json (ese archivo lo
        # genera export_dashboard_data.py en cada corrida del pipeline, pero
        # vive en SharePoint/local, no aca).
        #
        # 2026-08-22: el periodo (desde/hasta) ahora es seleccionable -- antes
        # el indicador de efectividad mezclaba TODO el historico desde el
        # inicio del sistema en un solo numero, sin poder acotarlo.
        hoy_peru = dt.datetime.now(PERU_TZ).date()
        try:
            hasta = dt.date.fromisoformat(request.args.get("hasta", "")) if request.args.get("hasta") else hoy_peru
        except ValueError:
            hasta = hoy_peru
        try:
            desde = dt.date.fromisoformat(request.args.get("desde", "")) if request.args.get("desde") else hasta - dt.timedelta(days=29)
        except ValueError:
            desde = hasta - dt.timedelta(days=29)
        if desde > hasta:
            desde, hasta = hasta, desde

        # Jinja no tiene timedelta a mano -- se precalculan los 3 atajos de
        # periodo acá en vez de hacer aritmetica de fechas en la plantilla.
        presets = {
            "7d": {"desde": (hoy_peru - dt.timedelta(days=6)).isoformat(), "hasta": hoy_peru.isoformat()},
            "30d": {"desde": (hoy_peru - dt.timedelta(days=29)).isoformat(), "hasta": hoy_peru.isoformat()},
            "mes": {"desde": hoy_peru.replace(day=1).isoformat(), "hasta": hoy_peru.isoformat()},
        }

        cond_scope = condicion_scope(Persona, current_user)  # None = sin restriccion

        dim_session = get_dim_session()
        try:
            query = (
                dim_session.query(ClasificacionDiaria.dni, Persona.nombre_completo, Persona.region,
                                   ClasificacionDiaria.fecha, ClasificacionDiaria.estado,
                                   ClasificacionDiaria.canal_esperado, ClasificacionDiaria.entrada_real,
                                   ClasificacionDiaria.salida_real, ClasificacionDiaria.comentario_supervisor)
                .join(Persona, Persona.dni == ClasificacionDiaria.dni)
                .filter(ClasificacionDiaria.fecha >= desde, ClasificacionDiaria.fecha <= hasta)
            )
            if cond_scope is not None:
                query = query.filter(cond_scope)
            filas = query.all()

            # "Hoy" es independiente del periodo elegido (si elegis un rango
            # pasado, igual queres ver como viene el dia de hoy) -- consulta
            # aparte, chica.
            query_hoy = (
                dim_session.query(ClasificacionDiaria.estado)
                .join(Persona, Persona.dni == ClasificacionDiaria.dni)
                .filter(ClasificacionDiaria.fecha == hoy_peru)
            )
            if cond_scope is not None:
                query_hoy = query_hoy.filter(cond_scope)
            filas_hoy = query_hoy.all()

            headcount_query = dim_session.query(Persona.dni).filter(Persona.estado == "Activo")
            if cond_scope is not None:
                headcount_query = headcount_query.filter(cond_scope)
            headcount_actual = headcount_query.count()
        finally:
            dim_session.close()

        periodo_args = {"desde": desde.isoformat(), "hasta": hasta.isoformat()}

        if not filas:
            return render_template(
                "dashboard.html", usuario=current_user, hay_datos=False,
                periodo_desde=desde, periodo_hasta=hasta, periodo_args=periodo_args, presets=presets,
            )

        r = pd.DataFrame(filas, columns=[
            "dni", "nombre", "region", "fecha", "estado", "canal", "entrada_real", "salida_real", "comentario",
        ])
        r["fecha"] = pd.to_datetime(r["fecha"])
        r["estado_base"] = r["estado"].apply(lambda s: s.split(" (")[0])
        r["region"] = r["region"].fillna("Sin región")

        hoy_es = [f.split(" (")[0] for (f,) in filas_hoy]
        resumen_hoy = {
            "asistio": hoy_es.count("ASISTIÓ A TIEMPO"),
            "tardanza": hoy_es.count("TARDANZA"),
            "falta": hoy_es.count("FALTA"),
            "vacante": hoy_es.count("VACANTE"),
            "vacaciones": hoy_es.count("VACACIONES"),
            "total": len(hoy_es),
        }

        total = len(r)
        n_asistio = int((r["estado_base"] == "ASISTIÓ A TIEMPO").sum())
        n_tardanza = int((r["estado_base"] == "TARDANZA").sum())
        n_falta = int((r["estado_base"] == "FALTA").sum())
        n_vacante = int((r["estado_base"] == "VACANTE").sum())
        n_vacaciones = int((r["estado_base"] == "VACACIONES").sum())
        pct_efectividad = round((n_asistio + n_tardanza) / total * 100, 1) if total else 0.0
        n_personas_evaluadas = r["dni"].nunique()
        n_incidencias = n_falta + n_vacante + n_vacaciones

        # Jornada promedio: solo dias con entrada Y salida real (asistio o
        # tardanza) -- ambas guardadas como texto "HH:MM:SS".
        con_jornada = r[r["entrada_real"].notna() & r["salida_real"].notna()].copy()
        if len(con_jornada):
            entrada_td = pd.to_timedelta(con_jornada["entrada_real"])
            salida_td = pd.to_timedelta(con_jornada["salida_real"])
            duracion = (salida_td - entrada_td).dt.total_seconds() / 3600
            duracion = duracion[(duracion > 0) & (duracion < 16)]  # descarta datos corruptos/turnos nocturnos raros
            jornada_promedio_h = duracion.mean() if len(duracion) else None
        else:
            jornada_promedio_h = None
        if jornada_promedio_h is not None:
            horas = int(jornada_promedio_h)
            minutos = int(round((jornada_promedio_h - horas) * 60))
            jornada_promedio = f"{horas}h {minutos:02d}m"
        else:
            jornada_promedio = "—"

        tendencia = r.groupby(r["fecha"].dt.date)["estado_base"].value_counts().unstack(fill_value=0)
        for col in ["ASISTIÓ A TIEMPO", "TARDANZA", "FALTA"]:
            if col not in tendencia.columns:
                tendencia[col] = 0
        tendencia = tendencia.sort_index()
        max_tendencia = max(1, int(tendencia[["FALTA", "TARDANZA"]].to_numpy().max())) if len(tendencia) else 1
        serie_tendencia = [
            {"dia": d.strftime("%d/%m"), "falta": int(fila["FALTA"]), "tardanza": int(fila["TARDANZA"])}
            for d, fila in tendencia.iterrows()
        ]

        por_canal = (
            r.groupby("canal")["estado_base"]
            .apply(lambda s: round((s.isin(["ASISTIÓ A TIEMPO", "TARDANZA"])).sum() / len(s) * 100, 1))
            .sort_values(ascending=False)
        )

        resumen_persona = r.groupby(["dni", "nombre"]).agg(
            dias=("estado_base", "size"),
            falta=("estado_base", lambda s: int((s == "FALTA").sum())),
            tardanza=("estado_base", lambda s: int((s == "TARDANZA").sum())),
        ).reset_index()
        top_falta = (
            resumen_persona[resumen_persona["falta"] > 0]
            .sort_values(["falta", "tardanza"], ascending=False).head(8).to_dict("records")
        )
        top_tardanza = (
            resumen_persona[resumen_persona["tardanza"] > 0]
            .sort_values(["tardanza", "falta"], ascending=False).head(8).to_dict("records")
        )

        # "Tiene reemplazo - {motivo cese}" no es un motivo de falta -- es el
        # reporte de baja/reemplazo que un supervisor manda desde la app
        # móvil (ver reporte_diario_9am.py::comentarios_supervisor_dia()),
        # que queda como comentario en los días que la persona sale Falta
        # hasta que se procesa el reemplazo. No cuenta como "motivo".
        faltas_sin_reemplazo = r[
            (r["estado_base"] == "FALTA")
            & ~r["comentario"].astype(str).str.strip().str.lower().str.startswith("tiene reemplazo")
        ]
        faltas_por_motivo = faltas_sin_reemplazo["comentario"].apply(_motivo_limpio).value_counts().head(8)

        # Agrupa los días VACACIONES sueltos en "viajes" contiguos por
        # persona y calcula la duración en días CALENDARIO (salida -> regreso
        # a marcar), no solo el conteo de filas VACACIONES -- el motor no
        # genera fila para domingos/feriados dentro del periodo, así que
        # contar filas subestimaba la duración real (pedido explícito:
        # "si salio el 10.08 y regreso el 17.08 a marcar, salio de
        # vacaciones 7 dias", contando domingos de por medio).
        vacaciones_rows = r[r["estado_base"] == "VACACIONES"][["dni", "nombre", "fecha"]].copy()
        vacaciones_rows["fecha"] = vacaciones_rows["fecha"].dt.date

        if len(vacaciones_rows):
            feriados_set = {f for (f,) in dim_session.query(Feriado.fecha).all()}

            def _no_habil(d):
                return d.weekday() == 6 or d in feriados_set

            viajes = []
            for dni, grupo in vacaciones_rows.groupby("dni"):
                nombre = grupo["nombre"].iloc[0]
                fechas = sorted(grupo["fecha"].unique())
                inicio = fin = fechas[0]
                for f in fechas[1:]:
                    # Si todos los días entre el último VACACIONES y este son
                    # domingo/feriado (por eso no tienen fila propia), es el
                    # mismo viaje -- si hay un día hábil de por medio sin fila
                    # VACACIONES, es que volvió y salió de nuevo: viaje nuevo.
                    d = fin + dt.timedelta(days=1)
                    puente_no_habil = True
                    while d < f:
                        if not _no_habil(d):
                            puente_no_habil = False
                            break
                        d += dt.timedelta(days=1)
                    if puente_no_habil:
                        fin = f
                    else:
                        viajes.append({"dni": dni, "nombre": nombre, "inicio": inicio, "fin": fin})
                        inicio = fin = f
                viajes.append({"dni": dni, "nombre": nombre, "inicio": inicio, "fin": fin})

            # Fecha real de regreso (primer día con CUALQUIER clasificación
            # después del último día de VACACIONES del viaje) -- se busca más
            # allá de "hasta" también, porque si el periodo elegido corta en
            # medio de las vacaciones, el regreso real puede caer después.
            dnis_con_viaje = {v["dni"] for v in viajes}
            fechas_por_dni = {}
            for dni, fecha in r[r["dni"].isin(dnis_con_viaje)][["dni", "fecha"]].itertuples(index=False):
                fechas_por_dni.setdefault(dni, set()).add(fecha.date())
            filas_futuras = (
                dim_session.query(ClasificacionDiaria.dni, ClasificacionDiaria.fecha)
                .filter(ClasificacionDiaria.dni.in_(dnis_con_viaje), ClasificacionDiaria.fecha > hasta)
                .all()
            )
            for dni, fecha in filas_futuras:
                fechas_por_dni.setdefault(dni, set()).add(fecha)

            for v in viajes:
                candidatas = sorted(f for f in fechas_por_dni.get(v["dni"], set()) if f > v["fin"])
                if candidatas:
                    v["regreso"] = candidatas[0].strftime("%d/%m/%Y")
                    v["dias"] = (candidatas[0] - v["inicio"]).days
                else:
                    v["regreso"] = None
                    v["dias"] = None
                v["inicio"] = v["inicio"].strftime("%d/%m/%Y")
                del v["fin"], v["dni"]

            vacaciones_detalle = sorted(
                viajes, key=lambda v: dt.datetime.strptime(v["inicio"], "%d/%m/%Y"), reverse=True
            )
        else:
            vacaciones_detalle = []

        # Dias marcados VACANTE dentro del periodo elegido -- antes solo
        # miraba Personas que HOY siguen con estado="Vacante" en el Maestro,
        # asi que una vacante ya cubierta durante el periodo no aparecia
        # aunque si hubiera tenido dias vacante reales.
        vacantes_detalle = (
            r[r["estado_base"] == "VACANTE"].groupby(["dni", "nombre"]).size()
            .reset_index(name="dias").sort_values("dias", ascending=False)
            .to_dict("records")
        )

        data = {
            "periodo": {"desde": desde.strftime("%d/%m/%Y"), "hasta": hasta.strftime("%d/%m/%Y")},
            "resumen_hoy": resumen_hoy,
            "resumen": {
                "total": total, "asistio": n_asistio, "tardanza": n_tardanza, "falta": n_falta,
                "vacante": n_vacante, "vacaciones": n_vacaciones, "pct_efectividad": pct_efectividad,
            },
            "kpis": {
                "headcount_actual": headcount_actual, "personas_evaluadas": int(n_personas_evaluadas),
                "incidencias": n_incidencias, "jornada_promedio": jornada_promedio,
            },
            "tendencia": serie_tendencia,
            "max_tendencia": max_tendencia,
            "efectividad_por_canal": list(por_canal.items()),
            "faltas_por_motivo": list(faltas_por_motivo.items()),
            "top_falta": top_falta,
            "top_tardanza": top_tardanza,
            "vacaciones_detalle": vacaciones_detalle,
            "vacantes_detalle": vacantes_detalle,
        }
        return render_template(
            "dashboard.html", usuario=current_user, hay_datos=True, data=data,
            periodo_desde=desde, periodo_hasta=hasta, periodo_args=periodo_args, presets=presets,
        )

    @app.get("/personal")
    @login_required
    def personal():
        # Fase 2: primera pantalla que lee directo de Postgres (dimension_models,
        # las mismas tablas que migrar_dimensiones_a_postgres.py pobló) --
        # solo lectura por ahora, el alta/baja/reemplazo sigue siendo
        # agregar_reemplazo.py (CLI local) hasta que haya un formulario acá.
        dim_session = get_dim_session()
        try:
            query = dim_session.query(Persona)
            cond_scope = condicion_scope(Persona, current_user)
            if cond_scope is not None:
                query = query.filter(cond_scope)
            personas = query.order_by(Persona.estado, Persona.nombre_completo).all()
        finally:
            dim_session.close()
        return render_template("personal.html", usuario=current_user, personas=personas)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
