# -*- coding: utf-8 -*-
"""
Fase 6: version portable de MAC/etl_athena_visitas.py -- mismo `traer_visitas()`
(mismo query, mismo mapeo tipo_negocio, misma normalizacion de DNI/hora/distancia),
pero lee las credenciales de AWS de VARIABLES DE ENTORNO (AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, ATHENA_S3_STAGING, AWS_REGION) en vez de
athena_credentials.local.env -- ese archivo no existe en un runner de GitHub
Actions. Mismo patron que graph_client.py (Fase 5).

MAC/etl_athena_visitas.py (el original) sigue existiendo tal cual para lo
que corre local -- este modulo es solo para lo que corre en GitHub Actions.
"""
import math
import os

import pandas as pd
from dotenv import load_dotenv
from pyathena import connect
from pyathena.pandas.cursor import PandasCursor

load_dotenv()

CLIENTE_ID = 295
WORKGROUP = "workgroup295"

CADENAS_AUTOSERVICIO = {
    "PLAZA VEA", "METRO", "METRO ALMACENES", "TOTTUS", "WONG", "MAKRO",
    "HIPERBODEGA PRECIO UNO", "VIVANDA", "AUTOSERVICIO", "AUTOSERVICIOS MINIMERCADOS",
    "SUPERMERCADO EL SUPER", "SUPERMERCADO CUSCO", "ECONOMAX",
}
CADENAS_FARMACIA = {
    "FARMACIAS INDEPENDIENTES", "FARMACIA INDEPEND.", "BOTICAS PERU", "BOTICAS Y SALUD",
    "MIFARMA", "INKAFARMA", "HOGAR Y SALUD", "NOVAFARMA", "FARMADESA", "DAREFARMA",
    "B&S", "OTIFARMA", "JHODHALL", "SG FARMA", "FASA", "MEGABOTICAS", "AMERICA Y SALUD",
    "MINI CADENA",
}
DEFAULT_TIPO_NEGOCIO = "TRADICIONAL"

# Ubicaciones administrativas/de auditoría (oficinas, Punto Censo, Punto de
# Análisis) -- NO son un PDV real de un solo negocio, sino un punto
# compartido que visitan mercaderistas de campañas distintas. El join contra
# dim_lf_general_campana_pdv (más abajo) solo puede resolver UN campana_id
# "vigente" por punto_venta_id, así que a un punto compartido como este le
# termina asignando SIEMPRE el mismo canal (el de la campaña más reciente
# ahí), sin importar bajo qué campaña haya sido esa visita puntual -- bug
# real (Davor, 2026-08-29): "PUNTO_ANALISIS" salía Tradicional en el reporte
# aunque la visita real fue bajo la campaña Farmacia (255) de esa persona.
# Mismos nombres que ya excluye cobertura.py::PUNTOS_EXCLUIDOS (por texto,
# no por tipo_negocio) para "visita larga" -- acá se corrige en el origen
# para que tampoco salga con un canal de negocio inventado en Marcaciones/
# Cobertura.
UBICACIONES_ADMINISTRATIVAS = ("AMOF", "OVERALL", "PUNTO CENSO", "PUNTO_ANALISIS", "PUNTO ANALISIS")
TIPO_NEGOCIO_ADMINISTRATIVO = "ADMINISTRATIVO"

# Mapeo directo campana_id -> tipo_negocio (Davor, 2026-08-26) -- más
# confiable que adivinar por nombre de cadena (cadena_a_tipo_negocio() abajo),
# que sirve como respaldo solo para campañas fuera de este mapeo.
CAMPANA_ID_A_TIPO_NEGOCIO = {
    924: "TRADICIONAL", 987: "TRADICIONAL", 998: "TRADICIONAL",
    255: "FARMACIA",
    10: "AUTOSERVICIOS",
}


def cadena_a_tipo_negocio(cadena):
    c = str(cadena).strip().upper()
    if c in CADENAS_AUTOSERVICIO:
        return "AUTOSERVICIOS"
    if c in CADENAS_FARMACIA:
        return "FARMACIA"
    return DEFAULT_TIPO_NEGOCIO


def conectar():
    return connect(
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        s3_staging_dir=os.environ["ATHENA_S3_STAGING"],
        region_name=os.environ["AWS_REGION"],
        work_group=WORKGROUP,
        cursor_class=PandasCursor,
    )


def haversine_m(lat1, lon1, lat2, lon2):
    if pd.isna(lat1) or pd.isna(lon1) or pd.isna(lat2) or pd.isna(lon2):
        return None
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def parse_latlon(s):
    if pd.isna(s):
        return None, None
    try:
        lat, lon = str(s).split(",")
        return float(lat), float(lon)
    except Exception:
        return None, None


