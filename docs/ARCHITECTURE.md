# Arquitectura

## Resumen

Sistema con dos partes desacopladas:

1. **Servicio de procesamiento** (Python + GPU): recibe una imagen y
   parámetros, devuelve zonas vectorizadas + paleta sugerida. Sin estado
   propio más allá de un proyecto en curso.
2. **Aplicación web** (Next.js): UI, gestión de usuarios/proyectos/paletas
   guardadas, orquesta las llamadas al servicio de procesamiento.

Diseño pensado para correr 100% local hoy (usando la GPU del usuario) y poder
migrar el servicio de procesamiento a un servidor con GPU en la nube más
adelante sin reescribir nada, moviendo solo el deploy.

Es un **monorepo**: front (`/web`) y back (`/processing-service`) conviven en
el mismo repositorio, como carpetas independientes con su propio entorno y
dependencias. Facilita el desarrollo con un agente de código y no impide que
cada parte se deployee por separado más adelante.

## Entorno Python

- **venv** (no Conda) para el servicio de procesamiento, incluso en
  desarrollo local. Instalar PyTorch con soporte CUDA vía pip dentro del
  venv (`pip install torch --index-url ...` según la versión de CUDA
  instalada).
- Mantener un único `requirements.txt` desde el principio, así el mismo
  archivo sirve para desarrollo local y para el Dockerfile de la Fase 7, sin
  tener que exportar/traducir dependencias de Conda a pip más adelante.

## Servicio de procesamiento (Python)

- **Framework**: FastAPI.
- **Segmentación**: modelo liviano tipo MobileSAM o FastSAM (segmentación por
  objetos/formas reales, no solo por color). Requiere GPU para tiempos
  razonables.
- **Fallback CPU**: k-means clásico sobre color (scikit-learn / OpenCV) para
  cuando no hay GPU disponible o como modo "rápido".
- **Post-procesamiento de contornos**: OpenCV (`findContours`) para pasar de
  máscaras de segmentación a polígonos.
- **Geometría de zonas** (fusionar/dividir): Shapely, para unión y diferencia
  de polígonos.
- **Empaquetado**: Docker con soporte CUDA, pensado para portabilidad futura
  a un servidor con GPU en la nube.

### Responsabilidades

- Recibir imagen + parámetros (cantidad de colores/zonas, área mínima) →
  devolver zonas vectorizadas (SVG/GeoJSON) + paleta de colores sugerida.
- Recibir operaciones de edición de zonas (fusionar A+B, dividir A por una
  línea) → recalcular geometría y renumerar.
- Generar el PDF final a partir del estado actual (zonas + paleta).

## Aplicación web (Next.js + TypeScript + Tailwind)

- Renderiza las zonas como SVG interactivo.
- Usa `turf.js` para preview instantáneo en el cliente de una fusión/división
  antes de confirmarla contra el backend, así la UI se siente ágil.
- Maneja el estado de: imagen actual, parámetros de sliders, paleta activa,
  historial de ediciones manuales (para poder deshacer).

## PDF

- Generado en el servicio de procesamiento (Python) con WeasyPrint o
  ReportLab, a partir del SVG final + tabla de leyenda (número, swatch,
  código hex).
- Mínimo 300dpi. Tamaño de papel (A4/Carta) y orientación configurables.

## Persistencia (base de datos)

- **SQLite**, manejada desde la aplicación web (Next.js), no desde el
  servicio de procesamiento.
- El servicio de procesamiento es stateless respecto a la DB: trabaja con
  archivos/memoria durante la sesión de edición (imagen, zonas, historial de
  undo) y no persiste nada por su cuenta.
- Lo que sí persiste en SQLite es lo que el usuario decide guardar:
  proyectos y paletas custom.

### Esquema inicial (a testear/ajustar en implementación)

```
projects
  id            TEXT PRIMARY KEY (uuid)
  name          TEXT
  mode          TEXT        -- "color" | "bw"
  num_zones     INTEGER
  min_zone_area_px INTEGER
  zones_geojson TEXT        -- último estado de zonas, snapshot en JSON
  palette_colors TEXT       -- paleta activa del proyecto, JSON {numero: hex}
  created_at    DATETIME
  updated_at    DATETIME

palettes
  id            TEXT PRIMARY KEY (uuid)
  name          TEXT
  colors_json   TEXT        -- {numero: hex}
  created_at    DATETIME
```

- No se modela `users` todavía: mientras sea de uso personal, todo es
  single-tenant (sin login). Si más adelante se abre a otros usuarios, se
  agrega una tabla `users` y una FK `user_id` en `projects` y `palettes` sin
  romper el resto del esquema.
- Este esquema es un punto de partida, no definitivo: se valida y ajusta
  durante la implementación de la Fase 4 (editor de paleta) en adelante,
  cuando haya casos reales de uso para probarlo.

## Estructura de carpetas sugerida

```
/processing-service      → FastAPI + modelos + Dockerfile
  /venv                  → entorno virtual (no versionado)
  requirements.txt
  /app
    main.py
    segmentation.py
    geometry.py
    palette.py
    pdf_export.py
/web                      → Next.js app
  /app
  /lib
  /components
  /db
    schema.sql            -- o migraciones, según ORM elegido en implementación
    app.db                -- archivo SQLite (no versionado)
```

## Por qué estas decisiones (contexto)

- Hay GPU disponible en la máquina de desarrollo → habilita segmentación por
  modelo (mejor calidad de zonas que clustering puro por color).
- Uso dual planeado (local ahora, escalar después) → procesamiento
  desacoplado y dockerizado desde el día uno.
- La edición manual de zonas se maneja como polígonos (no como píxeles) →
  necesario para que fusionar/dividir sea preciso y no degrade la imagen.
- SQLite en vez de Postgres → coherente con "local ahora, escalar después":
  cero setup, un archivo, migración directa a Postgres si el proyecto crece.
- venv en vez de Conda → mismo `requirements.txt` sirve para desarrollo
  local y para el Dockerfile de la Fase 7, sin traducir dependencias entre
  gestores más adelante.

## Arquitectura de código (aclaración)

No se usa un patrón formal tipo MVC o hexagonal — sería sobre-ingeniería
para este alcance. Se trabaja con una **arquitectura por capas simple**: cada
módulo (`segmentation.py`, `geometry.py`, `palette.py`, `pdf_export.py`)
tiene una responsabilidad clara y `main.py` los orquesta desde los endpoints
de FastAPI. Si el proyecto escala mucho (múltiples fuentes de datos o
clientes), esto se puede revisar más adelante.