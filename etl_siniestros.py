"""ETL idempotente de siniestros viales de Bogotá hacia PostgreSQL.

Ejecutar: python etl_siniestros.py
Antes, copie .env.example a .env y complete las credenciales.
"""
from __future__ import annotations

import io
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

SHEETS = ["SINIESTROS", "ACTOR_VIAL", "VEHICULOS", "HIPOTESIS", "DICCIONARIO"]
STAGING_COLUMNS = {
    "SINIESTROS": ["CODIGO_ACCIDENTE", "FECHA", "HORA", "GRAVEDAD", "CLASE", "CHOQUE", "OBJETO_FIJO", "DIRECCION", "CODIGO_LOCALIDAD", "DISENO_LUGAR"],
    # La hoja pone CODIGO_ACCIDENTE antes de CODIGO_ACCIDENTADO; staging los recibe en el orden de su tabla.
    "ACTOR_VIAL": ["CODIGO_ACCIDENTADO", "CODIGO_ACCIDENTE", "FECHA", "CONDICION", "ESTADO", "EDAD", "SEXO", "VEHICULO"],
    "VEHICULOS": ["CODIGO_ACCIDENTE", "FECHA", "VEHICULO", "CLASE", "SERVICIO", "MODALIDAD", "ENFUGA"],
    "HIPOTESIS": ["CODIGO_ACCIDENTE", "FECHA", "CODIGO_CAUSA"],
}
STAGING_TABLES = {
    "SINIESTROS": "staging.siniestros_raw",
    "ACTOR_VIAL": "staging.actores_viales_raw",
    "VEHICULOS": "staging.vehiculos_raw",
    "HIPOTESIS": "staging.hipotesis_raw",
}
CATALOGS = {
    # (tabla, columna del código, columna de la descripción).
    ("SINIESTROS", "CODIGO_LOCALIDAD"): ("localidades", "codigo_localidad", "nombre"),
    ("SINIESTROS", "GRAVEDAD"): ("gravedades", "codigo_gravedad", "nombre"),
    ("SINIESTROS", "CLASE"): ("tipos_siniestro", "codigo_clase", "nombre"),
    ("SINIESTROS", "CHOQUE"): ("tipos_choque", "codigo_choque", "nombre"),
    ("SINIESTROS", "OBJETO_FIJO"): ("objetos_fijos", "codigo_objeto_fijo", "nombre"),
    ("SINIESTROS", "DISENO_LUGAR"): ("disenos_lugar", "codigo_diseno", "nombre"),
    ("HIPOTESIS", "CODIGO_CAUSA"): ("hipotesis", "codigo_causa", "descripcion"),
}
FK_RULES = {
    "SINIESTROS": {"GRAVEDAD": ("SINIESTROS", "GRAVEDAD"), "CLASE": ("SINIESTROS", "CLASE"), "CHOQUE": ("SINIESTROS", "CHOQUE"), "OBJETO_FIJO": ("SINIESTROS", "OBJETO_FIJO"), "CODIGO_LOCALIDAD": ("SINIESTROS", "CODIGO_LOCALIDAD"), "DISENO_LUGAR": ("SINIESTROS", "DISENO_LUGAR")},
    "HIPOTESIS": {"CODIGO_CAUSA": ("HIPOTESIS", "CODIGO_CAUSA")},
}


@dataclass
class Stats:
    read: dict[str, int] = field(default_factory=dict)
    valid: dict[str, int] = field(default_factory=dict)
    rejected: dict[str, int] = field(default_factory=dict)
    duplicates: dict[str, int] = field(default_factory=dict)
    inserted: dict[str, int] = field(default_factory=dict)


def log(message: str) -> None:
    print(f"[ETL] {message}", flush=True)


def config() -> tuple[dict[str, Any], Path]:
    """Lee variables de entorno sin exponer la contraseña en el código."""
    load_dotenv()
    required = ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "EXCEL_PATH"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Faltan variables en .env: {', '.join(missing)}")
    return ({key[2:].lower(): os.environ[key] for key in required if key != "EXCEL_PATH"}, Path(os.environ["EXCEL_PATH"]))


