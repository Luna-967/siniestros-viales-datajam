"""Filtered PDF exports for the Streamlit siniestros dashboard."""
from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Sequence
from xml.sax.saxutils import escape

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from prevencion import summarize
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

load_dotenv()

RED = colors.HexColor("#E4032E")
YELLOW = colors.HexColor("#FAB62D")
INK = colors.HexColor("#252525")
PALE = colors.HexColor("#FFF8E8")
GRID = colors.HexColor("#E8E2D7")


def _db_config() -> dict[str, str]:
    names = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise RuntimeError("Faltan variables de conexión en .env: " + ", ".join(missing))
    return {name[2:].lower(): os.environ[name] for name in names}


def _filters(years: Sequence[int], localities: Sequence[str], severities: Sequence[int], alias="s"):
    clauses, params = ["1=1"], []
    if years:
        clauses.append(f"EXTRACT(YEAR FROM {alias}.fecha)::int = ANY(%s)")
        params.append(list(years))
    if localities:
        clauses.append("l.nombre = ANY(%s)")
        params.append(list(localities))
    if severities:
        clauses.append(f"{alias}.codigo_gravedad = ANY(%s)")
        params.append(list(severities))
    return " AND ".join(clauses), tuple(params)


def _read(sql: str, params: tuple) -> pd.DataFrame:
    connection = psycopg2.connect(**_db_config())
    try:
        return pd.read_sql_query(sql, connection, params=params)
    finally:
        connection.close()


def _filtered_data(years, localities, severities):
    where, params = _filters(years, localities, severities)
    events_sql = f"""SELECT s.codigo_accidente, s.fecha, s.hora,
        EXTRACT(HOUR FROM s.hora)::int AS hora_numero,
        s.codigo_gravedad, COALESCE(g.nombre, 'Sin gravedad') AS gravedad,
        COALESCE(ts.nombre, 'Sin tipo') AS clase,
        COALESCE(tc.nombre, 'Sin tipo') AS choque,
        COALESCE(ofj.nombre, 'Sin dato') AS objeto_fijo,
        s.direccion, COALESCE(l.nombre, 'Sin localidad') AS localidad,
        COALESCE(dl.nombre, 'Sin dato') AS diseno_lugar
        FROM siniestros s
        LEFT JOIN localidades l ON l.codigo_localidad=s.codigo_localidad
        LEFT JOIN gravedades g ON g.codigo_gravedad=s.codigo_gravedad
        LEFT JOIN tipos_siniestro ts ON ts.codigo_clase=s.codigo_clase
        LEFT JOIN tipos_choque tc ON tc.codigo_choque=s.codigo_choque
        LEFT JOIN objetos_fijos ofj ON ofj.codigo_objeto_fijo=s.codigo_objeto_fijo
        LEFT JOIN disenos_lugar dl ON dl.codigo_diseno=s.codigo_diseno
        WHERE {where}
        ORDER BY s.fecha, s.hora, s.codigo_accidente"""
    actor_sql = f"""SELECT a.codigo_accidentado, a.codigo_accidente, a.fecha,
        a.condicion, a.estado, a.edad, a.sexo
        FROM actores_viales a JOIN siniestros s ON s.codigo_accidente=a.codigo_accidente
        LEFT JOIN localidades l ON l.codigo_localidad=s.codigo_localidad
        LEFT JOIN gravedades g ON g.codigo_gravedad=s.codigo_gravedad
        WHERE {where} ORDER BY a.codigo_accidente, a.codigo_accidentado"""
    hypothesis_sql = f"""SELECT sh.codigo_accidente, sh.codigo_causa, h.descripcion
        FROM siniestro_hipotesis sh JOIN hipotesis h ON h.codigo_causa=sh.codigo_causa
        JOIN siniestros s ON s.codigo_accidente=sh.codigo_accidente
        LEFT JOIN localidades l ON l.codigo_localidad=s.codigo_localidad
        LEFT JOIN gravedades g ON g.codigo_gravedad=s.codigo_gravedad
        WHERE {where} ORDER BY sh.codigo_accidente, sh.codigo_causa"""
    return (_read(events_sql, params), _read(actor_sql, params), _read(hypothesis_sql, params))