def traer_visitas(tabla, filtro_fecha_sql=""):
    conn = conectar()
    cursor = conn.cursor()
    es_diaria = tabla == "dim_lf_general_visitas_diarias"

    # dim_lf_general_campana_pdv trae UNA FILA POR CADA CAMPAÑA que un PDV
    # tuvo alguna vez (cliente_id/campana_id son particiones -- es un
    # snapshot por campaña, no un maestro con una fila por PDV), asi que un
    # JOIN directo por punto_venta_id multiplica cada visita en N filas (un
    # PDV real llegó a tener 6 campañas distintas) -- este CTE sigue
    # resolviendo ESO (quedarse con una sola fila por PDV, la actualizada
    # más recientemente) para poder traer latitud/longitud sin duplicar
    # filas.
    #
    # OJO -- campana_id de ACÁ (p.campana_id) YA NO se usa para decidir el
    # canal: era un bug real. Un mismo PDV puede estar activo bajo VARIAS
    # campañas a la vez (Davor, 2026-08-29, verificado en Athena: el mismo
    # punto_venta_id de dim_lf_general_campana_pdv sale con campana_id=255
    # "ESSITY-FARMACIA" en una fila y campana_id=1147 "FRENTE A HOSPITALES"
    # en otra), así que "la más recientemente actualizada" no es
    # necesariamente la que aplicó a ESTA visita puntual -- eso fue lo que
    # rompía UNIVERSAL-*, METRO-OVALO PAPAL, PUNTO_ANALISIS, etc. La propia
    # tabla de visitas (dim_lf_general_visitas) YA trae su campana_id
    # correcto por fila ("cuando se genera la visita, hay una columna que
    # indica el canal donde salió la visita" -- directriz explícita de
    # Davor), así que el canal se resuelve con v.campana_id más abajo, no
    # con el de este join.
    campana_vigente_cte = f"""
        WITH campana_vigente AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY punto_venta_id ORDER BY updated_at DESC) AS rn
            FROM livetradebi.dim_lf_general_campana_pdv
            WHERE cliente_id = {CLIENTE_ID}
        )
    """

    if es_diaria:
        query = f"""
            {campana_vigente_cte}
            SELECT v.dni_usuario, v.nombre_usuario, v.punto_venta_id,
                   v.fecha_inicio, v.fecha_fin, v.hora_celular AS hora_inicio_raw,
                   v.hora_llegada_servidor AS hora_fin_raw,
                   v.posicion_inicio, v.posicion_fin, v.visita_efectiva,
                   v.bateria_inicio, v.bateria_fin, NULL AS motivo_visita,
                   p.nombre_personalizado, p.cadena_personalizada, p.empresa_personalizada,
                   p.departamento, p.provincia, p.distrito, p.latitud, p.longitud, p.campana_id
            FROM livetradebi.{tabla} v
            JOIN campana_vigente p
              ON v.punto_venta_id = p.punto_venta_id AND v.cliente_id = p.cliente_id AND p.rn = 1
            WHERE v.cliente_id = {CLIENTE_ID}
            {filtro_fecha_sql}
        """
    else:
        query = f"""
            {campana_vigente_cte}
            SELECT v.dni_usuario, v.nombre_usuario, v.punto_venta_id,
                   v.fecha_inicio, v.fecha_fin, v.hora_inicio AS hora_inicio_raw,
                   v.hora_fin AS hora_fin_raw,
                   v.posicion_inicio, v.posicion_fin, v.visita_efectiva,
                   v.bateria_inicio, v.bateria_fin, v.motivo_visita,
                   v.nombre_personalizado, v.cadena_personalizada, v.empresa_personalizada,
                   v.departamento, v.provincia, v.distrito, p.latitud, p.longitud, v.campana_id
            FROM livetradebi.{tabla} v
            LEFT JOIN campana_vigente p
              ON v.punto_venta_id = p.punto_venta_id AND v.cliente_id = p.cliente_id AND p.rn = 1
            WHERE v.cliente_id = {CLIENTE_ID}
            {filtro_fecha_sql}
        """

    print("Consultando Athena...")
    df = cursor.execute(query).as_pandas()
    print(f"Filas crudas: {len(df)}")
    df["latitud"] = pd.to_numeric(df["latitud"], errors="coerce")
    df["longitud"] = pd.to_numeric(df["longitud"], errors="coerce")

    claves = ["dni_usuario", "punto_venta_id", "fecha_inicio", "fecha_fin", "hora_inicio_raw", "hora_fin_raw"]
    df = df.drop_duplicates(subset=claves, keep="first")
    print(f"Filas tras deduplicar: {len(df)}")

    lat_i, lon_i = zip(*df["posicion_inicio"].map(parse_latlon)) if len(df) else ([], [])
    df["_lat_inicio"], df["_lon_inicio"] = lat_i, lon_i
    df["distancia_metros_inicio"] = df.apply(
        lambda r: haversine_m(r["_lat_inicio"], r["_lon_inicio"], r["latitud"], r["longitud"]), axis=1
    )
    lat_f, lon_f = zip(*df["posicion_fin"].map(parse_latlon)) if len(df) else ([], [])
    df["_lat_fin"], df["_lon_fin"] = lat_f, lon_f
    df["distancia_metros_fin"] = df.apply(
        lambda r: haversine_m(r["_lat_fin"], r["_lon_fin"], r["latitud"], r["longitud"]), axis=1
    )

    # campana_id es la fuente confiable (Davor, 2026-08-26); cadena_personalizada
    # queda como respaldo solo para campañas fuera de CAMPANA_ID_A_TIPO_NEGOCIO
    # (o sin campana_id, ej. si el join no matcheó).
    campana_id_num = pd.to_numeric(df["campana_id"], errors="coerce")
    df["tipo_negocio"] = campana_id_num.map(CAMPANA_ID_A_TIPO_NEGOCIO)
    sin_campana_conocida = df["tipo_negocio"].isna()
    df.loc[sin_campana_conocida, "tipo_negocio"] = (
        df.loc[sin_campana_conocida, "cadena_personalizada"].map(cadena_a_tipo_negocio)
    )

    # Ubicación administrativa/compartida -- pisa lo que haya resuelto el
    # join de campana_id de arriba (siempre va a ser "algún" canal real,
    # pero no necesariamente el de ESTA visita puntual, ver comentario en
    # UBICACIONES_ADMINISTRATIVAS).
    patron_admin = "|".join(UBICACIONES_ADMINISTRATIVAS)
    es_administrativo = df["nombre_personalizado"].astype(str).str.upper().str.contains(patron_admin, na=False)
    df.loc[es_administrativo, "tipo_negocio"] = TIPO_NEGOCIO_ADMINISTRATIVO

    if es_diaria:
        hora_inicio_dt = pd.to_datetime(df["hora_inicio_raw"], errors="coerce")
        hora_fin_dt = pd.to_datetime(df["hora_fin_raw"], errors="coerce")
        hora_inicio_str = hora_inicio_dt.dt.strftime("%H:%M:%S")
        hora_fin_str = hora_fin_dt.dt.strftime("%H:%M:%S")
    else:
        hora_inicio_str = df["hora_inicio_raw"].astype(str)
        hora_fin_str = df["hora_fin_raw"].astype(str)

    dni_normalizado = df["dni_usuario"].astype(str).str.strip().str.lstrip("0")
    dni_normalizado = dni_normalizado.mask(dni_normalizado == "", "0")

    out = pd.DataFrame({
        "nro_documento": dni_normalizado,
        "usuario": df["nombre_usuario"],
        "punto_venta_id": df["punto_venta_id"],
        "punto_venta": df["nombre_personalizado"],
        "cadena_personalizada": df["cadena_personalizada"],
        "empresa_personalizada": df["empresa_personalizada"],
        "tipo_negocio": df["tipo_negocio"],
        "departamento": df["departamento"],
        "provincia": df["provincia"],
        "distrito": df["distrito"],
        "posicion_inicio": df["posicion_inicio"],
        "posicion_fin": df["posicion_fin"],
        "distancia_metros_inicio": df["distancia_metros_inicio"],
        "distancia_metros_fin": df["distancia_metros_fin"],
        "fecha_inicio": pd.to_datetime(df["fecha_inicio"], format="%d-%m-%Y", errors="coerce").dt.strftime("%d-%m-%Y"),
        "hora_inicio": hora_inicio_str,
        "fecha_fin": pd.to_datetime(df["fecha_fin"], format="%d-%m-%Y", errors="coerce").dt.strftime("%d-%m-%Y"),
        "hora_fin": hora_fin_str,
        "visita_efectiva": df["visita_efectiva"].map({1: "SI", 0: "NO"}),
        "bateria_inicio": df["bateria_inicio"],
        "bateria_fin": df["bateria_fin"],
        "motivo_visita": df["motivo_visita"],
    })
    return out