def normalize_text(value: Any) -> Any:
    """Convierte vacíos, NaN y espacios repetidos a NULL sin alterar el Excel."""
    if pd.isna(value):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return None if text == "" else text


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Estandariza nombres y valores textuales en una copia de Pandas."""
    cleaned = frame.copy()
    cleaned.columns = [str(c).strip().upper() for c in cleaned.columns]
    return cleaned.map(normalize_text)


def as_integer(series: pd.Series) -> pd.Series:
    """Convierte códigos con apariencia decimal (p. ej. 1.0) a entero nullable."""
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def as_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", dayfirst=True).dt.date


def as_time(series: pd.Series) -> pd.Series:
    """Convierte horas Excel (fracción del día), texto o datetime a TIME."""
    def convert(value: Any) -> Any:
        if pd.isna(value):
            return None
        if isinstance(value, clock_time):
            return value
        if isinstance(value, (int, float)) and 0 <= value < 1:
            return (datetime.min + timedelta(seconds=round(value * 86400))).time()
        parsed = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(parsed) else parsed.time()
    return series.map(convert)


def read_workbook(path: Path, stats: Stats) -> dict[str, pd.DataFrame]:
    """Lee todas las hojas como texto para controlar conversiones explícitamente."""
    if not path.is_file():
        raise FileNotFoundError(f"No existe el Excel: {path}")
    log(f"Leyendo libro: {path}")
    frames = pd.read_excel(path, sheet_name=SHEETS, dtype=object)
    for sheet, frame in frames.items():
        frames[sheet] = normalize_frame(frame)
        stats.read[sheet] = len(frame)
        log(f"{sheet}: {frame.shape[0]:,} filas, {frame.shape[1]} columnas -> {list(frames[sheet].columns)}")
    return frames


def build_catalog_codes(dictionary: pd.DataFrame) -> dict[tuple[str, str], set[int]]:
    """Obtiene los códigos permitidos del DICCIONARIO y detecta duplicados contradictorios."""
    dictionary["CODIGO"] = as_integer(dictionary["CODIGO"])
    duplicate = dictionary.duplicated(["HOJA", "CAMPO", "CODIGO"], keep=False)
    conflicts = dictionary[duplicate].groupby(["HOJA", "CAMPO", "CODIGO"])["DESCRIPCION"].nunique()
    if (conflicts > 1).any():
        raise ValueError("DICCIONARIO contiene códigos con descripciones contradictorias.")
    return {key: set(as_integer(dictionary.loc[(dictionary.HOJA == key[0]) & (dictionary.CAMPO == key[1]), "CODIGO"]).dropna().astype(int)) for key in CATALOGS}


def validate_and_transform(frames: dict[str, pd.DataFrame], stats: Stats) -> dict[str, pd.DataFrame]:
    """Convierte tipos, valida claves y códigos; conserva solo registros aptos para staging."""
    allowed = build_catalog_codes(frames["DICCIONARIO"])
    output: dict[str, pd.DataFrame] = {}
    for sheet in ("SINIESTROS", "ACTOR_VIAL", "VEHICULOS", "HIPOTESIS"):
        data = frames[sheet].copy()
        for column in data.columns:
            if column in {"CODIGO_ACCIDENTE", "CODIGO_ACCIDENTADO", "EDAD", "GRAVEDAD", "CLASE", "CHOQUE", "OBJETO_FIJO", "CODIGO_LOCALIDAD", "DISENO_LUGAR", "SERVICIO", "MODALIDAD", "CODIGO_CAUSA"}:
                data[column] = as_integer(data[column])
        if "FECHA" in data:
            data["FECHA"] = as_date(data["FECHA"])
        if "HORA" in data:
            data["HORA"] = as_time(data["HORA"])
        nulls = {column: int(data[column].isna().sum()) for column in data.columns if data[column].isna().any()}
        if nulls:
            log(f"{sheet}: valores nulos detectados -> {nulls}")
        valid = pd.Series(True, index=data.index)
        required = {"SINIESTROS": ["CODIGO_ACCIDENTE"], "ACTOR_VIAL": ["CODIGO_ACCIDENTADO", "CODIGO_ACCIDENTE"], "VEHICULOS": ["CODIGO_ACCIDENTE"], "HIPOTESIS": ["CODIGO_ACCIDENTE", "CODIGO_CAUSA"]}[sheet]
        valid &= data[required].notna().all(axis=1)
        if "EDAD" in data:
            valid &= data["EDAD"].isna() | data["EDAD"].between(0, 120)
        for column, catalog_key in FK_RULES.get(sheet, {}).items():
            valid &= data[column].isna() | data[column].isin(allowed[catalog_key])
        before = len(data)
        dedupe_columns = {"SINIESTROS": ["CODIGO_ACCIDENTE"], "ACTOR_VIAL": ["CODIGO_ACCIDENTADO"], "VEHICULOS": list(data.columns), "HIPOTESIS": ["CODIGO_ACCIDENTE", "CODIGO_CAUSA"]}[sheet]
        duplicate = data.duplicated(dedupe_columns, keep="first")
        stats.duplicates[sheet] = int(duplicate.sum())
        data = data.loc[valid & ~duplicate].copy()
        stats.valid[sheet] = len(data)
        stats.rejected[sheet] = before - len(data) - stats.duplicates[sheet]
        output[sheet] = data
        log(f"{sheet}: válidas={stats.valid[sheet]:,}, rechazadas={stats.rejected[sheet]:,}, duplicadas={stats.duplicates[sheet]:,}")
    return output


def copy_dataframe(cursor: Any, table: str, frame: pd.DataFrame, columns: list[str]) -> None:
    """Carga masiva usando COPY; el marcador de nulo representa NULL en staging."""
    buffer = io.StringIO()
    frame.loc[:, columns].to_csv(buffer, index=False, header=False, sep="\t", na_rep="\\N")
    buffer.seek(0)
    cursor.copy_expert(f"COPY {table} ({', '.join(c.lower() for c in columns)}) FROM STDIN WITH (FORMAT CSV, DELIMITER E'\\t', NULL '\\N')", buffer)


def load_catalogs(cursor: Any, dictionary: pd.DataFrame) -> int:
    """Puebla catálogos desde DICCIONARIO con UPSERT por su código natural."""
    total = 0
    for (sheet, field), (table, code_column, description_column) in CATALOGS.items():
        data = dictionary[(dictionary.HOJA == sheet) & (dictionary.CAMPO == field)][["CODIGO", "DESCRIPCION"]].dropna().drop_duplicates("CODIGO")
        values = [(int(code), description) for code, description in data.itertuples(index=False, name=None)]
        execute_values(cursor, f"INSERT INTO {table} ({code_column}, {description_column}) VALUES %s ON CONFLICT ({code_column}) DO UPDATE SET {description_column} = EXCLUDED.{description_column}", values)
        total += len(values)
    return total


def ensure_foreign_keys(cursor: Any) -> None:
    """Verifica códigos de catálogo antes de promover siniestros."""
    checks = [
        ("siniestros sin catálogo", "SELECT count(*) FROM staging.siniestros_raw s LEFT JOIN localidades l ON NULLIF(s.codigo_localidad, '')::int = l.codigo_localidad WHERE NULLIF(s.codigo_localidad, '') IS NOT NULL AND l.codigo_localidad IS NULL"),
        ("hipótesis sin catálogo", "SELECT count(*) FROM staging.hipotesis_raw h LEFT JOIN hipotesis p ON NULLIF(h.codigo_causa, '')::int = p.codigo_causa WHERE p.codigo_causa IS NULL"),
    ]
    validate_checks(cursor, checks)


def ensure_siniestro_references(cursor: Any) -> dict[str, int]:
    """Cuenta detalles sin padre; se rechazan para preservar las FK sin abortar la carga."""
    checks = [
        ("ACTOR_VIAL", "SELECT count(*) FROM staging.actores_viales_raw a LEFT JOIN siniestros s ON NULLIF(a.codigo_accidente, '')::bigint = s.codigo_accidente WHERE s.codigo_accidente IS NULL"),
        ("VEHICULOS", "SELECT count(*) FROM staging.vehiculos_raw v LEFT JOIN siniestros s ON NULLIF(v.codigo_accidente, '')::bigint = s.codigo_accidente WHERE s.codigo_accidente IS NULL"),
        ("HIPOTESIS", "SELECT count(*) FROM staging.hipotesis_raw h LEFT JOIN siniestros s ON NULLIF(h.codigo_accidente, '')::bigint = s.codigo_accidente WHERE s.codigo_accidente IS NULL"),
    ]
    rejected = {}
    for sheet, query in checks:
        cursor.execute(query)
        rejected[sheet] = cursor.fetchone()[0]
        if rejected[sheet]:
            log(f"{sheet}: {rejected[sheet]:,} filas rechazadas por FK sin siniestro padre.")
    return rejected


def audit_orphan_rows(cursor: Any) -> None:
    """Registra en staging los detalles sin siniestro padre, sin debilitar las FK."""
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS staging.rechazos_carga (
            id_rechazo BIGSERIAL PRIMARY KEY,
            fecha_carga TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            hoja_origen VARCHAR(50) NOT NULL,
            motivo VARCHAR(255) NOT NULL,
            codigo_accidente TEXT,
            datos JSONB
        )"""
    )
    queries = {
        "ACTOR_VIAL": """INSERT INTO staging.rechazos_carga (hoja_origen, motivo, codigo_accidente, datos)
            SELECT 'ACTOR_VIAL', 'CODIGO_ACCIDENTE no existe en SINIESTROS', a.codigo_accidente,
            jsonb_build_object('codigo_accidentado', a.codigo_accidentado, 'fecha', a.fecha, 'condicion', a.condicion)
            FROM staging.actores_viales_raw a LEFT JOIN siniestros s ON s.codigo_accidente = NULLIF(a.codigo_accidente, '')::bigint
            WHERE s.codigo_accidente IS NULL""",
        "VEHICULOS": """INSERT INTO staging.rechazos_carga (hoja_origen, motivo, codigo_accidente, datos)
            SELECT 'VEHICULOS', 'CODIGO_ACCIDENTE no existe en SINIESTROS', v.codigo_accidente,
            jsonb_build_object('fecha', v.fecha, 'vehiculo', v.vehiculo, 'clase', v.clase)
            FROM staging.vehiculos_raw v LEFT JOIN siniestros s ON s.codigo_accidente = NULLIF(v.codigo_accidente, '')::bigint
            WHERE s.codigo_accidente IS NULL""",
        "HIPOTESIS": """INSERT INTO staging.rechazos_carga (hoja_origen, motivo, codigo_accidente, datos)
            SELECT 'HIPOTESIS', 'CODIGO_ACCIDENTE no existe en SINIESTROS', h.codigo_accidente,
            jsonb_build_object('fecha', h.fecha, 'codigo_causa', h.codigo_causa)
            FROM staging.hipotesis_raw h LEFT JOIN siniestros s ON s.codigo_accidente = NULLIF(h.codigo_accidente, '')::bigint
            WHERE s.codigo_accidente IS NULL""",
    }
    for statement in queries.values():
        cursor.execute(statement)


