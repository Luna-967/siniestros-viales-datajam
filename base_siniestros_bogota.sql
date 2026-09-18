-- ============================================================
-- BASE DE DATOS CORREGIDA: SINIESTROS VIALES BOGOTÁ D.C.
-- PostgreSQL 14+
-- Fuente: siniestros_viales_consolidados_bogota_dc.xlsx
-- ============================================================
-- Correcciones frente al primer diseño:
-- 1. hipotesis.descripcion NO es UNIQUE: el diccionario fuente puede
--    asignar la misma descripción a códigos de causa distintos.
-- 2. El código de causa sigue siendo la PK y la referencia correcta.
-- 3. Se agrega staging.rechazos_carga para auditar filas que no pueden
--    llegar a tablas definitivas (por ejemplo, detalles sin siniestro).
-- ============================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS staging;

-- Para recrear toda la estructura desde cero, descomente este bloque.
-- DROP TABLE IF EXISTS staging.rechazos_carga, siniestro_hipotesis, vehiculos,
--     actores_viales, siniestros, hipotesis, disenos_lugar, objetos_fijos,
--     tipos_choque, tipos_siniestro, gravedades, localidades CASCADE;
-- DROP TABLE IF EXISTS staging.siniestros_raw, staging.actores_viales_raw,
--     staging.vehiculos_raw, staging.hipotesis_raw CASCADE;

