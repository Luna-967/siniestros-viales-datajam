"""Dashboard Streamlit para la base de siniestros viales de Bogotá."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date

import pandas as pd
import plotly.express as px
import psycopg2
import streamlit as st
from dotenv import load_dotenv
from export_service import exportar_pdf
from prevencion import EVENTS_SQL, summarize

load_dotenv()
st.set_page_config(page_title="Riesgo vial Bogotá", layout="wide")
SEVERITY_LABELS = {1: "Con muertos", 2: "Con heridos", 3: "Solo daños"}
BRAND_RED = "#E4032E"
BRAND_YELLOW = "#FAB62D"
BRAND_INK = "#252525"
BRAND_PALETTE = [BRAND_RED, BRAND_YELLOW, "#4B4B4B", "#A30D28", "#E8A51F"]


def apply_brand_theme() -> None:
    """Apply Bogotá City brand colors to the existing Streamlit interface."""
    st.markdown(
        """
        <style>
        :root {
            --bogota-red: #E4032E;
            --bogota-yellow: #FAB62D;
            --bogota-ink: #252525;
            --bogota-muted: #5C5C5C;
            --bogota-paper: #FFFFFF;
            --bogota-warm: #FFF8E8;
        }
        html, body, [class*="css"], [data-testid="stAppViewContainer"] {
            color: var(--bogota-ink);
            font-family: "Museo Sans", "Arial", sans-serif;
        }
        [data-testid="stAppViewContainer"] {
            background: var(--bogota-paper);
        }
        [data-testid="stHeader"] {
            background: transparent;
        }
        [data-testid="stMainBlockContainer"] {
            padding-top: 2.1rem;
            padding-bottom: 3rem;
            max-width: 1500px;
        }
        h1, h2, h3 {
            color: var(--bogota-ink) !important;
            font-family: "Museo Sans", "Arial", sans-serif !important;
            letter-spacing: -0.025em;
        }
        h1 {
            border-bottom: 5px solid var(--bogota-yellow);
            padding-bottom: .45rem;
            width: fit-content;
        }
        h2 {
            border-left: 5px solid var(--bogota-red);
            padding-left: .65rem;
        }
        [data-testid="stCaptionContainer"] p,
        [data-testid="stMarkdownContainer"] p {
            color: var(--bogota-muted);
        }
        [data-testid="stMetric"] {
            background: #FFFFFF;
            border: 1px solid #E8E2D7;
            border-top: 4px solid var(--bogota-red);
            border-radius: 10px;
            padding: .9rem 1rem;
            box-shadow: 0 2px 8px rgba(37, 37, 37, .06);
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            background: #FFFFFF;
            border: 1px solid #E8E2D7;
            border-radius: 12px;
            box-shadow: 0 3px 12px rgba(37, 37, 37, .07);
            padding: 1.1rem 1.2rem;
        }
        [data-testid="stVerticalBlockBorderWrapper"] h2 {
            margin-top: .15rem;
        }
        [data-testid="stMetricLabel"] p {
            color: var(--bogota-muted) !important;
            font-weight: 600;
        }
        [data-testid="stMetricValue"] {
            color: var(--bogota-ink) !important;
        }
        [data-testid="stSidebar"] {
            background: var(--bogota-warm);
            border-right: 1px solid #F0D79C;
        }
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            border-left: 0;
            padding-left: 0;
        }
        [data-testid="stBaseButton-primary"],
        [data-testid="stFormSubmitButton"] button {
            background: var(--bogota-red);
            border: 1px solid var(--bogota-red);
            color: #FFFFFF;
            border-radius: 7px;
            font-weight: 700;
        }
        [data-testid="stBaseButton-primary"]:hover,
        [data-testid="stFormSubmitButton"] button:hover {
            background: #B90025;
            border-color: #B90025;
            color: #FFFFFF;
        }
        [data-baseweb="select"] > div,
        [data-baseweb="input"] > div {
            border-radius: 7px;
        }
        [data-baseweb="select"]:focus-within > div,
        [data-baseweb="input"]:focus-within > div {
            border-color: var(--bogota-red);
            box-shadow: 0 0 0 1px var(--bogota-red);
        }
        [data-testid="stDataFrame"] {
            border: 1px solid #E8E2D7;
            border-radius: 9px;
            overflow: hidden;
        }
        hr {
            border-color: #E8E2D7;
        }
        @media (max-width: 700px) {
            [data-testid="stMainBlockContainer"] {
                padding: 1.1rem .9rem 2rem;
            }
            h1 { font-size: 1.8rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


apply_brand_theme()


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
        figure = px.bar(
            data,
            x=x,
            y=y,
            title=title,
            text_auto=True,
            color_discrete_sequence=BRAND_PALETTE,
            template="plotly_white",
        )
        figure.update_layout(
            font={"family": "Museo Sans, Arial, sans-serif", "color": BRAND_INK},
            title_font={"family": "Museo Sans, Arial, sans-serif", "size": 18, "color": BRAND_INK},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin={"t": 55, "r": 20, "b": 35, "l": 15},
        )
        figure.update_xaxes(showgrid=False, linecolor="#E8E2D7")
        figure.update_yaxes(gridcolor="#EEEAE3", zeroline=False)
        st.plotly_chart(figure, use_container_width=True)


def prevention_section() -> None:
    """Presenta una prioridad explicable sobre los datos históricos disponibles."""
    options = query("SELECT nombre FROM localidades ORDER BY nombre")
    years = query("SELECT DISTINCT EXTRACT(YEAR FROM fecha)::int AS anio FROM siniestros WHERE fecha IS NOT NULL ORDER BY anio")
    with st.container(border=True):
        st.header("Apoyo a la prevención")
        st.caption("Patrones históricos para orientar una revisión preventiva. No son tasas de riesgo ni demuestran causalidad.")
        if options.empty or years.empty:
            st.info("La base aún no contiene localidades y fechas para analizar. Carga los datos con el ETL y vuelve a consultar.")
            return

        filters = st.columns([2, 3], gap="large")
        with filters[0]:
            locality = st.selectbox("Localidad para priorizar", options.nombre.tolist())
        year_values = [int(value) for value in years.anio.tolist()]
        with filters[1]:
            if len(year_values) == 1:
                first_year = last_year = year_values[0]
                st.caption(f"Periodo disponible: {first_year}")
            else:
                first_year, last_year = st.select_slider(
                    "Periodo de análisis", options=year_values,
                    value=(year_values[0], year_values[-1]),
                    format_func=lambda value: str(value),
                )
        start, end = date(int(first_year), 1, 1), date(int(last_year) + 1, 1, 1)
        events = query(EVENTS_SQL, (start, end))
        ids_sql = """SELECT a.codigo_accidente, a.condicion FROM actores_viales a
            JOIN siniestros s ON s.codigo_accidente=a.codigo_accidente
            WHERE s.fecha >= %s AND s.fecha < %s"""
        actors = query(ids_sql, (start, end))
        hypothesis_sql = """SELECT sh.codigo_accidente, h.descripcion FROM siniestro_hipotesis sh
            JOIN hipotesis h ON h.codigo_causa=sh.codigo_causa
            JOIN siniestros s ON s.codigo_accidente=sh.codigo_accidente
            WHERE s.fecha >= %s AND s.fecha < %s"""
        hypotheses = query(hypothesis_sql, (start, end))
        result = summarize(events, actors, hypotheses, locality, events)
        if not result["count"]:
            st.warning("No hay siniestros de esta localidad dentro del periodo seleccionado.")
            return

        metrics = st.columns(4, gap="medium")
        metrics[0].metric("Prioridad analítica", result["priority"])
        metrics[1].metric("Siniestros", f"{result['count']:,}")
        metrics[2].metric("Participación en Bogotá", f"{result['share']:.1%}")
        metrics[3].metric("Con muertos o heridos", f"{result['severe_share']:.1%}")
        st.caption(f"Puntaje transparente: {result['score']} (concentración/volumen: hasta 2; gravedad: hasta 2; recurrencia horaria: hasta 1). Umbrales indicativos del MVP; la participación no ajusta por población, viajes ni extensión territorial.")

    detail, actions = st.columns([1.15, 0.85], gap="large")
    with detail:
        with st.container(border=True):
            st.subheader("Situación y evidencia")
            for reason in result["reasons"]:
                st.markdown(f"- {reason}")
            segment = result["events"]
            chart(segment.gravedad.value_counts().rename_axis("gravedad").reset_index(name="siniestros"), "gravedad", "siniestros", "Gravedad registrada")
            chart(segment.choque.value_counts().head(8).rename_axis("choque").reset_index(name="siniestros"), "choque", "siniestros", "Tipos de choque frecuentes")
    with actions:
        with st.container(border=True):
            st.subheader("Recomendaciones derivadas")
            for action in result["recommendations"]:
                st.markdown(f"- {action}")
            st.caption("Las hipótesis son categorías registradas en los datos; no constituyen causalidad demostrada. La decisión de actuar requiere validación en campo.")


def main() -> None:
    try:
        years, localities, severities = filters()
    except Exception as exc:
        st.title("Siniestros viales en Bogotá D.C.")
        st.error("No fue posible cargar los filtros. Verifica la conexión y que el esquema base esté instalado.")
        st.exception(exc)
        st.stop()
    title_col, pdf_col = st.columns([6.5, 1.25], gap="small", vertical_alignment="center")
    with title_col:
        st.title("Siniestros viales en Bogotá D.C.")
    with pdf_col:
        st.download_button(
            "Descargar PDF",
            data=lambda: exportar_pdf(years, localities, severities),
            file_name="reporte_siniestros_bogota.pdf",
            mime="application/pdf",
            key="exportar_pdf",
            type="primary",
            use_container_width=True,
            on_click="ignore",
        )
    try:
        prevention_section()
    except Exception as exc:
        st.error("No fue posible consultar el módulo preventivo. Verifica la conexión y que el esquema base esté instalado.")
        st.exception(exc)
        st.stop()
    st.divider()
    st.subheader("Exploración histórica")
    st.caption("Análisis exploratorio de riesgo vial. Los filtros se aplican sobre siniestros.")
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
    actor_sql = f"SELECT COALESCE(a.condicion, 'Sin condición') AS actor_vial, count(*) AS involucrados FROM actores_viales a JOIN siniestros s ON s.codigo_accidente=a.codigo_accidente LEFT JOIN localidades l ON l.codigo_localidad=s.codigo_localidad LEFT JOIN gravedades g ON g.codigo_gravedad=s.codigo_gravedad WHERE {where} GROUP BY 1 ORDER BY 2 DESC"
    causes_sql = f"SELECT h.descripcion AS causa, count(*) AS siniestros FROM siniestro_hipotesis sh JOIN hipotesis h ON h.codigo_causa=sh.codigo_causa JOIN siniestros s ON s.codigo_accidente=sh.codigo_accidente LEFT JOIN localidades l ON l.codigo_localidad=s.codigo_localidad LEFT JOIN gravedades g ON g.codigo_gravedad=s.codigo_gravedad WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT 20"
    chart(query(actor_sql, tuple(params)), "actor_vial", "involucrados", "Actores viales")
    chart(query(causes_sql, tuple(params)), "causa", "siniestros", "Principales hipótesis o causas")
    st.subheader("Cruce de riesgo")
    crossing = query(f"SELECT COALESCE(l.nombre, 'Sin localidad') AS localidad, EXTRACT(HOUR FROM s.hora)::int AS hora, COALESCE(g.nombre, 'Sin gravedad') AS gravedad, count(*) AS siniestros {base} GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 100", tuple(params))
    st.dataframe(crossing, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