def _scope(years, localities, severities) -> str:
    parts = []
    if years:
        parts.append("Año: " + ", ".join(map(str, years)))
    if localities:
        parts.append("Localidad: " + ", ".join(localities))
    if severities:
        labels = {1: "Con muertos", 2: "Con heridos", 3: "Solo daños"}
        parts.append("Gravedad: " + ", ".join(labels.get(int(code), f"Código {code}") for code in severities))
    return " | ".join(parts) if parts else "Todos los registros disponibles"


def _paragraph(value, style):
    return Paragraph(escape(str(value)), style)


def _summaries(events: pd.DataFrame, actors: pd.DataFrame, hypotheses: pd.DataFrame):
    total = len(events)
    known = events["codigo_gravedad"].dropna()
    severe = float(known.isin([1, 2]).mean()) if not known.empty else 0.0
    metrics = [
        ("Siniestros", total),
        ("Localidades con registros", int(events.loc[events.localidad != "Sin localidad", "localidad"].nunique())),
        ("Casos con muertos o heridos (gravedad conocida)", f"{severe:.1%}"),
        ("Actores registrados", len(actors)),
        ("Hipótesis asociadas", len(hypotheses)),
    ]
    grouped = {
        "Por año": events.loc[events.fecha.notna()].assign(anio=lambda x: pd.to_datetime(x.fecha).dt.year).groupby("anio").size().sort_values(ascending=False),
        "Por localidad": events.localidad.value_counts().head(10),
        "Por gravedad": events.gravedad.value_counts(),
        "Por clase": events.clase.value_counts().head(10),
        "Por tipo de choque": events.choque.value_counts().head(10),
    }
    if not events.empty and events.hora.notna().any():
        hours = pd.to_datetime(events.hora.astype(str), errors="coerce").dt.hour.dropna()
        grouped["Por hora"] = hours.value_counts().sort_index().head(24)
    if not actors.empty and actors.condicion.notna().any():
        grouped["Actores por condición"] = actors.condicion.value_counts().head(10)
    if not hypotheses.empty and hypotheses.descripcion.notna().any():
        grouped["Hipótesis registradas"] = hypotheses.descripcion.value_counts().head(10)
    return metrics, grouped


def _prevention_by_locality(years, severities) -> pd.DataFrame:
    """Run the same prevention rules for each locality in the selected period."""
    events, actors, hypotheses = _filtered_data(years, [], severities)
    names = sorted(name for name in events.localidad.dropna().unique() if name != "Sin localidad")
    period = ", ".join(map(str, years)) if years else "Todos los años disponibles"
    rows = []
    for locality in names:
        result = summarize(events, actors, hypotheses, locality, events)
        rows.append({
            "Localidad": locality,
            "Periodo": period,
            "Siniestros": result["count"],
            "Participación en Bogotá": result.get("share", 0),
            "Veces el referente uniforme": result.get("concentration", 0),
            "Casos graves en localidad": result.get("severe_share", 0),
            "Casos graves en Bogotá": result.get("city_severe_share", 0),
            "Puntaje": result["score"],
            "Prioridad": result["priority"],
            "Motivos": "\n".join(result["reasons"]),
            "Recomendaciones": "\n".join(result["recommendations"]),
        })
    return pd.DataFrame(rows, columns=[
        "Localidad", "Periodo", "Siniestros", "Participación en Bogotá",
        "Veces el referente uniforme", "Casos graves en localidad",
        "Casos graves en Bogotá", "Puntaje", "Prioridad", "Motivos",
        "Recomendaciones",
    ])


def _pdf_table(rows, widths, header=True):
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    styles = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), .45, GRID),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
    ]
    if header:
        styles.extend([("BACKGROUND", (0, 0), (-1, 0), RED), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")])
        if len(rows) > 1:
            styles.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]))
    table.setStyle(TableStyle(styles))
    return table


