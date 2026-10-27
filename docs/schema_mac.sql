-- Schema actual del Proyecto MAC (generado desde los modelos SQLAlchemy, no es un pg_dump en vivo)
-- Dos grupos de tablas en el codigo (misma Postgres, dos metadata distintos):
--   1) extensions.py / models.py (Flask-SQLAlchemy): usuarios, usuario_supervisor_visible
--   2) dimension_models.py + fact_models.py (SQLAlchemy standalone): el resto

CREATE TABLE usuarios (
	id SERIAL NOT NULL, 
	email VARCHAR(150) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	rol rol_usuario NOT NULL, 
	dni_asociado VARCHAR(8), 
	cliente_id_athena INTEGER, 
	canal_asignado VARCHAR(50), 
	paginas_permitidas TEXT, 
	activo BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	last_login TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (email)
);

CREATE TABLE usuario_supervisor_visible (
	id SERIAL NOT NULL, 
	usuario_id INTEGER NOT NULL, 
	supervisor_dni VARCHAR(15) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_usuario_supervisor_visible UNIQUE (usuario_id, supervisor_dni), 
	FOREIGN KEY(usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE
);

CREATE TABLE catalogo_motivos (
	id SERIAL NOT NULL, 
	motivo VARCHAR(100) NOT NULL, 
	categoria VARCHAR(50), 
	requiere_sustento VARCHAR(150), 
	efecto_indicador VARCHAR(100), 
	efecto_horas VARCHAR(100), 
	PRIMARY KEY (id), 
	UNIQUE (motivo)
);

CREATE TABLE feriados (
	id SERIAL NOT NULL, 
	fecha DATE NOT NULL, 
	dia_semana VARCHAR(15), 
	motivo VARCHAR(150), 
	feriado_nacional BOOLEAN, 
	PRIMARY KEY (id), 
	UNIQUE (fecha)
);

CREATE TABLE personas (
	dni VARCHAR(15) NOT NULL, 
	nombre_completo VARCHAR(150) NOT NULL, 
	rol VARCHAR(50), 
	canal VARCHAR(50), 
	subcanal VARCHAR(50), 
	region VARCHAR(50), 
	ciudad VARCHAR(80), 
	zona VARCHAR(100), 
	supervisor_dni VARCHAR(15), 
	correo VARCHAR(150), 
	fecha_ingreso DATE, 
	fecha_baja DATE, 
	estado VARCHAR(20) NOT NULL, 
	reemplaza_a_dni VARCHAR(15), 
	es_reingreso BOOLEAN, 
	motivo_baja VARCHAR(200), 
	dado_de_baja_por VARCHAR(150), 
	registrado_por VARCHAR(150), 
	fecha_registro DATE, 
	analista_propietario VARCHAR(150), 
	PRIMARY KEY (dni), 
	CONSTRAINT ck_personas_estado CHECK (estado IN ('Activo','Inactivo','Vacante')), 
	FOREIGN KEY(supervisor_dni) REFERENCES personas (dni) ON DELETE SET NULL, 
	FOREIGN KEY(reemplaza_a_dni) REFERENCES personas (dni) ON DELETE SET NULL
);

CREATE TABLE pipeline_lock (
	nombre VARCHAR(50) NOT NULL, 
	quien VARCHAR(150) NOT NULL, 
	adquirido_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (nombre)
);

CREATE TABLE planning_pdv (
	id SERIAL NOT NULL, 
	periodo DATE NOT NULL, 
	co_li VARCHAR(50) NOT NULL, 
	tipo VARCHAR(15) NOT NULL, 
	nombre_pdv VARCHAR(200), 
	ciudad VARCHAR(80), 
	region VARCHAR(50), 
	subcanal VARCHAR(50), 
	mercado VARCHAR(150), 
	dni_asignado VARCHAR(15), 
	mercaderista_nombre VARCHAR(150), 
	supervisor VARCHAR(150), 
	dias_programados INTEGER, 
	cargado_por VARCHAR(150), 
	cargado_en DATE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_planning_pdv UNIQUE (periodo, co_li, tipo)
);

CREATE TABLE visitas (
	id SERIAL NOT NULL, 
	dni VARCHAR(15) NOT NULL, 
	punto_venta_id VARCHAR(50), 
	punto_venta VARCHAR(200), 
	tipo_negocio VARCHAR(30), 
	fecha_inicio DATE NOT NULL, 
	hora_inicio VARCHAR(10), 
	fecha_fin DATE, 
	hora_fin VARCHAR(10), 
	distancia_metros_inicio FLOAT, 
	motivo_visita VARCHAR(100), 
	PRIMARY KEY (id),
	CONSTRAINT uq_visita_dedup UNIQUE (dni, punto_venta_id, fecha_inicio, hora_inicio)
);

CREATE TABLE clasificacion_diaria (
	id SERIAL NOT NULL, 
	dni VARCHAR(15) NOT NULL, 
	fecha DATE NOT NULL, 
	dia_semana VARCHAR(15), 
	canal_esperado VARCHAR(50), 
	canal_turno VARCHAR(50), 
	canales_marcados VARCHAR(200), 
	entrada_esperada VARCHAR(10), 
	entrada_real VARCHAR(10), 
	salida_esperada VARCHAR(10), 
	salida_real VARCHAR(10), 
	estado VARCHAR(60) NOT NULL, 
	salida_anticipada_min INTEGER, 
	trabajo_otro_canal BOOLEAN, 
	alerta_geofence BOOLEAN, 
	fuente_dato TEXT, 
	comentario_supervisor TEXT, 
	alerta_analista BOOLEAN, 
	procesado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_clasificacion_dni_fecha_canal UNIQUE (dni, fecha, canal_turno), 
	FOREIGN KEY(dni) REFERENCES personas (dni) ON DELETE CASCADE
);

CREATE TABLE correcciones_web (
	id SERIAL NOT NULL,
	dni VARCHAR(15) NOT NULL,
	fecha DATE NOT NULL,
	comentario_entrada TEXT,
	comentario_salida TEXT,
	hora_entrada_corregida VARCHAR(10),
	hora_salida_corregida VARCHAR(10),
	estado_reportado VARCHAR(30),
	registrado_por VARCHAR(150),
	fecha_registro TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	FOREIGN KEY(dni) REFERENCES personas (dni) ON DELETE CASCADE
);

CREATE TABLE historial_cambios (
	id SERIAL NOT NULL, 
	dni VARCHAR(15) NOT NULL, 
	campo VARCHAR(50) NOT NULL, 
	valor_nuevo VARCHAR(255) NOT NULL, 
	fecha_desde DATE NOT NULL, 
	fecha_hasta DATE, 
	dia_semana VARCHAR(15), 
	canal VARCHAR(50), 
	comentario TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(dni) REFERENCES personas (dni) ON DELETE CASCADE
);

CREATE TABLE patron_recurrente (
	id SERIAL NOT NULL, 
	dni VARCHAR(15) NOT NULL, 
	dia_semana VARCHAR(15) NOT NULL, 
	hora_entrada_prog TIME WITHOUT TIME ZONE, 
	hora_salida_prog TIME WITHOUT TIME ZONE, 
	canal_dia VARCHAR(50), 
	refrigerio VARCHAR(30), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_patron_dni_dia_canal UNIQUE (dni, dia_semana, canal_dia), 
	FOREIGN KEY(dni) REFERENCES personas (dni) ON DELETE CASCADE
);

CREATE TABLE persona_supervisor_canal (
	id SERIAL NOT NULL, 
	dni VARCHAR(15) NOT NULL, 
	canal VARCHAR(50) NOT NULL, 
	supervisor_dni VARCHAR(15) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_persona_supervisor_canal UNIQUE (dni, canal), 
	FOREIGN KEY(dni) REFERENCES personas (dni) ON DELETE CASCADE, 
	FOREIGN KEY(supervisor_dni) REFERENCES personas (dni) ON DELETE CASCADE
);

CREATE TABLE persona_zona_canal (
	id SERIAL NOT NULL, 
	dni VARCHAR(15) NOT NULL, 
	canal VARCHAR(50) NOT NULL, 
	zona VARCHAR(150) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_persona_zona_canal UNIQUE (dni, canal), 
	FOREIGN KEY(dni) REFERENCES personas (dni) ON DELETE CASCADE
);

CREATE TABLE planning_pdv_dia (
	id SERIAL NOT NULL, 
	planning_pdv_id INTEGER NOT NULL, 
	fecha DATE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_planning_pdv_dia UNIQUE (planning_pdv_id, fecha), 
	FOREIGN KEY(planning_pdv_id) REFERENCES planning_pdv (id) ON DELETE CASCADE
);

CREATE TABLE reuniones_registradas (
	id SERIAL NOT NULL, 
	dni VARCHAR(15) NOT NULL, 
	fecha DATE NOT NULL, 
	hora_inicio VARCHAR(10), 
	hora_fin VARCHAR(10), 
	nota TEXT, 
	registrado_por VARCHAR(150), 
	fecha_registro TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(dni) REFERENCES personas (dni) ON DELETE CASCADE
);

CREATE TABLE vacaciones_registradas (
	id SERIAL NOT NULL, 
	dni VARCHAR(15) NOT NULL, 
	fecha_inicio DATE NOT NULL, 
	fecha_fin DATE NOT NULL, 
	motivo VARCHAR(30) DEFAULT 'Vacaciones' NOT NULL, 
	registrado_por VARCHAR(150), 
	fecha_registro TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	actualizado_por VARCHAR(150), 
	fecha_actualizacion TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(dni) REFERENCES personas (dni) ON DELETE CASCADE
);

CREATE TABLE vacante_seguimiento (
	dni VARCHAR(15) NOT NULL,
	prioridad_cobertura VARCHAR(10),
	estado_cobertura VARCHAR(20),
	actualizado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
	PRIMARY KEY (dni),
	FOREIGN KEY(dni) REFERENCES personas (dni) ON DELETE CASCADE
);

-- Vista (dimension_models.py::VACANTES_VIEW_SQL, no es un CREATE TABLE de un modelo)
CREATE OR REPLACE VIEW vacantes AS
SELECT p.dni, p.zona, p.rol, p.canal, p.region, p.ciudad, p.supervisor_dni,
       p.fecha_baja, p.motivo_baja,
       vs.prioridad_cobertura,
       COALESCE(vs.estado_cobertura, 'Abierta') AS estado_cobertura
FROM personas p
LEFT JOIN vacante_seguimiento vs ON vs.dni = p.dni
WHERE p.estado = 'Vacante';
