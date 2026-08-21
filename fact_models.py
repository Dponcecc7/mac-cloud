# -*- coding: utf-8 -*-
"""
Modelo SQLAlchemy standalone para la tabla de hechos sombra de la Fase 3
(clasificación diaria) -- mismo patrón que dimension_models.py. Vive separada
de cualquier tabla que use el pipeline en producción; es segura de crear/
llenar/borrar sin ningún impacto en lo que ya corre cada 10 minutos.
"""
from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, String, Text, TIMESTAMP, UniqueConstraint, func

from dimension_models import Base, get_engine, get_session  # noqa: F401 -- reusa el MISMO Base/metadata que
# dimension_models.py: la FK de acá a personas.dni necesita que ambas tablas
# vivan en el mismo MetaData para que create_all() la pueda resolver -- con
# un declarative_base() propio y separado, SQLAlchemy no encuentra la tabla
# "personas" al armar la FK (NoReferencedTableError).


class ClasificacionDiaria(Base):
    __tablename__ = "clasificacion_diaria"

    id = Column(Integer, primary_key=True)
    dni = Column(String(15), ForeignKey("personas.dni", ondelete="CASCADE"), nullable=False)
    fecha = Column(Date, nullable=False)
    dia_semana = Column(String(15))
    canal_esperado = Column(String(50))
    canales_marcados = Column(String(200))
    entrada_esperada = Column(String(10))  # texto "HH:MM:SS", igual que el Excel -- evita ambiguedad de TIME con NULL/formatos
    entrada_real = Column(String(10))
    salida_esperada = Column(String(10))
    salida_real = Column(String(10))
    estado = Column(String(60), nullable=False)
    salida_anticipada_min = Column(Integer)
    trabajo_otro_canal = Column(Boolean, default=False)
    alerta_geofence = Column(Boolean, default=False)
    fuente_dato = Column(Text)  # texto libre historico puede superar 60 chars (ver correcciones manuales viejas) -- ampliado 2026-08-21
    comentario_supervisor = Column(Text)
    alerta_analista = Column(Boolean, default=False)
    procesado_en = Column(TIMESTAMP, server_default=func.now())

    __table_args__ = (UniqueConstraint("dni", "fecha", name="uq_clasificacion_dni_fecha"),)


def crear_tablas():
    Base.metadata.create_all(get_engine())


if __name__ == "__main__":
    crear_tablas()
    print("Tabla 'clasificacion_diaria' (sombra) creada/verificada.")