def validate_checks(cursor: Any, checks: list[tuple[str, str]]) -> None:
    """Ejecuta un grupo de controles de integridad y detiene la transacción si falla."""
    failures = []
    for label, query in checks:
        cursor.execute(query)
        count = cursor.fetchone()[0]
        if count:
            failures.append(f"{label}: {count}")
    if failures:
        raise ValueError("Falló la validación referencial: " + "; ".join(failures))


def insert_final_tables(cursor: Any, stats: Stats, selected_tables: tuple[str, ...]) -> None:
    """Promueve staging a tablas normalizadas en orden de dependencia, sin duplicar registros."""
    statements = {
        "siniestros": """INSERT INTO siniestros SELECT NULLIF(codigo_accidente,'')::bigint, NULLIF(fecha,'')::date, NULLIF(hora,'')::time, NULLIF(gravedad,'')::int, NULLIF(clase,'')::int, NULLIF(choque,'')::int, NULLIF(objeto_fijo,'')::int, NULLIF(direccion,''), NULLIF(codigo_localidad,'')::int, NULLIF(diseno_lugar,'')::int FROM staging.siniestros_raw ON CONFLICT (codigo_accidente) DO NOTHING""",
        "actores_viales": """INSERT INTO actores_viales SELECT NULLIF(a.codigo_accidentado,'')::bigint, NULLIF(a.codigo_accidente,'')::bigint, NULLIF(a.fecha,'')::date, NULLIF(a.condicion,''), NULLIF(a.estado,''), NULLIF(a.edad,'')::int, NULLIF(a.sexo,''), NULLIF(a.vehiculo,'') FROM staging.actores_viales_raw a JOIN siniestros s ON s.codigo_accidente = NULLIF(a.codigo_accidente,'')::bigint ON CONFLICT (codigo_accidentado) DO NOTHING""",
        # VEHICULO no es PK: se usan todos los atributos disponibles para reconocer una misma ocurrencia.
        "vehiculos": """INSERT INTO vehiculos (codigo_accidente,fecha,vehiculo,clase,servicio,modalidad,enfuga) SELECT NULLIF(v.codigo_accidente,'')::bigint, NULLIF(v.fecha,'')::date, NULLIF(v.vehiculo,''), NULLIF(v.clase,'')::int, NULLIF(v.servicio,'')::int, NULLIF(v.modalidad,'')::int, NULLIF(v.enfuga,'')::char(1) FROM staging.vehiculos_raw v JOIN siniestros s ON s.codigo_accidente = NULLIF(v.codigo_accidente,'')::bigint WHERE NOT EXISTS (SELECT 1 FROM vehiculos x WHERE x.codigo_accidente = NULLIF(v.codigo_accidente,'')::bigint AND x.fecha IS NOT DISTINCT FROM NULLIF(v.fecha,'')::date AND x.vehiculo IS NOT DISTINCT FROM NULLIF(v.vehiculo,'') AND x.clase IS NOT DISTINCT FROM NULLIF(v.clase,'')::int AND x.servicio IS NOT DISTINCT FROM NULLIF(v.servicio,'')::int AND x.modalidad IS NOT DISTINCT FROM NULLIF(v.modalidad,'')::int AND x.enfuga IS NOT DISTINCT FROM NULLIF(v.enfuga,'')::char(1))""",
        "siniestro_hipotesis": """INSERT INTO siniestro_hipotesis SELECT NULLIF(h.codigo_accidente,'')::bigint, NULLIF(h.codigo_causa,'')::int FROM staging.hipotesis_raw h JOIN siniestros s ON s.codigo_accidente = NULLIF(h.codigo_accidente,'')::bigint ON CONFLICT (codigo_accidente,codigo_causa) DO NOTHING""",
    }
    for table in selected_tables:
        statement = statements[table]
        cursor.execute(statement)
        stats.inserted[table] = cursor.rowcount
        log(f"Insertadas en {table}: {cursor.rowcount:,}")


