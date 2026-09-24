# Roadmap de implementación

## Fase 1 — Prototipo de segmentación (sin UI)

**Objetivo**: validar que la segmentación funciona bien y corre en la GPU
local.

- Script Python que reciba una imagen y devuelva zonas (usando
  MobileSAM/FastSAM + OpenCV).
- Probar con 3–5 imágenes de prueba distintas (foto realista, ilustración
  simple, imagen ya en blanco y negro).
- **Hecho cuando**: el script genera zonas razonables (sin ruido excesivo)
  en tiempos aceptables (menos de 10s por imagen) usando la GPU.

## Fase 2 — Servicio de procesamiento (API)

**Objetivo**: envolver el prototipo en una API según `API_CONTRACT.md`.

- Implementar `/segment` primero.
- Agregar generación de paletas sugeridas.
- **Hecho cuando**: se puede llamar a `/segment` con curl/Postman y obtener
  una respuesta válida.

## Fase 3 — Frontend: flujo básico

**Objetivo**: subir imagen, ver preview, sin edición manual todavía.

- Pantalla de upload + sliders + preview del resultado (pasos 1–3 de
  `FUNCTIONAL_SPEC.md`).
- **Hecho cuando**: un usuario puede subir una imagen y ver el resultado
  segmentado con números.

## Fase 4 — Editor de paleta

- Implementar el editor de paleta (paso 5 de `FUNCTIONAL_SPEC.md`),
  conectado al preview a color.
- **Hecho cuando**: cambiar un color en la paleta actualiza el preview en
  vivo.

## Fase 5 — Edición manual de zonas

**Objetivo**: implementar fusionar/dividir (paso 4 de `FUNCTIONAL_SPEC.md`).

- Endpoints `/zones/merge` y `/zones/split` en el backend.
- Herramientas de UI (modo fusionar / modo dividir) + renumeración
  automática.
- **Hecho cuando**: se puede fusionar dos zonas y dividir una zona desde la
  UI, y la numeración se actualiza sin huecos.

## Fase 6 — Export a PDF

- Endpoint `/export/pdf`.
- Botón de descarga en la UI con opciones de tamaño/orientación.
- **Hecho cuando**: se descarga un PDF a 300dpi, imprimible, con leyenda de
  colores.

## Fase 7 — Empaquetado para escalar

- Dockerizar el servicio de procesamiento.
- Documentar las variables de entorno necesarias para correrlo en un
  servidor con GPU en la nube.
- **Hecho cuando**: el servicio corre igual dentro de un container Docker
  con acceso a GPU (por ejemplo `--gpus all`).

## Fase 8 — Testing y validación con casos reales

**Objetivo**: cerrar dos huecos que quedaron pendientes de todas las fases
anteriores: nunca se probó con fotos reales (solo placeholders sintéticos
de la Fase 1), y no hay ningún test automatizado de la lógica de negocio
más frágil (merge/split, renumeración, generación de paletas).

- Probar el pipeline completo con 5-10 fotos reales (no sintéticas),
  variadas: retratos, paisajes, objetos con fondos complejos, imágenes de
  baja/alta resolución.
- Evaluar si la calidad de segmentación es usable en la práctica, o si hay
  que ajustar parámetros (num_zones, min_zone_area_px, umbral del modelo).
- Agregar tests automatizados (no exhaustivos, sí de la lógica crítica):
  reglas de merge (color/número que se conserva, umbral del 20%), reglas
  de split (pieza mayor conserva identidad), renumeración contigua sin
  huecos, y generación de paletas sugeridas.
- **Hecho cuando**: hay evidencia concreta (capturas o archivos de salida)
  de que el pipeline funciona bien con fotos reales, y existe una suite de
  tests que corre en un solo comando y cubre las reglas de negocio de
  merge/split/renumeración/paletas.