# -*- coding: utf-8 -*-
"""
Refresca automáticamente la hoja "MERCH" de ESSITY_HEADCOUNT.xlsx (headcount
manual que Davor mantenía a mano antes de que existiera Personal en
mac_cloud) desde la tabla `personas` de Postgres -- vive en el mismo drive
SharePoint que ASISTENCIA/MAC/ (drive "Sancela"), así que reusa las mismas
credenciales Graph, sin permisos nuevos.

REESCRITO 2026-09-23 después de un incidente real: la primera versión usaba
openpyxl (descargar todo el libro, editar celdas con la API normal, volver
a subir todo el libro) -- eso corrompió el archivo en producción, porque
`Cruce Ceses` tiene fórmulas XLOOKUP contra OTRO libro (ESSITY_CESES.xlsx,
referencia externa) y openpyxl no las preserva al volver a guardar (bug
conocido de la librería, no de este código). Excel pudo reparar el archivo,
pero de paso congeló ~1400 celdas con fórmula a valor fijo en todo MERCH, y
un segundo round-trip de openpyxl (aun sin fórmulas externas) seguía
perdiendo comentarios de celda y metadata custom del documento en las
pruebas.

Por eso este script NO abre el libro con openpyxl -- edita el .xlsx como lo
que realmente es (un .zip con archivos XML adentro), tocando por cirugía de
texto SOLO las celdas exactas de xl/worksheets/sheet1.xml (la hoja MERCH,
confirmado via xl/workbook.xml + xl/_rels/workbook.xml.rels -- si algún día
cambia el orden de hojas del archivo, HOJA_XML hay que recalcularlo, no
asumir que sigue siendo sheet1.xml) que necesita, dejando byte por byte sin
tocar TODO lo demás del paquete (las otras 5 hojas, comentarios, XML custom,
fórmulas de TT/DETALLE TRADICIONAL, estilos, etc.).

Alcance acordado con Davor (2026-09-23), a propósito NO es un reemplazo
completo del archivo:
- No toca las ~650 filas Inactivas desde 2022 que Postgres nunca tuvo
  cargadas (existen desde antes de MAC).
- Alguien Activo en Postgres que YA está en el Excel: se actualizan sus
  datos "limpios" (ciudad/región/supervisor/nombre/mercado/fecha ingreso/
  estado), pero NO "TIPO DE MERCADERISMO" ni "Canal" -- esas 2 columnas
  mezclan rol+canal+matices (COBERTURA/MAYORISTA/ASESORA/...) que Postgres
  no distingue, así que se preserva lo que ya estaba tipeado (o, en Canal,
  lo que haya quedado tras el incidente -- ya no es fórmula).
- Alguien que estaba Activo en el Excel y pasó a Inactivo en Postgres: se
  actualiza SOLO su ESTADO a "INACTIVO" (el resto de sus datos queda como
  estaba) -- así el headcount no sigue mostrando como activo a alguien que
  ya se fue. Nunca toca una fila que YA era histórica/Inactiva en el Excel.
- Alguien Activo en Postgres que NUNCA estuvo en el Excel (nuevo ingreso):
  fila nueva al final, con TIPO DE MERCADERISMO/Canal completados con la
  mejor traducción posible (best-effort, ver _tipo_mercaderismo_heuristico()/
  _canal_columna_heuristico()) y "Status" marcado "NUEVO -- revisar Canal"
  para revisión manual -- esa traducción es una aproximación, no un dato
  confiable. El DNI de una fila nueva se escribe como TEXTO (no número) para
  no arriesgarse a perder un cero a la izquierda -- mismo gotcha de siempre
  en este proyecto con pandas.to_numeric/Excel tratando el DNI como número.

Uso: python actualizar_essity_headcount.py
(usa DATABASE_URL, TENANT_ID, CLIENT_ID, CLIENT_SECRET del entorno)
"""
import datetime as dt
import io
import os
import re
import sys
import xml.etree.ElementTree as ET
import xml.sax.saxutils as saxutils
import zipfile

from dimension_models import PatronRecurrente, Persona, get_session
from excel_safety import texto_seguro_excel
from graph_client import descargar, subir_in_place

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline"))
from db_lock import adquirir_lock, liberar_lock  # noqa: E402

NOMBRE_LOCK = "actualizar_essity_headcount"
LOCK_MAX_MINUTOS = 10

RUTA_EXCEL = "ESSITY/ESSITY_HEADCOUNT.xlsx"
HOJA_XML = "xl/worksheets/sheet1.xml"  # MERCH -- ver nota arriba si esto cambia

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