def run_validations(cursor: Any) -> None:
    """Ejecuta controles post-carga visibles en consola."""
    for label, query in [
        ("siniestros", "SELECT count(*) FROM siniestros"),
        ("actores", "SELECT count(*) FROM actores_viales"),
        ("vehículos", "SELECT count(*) FROM vehiculos"),
        ("relaciones siniestro-hipótesis", "SELECT count(*) FROM siniestro_hipotesis"),
        ("FK huérfanas", "SELECT (SELECT count(*) FROM actores_viales a LEFT JOIN siniestros s ON s.codigo_accidente=a.codigo_accidente WHERE s.codigo_accidente IS NULL) + (SELECT count(*) FROM vehiculos v LEFT JOIN siniestros s ON s.codigo_accidente=v.codigo_accidente WHERE s.codigo_accidente IS NULL)"),
    ]:
        cursor.execute(query)
        log(f"Validación {label}: {cursor.fetchone()[0]:,}")


def print_summary(stats: Stats, seconds: float) -> None:
    log("Resumen final")
    for sheet in ("SINIESTROS", "ACTOR_VIAL", "VEHICULOS", "HIPOTESIS"):
        log(f"{sheet}: leídas={stats.read[sheet]:,}, válidas={stats.valid[sheet]:,}, rechazadas={stats.rejected[sheet]:,}, duplicadas={stats.duplicates[sheet]:,}")
    log("Filas insertadas: " + ", ".join(f"{table}={value:,}" for table, value in stats.inserted.items()))
    log(f"Tiempo de ejecución: {seconds:.1f} segundos")


