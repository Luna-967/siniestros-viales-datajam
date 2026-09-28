"""Análisis descriptivo y reglas explicables para priorizar prevención vial.

Las señales describen concentración histórica; no estiman causalidad ni tasas
de exposición. Requiere las tablas creadas por base_siniestros_bogota.sql.
"""
from __future__ import annotations

import pandas as pd


EVENTS_SQL = """
SELECT s.codigo_accidente, s.fecha, s.hora, s.codigo_gravedad,
       COALESCE(g.nombre, 'Sin dato') AS gravedad,
       COALESCE(ts.nombre, 'Sin dato') AS clase,
       COALESCE(tc.nombre, 'Sin dato') AS choque,
       COALESCE(dl.nombre, 'Sin dato') AS diseno_lugar,
       EXTRACT(HOUR FROM s.hora)::int AS hora_numero,
       COALESCE(l.nombre, 'Sin localidad') AS localidad
FROM siniestros s
LEFT JOIN gravedades g ON g.codigo_gravedad=s.codigo_gravedad
LEFT JOIN tipos_siniestro ts ON ts.codigo_clase=s.codigo_clase
LEFT JOIN tipos_choque tc ON tc.codigo_choque=s.codigo_choque
LEFT JOIN disenos_lugar dl ON dl.codigo_diseno=s.codigo_diseno
LEFT JOIN localidades l ON l.codigo_localidad=s.codigo_localidad
WHERE s.fecha >= %s AND s.fecha < %s
"""


def summarize(events: pd.DataFrame, actors: pd.DataFrame, hypotheses: pd.DataFrame,
              locality: str, city_events: pd.DataFrame) -> dict:
    """Return evidence, a transparent score, and evidence-dependent actions."""
    segment = events.loc[events.localidad == locality].copy()
    city = city_events.copy()
    if segment.empty:
        return {"locality": locality, "count": 0, "priority": "SIN DATOS",
                "score": 0, "reasons": [], "recommendations": [],
                "events": segment, "city_count": len(city)}

    count, city_count = len(segment), len(city)
    known_severity = segment.codigo_gravedad.dropna()
    city_known_severity = city.codigo_gravedad.dropna()
    severe_share = float(known_severity.isin([1, 2]).mean()) if not known_severity.empty else 0.0
    city_severe_share = float(city_known_severity.isin([1, 2]).mean()) if not city_known_severity.empty else 0.0
    share = count / city_count if city_count else 0.0
    locality_count = max(1, int(city.loc[city.localidad != "Sin localidad", "localidad"].nunique()))
    # Compare observed locality share to an even-share reference. This is a
    # screening signal, not a population/exposure-adjusted risk estimate.
    concentration = share / (1 / locality_count)

    reasons: list[str] = []
    recommendations: list[str] = []
    score = 0
    reasons.append(f"{count:,} siniestros en {locality} de {city_count:,} registrados en Bogotá durante el periodo ({share:.1%}).")
    for column, label in (("clase", "clase de siniestro"), ("choque", "tipo de choque")):
        values = segment[column].dropna()
        values = values[values != "Sin dato"]
        if not values.empty:
            reasons.append(f"{label.capitalize()} más frecuente: «{values.value_counts().index[0]}» ({values.value_counts(normalize=True).iloc[0]:.1%} de los registros con dato).")
    if count >= 30 and concentration >= 1.5:
        score += 2
        reasons.append(f"La participación local es {concentration:.1f} veces el referente de reparto uniforme entre {locality_count} localidades.")
        recommendations.append("Priorizar una evaluación de campo en esta localidad y contrastar los puntos con mayor concentración antes de definir obras o controles.")
    elif count >= 30:
        score += 1
        reasons.append("El volumen histórico permite revisar agrupaciones internas, aunque su participación no supera el umbral de concentración definido.")

    if severe_share >= city_severe_share + 0.10 and severe_share >= 0.20:
        score += 2
        reasons.append(f"Siniestros con muertos o heridos: {severe_share:.1%}, frente a {city_severe_share:.1%} en Bogotá para el mismo periodo.")
        recommendations.append("Dar prioridad a la valoración de los sitios asociados a eventos graves y revisar medidas preventivas con el equipo local de seguridad vial.")

    # La columna SQL s.hora (TIME) y la hora entera extraída deben tener
    # nombres distintos; read_sql_query no garantiza columnas únicas.
    hour_column = "hora_numero" if "hora_numero" in segment.columns else "hora"
    timed = segment.dropna(subset=[hour_column])
    if not timed.empty:
        top_hour = int(timed[hour_column].value_counts().index[0])
        top_hour_share = float((timed[hour_column] == top_hour).mean())
        if len(timed) >= 10 and top_hour_share >= 0.15:
            score += 1
            reasons.append(f"La franja {top_hour:02d}:00–{top_hour:02d}:59 concentra {top_hour_share:.1%} de los registros con hora conocida.")
            recommendations.append(f"Programar observación preventiva y campañas en la franja {top_hour:02d}:00–{top_hour:02d}:59; validar disponibilidad y condiciones en campo.")

    local_ids = set(segment.codigo_accidente)
    local_actors = actors.loc[actors.codigo_accidente.isin(local_ids)] if not actors.empty else actors
    if not local_actors.empty:
        actor_col = "condicion"
        valid = local_actors[actor_col].dropna()
        if not valid.empty:
            name = str(valid.value_counts().index[0])
            proportion = float((valid == name).mean())
            if proportion >= 0.35:
                reasons.append(f"El actor/condición más frecuente es «{name}» ({proportion:.1%} de actores con dato).")
                recommendations.append(f"Orientar una acción pedagógica a personas registradas como «{name}», adaptando el contenido a la revisión local.")

    local_hyp = hypotheses.loc[hypotheses.codigo_accidente.isin(local_ids)] if not hypotheses.empty else hypotheses
    if not local_hyp.empty and "descripcion" in local_hyp:
        valid = local_hyp.descripcion.dropna()
        if not valid.empty:
            name = str(valid.value_counts().index[0])
            proportion = float((valid == name).mean())
            if proportion >= 0.20:
                reasons.append(f"La hipótesis más registrada es «{name}» ({proportion:.1%} de hipótesis asociadas); es un registro analítico, no una causa demostrada.")
                recommendations.append("Revisar en campo si las circunstancias vinculadas a la hipótesis registrada son observables y si una acción preventiva resulta pertinente.")

    designs = segment.diseno_lugar.dropna()
    if not designs.empty:
        design = str(designs.value_counts().index[0])
        proportion = float((designs == design).mean())
        if len(designs) >= 10 and proportion >= 0.35:
            reasons.append(f"El diseño del lugar más frecuente es «{design}» ({proportion:.1%} de los registros con dato).")
            recommendations.append(f"Inspeccionar señalización y condiciones del diseño «{design}» en los lugares concretos asociados; el patrón por sí solo no atribuye causalidad.")

    priority = "ALTA" if score >= 4 else "MEDIA" if score >= 2 else "SEGUIMIENTO"
    if not recommendations:
        recommendations.append("Mantener seguimiento de los registros y completar la verificación territorial; los indicadores actuales no activan una recomendación específica.")
    return {"locality": locality, "count": count, "city_count": city_count,
            "share": share, "concentration": concentration,
            "severe_share": severe_share, "city_severe_share": city_severe_share,
            "score": score, "priority": priority, "reasons": reasons,
            "recommendations": recommendations, "events": segment,
            "actors": local_actors, "hypotheses": local_hyp}
