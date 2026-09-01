# -*- coding: utf-8 -*-
"""
Modelos SQLAlchemy "standalone" (no atados a Flask-SQLAlchemy) para las 6
tablas dimensión del Proyecto MAC (Fase 2 de la migración) -- personas,
patron_recurrente, vacante_seguimiento (+ vista vacantes), catalogo_motivos,
historial_cambios, feriados.

Standalone a propósito: tanto mac_cloud/app.py (la app Flask) COMO los
scripts sueltos de MAC/ (ventana_reemplazos.py, el exportador a Excel, la
migración inicial) necesitan importar estos modelos, y los scripts de MAC/
no levantan una app Flask -- solo necesitan una sesión de DB directa. Reusa
el mismo DATABASE_URL que usa mac_cloud/extensions.py, así que apunta a la
MISMA Postgres.
"""
import os

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, Float, ForeignKey, Integer, String,
    Text, Time, TIMESTAMP, UniqueConstraint, create_engine, func,
)
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(os.environ["DATABASE_URL"])
    return _engine


def get_session():
    """Uso: with get_session() as s: ..."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


Base = declarative_base()


class Persona(Base):
    __tablename__ = "personas"

    dni = Column(String(15), primary_key=True)
    nombre_completo = Column(String(150), nullable=False)
    rol = Column(String(50))
    canal = Column(String(50))
    subcanal = Column(String(50))
    region = Column(String(50))
    ciudad = Column(String(80))
    zona = Column(String(100))
    supervisor_dni = Column(String(15), ForeignKey("personas.dni", ondelete="SET NULL"))
    correo = Column(String(150))
    fecha_ingreso = Column(Date)
    fecha_baja = Column(Date, nullable=True)
    estado = Column(String(20), nullable=False)  # Activo / Inactivo / Vacante
    reemplaza_a_dni = Column(String(15), ForeignKey("personas.dni", ondelete="SET NULL"))
    es_reingreso = Column(Boolean, default=False)
    motivo_baja = Column(String(200))
    # Quien dio de baja (Davor, 2026-09-01: "en dar de baja debe quedar el
    # historico de a quienes hemos sacado, lo mismo en agregar headcount")
    # -- mismo criterio que registrado_por/fecha_registro para altas, pero
    # para el otro extremo del ciclo de vida.
    dado_de_baja_por = Column(String(150))
    registrado_por = Column(String(150))
    fecha_registro = Column(Date)
    # Fase 4 (2026-08-21): a quien pertenece esta persona -- el correo del
    # analista que la cargo. Permite que varios analistas usen el mismo MAC
    # con sus propios equipos sin pisarse datos entre si (ver mac_cloud/cargas.py).
    analista_propietario = Column(String(150))

    __table_args__ = (
        CheckConstraint("estado IN ('Activo','Inactivo','Vacante')", name="ck_personas_estado"),
    )


# Lista fija (Davor, 2026-08-29) -- editable desde la pantalla Personal, sin
# vinculo con `canal` (un mercaderista Farmacia puede ser Minorista o
# Mayorista igual que uno Tradicional).
SUBCANALES = ["Minorista", "Mayorista", "Autoservicio", "Farmacia"]


class PatronRecurrente(Base):
    __tablename__ = "patron_recurrente"

    id = Column(Integer, primary_key=True)
    dni = Column(String(15), ForeignKey("personas.dni", ondelete="CASCADE"), nullable=False)
    dia_semana = Column(String(15), nullable=False)  # "Lunes".."Sábado", texto tal cual el Excel
    hora_entrada_prog = Column(Time)
    hora_salida_prog = Column(Time)
    canal_dia = Column(String(50))
    refrigerio = Column(String(30))

    __table_args__ = (UniqueConstraint("dni", "dia_semana", name="uq_patron_dni_dia"),)


class PersonaSupervisorCanal(Base):
    """Override de supervisor_dni por CANAL (no por día de la semana, ver
    scoping.py::resolver_supervisor_dni) -- caso puntual (Davor, 2026-08-29):
    un mercaderista compartido entre canales puede tener un supervisor real
    distinto según el canal ("por Tradicional 1, por Farmacia y AU tiene
    otro"), y ese supervisor NO cambia según qué canal le toque trabajar
    ESE día puntual -- es fijo por canal, así que PatronRecurrente
    (variación por día) no sirve para esto. Sin fila acá = sin override,
    sigue valiendo Persona.supervisor_dni como hasta ahora (la inmensa
    mayoría de personas nunca tiene una fila en esta tabla)."""
    __tablename__ = "persona_supervisor_canal"

    id = Column(Integer, primary_key=True)
    dni = Column(String(15), ForeignKey("personas.dni", ondelete="CASCADE"), nullable=False)
    canal = Column(String(50), nullable=False)
    supervisor_dni = Column(String(15), ForeignKey("personas.dni", ondelete="CASCADE"), nullable=False)

    __table_args__ = (UniqueConstraint("dni", "canal", name="uq_persona_supervisor_canal"),)


class VacanteSeguimiento(Base):
    """Solo los 2 campos que hoy se pierden en cada corrida de
    update_vacantes.py (Prioridad, Estado de cobertura) -- el resto de una
    "vacante" es simplemente una fila de Persona con estado='Vacante', ver
    la vista `vacantes` más abajo."""
    __tablename__ = "vacante_seguimiento"

    dni = Column(String(15), ForeignKey("personas.dni", ondelete="CASCADE"), primary_key=True)
    prioridad_cobertura = Column(String(10))  # Alta / Media / Baja
    estado_cobertura = Column(String(20), default="Abierta")  # Abierta / En proceso / Cubierta
    actualizado_en = Column(TIMESTAMP, server_default=func.now())


class CatalogoMotivo(Base):
    __tablename__ = "catalogo_motivos"

    id = Column(Integer, primary_key=True)
    motivo = Column(String(100), nullable=False, unique=True)
    categoria = Column(String(50))
    requiere_sustento = Column(String(150))
    efecto_indicador = Column(String(100))
    efecto_horas = Column(String(100))


class HistorialCambio(Base):
    __tablename__ = "historial_cambios"

    id = Column(Integer, primary_key=True)
    dni = Column(String(15), ForeignKey("personas.dni", ondelete="CASCADE"), nullable=False)
    campo = Column(String(50), nullable=False)
    valor_nuevo = Column(String(255), nullable=False)
    fecha_desde = Column(Date, nullable=False)
    fecha_hasta = Column(Date, nullable=True)
    dia_semana = Column(String(15), nullable=True)
    comentario = Column(Text)
    created_at = Column(TIMESTAMP, server_default=func.now())


class Feriado(Base):
    __tablename__ = "feriados"

    id = Column(Integer, primary_key=True)
    fecha = Column(Date, nullable=False, unique=True)
    dia_semana = Column(String(15))
    motivo = Column(String(150))
    feriado_nacional = Column(Boolean, default=True)


class PipelineLock(Base):
    """Fase 6 (2026-08-21): candado distribuido -- el candado por archivo
    local (logs/*.lock) que ya usan pipeline_completo.py/export_sharepoint.py
    no sirve entre dos maquinas distintas (la laptop y un runner de GitHub
    Actions corriendo el mismo pipeline en paralelo durante la validacion).
    Una fila por nombre de tarea ("pipeline_completo", "reporte_9am", etc.);
    ver mac_cloud/pipeline/db_lock.py para adquirir/liberar."""
    __tablename__ = "pipeline_lock"

    nombre = Column(String(50), primary_key=True)
    quien = Column(String(150), nullable=False)
    adquirido_en = Column(TIMESTAMP, server_default=func.now(), nullable=False)


class CorreccionWeb(Base):
    """Correccion diaria de asistencia (comentario/hora corregida) hecha
    desde el panel web de mac_cloud/asistencia.py. Escritura DOBLE a
    proposito (decision del usuario, 2026-08-21): la Tabla 3 en SharePoint
    (via Graph) sigue siendo la fuente REAL que lee el motor de
    clasificacion -- esto es solo una copia para auditoria/consulta en
    Postgres, ningun motor la lee todavia."""
    __tablename__ = "correcciones_web"

    id = Column(Integer, primary_key=True)
    dni = Column(String(15), ForeignKey("personas.dni", ondelete="CASCADE"), nullable=False)
    fecha = Column(Date, nullable=False)
    comentario_entrada = Column(Text)
    comentario_salida = Column(Text)
    hora_entrada_corregida = Column(String(10))
    hora_salida_corregida = Column(String(10))
    registrado_por = Column(String(150))
    fecha_registro = Column(TIMESTAMP, server_default=func.now())


class Visita(Base):
    """Fase 6+ (2026-08-23): detalle de visitas a PDV (GPS), tal cual llega
    de Athena (ver pipeline/athena_client.py::traer_visitas()) -- antes solo
    volaba por el runner de GitHub Actions (disco efímero) y se descartaba
    al terminar cada corrida. Sin FK a personas a propósito: el filtro de
    Athena (cliente_id=295) trae DNIs que no están en nuestra tabla
    `personas` (personal de otros clientes/roles no migrados) -- una FK
    estricta haría fallar el upsert masivo por esas filas. Los reportes que
    sí necesitan cruzar con Persona lo hacen con un JOIN normal, que
    descarta solas las filas sin match."""
    __tablename__ = "visitas"

    id = Column(Integer, primary_key=True)
    dni = Column(String(15), nullable=False, index=True)
    punto_venta_id = Column(String(50))
    punto_venta = Column(String(200))
    tipo_negocio = Column(String(30))
    fecha_inicio = Column(Date, nullable=False, index=True)
    hora_inicio = Column(String(10))
    fecha_fin = Column(Date)
    hora_fin = Column(String(10))
    distancia_metros_inicio = Column(Float)
    motivo_visita = Column(String(100))

    __table_args__ = (
        UniqueConstraint(
            "dni", "punto_venta_id", "fecha_inicio", "hora_inicio", "fecha_fin", "hora_fin",
            name="uq_visita_dedup",
        ),
    )


VACANTES_VIEW_SQL = """
CREATE OR REPLACE VIEW vacantes AS
SELECT p.dni, p.zona, p.rol, p.canal, p.region, p.ciudad, p.supervisor_dni,
       p.fecha_baja, p.motivo_baja,
       vs.prioridad_cobertura,
       COALESCE(vs.estado_cobertura, 'Abierta') AS estado_cobertura
FROM personas p
LEFT JOIN vacante_seguimiento vs ON vs.dni = p.dni
WHERE p.estado = 'Vacante';
"""


def crear_tablas():
    """Crea las tablas (si no existen) y la vista `vacantes`. Idempotente --
    seguro de correr de nuevo."""
    Base.metadata.create_all(get_engine())
    with get_engine().begin() as conn:
        conn.exec_driver_sql(VACANTES_VIEW_SQL)


if __name__ == "__main__":
    crear_tablas()
    print("Tablas y vista 'vacantes' creadas/verificadas.")