def main() -> None:
    started = time.perf_counter()
    stats = Stats()
    connection = None
    try:
        db, excel = config()
        frames = read_workbook(excel, stats)
        cleaned = validate_and_transform(frames, stats)
        log("Conectando a PostgreSQL")
        connection = psycopg2.connect(**db)
        with connection:
            with connection.cursor() as cursor:
                for table in STAGING_TABLES.values():
                    cursor.execute(f"TRUNCATE TABLE {table}")
                for sheet, table in STAGING_TABLES.items():
                    copy_dataframe(cursor, table, cleaned[sheet], STAGING_COLUMNS[sheet])
                    log(f"Staging cargado por COPY: {table}")
                stats.inserted["catálogos"] = load_catalogs(cursor, frames["DICCIONARIO"])
                ensure_foreign_keys(cursor)
                # Los siniestros son el padre de actores, vehículos e hipótesis.
                insert_final_tables(cursor, stats, ("siniestros",))
                fk_rejected = ensure_siniestro_references(cursor)
                audit_orphan_rows(cursor)
                for sheet, count in fk_rejected.items():
                    stats.rejected[sheet] += count
                    stats.valid[sheet] -= count
                insert_final_tables(cursor, stats, ("actores_viales", "vehiculos", "siniestro_hipotesis"))
                run_validations(cursor)
        print_summary(stats, time.perf_counter() - started)
    except Exception as exc:
        if connection is not None:
            connection.rollback()
        log(f"ERROR: {exc}. Se revirtió la etapa transaccional.")
        raise
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    sys.exit(main())
