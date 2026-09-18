# Data Jam: siniestros viales de Bogotá D.C.

Pipeline reproducible para cargar y analizar siniestros viales: **Excel → Pandas → PostgreSQL → Streamlit/Plotly**. El ETL está adaptado a las columnas reales de `SINIESTROS`, `ACTOR_VIAL`, `VEHICULOS`, `HIPOTESIS` y `DICCIONARIO`.

## Requisitos

- Python 3.10 o superior.
- PostgreSQL 14 o superior.
- El libro `siniestros_viales_consolidados_bogota_dc.xlsx`.

## Instalación y ejecución

1. Cree una base vacía llamada `siniestros_bogota` en PostgreSQL.
2. En pgAdmin, ejecute [base_siniestros_bogota.sql](base_siniestros_bogota.sql).
3. Cree y active un entorno virtual:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

4. Instale dependencias:

   ```powershell
   pip install -r requirements.txt
   ```

5. Copie `.env.example` como `.env` y complete la conexión y la ruta absoluta del Excel. Nunca publique `.env`.
6. Ejecute el ETL:

   ```powershell
   python etl_siniestros.py
   ```

7. Inicie el dashboard:

   ```powershell
   python -m streamlit run dashboard.py
   ```

## Funcionamiento del ETL

- Lee las cinco hojas, muestra dimensiones y columnas, y trabaja sobre copias en memoria: el archivo fuente no se modifica.
- Convierte vacíos a `NULL`, fechas a `DATE`, horas a `TIME` y códigos a enteros.
- Construye los catálogos desde `DICCIONARIO` y valida códigos antes de la promoción.
- Carga staging mediante `COPY`, evitando inserciones fila a fila.
- Inserta en el orden correcto: catálogos → siniestros → actores/vehículos → relación siniestro-hipótesis.
- Detecta duplicados e impide repeticiones con PK, claves compuestas, `ON CONFLICT` y comparaciones `IS NOT DISTINCT FROM`.
- Conserva las claves foráneas: los detalles sin siniestro padre no se cargan y se registran en `staging.rechazos_carga`.
- Usa una transacción: cualquier fallo importante revierte la etapa en curso.

`VEHICULO` no sirve como PK porque puede repetirse, o estar vacío, en distintos siniestros. `id_vehiculo` es una PK artificial; la deduplicación compara el accidente y los atributos disponibles.

En `hipotesis`, `codigo_causa` es la PK. `descripcion` no es única porque el diccionario fuente contiene descripciones repetidas para códigos diferentes.

## Dashboard

El dashboard filtra por año, localidad y gravedad. Muestra siniestros por año, localidad, hora, gravedad y tipo, además de vehículos, actores viales, hipótesis y un cruce de localidad-hora-gravedad. Los conteos sirven para identificar concentración de eventos; no representan por sí solos una tasa de riesgo ni prueban causalidad.

## Publicación en GitHub

`.gitignore` excluye `.env`, entornos virtuales, cachés y archivos de datos pesados. Sube el Excel solo si tienes autorización para redistribuirlo. Antes de publicar, confirma que `git status` no muestre `.env` ni archivos con credenciales.
