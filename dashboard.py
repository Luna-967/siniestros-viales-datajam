"""Dashboard Streamlit para la base de siniestros viales de Bogotá."""
from __future__ import annotations

import os
from contextlib import contextmanager

import pandas as pd
import plotly.express as px
import psycopg2
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
st.set_page_config(page_title="Riesgo vial Bogotá", layout="wide")
SEVERITY_LABELS = {1: "Con muertos", 2: "Con heridos", 3: "Solo daños"}


def db_config() -> dict[str, str]:
    fields = ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]
    missing = [f for f in fields if not os.getenv(f)]
    if missing:
        st.error("Faltan variables de conexión: " + ", ".join(missing))
        st.stop()
    return {f[2:].lower(): os.environ[f] for f in fields}


@contextmanager
def connection():
    conn = psycopg2.connect(**db_config())
    try:
        yield conn
    finally:
        conn.close()


@st.cache_data(ttl=300)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    with connection() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def filters() -> tuple[list[int], list[str], list[int]]:
    years = query("SELECT DISTINCT EXTRACT(YEAR FROM fecha)::int AS anio FROM siniestros WHERE fecha IS NOT NULL ORDER BY anio")
    localities = query("SELECT nombre FROM localidades ORDER BY nombre")
    severities = query("SELECT DISTINCT codigo_gravedad FROM siniestros WHERE codigo_gravedad IS NOT NULL ORDER BY codigo_gravedad")
    with st.sidebar:
        st.header("Filtros")
        selected_years = st.multiselect("Año", years["anio"].tolist(), default=years["anio"].tolist())
        selected_localities = st.multiselect("Localidad", localities["nombre"].tolist())
        selected_severities = st.multiselect(
            "Gravedad",
            severities["codigo_gravedad"].tolist(),
            format_func=lambda code: SEVERITY_LABELS.get(code, f"Código {code}"),
        )
    return selected_years, selected_localities, selected_severities


def where_clause(years: list[int], localities: list[str], severities: list[int]) -> tuple[str, list[object]]:
    pieces, params = ["1=1"], []
    if years:
        pieces.append("EXTRACT(YEAR FROM s.fecha)::int = ANY(%s)")
        params.append(years)
    if localities:
        pieces.append("l.nombre = ANY(%s)")
        params.append(localities)
    if severities:
        pieces.append("s.codigo_gravedad = ANY(%s)")
        params.append(severities)
    return " AND ".join(pieces), params


def chart(data: pd.DataFrame, x: str, y: str, title: str) -> None:
    if data.empty:
        st.info("No hay datos para los filtros seleccionados.")
    else:
        st.plotly_chart(px.bar(data, x=x, y=y, title=title, text_auto=True), use_container_width=True)


def main() -> None:
    st.title("Siniestros viales en Bogotá D.C.")
    st.caption("Análisis exploratorio de riesgo vial. Los filtros se aplican sobre siniestros.")
    years, localities, severities = filters()
    where, params = where_clause(years, localities, severities)
    base = f"FROM siniestros s LEFT JOIN localidades l ON l.codigo_localidad=s.codigo_localidad LEFT JOIN gravedades g ON g.codigo_gravedad=s.codigo_gravedad WHERE {where}"
    total = query(f"SELECT count(*) AS total {base}", tuple(params)).iloc[0, 0]
    years_count = query(f"SELECT EXTRACT(YEAR FROM s.fecha)::int AS año, count(*) AS siniestros {base} GROUP BY 1 ORDER BY 1", tuple(params))
    locations = query(f"SELECT COALESCE(l.nombre, 'Sin localidad') AS localidad, count(*) AS siniestros {base} GROUP BY 1 ORDER BY 2 DESC LIMIT 20", tuple(params))
    hours = query(f"SELECT EXTRACT(HOUR FROM s.hora)::int AS hora, count(*) AS siniestros {base} GROUP BY 1 ORDER BY 1", tuple(params))
    severity = query(
        f"SELECT CASE s.codigo_gravedad "
        f"WHEN 1 THEN 'Con muertos' WHEN 2 THEN 'Con heridos' "
        f"WHEN 3 THEN 'Solo daños' ELSE 'Sin gravedad' END AS gravedad, "
        f"count(*) AS siniestros {base} GROUP BY 1 ORDER BY 2 DESC",
        tuple(params),
    )
    classes = query(f"SELECT COALESCE(t.nombre, 'Sin tipo') AS tipo, count(*) AS siniestros {base.replace('WHERE', 'LEFT JOIN tipos_siniestro t ON t.codigo_clase=s.codigo_clase WHERE')} GROUP BY 1 ORDER BY 2 DESC", tuple(params))
    st.metric("Siniestros seleccionados", f"{total:,}")
    c1, c2 = st.columns(2)
    with c1: chart(years_count, "año", "siniestros", "Siniestros por año")
    with c2: chart(hours, "hora", "siniestros", "Siniestros por hora")
    c3, c4 = st.columns(2)
    with c3: chart(locations, "localidad", "siniestros", "Siniestros por localidad")
    with c4: chart(severity, "gravedad", "siniestros", "Siniestros por gravedad")
    chart(classes, "tipo", "siniestros", "Tipos de siniestro")
    vehicle_sql = f"SELECT COALESCE(v.vehiculo, 'Sin vehículo') AS vehiculo, count(*) AS involucrados FROM vehiculos v JOIN siniestros s ON s.codigo_accidente=v.codigo_accidente LEFT JOIN localidades l ON l.codigo_localidad=s.codigo_localidad LEFT JOIN gravedades g ON g.codigo_gravedad=s.codigo_gravedad WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT 20"
    actor_sql = f"SELECT COALESCE(a.condicion, 'Sin condición') AS actor_vial, count(*) AS involucrados FROM actores_viales a JOIN siniestros s ON s.codigo_accidente=a.codigo_accidente LEFT JOIN localidades l ON l.codigo_localidad=s.codigo_localidad LEFT JOIN gravedades g ON g.codigo_gravedad=s.codigo_gravedad WHERE {where} GROUP BY 1 ORDER BY 2 DESC"
    causes_sql = f"SELECT h.descripcion AS causa, count(*) AS siniestros FROM siniestro_hipotesis sh JOIN hipotesis h ON h.codigo_causa=sh.codigo_causa JOIN siniestros s ON s.codigo_accidente=sh.codigo_accidente LEFT JOIN localidades l ON l.codigo_localidad=s.codigo_localidad LEFT JOIN gravedades g ON g.codigo_gravedad=s.codigo_gravedad WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT 20"
    c5, c6 = st.columns(2)
    with c5: chart(query(vehicle_sql, tuple(params)), "vehiculo", "involucrados", "Vehículos involucrados")
    with c6: chart(query(actor_sql, tuple(params)), "actor_vial", "involucrados", "Actores viales")
    chart(query(causes_sql, tuple(params)), "causa", "siniestros", "Principales hipótesis o causas")
    st.subheader("Cruce de riesgo")
    crossing = query(f"SELECT COALESCE(l.nombre, 'Sin localidad') AS localidad, EXTRACT(HOUR FROM s.hora)::int AS hora, COALESCE(g.nombre, 'Sin gravedad') AS gravedad, count(*) AS siniestros {base} GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 100", tuple(params))
    st.dataframe(crossing, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
