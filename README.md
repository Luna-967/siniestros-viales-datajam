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

## Sistema de apoyo a la prevención

La pestaña principal del dashboard integra un módulo preventivo con los catálogos y tablas que ya carga el ETL: `siniestros`, `localidades`, `gravedades`, `tipos_siniestro`, `tipos_choque`, `disenos_lugar`, `actores_viales`, `siniestro_hipotesis` e `hipotesis`. No requiere tablas ni cambios destructivos en PostgreSQL.

Selecciona una localidad y el intervalo de años. La vista muestra el volumen y su participación en los siniestros bogotanos del mismo periodo, proporción de casos con muertos o heridos entre los que tienen gravedad registrada, patrones horarios, clases y tipos de choque, condiciones de actores, hipótesis registradas y diseño del lugar. Los registros sin dato se excluyen de las proporciones de cada categoría.

### Prioridad explicable

`prevencion.py` aplica reglas explícitas y acumulables: hasta 2 puntos si hay al menos 30 casos (1 punto) y/o una participación local al menos 1.5 veces el reparto uniforme entre localidades (2 puntos); 2 puntos si la proporción de siniestros con muertos o heridos supera en 10 puntos porcentuales la de Bogotá y alcanza al menos 20%; 1 punto si, con al menos 10 horas conocidas, una hora concentra 15% o más. 4 o más puntos = **ALTA**, 2–3 = **MEDIA**, 0–1 = **SEGUIMIENTO**. Sin registros, no asigna prioridad. La interfaz enseña las evidencias y el puntaje.

Las recomendaciones se activan por señales concretas: concentración de volumen → evaluación de campo; gravedad elevada → evaluación prioritaria de sitios graves; concentración horaria → observación y campaña en esa franja; actor/condición frecuente (35% o más) → orientación pedagógica; hipótesis frecuente (20% o más) → validación de campo; diseño repetido (35% o más y 10 casos con dato) → inspección de señalización/condiciones. Cuando ninguna regla se activa, recomienda seguimiento y validación territorial. Son sugerencias analíticas, no órdenes.

**Alcance y límites:** el reparto uniforme entre localidades es una referencia simple de concentración, no una tasa de riesgo. No hay denominadores de población, viajes, flujo vehicular ni longitud vial. Los datos observacionales no prueban causalidad; en particular, hipótesis y condiciones son categorías registradas. No se añade aprendizaje automático porque el MVP busca reglas comprensibles y los datos disponibles no ofrecen por sí solos una etiqueta de resultado futuro. Los umbrales son configurables en `prevencion.py` y deben revisarse con especialistas. Valida en terreno cualquier acción.

### Ejecución y demo

Después de instalar requisitos, configurar `.env`, ejecutar el SQL inicial y cargar el Excel con el ETL, inicia la app:

```powershell
python -m streamlit run dashboard.py
```

Abre **Apoyo a la prevención**, escoge una localidad disponible y deja el intervalo en todo el histórico cargado para una primera demostración. Se muestran situación, evidencia, prioridad y recomendaciones derivadas. El escenario concreto depende de las localidades, fechas y patrones que efectivamente haya cargado la base; no se incluye una demostración ficticia ni datos de ejemplo inventados. Si no hay datos, ejecuta primero `python etl_siniestros.py`.

## Dashboard exploratorio

El dashboard filtra por año, localidad y gravedad. Muestra siniestros por año, localidad, hora, gravedad y tipo, además de vehículos, actores viales, hipótesis y un cruce de localidad-hora-gravedad. Los conteos sirven para identificar concentración de eventos; no representan por sí solos una tasa de riesgo ni prueban causalidad.

## Publicación en GitHub

`.gitignore` excluye `.env`, entornos virtuales, cachés y archivos de datos pesados. Sube el Excel solo si tienes autorización para redistribuirlo. Antes de publicar, confirma que `git status` no muestre `.env` ni archivos con credenciales.