COLUMNAS_ESPERADAS = [
    "CIUDAD", "REGION", "SUPERVISOR", "MERCADERISTA", "DNI",
    "TIPO DE MERCADERISMO", "Mercado", "Canal", "ESTADO", "FECHA INGRESO",
    "Cruce Ceses", "Status",
]
LETRAS = "ABCDEFGHIJKL"
COL_LETRA = dict(zip(COLUMNAS_ESPERADAS, LETRAS))

CANAL_A_TIPO_MERCADERISMO = {
    "TRADICIONAL": "TRADICIONAL", "FARMACIA": "FARMACIA",
    "AUTOSERVICIO": "MODERNO", "MULTICANAL": "MULTICANAL",
}


# ---------------------------------------------------------------- lectura --

def _cargar_shared_strings(z):
    """Lista texto por índice de xl/sharedStrings.xml -- de sólo lectura
    (nunca se reescribe este archivo, así que no hay riesgo de corromperlo)."""
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    ns = f"{{{MAIN_NS}}}"
    return ["".join(t.text or "" for t in si.iter(f"{ns}t")) for si in root.findall(f"{ns}si")]


def _valor_dni_de_celda(celda_xml, shared_strings):
    """Extrae el DNI (o cualquier texto, se reusa para encabezados) de una
    celda <c>...</c>/<c .../> tal cual viene en el XML crudo -- soporta las
    3 formas que puede tener: numero plano (filas viejas tal como las
    tipeó Excel), referencia a shared string (t="s"), o inlineStr (filas
    nuevas que agrega este mismo script).

    OJO -- Davor, DNI con cero a la izquierda (09917175, caso real): un
    DNI guardado como TEXTO (t="s" o inlineStr, justamente para no perderlo)
    NO debe pasar por int(float(...)) -- eso es solo para "numero plano"
    (sin t, Excel ya lo coaccionó a número al tipearlo -- ahí el cero ya se
    perdió hace rato, no hay nada que preservar, pero sí hay que limpiar el
    ".0" que separa pandas/Excel cuando una columna numérica tiene alguna
    fila vacía)."""
    if celda_xml is None:
        return None
    m_t = re.search(r' t="([^"]+)"', celda_xml)
    tipo = m_t.group(1) if m_t else None
    if tipo == "inlineStr":
        m = re.search(r"<t[^>]*>([^<]*)</t>", celda_xml)
        texto = saxutils.unescape((m.group(1) if m else "")).strip()
        return texto or None
    if tipo == "s":
        m = re.search(r"<v>(\d+)</v>", celda_xml)
        idx = int(m.group(1)) if m else None
        texto = shared_strings[idx] if idx is not None and idx < len(shared_strings) else ""
        texto = saxutils.unescape(texto).strip()
        return texto or None
    m = re.search(r"<v>([^<]*)</v>", celda_xml)
    texto = (m.group(1) if m else "").strip()
    if not texto:
        return None
    try:
        return str(int(float(texto)))  # "72205235" o "72205235.0" -> "72205235"
    except ValueError:
        return texto


_RE_FILA = re.compile(r'<row r="(\d+)"[^>]*>.*?</row>', re.DOTALL)


def _celda_en_fila(fila_xml, ref):
    # [^>]*? (NO codicioso) -- Davor, 2026-09-23, bug real encontrado en
    # pruebas: con [^>]* codicioso, una celda vacia autocerrada (ej.
    # <c r="C551" s="32"/>) se "comia" tambien la celda SIGUIENTE entera
    # (D551) -- el "/" de "/>" no es un ">", asi que [^>]* codicioso lo
    # consume y salta la alternativa "/>", cayendo en ">.*?</c>" que busca
    # el primer </c> que encuentre (el de la celda de al lado, no la propia).
    # No codicioso prueba la alternativa "/>" apenas puede, sin ese salto.
    m = re.search(r'<c r="%s"[^>]*?(?:/>|>.*?</c>)' % re.escape(ref), fila_xml, re.DOTALL)
    return m.group(0) if m else None


def _mapear_dnis(xml_texto, shared_strings):
    """{dni: numero_de_fila} -- primera fila si hubiera un DNI duplicado
    (no se espera, pero no debe reventar el script)."""
    dni_a_fila = {}
    for m in _RE_FILA.finditer(xml_texto):
        fila_num = int(m.group(1))
        celda_dni = _celda_en_fila(m.group(0), f"{COL_LETRA['DNI']}{fila_num}")
        dni = _valor_dni_de_celda(celda_dni, shared_strings)
        if dni:
            dni_a_fila.setdefault(dni, fila_num)
    return dni_a_fila


