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
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template
from flask_login import current_user, login_required
from sqlalchemy import text

PERU_TZ = ZoneInfo("America/Lima")  # sin horario de verano -- offset fijo UTC-5

from extensions import db, login_manager
from models import Usuario
from dimension_models import Persona
from dimension_models import get_session as get_dim_session
from fact_models import ClasificacionDiaria

load_dotenv()  # lee .env en local; en PythonAnywhere las variables se cargan desde su panel, no de este archivo


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-no-usar-en-produccion")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///dev.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    login_manager.init_app(app)

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
        # Fase 6 (2026-08-21): primer dashboard real -- consulta Postgres en
        # vivo en cada visita (clasificacion_diaria + personas), no depende
        # de dashboard_data.json (ese archivo lo genera export_dashboard_data.py
        # en cada corrida del pipeline, pero vive en SharePoint/local, no
        # aca). Deja afuera compensacion/indicador/alertas operativas (visita
        # larga, Punto Censo) -- esos datos hoy solo existen como Excel
        # transitorio dentro de una corrida del pipeline, nunca se persisten
        # en Postgres, asi que no hay de donde consultarlos en vivo.
        dim_session = get_dim_session()
        try:
            filas = (
                dim_session.query(ClasificacionDiaria.dni, Persona.nombre_completo, Persona.region,
                                   ClasificacionDiaria.fecha, ClasificacionDiaria.estado,
                                   ClasificacionDiaria.canal_esperado, ClasificacionDiaria.trabajo_otro_canal,
                                   ClasificacionDiaria.alerta_analista)
                .join(Persona, Persona.dni == ClasificacionDiaria.dni)
                .all()
            )
        finally:
            dim_session.close()

        if not filas:
            return render_template("dashboard.html", usuario=current_user, hay_datos=False)

        r = pd.DataFrame(filas, columns=[
            "dni", "nombre", "region", "fecha", "estado", "canal", "otro_canal", "alerta_analista",
        ])
        r["fecha"] = pd.to_datetime(r["fecha"])
        r["estado_base"] = r["estado"].apply(lambda s: s.split(" (")[0])
        r["region"] = r["region"].fillna("Sin región")

        # Render corre en UTC, no en hora de Peru -- "hoy" calculado con el
        # reloj del servidor puede ser un dia distinto al de Peru varias
        # horas por dia (ej. entre las 19:00 y 23:59 hora Peru, UTC ya paso
        # a "manana"). Los datos en Postgres estan en fecha calendario de
        # Peru (vienen de Athena), asi que "hoy" tiene que calcularse igual.
        hoy = pd.Timestamp(dt.datetime.now(PERU_TZ).date())
        hoy_df = r[r["fecha"] == hoy]
        resumen_hoy = {
            "asistio": int((hoy_df["estado_base"] == "ASISTIÓ A TIEMPO").sum()),
            "tardanza": int((hoy_df["estado_base"] == "TARDANZA").sum()),
            "falta": int((hoy_df["estado_base"] == "FALTA").sum()),
            "vacante": int((hoy_df["estado_base"] == "VACANTE").sum()),
            "vacaciones": int((hoy_df["estado_base"] == "VACACIONES").sum()),
            "total": int(len(hoy_df)),
        }

        total = len(r)
        n_asistio = int((r["estado_base"] == "ASISTIÓ A TIEMPO").sum())
        n_tardanza = int((r["estado_base"] == "TARDANZA").sum())
        n_falta = int((r["estado_base"] == "FALTA").sum())
        n_geofence = int(r["estado_base"].str.startswith("ALERTA").sum())
        pct_efectividad = round((n_asistio + n_tardanza) / total * 100, 1) if total else 0.0

        tendencia = r.groupby(r["fecha"].dt.date)["estado_base"].value_counts().unstack(fill_value=0)
        for col in ["ASISTIÓ A TIEMPO", "TARDANZA", "FALTA"]:
            if col not in tendencia.columns:
                tendencia[col] = 0
        tendencia = tendencia.sort_index().tail(30)  # ultimos 30 dias -- no saturar el grafico
        max_tendencia = max(1, int(tendencia[["FALTA", "TARDANZA"]].to_numpy().max()))
        serie_tendencia = [
            {"dia": d.strftime("%d/%m"), "falta": int(fila["FALTA"]), "tardanza": int(fila["TARDANZA"])}
            for d, fila in tendencia.iterrows()
        ]

        por_canal = (
            r.groupby("canal")["estado_base"]
            .apply(lambda s: round((s.isin(["ASISTIÓ A TIEMPO", "TARDANZA"])).sum() / len(s) * 100, 1))
            .sort_values(ascending=False)
        )

        n_otro_canal = int(r["otro_canal"].sum())
        n_alerta_analista = int(r["alerta_analista"].sum())

        resumen_persona = r.groupby(["dni", "nombre"]).agg(
            dias=("estado_base", "size"),
            falta=("estado_base", lambda s: int((s == "FALTA").sum())),
            tardanza=("estado_base", lambda s: int((s == "TARDANZA").sum())),
        ).reset_index()
        resumen_persona["pct_falta"] = (resumen_persona["falta"] / resumen_persona["dias"] * 100).round(1)
        top_falta = resumen_persona.sort_values(["falta", "tardanza"], ascending=False).head(6).to_dict("records")
        n_perfectas = int(((resumen_persona["falta"] == 0) & (resumen_persona["tardanza"] == 0)).sum())
        n_personas = len(resumen_persona)

        data = {
            "periodo": {"desde": r["fecha"].min().strftime("%d/%m/%Y"), "hasta": r["fecha"].max().strftime("%d/%m/%Y")},
            "resumen_hoy": resumen_hoy,
            "resumen": {
                "total": total, "asistio": n_asistio, "tardanza": n_tardanza, "falta": n_falta,
                "geofence": n_geofence, "pct_efectividad": pct_efectividad,
            },
            "tendencia": serie_tendencia,
            "max_tendencia": max_tendencia,
            "efectividad_por_canal": list(por_canal.items()),
            "alertas": {"otro_canal": n_otro_canal, "alerta_analista": n_alerta_analista, "geofence": n_geofence},
            "top_falta": top_falta,
            "asistencia_perfecta": {"n": n_perfectas, "total": n_personas},
        }
        return render_template("dashboard.html", usuario=current_user, hay_datos=True, data=data)

    @app.get("/personal")
    @login_required
    def personal():
        # Fase 2: primera pantalla que lee directo de Postgres (dimension_models,
        # las mismas tablas que migrar_dimensiones_a_postgres.py pobló) --
        # solo lectura por ahora, el alta/baja/reemplazo sigue siendo
        # agregar_reemplazo.py (CLI local) hasta que haya un formulario acá.
        dim_session = get_dim_session()
        try:
            personas = dim_session.query(Persona).order_by(Persona.estado, Persona.nombre_completo).all()
        finally:
            dim_session.close()
        return render_template("personal.html", usuario=current_user, personas=personas)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
