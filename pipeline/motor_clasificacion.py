# -*- coding: utf-8 -*-
"""
Fase 6: version portable de MAC/motor_clasificacion_diaria.py -- `clasificar_dia()`
y todas las constantes de negocio se copian TAL CUAL (ni una linea de cambio,
misma disciplina que motor_clasificacion_shadow.py en Fase 3: nunca reescribir
la funcion probada). Dos cambios reales de diseno para poder correr en un
runner de GitHub Actions (sin disco persistente entre corridas):

1. El "ya procesado" (logica append-only) se lee de Postgres
   (clasificacion_diaria) en vez de volver a descargar el Excel completo por
   Graph -- Postgres ya tiene el historico completo (backfill de Fase 3) y
   es el mismo store al que este script escribe, asi que no hace falta el
   patron fragil de "descargar, parchear, resubir".
2. Ya NO escribe el Excel append-only original -- en su lugar sube un
   snapshot de auditoria a SharePoint (mismo patron que
   mac_cloud/exportar_dimensiones.py), a una ruta APARTE de la de produccion
   mientras dure la validacion en paralelo (ver RUTA_GRAPH_SALIDA abajo).

Visitas/*.xlsx se sigue leyendo del directorio de trabajo actual, igual que
el original -- en la nube, un paso previo del pipeline (usa
pipeline/athena_client.py) los deja ahi antes de correr este script.
"""
import glob
import os
import sys

import pandas as pd
from sqlalchemy import func

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dimension_models import Persona, get_session  # noqa: E402
from fact_models import ClasificacionDiaria, crear_tablas  # noqa: E402
from graph_client import leer_excel, subir_creando_si_no_existe  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from historial_cambios import cargar_historial, valor_efectivo  # noqa: E402

RUTA_GRAPH_MAC = "ASISTENCIA/MAC/"
# Mientras dure la validacion en paralelo (ver plan Fase 6), el snapshot de
# auditoria va a una ruta DISTINTA de la de produccion -- nunca pisa
# 7_Clasificacion_Diaria.xlsx hasta el corte manual.
NOMBRE_SALIDA_AUDITORIA = "_nube_7_Clasificacion_Diaria.xlsx"