def _verificar_encabezados(xml_texto, shared_strings):
    fila1 = _RE_FILA.search(xml_texto)
    if not fila1 or fila1.group(1) != "1":
        raise ValueError(f"'{HOJA_XML}' no tiene una fila 1 reconocible -- revisar antes de seguir.")
    encontrados = []
    for col in COLUMNAS_ESPERADAS:
        celda = _celda_en_fila(fila1.group(0), f"{COL_LETRA[col]}1")
        encontrados.append(_valor_dni_de_celda(celda, shared_strings) if celda else None)
        # _valor_dni_de_celda sirve igual acá -- solo lee texto, el nombre es
        # por el uso principal (DNI) pero no hace nada DNI-específico.
    if encontrados != COLUMNAS_ESPERADAS:
        raise ValueError(
            f"'{HOJA_XML}' no tiene el formato esperado -- encabezados actuales: {encontrados}. "
            f"Alguien cambió las columnas a mano; hay que revisar este script antes de que siga escribiendo."
        )


# ------------------------------------------------------------- escritura --

def _excel_serial(fecha):
    return (fecha - dt.date(1899, 12, 30)).days


def _extraer_estilo(celda_xml):
    if celda_xml is None:
        return ""
    m = re.search(r' s="(\d+)"', celda_xml)
    return f' s="{m.group(1)}"' if m else ""


def _celda_texto(ref, estilo, valor):
    valor = texto_seguro_excel(valor)
    if valor is None or valor == "":
        return f'<c r="{ref}"{estilo}/>'
    return f'<c r="{ref}"{estilo} t="inlineStr"><is><t xml:space="preserve">{saxutils.escape(str(valor))}</t></is></c>'


def _celda_fecha(ref, estilo, fecha):
    if fecha is None:
        return f'<c r="{ref}"{estilo}/>'
    return f'<c r="{ref}"{estilo}><v>{_excel_serial(fecha)}</v></c>'


def _nombre_supervisor(personas_por_dni, supervisor_dni):
    sup = personas_por_dni.get(supervisor_dni) if supervisor_dni else None
    return sup.nombre_completo if sup else None


def _canal_traducido(valor):
    return CANAL_A_TIPO_MERCADERISMO.get((valor or "").strip().upper(), valor)


def _tipo_mercaderismo_heuristico(persona, canales_dia_por_dni):
    """Aproximación de "TIPO DE MERCADERISMO" para una fila NUEVA -- no
    intenta adivinar los matices reales (COBERTURA vs MAYORISTA vs
    ASESORA...), solo da un punto de partida razonable para que Davor lo
    ajuste. Patrón observado en el Excel: supervisores = "SUPERVISORES";
    rol "Asesora" = "ASESORA <canal>"; el resto = el canal solo.

    Multicanal (Davor, 2026-09-23, caso real: "BENDEZU NUÑEZ DAYASKA KATY
    seria MODERNO/TRADICIONAL") -- el Excel nunca usa la palabra "MULTICANAL"
    tal cual, lista los canales reales separados por "/" (ej.
    "MODERNO/TRADICIONAL", "TRADICIONAL/MODERNO/FARMA" ya se vieron en el
    archivo real). Se arma desde los canal_dia DISTINTOS de su Patrón
    Recurrente (la fuente real de qué canales trabaja, no un solo campo
    fijo), traducidos y ordenados alfabéticamente para que el orden sea
    predecible."""
    rol = (persona.rol or "").strip().upper()
    if (persona.canal or "").strip().upper() == "MULTICANAL":
        canales = canales_dia_por_dni.get(persona.dni) or set()
        traducidos = sorted({_canal_traducido(c) for c in canales if c})
        canal_trad = "/".join(traducidos) if traducidos else "MULTICANAL"
    else:
        canal_trad = _canal_traducido(persona.canal)
    if rol.startswith("SUPERVISOR"):
        return "SUPERVISORES"
    if rol == "ASESORA":
        return f"ASESORA {canal_trad}" if canal_trad else "ASESORA"
    return canal_trad or rol or None


def _canal_columna_heuristico(persona):
    """Aproximación de la columna "Canal" (la segunda, no Persona.canal) --
    un supervisor traduce razonablemente bien ("SUPERVISOR <canal>"), pero
    para un mercaderista regular no hay forma confiable de distinguir
    COBERTURA de MAYORISTA de ASESORA desde Postgres -- se usa el subcanal
    si existe, si no se deja vacío en vez de inventar un valor."""
    rol = (persona.rol or "").strip().upper()
    if rol.startswith("SUPERVISOR"):
        canal_trad = _canal_traducido(persona.canal)
        return f"SUPERVISOR {canal_trad}" if canal_trad else "SUPERVISOR"
    if persona.subcanal:
        return persona.subcanal.strip().upper()
    return None