def exportar_pdf(years: Sequence[int], localities: Sequence[str], severities: Sequence[int]) -> bytes:
    """Create a filtered PDF summary; called lazily from the download button."""
    events, actors, hypotheses = _filtered_data(years, localities, severities)
    metrics, summaries = _summaries(events, actors, hypotheses)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=.6*inch, leftMargin=.6*inch, topMargin=.65*inch, bottomMargin=.6*inch, title="Reporte de siniestros viales de Bogotá")
    sample = getSampleStyleSheet()
    title_style = ParagraphStyle("BogotaTitle", parent=sample["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=INK, alignment=TA_LEFT, spaceAfter=5)
    subtitle = ParagraphStyle("BogotaSubtitle", parent=sample["Normal"], fontSize=9, leading=13, textColor=colors.HexColor("#5C5C5C"), spaceAfter=12)
    section = ParagraphStyle("BogotaSection", parent=sample["Heading2"], fontSize=13, leading=16, textColor=INK, spaceBefore=10, spaceAfter=7)
    locality_title = ParagraphStyle("BogotaLocality", parent=sample["Heading3"], fontSize=11, leading=14, textColor=RED, spaceBefore=8, spaceAfter=4)
    small = ParagraphStyle("BogotaSmall", parent=sample["Normal"], fontSize=8, leading=10, textColor=INK)
    body = ParagraphStyle("BogotaBody", parent=sample["Normal"], fontSize=8.5, leading=11, textColor=INK, spaceAfter=3)
    story = [
        Paragraph("Siniestros viales en Bogotá D.C.", title_style),
        _paragraph(f"Resumen de registros filtrados · {_scope(years, localities, severities)} · Generado {datetime.now():%Y-%m-%d %H:%M}", subtitle),
        Paragraph("Resumen ejecutivo", section),
    ]
    metric_rows = [[_paragraph("Indicador", small), _paragraph("Resultado", small)]]
    metric_rows.extend([[_paragraph(k, small), _paragraph(v, small)] for k, v in metrics])
    story.extend([_pdf_table(metric_rows, [4.9*inch, 1.4*inch]), Spacer(1, 8)])
    for label, values in summaries.items():
        if values is None or len(values) == 0:
            continue
        story.append(_paragraph(label, section))
        rows = [[_paragraph("Categoría", small), _paragraph("Siniestros", small)]]
        rows.extend([[_paragraph(key, small), _paragraph(f"{int(value):,}", small)] for key, value in values.items()])
        story.append(_pdf_table(rows, [4.9*inch, 1.4*inch]))

    story.append(Paragraph("Apoyo a la prevención por localidad", section))
    story.append(_paragraph(
        "Aplicación de las reglas explicables del dashboard a cada localidad, "
        "comparadas para el mismo periodo y gravedad. La participación es una "
        "referencia descriptiva y no una tasa de riesgo ajustada por exposición.", body
    ))
    prevention = _prevention_by_locality(years, severities)
    if prevention.empty:
        story.append(_paragraph("No hay localidades con registros para los filtros seleccionados.", body))
    else:
        for _, row in prevention.iterrows():
            story.append(Paragraph(escape(str(row["Localidad"])), locality_title))
            locality_metrics = [
                [_paragraph("Indicador", small), _paragraph("Resultado", small)],
                [_paragraph("Prioridad / puntaje", small), _paragraph(f"{row['Prioridad']} / {row['Puntaje']}", small)],
                [_paragraph("Siniestros / participación", small), _paragraph(f"{int(row['Siniestros']):,} / {row['Participación en Bogotá']:.1%}", small)],
                [_paragraph("Veces el referente uniforme", small), _paragraph(f"{row['Veces el referente uniforme']:.2f}", small)],
                [_paragraph("Casos graves: localidad / Bogotá", small), _paragraph(f"{row['Casos graves en localidad']:.1%} / {row['Casos graves en Bogotá']:.1%}", small)],
            ]
            story.append(_pdf_table(locality_metrics, [2.8*inch, 3.5*inch]))
            story.append(_paragraph("Evidencia: " + str(row["Motivos"]), body))
            story.append(_paragraph("Recomendaciones: " + str(row["Recomendaciones"]), body))
    if events.empty:
        story.append(_paragraph("No hay siniestros para los filtros seleccionados.", sample["Normal"]))

    def page_footer(canvas, document):
        canvas.saveState()
        page_width, _ = letter
        canvas.setFillColor(YELLOW)
        canvas.rect(.6*inch, .38*inch, page_width-1.2*inch, 3, fill=1, stroke=0)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#5C5C5C"))
        canvas.drawRightString(page_width-.6*inch, .22*inch, f"Página {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    return buffer.getvalue()