-- -------------------- Catálogos -----------------------------
CREATE TABLE IF NOT EXISTS localidades (
    codigo_localidad INTEGER PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS gravedades (
    codigo_gravedad INTEGER PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tipos_siniestro (
    codigo_clase INTEGER PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tipos_choque (
    codigo_choque INTEGER PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS objetos_fijos (
    codigo_objeto_fijo INTEGER PRIMARY KEY,
    nombre VARCHAR(150) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS disenos_lugar (
    codigo_diseno INTEGER PRIMARY KEY,
    nombre VARCHAR(150) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS hipotesis (
    codigo_causa INTEGER PRIMARY KEY,
    descripcion VARCHAR(255) NOT NULL
);

-- Corrige instalaciones realizadas con el script anterior.
ALTER TABLE hipotesis DROP CONSTRAINT IF EXISTS hipotesis_descripcion_key;

-- --------------------- Hecho principal -----------------------
CREATE TABLE IF NOT EXISTS siniestros (
    codigo_accidente BIGINT PRIMARY KEY,
    fecha DATE,
    hora TIME,
    codigo_gravedad INTEGER REFERENCES gravedades(codigo_gravedad),
    codigo_clase INTEGER REFERENCES tipos_siniestro(codigo_clase),
    codigo_choque INTEGER REFERENCES tipos_choque(codigo_choque),
    codigo_objeto_fijo INTEGER REFERENCES objetos_fijos(codigo_objeto_fijo),
    direccion VARCHAR(255),
    codigo_localidad INTEGER REFERENCES localidades(codigo_localidad),
    codigo_diseno INTEGER REFERENCES disenos_lugar(codigo_diseno)
);

CREATE TABLE IF NOT EXISTS actores_viales (
    codigo_accidentado BIGINT PRIMARY KEY,
    codigo_accidente BIGINT NOT NULL REFERENCES siniestros(codigo_accidente) ON DELETE CASCADE,
    fecha DATE,
    condicion VARCHAR(100),
    estado VARCHAR(100),
    edad INTEGER CHECK (edad IS NULL OR edad BETWEEN 0 AND 120),
    sexo VARCHAR(20),
    vehiculo VARCHAR(50)
);

-- VEHICULO no es una PK: puede repetirse entre siniestros o ser NULL.
-- id_vehiculo proporciona una identidad artificial estable para cada fila.
CREATE TABLE IF NOT EXISTS vehiculos (
    id_vehiculo BIGSERIAL PRIMARY KEY,
    codigo_accidente BIGINT NOT NULL REFERENCES siniestros(codigo_accidente) ON DELETE CASCADE,
    fecha DATE,
    vehiculo VARCHAR(50),
    clase INTEGER,
    servicio INTEGER,
    modalidad INTEGER,
    enfuga CHAR(1)
);

CREATE TABLE IF NOT EXISTS siniestro_hipotesis (
    codigo_accidente BIGINT NOT NULL REFERENCES siniestros(codigo_accidente) ON DELETE CASCADE,
    codigo_causa INTEGER NOT NULL REFERENCES hipotesis(codigo_causa),
    PRIMARY KEY (codigo_accidente, codigo_causa)
);

-- --------------------- Índices analíticos ---------------------
CREATE INDEX IF NOT EXISTS idx_siniestros_fecha ON siniestros(fecha);
CREATE INDEX IF NOT EXISTS idx_siniestros_localidad ON siniestros(codigo_localidad);
CREATE INDEX IF NOT EXISTS idx_siniestros_gravedad ON siniestros(codigo_gravedad);
CREATE INDEX IF NOT EXISTS idx_siniestros_clase ON siniestros(codigo_clase);
CREATE INDEX IF NOT EXISTS idx_siniestros_hora ON siniestros(hora);
CREATE INDEX IF NOT EXISTS idx_actores_accidente ON actores_viales(codigo_accidente);
CREATE INDEX IF NOT EXISTS idx_vehiculos_accidente ON vehiculos(codigo_accidente);
CREATE INDEX IF NOT EXISTS idx_sh_causa ON siniestro_hipotesis(codigo_causa);

-- ------------------------- Staging ----------------------------
CREATE TABLE IF NOT EXISTS staging.siniestros_raw (
    codigo_accidente TEXT, fecha TEXT, hora TEXT, gravedad TEXT, clase TEXT,
    choque TEXT, objeto_fijo TEXT, direccion TEXT, codigo_localidad TEXT,
    diseno_lugar TEXT
);

CREATE TABLE IF NOT EXISTS staging.actores_viales_raw (
    codigo_accidentado TEXT, codigo_accidente TEXT, fecha TEXT,
    condicion TEXT, estado TEXT, edad TEXT, sexo TEXT, vehiculo TEXT
);

CREATE TABLE IF NOT EXISTS staging.vehiculos_raw (
    codigo_accidente TEXT, fecha TEXT, vehiculo TEXT, clase TEXT,
    servicio TEXT, modalidad TEXT, enfuga TEXT
);

CREATE TABLE IF NOT EXISTS staging.hipotesis_raw (
    codigo_accidente TEXT, fecha TEXT, codigo_causa TEXT
);

-- Auditoría de filas rechazadas durante ETL. No debilita las FK.
CREATE TABLE IF NOT EXISTS staging.rechazos_carga (
    id_rechazo BIGSERIAL PRIMARY KEY,
    fecha_carga TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    hoja_origen VARCHAR(50) NOT NULL,
    motivo VARCHAR(255) NOT NULL,
    codigo_accidente TEXT,
    datos JSONB
);

-- --------------------- Vista de análisis ----------------------
CREATE OR REPLACE VIEW vw_siniestros_detalle AS
SELECT s.codigo_accidente, s.fecha, s.hora, g.nombre AS gravedad,
       ts.nombre AS clase, tc.nombre AS choque, ofj.nombre AS objeto_fijo,
       s.direccion, l.nombre AS localidad, dl.nombre AS diseno_lugar
FROM siniestros s
LEFT JOIN gravedades g ON g.codigo_gravedad = s.codigo_gravedad
LEFT JOIN tipos_siniestro ts ON ts.codigo_clase = s.codigo_clase
LEFT JOIN tipos_choque tc ON tc.codigo_choque = s.codigo_choque
LEFT JOIN objetos_fijos ofj ON ofj.codigo_objeto_fijo = s.codigo_objeto_fijo
LEFT JOIN localidades l ON l.codigo_localidad = s.codigo_localidad
LEFT JOIN disenos_lugar dl ON dl.codigo_diseno = s.codigo_diseno;

COMMIT;

-- Orden de carga recomendado:
-- staging -> catálogos -> siniestros -> actores/vehículos -> siniestro_hipotesis.
-- Las filas sin siniestro padre se auditan en staging.rechazos_carga y no se
-- insertan en las tablas definitivas, para conservar la integridad referencial.