def _reemplazar_celdas_en_fila(xml_texto, fila_num, cambios):
    """cambios: {col: valor_texto_o_None} -- solo columnas de tipo texto acá
    (CIUDAD/REGION/SUPERVISOR/MERCADERISTA/Mercado/ESTADO); FECHA INGRESO se
    maneja aparte por ser numérica. Devuelve el xml con SOLO esas celdas de
    esa fila reemplazadas, todo lo demás byte por byte igual."""
    m_fila = re.search(r'<row r="%d"[^>]*>.*?</row>' % fila_num, xml_texto, re.DOTALL)
    if not m_fila:
        raise ValueError(f"fila {fila_num} no encontrada en {HOJA_XML}")
    fila_nueva = m_fila.group(0)
    for col, valor in cambios.items():
        ref = f"{COL_LETRA[col]}{fila_num}"
        celda_actual = _celda_en_fila(fila_nueva, ref)
        if celda_actual is None:
            raise ValueError(f"celda {ref} no encontrada -- formato de fila inesperado")
        estilo = _extraer_estilo(celda_actual)
        if col == "FECHA INGRESO":
            nueva = _celda_fecha(ref, estilo, valor)
        else:
            nueva = _celda_texto(ref, estilo, valor)
        idx = fila_nueva.index(celda_actual)
        fila_nueva = fila_nueva[:idx] + nueva + fila_nueva[idx + len(celda_actual):]
    return xml_texto[:m_fila.start()] + fila_nueva + xml_texto[m_fila.end():]


def _estilos_de_fila(xml_texto, fila_num):
    m_fila = re.search(r'<row r="%d"[^>]*>.*?</row>' % fila_num, xml_texto, re.DOTALL)
    fila = m_fila.group(0)
    return {col: _extraer_estilo(_celda_en_fila(fila, f"{COL_LETRA[col]}{fila_num}")) for col in COLUMNAS_ESPERADAS}


def _construir_fila_nueva(fila_num, estilos, valores):
    """valores: {col: valor} -- FECHA INGRESO se trata como fecha, el resto
    como texto (incluido DNI, a propósito, para no perder ceros a la
    izquierda)."""
    celdas = []
    for col in COLUMNAS_ESPERADAS:
        ref = f"{COL_LETRA[col]}{fila_num}"
        estilo = estilos.get(col, "")
        valor = valores.get(col)
        if col == "FECHA INGRESO":
            celdas.append(_celda_fecha(ref, estilo, valor))
        else:
            celdas.append(_celda_texto(ref, estilo, valor))
    return f'<row r="{fila_num}" spans="1:12" x14ac:dyDescent="0.3">' + "".join(celdas) + "</row>"


def _actualizar_rango(xml_texto, max_row_nuevo):
    xml_texto = re.sub(r'(<dimension ref="A1:L)\d+(")', rf'\g<1>{max_row_nuevo}\g<2>', xml_texto, count=1)
    xml_texto = re.sub(r'(<autoFilter ref="A1:L)\d+(")', rf'\g<1>{max_row_nuevo}\g<2>', xml_texto, count=1)
    return xml_texto


def _reempaquetar(zip_bytes_original, hoja_xml_nueva):
    """Devuelve un .xlsx nuevo con TODOS los archivos del original, byte por
    byte iguales, salvo HOJA_XML que se reemplaza por hoja_xml_nueva."""
    z_orig = zipfile.ZipFile(io.BytesIO(zip_bytes_original))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z_nuevo:
        for info in z_orig.infolist():
            contenido = hoja_xml_nueva.encode("utf-8") if info.filename == HOJA_XML else z_orig.read(info.filename)
            z_nuevo.writestr(info, contenido)
    return buf.getvalue()


# ----------------------------------------------------------------- main --