def fmt_hora(td):
    if td is None or pd.isna(td):
        return None
    total_seg = int(td.total_seconds())
    h, resto = divmod(total_seg, 3600)
    m, s = divmod(resto, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


GEOFENCE_MAX_M = 200
TOLERANCIA_MIN = 15
DIAS_REPROCESO = 10

TIPO_NEGOCIO_A_CANAL = {
    "TRADICIONAL": "Tradicional", "PUESTO DE MERCADO": "Tradicional", "TIENDA": "Tradicional",
    "A DOMICILIO": "Tradicional", "OFICINA": "Tradicional", "LIBRERÍA": "Tradicional",
    "AUTOSERVICIOS": "Autoservicio", "CASH & CARRY": "Autoservicio",
    "MINIMERCADO": "Autoservicio", "MINIMARKET": "Autoservicio",
    "FARMACIA": "Farmacia",
}
CIERRE_AUTOMATICO = pd.Timedelta(hours=23, minutes=30, seconds=0)


def _bool_si_no(valor):
    return str(valor).strip().upper() == "SÍ"


def _upsert_postgres(session, existentes_por_clave, fila):
    def _limpio(v):
        return None if pd.isna(v) else v

    dni, fecha = fila["DNI"], fila["Fecha"]
    fila_bd = dict(
        dni=dni, fecha=fecha, dia_semana=fila["Día"], canal_esperado=fila["Canal esperado (Patrón)"],
        canales_marcados=_limpio(fila["Canal(es) marcado(s)"]), entrada_esperada=fila["Entrada esperada"],
        entrada_real=_limpio(fila["Entrada real"]), salida_esperada=fila["Salida esperada"], salida_real=_limpio(fila["Salida real"]),
        estado=fila["Estado"], salida_anticipada_min=_limpio(fila["Salida anticipada (min)"]),
        trabajo_otro_canal=_bool_si_no(fila["Trabajó para otro canal"]),
        alerta_geofence=_bool_si_no(fila["Alerta geofence (solo Punto Censo/fuera de rango)"]),
        fuente_dato=fila["Fuente del dato"], comentario_supervisor=_limpio(fila["Comentario supervisor"]),
        alerta_analista=_bool_si_no(fila["Alerta para analista"]),
        # server_default=func.now() de la columna solo dispara en el INSERT
        # original -- sin esto, una fila ya existente (el caso normal en
        # cada corrida despues de la primera del dia) nunca actualizaba
        # procesado_en, aunque su entrada_real/estado si se refrescaban.
        # Resultado: "Datos hasta las X" en el reporte quedaba pegado a la
        # hora de la primera corrida del dia, mostrando una hora mas vieja
        # que datos que en realidad ya estaban frescos.
        procesado_en=func.now(),
    )
    existente = existentes_por_clave.get((dni, fecha))
    if existente:
        for k, val in fila_bd.items():
            setattr(existente, k, val)
    else:
        nuevo = ClasificacionDiaria(**fila_bd)
        session.add(nuevo)
        existentes_por_clave[(dni, fecha)] = nuevo


def clasificar_dia(dni, nombre, fecha, weekday, pat, col_ent, col_sal, col_canal_dia, v, registro_sup, idx_historial):
    """Calcula la fila de un dia-persona. Copiada TAL CUAL de
    MAC/motor_clasificacion_diaria.py -- no tocar sin revisar el original
    primero (ver docstring del modulo)."""
    entrada_esp = valor_efectivo(idx_historial, dni, "Hora entrada programada", fecha, pat[col_ent])
    salida_esp = valor_efectivo(idx_historial, dni, "Hora salida programada", fecha, pat[col_sal])
    canal_esp_norm = str(valor_efectivo(idx_historial, dni, "Canal del día", fecha, pat[col_canal_dia])).strip()

    visitas_dia = v[(v["nro_documento"] == dni) & (v["fecha_inicio_dt"] == fecha)]
    visitas_validas = visitas_dia[visitas_dia["geofence_ok"]]
    salida_anticipada = None
    alerta_analista = False
    fuente = "Aplicativo"
    alerta_geofence = len(visitas_dia) > 0 and len(visitas_validas) == 0

    if len(visitas_dia) == 0:
        sin_marcacion_valida = True
        entrada_real = salida_real = None
        canales_marcados = []
        estado_base = "FALTA (sin marcación)"
    else:
        sin_marcacion_valida = False
        entrada_real = visitas_dia["hora_inicio_td"].min()
        fuente_salida = visitas_validas if len(visitas_validas) else visitas_dia
        canales_marcados = sorted(fuente_salida["canal_visita"].unique())
        hora_fin_confiable = visitas_dia.loc[visitas_dia["hora_fin_td"] != CIERRE_AUTOMATICO, "hora_fin_td"].dropna()
        salida_real = hora_fin_confiable.max() if len(hora_fin_confiable) else fuente_salida["hora_inicio_td"].max()
        entrada_esp_td = pd.to_timedelta(str(entrada_esp))
        salida_esp_td = pd.to_timedelta(str(salida_esp))
        marcas_tardias_tf = visitas_dia[
            (visitas_dia["hora_fin_td"] == CIERRE_AUTOMATICO)
            & (visitas_dia["hora_inicio_td"] > salida_real)
            & (visitas_dia["canal_visita"].isin(["Tradicional", "Farmacia"]))
        ]
        if len(marcas_tardias_tf):
            salida_real = marcas_tardias_tf["hora_inicio_td"].max() + pd.Timedelta(minutes=20)
        marcas_tardias_auto = visitas_dia[
            (visitas_dia["hora_fin_td"] == CIERRE_AUTOMATICO)
            & (visitas_dia["hora_inicio_td"] > salida_real)
            & (visitas_dia["canal_visita"] == "Autoservicio")
        ]
        if len(marcas_tardias_auto):
            salida_real = salida_esp_td + pd.Timedelta(hours=1)
        if salida_real > salida_esp_td + pd.Timedelta(hours=1):
            salida_real = salida_esp_td + pd.Timedelta(hours=1)
        diff_entrada_min = (entrada_real - entrada_esp_td).total_seconds() / 60
        diff_salida_min = (salida_esp_td - salida_real).total_seconds() / 60
        estado_base = f"TARDANZA ({diff_entrada_min:.0f} min)" if diff_entrada_min > TOLERANCIA_MIN else "ASISTIÓ A TIEMPO"
        if diff_salida_min > 0:
            salida_anticipada = round(diff_salida_min)

    comentario_sup = None
    if registro_sup is not None:
        comentario_sup = registro_sup.get("Comentario")
        hora_ent_corr = registro_sup.get("Hora entrada corregida")
        hora_sal_corr = registro_sup.get("Hora salida corregida")
        tiene_correccion_hora = pd.notna(hora_ent_corr) or pd.notna(hora_sal_corr)

        comentario_sup_norm = str(comentario_sup).upper() if pd.notna(comentario_sup) else ""

        if tiene_correccion_hora:
            entrada_esp_td = pd.to_timedelta(str(entrada_esp))
            salida_esp_td = pd.to_timedelta(str(salida_esp))
            if pd.notna(hora_ent_corr):
                entrada_real = pd.to_timedelta(str(hora_ent_corr))
            if pd.notna(hora_sal_corr):
                salida_real = pd.to_timedelta(str(hora_sal_corr))
            # Corrección parcial (solo entrada O solo salida, día sin
            # marcación real de la app -- entrada_real/salida_real arrancan
            # en None): cada diff se calcula solo si ese lado tiene un valor
            # real, si no "salida_esp_td - None" tira TypeError y aborta
            # toda la corrida del motor para ese día.
            if pd.notna(entrada_real):
                diff_entrada_min = (entrada_real - entrada_esp_td).total_seconds() / 60
                estado_base = f"TARDANZA ({diff_entrada_min:.0f} min)" if diff_entrada_min > TOLERANCIA_MIN else "ASISTIÓ A TIEMPO"
            if pd.notna(salida_real):
                diff_salida_min = (salida_esp_td - salida_real).total_seconds() / 60
                salida_anticipada = round(diff_salida_min) if diff_salida_min > 0 else None
            sin_marcacion_valida = False
            fuente = "Corregido manualmente (Tabla 3)"
            if comentario_sup_norm.startswith((
                "ASISTIÓ (REPORTADO POR SUPERVISOR",
                "CORRECCIÓN DESDE REPORTE DIARIO 9AM",
                "SALIDA NO REGISTRADA",
            )) or comentario_sup_norm in ("ASISTIÓ", "FALTÓ (SIN MOTIVO REGISTRADO)"):
                comentario_sup = None
        elif comentario_sup_norm.startswith("FALTA") and "VACANTE" in comentario_sup_norm and sin_marcacion_valida:
            estado_base = "VACANTE (comentario supervisor)"
            fuente = "Aplicativo (con comentario de supervisor)"
        elif comentario_sup_norm.startswith("FALTA") and "VACACIONES" in comentario_sup_norm and sin_marcacion_valida:
            estado_base = "VACACIONES (comentario supervisor)"
            fuente = "Aplicativo (con comentario de supervisor)"
        elif comentario_sup_norm.startswith("FALTA"):
            estado_base = "FALTA (comentario supervisor)"
            fuente = "Aplicativo (con comentario de supervisor)"
            salida_anticipada = None
        elif sin_marcacion_valida and "VACANTE" in comentario_sup_norm:
            estado_base = "VACANTE (comentario supervisor)"
            fuente = "Aplicativo (con comentario de supervisor)"
        elif sin_marcacion_valida and "VACACIONES" in comentario_sup_norm:
            estado_base = "VACACIONES (comentario supervisor)"
            fuente = "Aplicativo (con comentario de supervisor)"
        elif sin_marcacion_valida and not pd.isna(registro_sup.get("Estado reportado")) and str(registro_sup.get("Estado reportado")).strip():
            estado_reportado = str(registro_sup.get("Estado reportado")).strip()
            estado_base = f"{estado_reportado.upper()} (según supervisor, sin marcación app)"
            fuente = "Supervisor (sin marcación app)"
            alerta_analista = True
        elif sin_marcacion_valida:
            fuente = "Aplicativo (con comentario de supervisor)"
            alerta_analista = True
        else:
            fuente = "Aplicativo (con comentario de supervisor)"

    if sin_marcacion_valida and fuente == "Aplicativo":
        alerta_analista = True

    trabajo_otro_canal = len(canales_marcados) > 0 and canal_esp_norm not in canales_marcados

    return {
        "DNI": dni, "Nombre": nombre, "Fecha": fecha.date() if hasattr(fecha, "date") else fecha,
        "Día": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"][weekday],
        "Canal esperado (Patrón)": canal_esp_norm,
        "Canal(es) marcado(s)": ", ".join(canales_marcados) if canales_marcados else None,
        "Entrada esperada": str(entrada_esp), "Entrada real": fmt_hora(entrada_real),
        "Salida esperada": str(salida_esp), "Salida real": fmt_hora(salida_real),
        "Estado": estado_base,
        "Salida anticipada (min)": salida_anticipada,
        "Trabajó para otro canal": "SÍ" if trabajo_otro_canal else "NO",
        "Alerta geofence (solo Punto Censo/fuera de rango)": "SÍ" if alerta_geofence else "NO",
        "Fuente del dato": fuente,
        "Comentario supervisor": comentario_sup,
        "Alerta para analista": "SÍ" if alerta_analista else "NO",
    }


def _cargar_existente_desde_postgres():
    """Reemplaza a la lectura del Excel append-only -- misma forma
    (DataFrame con las mismas columnas), pero la fuente es Postgres."""
    session = get_session()
    try:
        filas = (
            session.query(ClasificacionDiaria, Persona.nombre_completo)
            .join(Persona, Persona.dni == ClasificacionDiaria.dni)
            .all()
        )
    finally:
        session.close()

    registros = [{
        "DNI": c.dni, "Nombre": nombre, "Fecha": c.fecha, "Día": c.dia_semana,
        "Canal esperado (Patrón)": c.canal_esperado, "Canal(es) marcado(s)": c.canales_marcados,
        "Entrada esperada": c.entrada_esperada, "Entrada real": c.entrada_real,
        "Salida esperada": c.salida_esperada, "Salida real": c.salida_real,
        "Estado": c.estado, "Salida anticipada (min)": c.salida_anticipada_min,
        "Trabajó para otro canal": "SÍ" if c.trabajo_otro_canal else "NO",
        "Alerta geofence (solo Punto Censo/fuera de rango)": "SÍ" if c.alerta_geofence else "NO",
        "Fuente del dato": c.fuente_dato, "Comentario supervisor": c.comentario_supervisor,
        "Alerta para analista": "SÍ" if c.alerta_analista else "NO",
    } for c, nombre in filas]
    return pd.DataFrame(registros)


def _sincronizar_postgres(res, claves_recalculadas):
    crear_tablas()
    session = get_session()
    try:
        existentes = session.query(ClasificacionDiaria).all()
        existentes_por_clave = {(row.dni, row.fecha): row for row in existentes}
        res_por_clave = {(r["DNI"], r["Fecha"]): r for r in res.to_dict("records")}

        for clave in claves_recalculadas:
            fila = res_por_clave.get(clave)
            if fila is None:
                continue
            _upsert_postgres(session, existentes_por_clave, fila)

        claves_finales = set(res_por_clave.keys())
        n_eliminadas = 0
        for clave in list(existentes_por_clave.keys()):
            if clave not in claves_finales:
                session.delete(existentes_por_clave[clave])
                n_eliminadas += 1

        session.commit()
        print(f"Postgres (clasificacion_diaria) sincronizado: {len(claves_recalculadas)} filas escritas, {n_eliminadas} eliminadas.")
    finally:
        session.close()


def _escribir_snapshot_auditoria(res):
    """Snapshot completo (no append-only) a SharePoint, solo para que Davor
    lo pueda revisar -- Postgres es la fuente de verdad, esto es un espejo
    legible. No debe poder tumbar el pipeline."""
    try:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Clasificacion Diaria"
        ws.append(list(res.columns))
        for _, row in res.iterrows():
            ws.append([None if pd.isna(v) else v for v in row])
        subir_creando_si_no_existe(RUTA_GRAPH_MAC + NOMBRE_SALIDA_AUDITORIA, wb)
        print(f"Snapshot de auditoría subido: {NOMBRE_SALIDA_AUDITORIA}")
    except Exception as e:
        print(f"AVISO: no se pudo subir el snapshot de auditoría -- {e}")


def main():
    dfs = []
    for f in sorted(glob.glob("Visitas/*.xlsx")):
        d = pd.read_excel(f)
        d["_archivo"] = f.split("\\")[-1].split("/")[-1]
        dfs.append(d)
    v = pd.concat(dfs, ignore_index=True)
    v["nro_documento"] = v["nro_documento"].astype(str).str.strip()

    dedup_keys = ["nro_documento", "punto_venta_id", "fecha_inicio", "hora_inicio", "fecha_fin", "hora_fin"]
    before = len(v)
    v = v.drop_duplicates(subset=dedup_keys, keep="first")
    print(f"Visitas: {before} -> {len(v)} tras deduplicar ({before - len(v)} duplicados exactos removidos)")

    v["fecha_inicio_dt"] = pd.to_datetime(v["fecha_inicio"], format="%d-%m-%Y", errors="coerce")
    v["hora_inicio_td"] = pd.to_timedelta(v["hora_inicio"].astype(str), errors="coerce")
    v["hora_fin_td"] = pd.to_timedelta(v["hora_fin"].astype(str), errors="coerce")
    v["geofence_ok"] = v["distancia_metros_inicio"] <= GEOFENCE_MAX_M
    v["canal_visita"] = v["tipo_negocio"].map(TIPO_NEGOCIO_A_CANAL).fillna("Otro")

    n_cierre_auto = (v["hora_fin_td"] == CIERRE_AUTOMATICO).sum()
    if n_cierre_auto:
        print(f"Aviso: {n_cierre_auto} visitas con cierre automático (hora_fin=23:30:00) detectadas y excluidas del cálculo de salida real")

    sin_mapear = v[v["canal_visita"] == "Otro"]
    if len(sin_mapear):
        print(f"Aviso: {len(sin_mapear)} visitas con tipo_negocio sin mapear a canal: {sin_mapear['tipo_negocio'].unique()}")

    m = leer_excel("ASISTENCIA/MAC/1_Maestro_Headcount.xlsx", sheet_name="Maestro Headcount", header=3).dropna(how="all")
    m.columns = [str(c).strip() for c in m.columns]
    m = m.rename(columns={m.columns[0]: "DNI"})
    # Mismo gotcha que parseo_headcount.py -- si ALGUNA fila de DNI viene
    # vacía, pandas sube toda la columna a float64 y sobrevive al filtro,
    # dejando "18074336.0" en vez de "18074336" para todos los DNIs válidos.
    dni_num = pd.to_numeric(m["DNI"], errors="coerce")
    m = m[dni_num.notna()].copy()
    m["DNI"] = dni_num[dni_num.notna()].astype("int64").astype(str)
    activos = m[m["Estado"].astype(str).str.strip() == "Activo"].copy()

    p = leer_excel("ASISTENCIA/MAC/2A_Patron_Recurrente.xlsx", sheet_name="Patrón recurrente", header=3).dropna(how="all")
    p.columns = [str(c).strip() for c in p.columns]
    p = p.rename(columns={p.columns[0]: "DNI", p.columns[1]: "Dia"})
    p["DNI"] = p["DNI"].astype(str).str.strip()
    p["Dia"] = p["Dia"].astype(str).str.strip().str.lower()
    dia_map = {"lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2, "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5}
    p["weekday"] = p["Dia"].map(dia_map)
    col_ent = [c for c in p.columns if "entrada" in c.lower()][0]
    col_sal = [c for c in p.columns if "salida" in c.lower()][0]
    col_canal_dia = [c for c in p.columns if "canal" in c.lower()][0]
    p_idx = p.set_index(["DNI", "weekday"])

    feriados = leer_excel("ASISTENCIA/MAC/5_Feriados_2026.xlsx", sheet_name="Feriados 2026", header=3)
    feriados_set = set(pd.to_datetime(feriados["Fecha"], format="%d/%m/%Y", dayfirst=True).dt.date)

    sup = leer_excel("ASISTENCIA/MAC/3_Registro_Diario_Supervisor.xlsx", sheet_name="Registro diario supervisor", header=3).dropna(how="all")
    sup.columns = [str(c).strip() for c in sup.columns]
    sup = sup.rename(columns={sup.columns[0]: "DNI"})
    # Mismo gotcha que parseo_headcount.py -- ver comentario más arriba.
    dni_num_sup = pd.to_numeric(sup["DNI"], errors="coerce")
    sup = sup[dni_num_sup.notna()].copy()
    sup["DNI"] = dni_num_sup[dni_num_sup.notna()].astype("int64").astype(str)
    sup["Fecha_dt"] = pd.to_datetime(sup["Fecha"], errors="coerce").dt.date
    sup_idx = {}
    for (dni_sup, fecha_sup), grupo in sup.groupby(["DNI", "Fecha_dt"]):
        if len(grupo) == 1:
            sup_idx[(dni_sup, fecha_sup)] = grupo.iloc[0]
            continue
        fusion = grupo.iloc[-1].copy()
        for col in grupo.columns:
            no_nulos = grupo[col].dropna()
            if len(no_nulos):
                fusion[col] = no_nulos.iloc[-1]
        sup_idx[(dni_sup, fecha_sup)] = fusion

    idx_historial = cargar_historial()
    rangos_por_dni = {}
    for (dni_h, _campo), entradas in idx_historial.items():
        rangos_por_dni.setdefault(dni_h, []).extend((fd, fh, dia) for fd, fh, _v, dia in entradas)

    def tiene_cambio_historial(dni, fecha_date):
        for fd, fh, dia_semana in rangos_por_dni.get(dni, []):
            if fd <= fecha_date and (pd.isna(fh) or fh >= fecha_date) and (pd.isna(dia_semana) or dia_semana == fecha_date.weekday()):
                return True
        return False

    existente = _cargar_existente_desde_postgres()

    dias_ya_procesados = set(zip(existente["DNI"], existente["Fecha"])) if len(existente) else set()
    dias_con_correccion = set(sup_idx.keys())

    dnis_activos = set(activos["DNI"])
    dnis_inactivos_con_correccion = {dni for dni, _fecha in dias_con_correccion if dni not in dnis_activos}
    personas_a_procesar = activos
    if dnis_inactivos_con_correccion:
        personas_a_procesar = pd.concat(
            [activos, m[m["DNI"].isin(dnis_inactivos_con_correccion)]], ignore_index=True,
        )

    fecha_min, fecha_max = v["fecha_inicio_dt"].min(), v["fecha_inicio_dt"].max()
    calendario = pd.date_range(fecha_min, fecha_max, freq="D")
    hoy_date = pd.Timestamp.today().normalize().date()

    resultados_nuevos = []
    filas_corregidas = 0
    filas_actualizadas_recientes = 0
    for _, persona in personas_a_procesar.iterrows():
        dni = persona["DNI"]
        fecha_ingreso = persona.get("Fecha de ingreso")
        fecha_baja = persona.get("Fecha de baja")
        for fecha in calendario:
            weekday = fecha.weekday()
            if weekday == 6 or fecha.date() in feriados_set:
                continue
            if pd.notna(fecha_ingreso) and fecha < pd.Timestamp(fecha_ingreso):
                continue
            if pd.notna(fecha_baja) and fecha > pd.Timestamp(fecha_baja):
                continue

            clave = (dni, fecha.date())
            ya_procesado = clave in dias_ya_procesados
            es_reciente = 0 <= (hoy_date - fecha.date()).days <= DIAS_REPROCESO
            tiene_correccion = clave in dias_con_correccion or tiene_cambio_historial(dni, fecha.date())
            if ya_procesado and not tiene_correccion and not es_reciente:
                continue

            key_pat = (dni, weekday)
            if key_pat not in p_idx.index:
                continue
            pat = p_idx.loc[key_pat]
            if isinstance(pat, pd.DataFrame):
                pat = pat.iloc[0]

            registro_sup = sup_idx.get(clave)

            fila = clasificar_dia(dni, persona["Nombre completo"], fecha, weekday, pat,
                                   col_ent, col_sal, col_canal_dia, v, registro_sup, idx_historial)
            resultados_nuevos.append(fila)
            if ya_procesado and tiene_correccion:
                filas_corregidas += 1
            elif ya_procesado and es_reciente:
                filas_actualizadas_recientes += 1

    nuevos_df = pd.DataFrame(resultados_nuevos)

    if len(existente):
        claves_nuevas = set(zip(nuevos_df["DNI"], nuevos_df["Fecha"])) if len(nuevos_df) else set()
        existente_sin_corregidas = existente[~existente.apply(lambda r: (r["DNI"], r["Fecha"]) in claves_nuevas, axis=1)]
        res = pd.concat([existente_sin_corregidas, nuevos_df], ignore_index=True)
    else:
        res = nuevos_df

    baja_map = m.set_index("DNI")["Fecha de baja"].to_dict()

    def excede_baja(dni, fecha):
        fb = baja_map.get(dni)
        return pd.notna(fb) and pd.Timestamp(fecha) > pd.Timestamp(fb)

    mask_excede = [excede_baja(dni, fecha) for dni, fecha in zip(res["DNI"], res["Fecha"])]
    n_excede = sum(mask_excede)
    if n_excede:
        print(f"Filas removidas por baja registrada después de esa fecha: {n_excede}")
        res = res[[not x for x in mask_excede]].reset_index(drop=True)

    res = res.sort_values(["Fecha", "DNI"]).reset_index(drop=True)

    claves_recalculadas = set(zip(nuevos_df["DNI"], nuevos_df["Fecha"])) if len(nuevos_df) else set()
    _sincronizar_postgres(res, claves_recalculadas)
    _escribir_snapshot_auditoria(res)

    print()
    print("=" * 90)
    print(f"CLASIFICACION (nube) -- append-only ({fecha_min.date()} a {fecha_max.date()})")
    print("=" * 90)
    print(f"Filas nuevas agregadas: {len(nuevos_df) - filas_corregidas - filas_actualizadas_recientes}")
    print(f"Filas corregidas (Tabla 3, dias ya existentes): {filas_corregidas}")
    print(f"Filas de los últimos {DIAS_REPROCESO} días actualizadas con marcaciones nuevas: {filas_actualizadas_recientes}")
    print(f"Total dia-persona: {len(res)}")
    print()
    print(res["Estado"].apply(lambda s: s.split(" (")[0]).value_counts().to_string())


if __name__ == "__main__":
    main()
