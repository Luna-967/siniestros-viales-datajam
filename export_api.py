"""Optional REST endpoints for filtered dashboard exports.

Run with: uvicorn export_api:app --host 127.0.0.1 --port 8000
Repeated filters are supported, for example ?anio=2022&localidad=Suba&gravedad=1.
"""
from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.responses import Response

from export_service import exportar_pdf

app = FastAPI(title="API de exportación de siniestros", docs_url="/api/docs")


@app.get("/api/siniestros/exportar-pdf", response_class=Response, tags=["Siniestros"])
def exportar_siniestros_pdf(
    anio: list[int] = Query(default=[]),
    localidad: list[str] = Query(default=[]),
    gravedad: list[int] = Query(default=[]),
) -> Response:
    content = exportar_pdf(anio, localidad, gravedad)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="reporte_siniestros_bogota.pdf"'},
    )