def _procesar(zip_bytes, personas, canales_dia_por_dni):
    """Devuelve (nuevo_zip_bytes_o_None, resumen_str). None si no había
    nada que cambiar -- para no hacer un PUT innecesario. `canales_dia_por_dni`:
    {dni: {canal_dia, ...}} -- ver _tipo_mercaderismo_heuristico()."""
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    xml_texto = z.read(HOJA_XML).decode("utf-8")
    shared_strings = _cargar_shared_strings(z)

    _verificar_encabezados(xml_texto, shared_strings)
    dni_a_fila = _mapear_dnis(xml_texto, shared_strings)
    personas_por_dni = {p.dni: p for p in personas}

    max_fila = max(int(m.group(1)) for m in _RE_FILA.finditer(xml_texto))
    filas_nuevas_xml = []
    actualizados = pasados_a_inactivo = nuevos = 0

    for persona in personas:
        fila_num = dni_a_fila.get(persona.dni)

        if fila_num is not None:
            if persona.estado == "Activo":
                xml_texto = _reemplazar_celdas_en_fila(xml_texto, fila_num, {
                    "CIUDAD": persona.ciudad, "REGION": persona.region,
                    "SUPERVISOR": _nombre_supervisor(personas_por_dni, persona.supervisor_dni),
                    "MERCADERISTA": persona.nombre_completo, "Mercado": persona.zona,
                    "ESTADO": "ACTIVO", "FECHA INGRESO": persona.fecha_ingreso,
                })
                actualizados += 1
            elif persona.estado == "Inactivo":
                celda_estado = _celda_en_fila(
                    re.search(r'<row r="%d"[^>]*>.*?</row>' % fila_num, xml_texto, re.DOTALL).group(0),
                    f"{COL_LETRA['ESTADO']}{fila_num}",
                )
                estado_actual = (_valor_dni_de_celda(celda_estado, shared_strings) or "").upper()
                if estado_actual == "ACTIVO":
                    xml_texto = _reemplazar_celdas_en_fila(xml_texto, fila_num, {"ESTADO": "INACTIVO"})
                    pasados_a_inactivo += 1
            # estado == "Vacante": no es una persona real, se ignora.
            continue

        if persona.estado != "Activo":
            continue  # nuevo pero no Activo -- no hace falta agregarlo

        max_fila += 1
        estilos = _estilos_de_fila(xml_texto, max_fila - 1) if filas_nuevas_xml or max_fila > 1 else {}
        valores = {
            "CIUDAD": persona.ciudad, "REGION": persona.region,
            "SUPERVISOR": _nombre_supervisor(personas_por_dni, persona.supervisor_dni),
            "MERCADERISTA": persona.nombre_completo, "DNI": persona.dni,
            "TIPO DE MERCADERISMO": _tipo_mercaderismo_heuristico(persona, canales_dia_por_dni),
            "Mercado": persona.zona, "Canal": _canal_columna_heuristico(persona),
            "ESTADO": "ACTIVO", "FECHA INGRESO": persona.fecha_ingreso,
            "Status": "NUEVO -- revisar Canal",
        }
        filas_nuevas_xml.append(_construir_fila_nueva(max_fila, estilos, valores))
        dni_a_fila[persona.dni] = max_fila
        nuevos += 1

    if not (actualizados or pasados_a_inactivo or nuevos):
        return None, "Sin cambios -- nada que actualizar."

    if filas_nuevas_xml:
        idx = xml_texto.index("</sheetData>")
        xml_texto = xml_texto[:idx] + "".join(filas_nuevas_xml) + xml_texto[idx:]
        xml_texto = _actualizar_rango(xml_texto, max_fila)

    nuevo_zip = _reempaquetar(zip_bytes, xml_texto)
    resumen = (
        f"ESSITY_HEADCOUNT / MERCH: {actualizados} actualizados, "
        f"{pasados_a_inactivo} pasados a INACTIVO, {nuevos} nuevos agregados (revisar Canal)."
    )
    return nuevo_zip, resumen


def main():
    session = get_session()
    try:
        personas = session.query(Persona).all()
        canales_dia_por_dni = {}
        for dni, canal_dia in session.query(PatronRecurrente.dni, PatronRecurrente.canal_dia).all():
            if canal_dia:
                canales_dia_por_dni.setdefault(dni, set()).add(canal_dia)
    finally:
        session.close()

    zip_bytes = descargar(RUTA_EXCEL)
    nuevo_zip, resumen = _procesar(zip_bytes, personas, canales_dia_por_dni)
    print(resumen)
    if nuevo_zip is not None:
        subir_in_place(RUTA_EXCEL, nuevo_zip)


if __name__ == "__main__":
    ok, motivo = adquirir_lock(NOMBRE_LOCK, "github-actions", max_minutos=LOCK_MAX_MINUTOS)
    if not ok:
        print(f"actualizar_essity_headcount no iniciado: {motivo}")
        sys.exit(0)
    try:
        main()
    finally:
        liberar_lock(NOMBRE_LOCK)
