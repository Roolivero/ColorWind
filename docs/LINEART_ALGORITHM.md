# Investigación: algoritmo de segmentación para modo Line-Art

## Estado de implementación

- Etapa 1 implementada en `processing-service/app/lineart.py`.
- Para `mode="bw"`, `/segment` usa un pipeline CPU de line-art basado en
  binarización adaptativa, cierre morfológico de líneas y
  `connectedComponentsWithStats`.
- Los overlays de inspección se guardan en
  `processing-service/runtime/lineart-overlays/` cuando la llamada entra por
  la API, y en `processing-service/outputs/lineart-stage1/` para las pruebas
  manuales de esta etapa.
- Etapa 3 implementada: las zonas grandes se subdividen con cortes curvos
  internos sobre el polígono de cada región. SLIC queda disponible como
  fallback técnico, pero no es la estrategia principal porque en pruebas
  reales generó celdas demasiado hexagonales/Voronoi.
- Etapa 4 implementada: las subzonas por debajo de `min_area_px` se
  intentan fusionar automáticamente con la vecina de mayor área del mismo
  parent de subdivisión, usando la unión geométrica de `processing-service/app/geometry.py`.
  Si una subzona no tiene vecino fusionable, queda reportada en métricas.
- Etapa 5 implementada: para `mode="bw"`, `paint_number` se asigna con
  coloreo greedy de un grafo de adyacencias entre zonas, limitado por
  `num_colors`.
- Posicionamiento avanzado con pole of inaccessibility implementado para
  PDF/SVG numerado.

## 1. Diagnóstico

FastSAM (y toda la familia SAM) está entrenado para detectar **objetos
semánticos**: agrupa píxeles según features visuales aprendidas (textura,
color, forma reconocible). Funciona muy bien en fotos, donde cada objeto
tiene señal visual propia.

Un dibujo line-art es casi lo opuesto: fondo blanco uniforme, líneas negras
finas, sin textura ni color interno. Para un modelo entrenado en "objetos",
el interior del oso y el interior del árbol son visualmente indistinguibles
(ambos son "blanco liso"), así que colapsan en una sola zona gigante. El
modelo no está fallando por un mal parámetro: está resolviendo el problema
para el que fue diseñado (semántica de objetos), que no es el problema que
tenemos (topología de regiones cerradas por líneas).

Conclusión: para line-art hay que reemplazar la detección semántica por
**detección topológica** — qué áreas quedan encerradas por las líneas del
dibujo, sin importar qué "objeto" representan.

## 2. Algoritmo recomendado

Pipeline para `mode: "bw"` (line-art):

1. **Preprocesamiento**: escala de grises, binarización (umbral adaptativo
   u Otsu), denoise leve si la imagen viene escaneada (median blur).
2. **Cierre de líneas**: operación morfológica de cierre (dilatar + erosionar)
   con kernel chico, para tapar micro-cortes en el trazo. El tamaño del
   kernel se deriva de `detail_level`, no lo elige el usuario directamente.
3. **Extracción de regiones cerradas**: invertir la máscara (el área
   pintable queda en blanco) y correr `connectedComponentsWithStats` —
   esto da directamente las regiones encerradas por líneas, que es
   exactamente la estructura de una lámina de colorear.
4. **Filtrado de ruido**: la detección inicial usa un umbral interno
   permisivo (`component_min_area_px`) para no perder regiones reales del
   dibujo. Ese umbral es más bajo que `min_area_px`, que se reserva para
   decidir subdivisión/fusión y controlar el nivel de detalle. Esto evita
   huecos blancos sin zona en láminas densas.
5. **Subdivisión de regiones grandes**: cualquier región por encima de un
   umbral de área se subdivide con cortes curvos internos, generados sobre
   el polígono de la región y proporcionales al área (parámetro interno, no
   expuesto al usuario). Esto evita el patrón hexagonal/Voronoi que apareció
   con SLIC y se acerca más a las curvas típicas de láminas comerciales.
6. **Vectorización**: `findContours` sobre cada región final, simplificado
   con `approxPolyDP` o `shapely.simplify()`, para bordes limpios sin
   ruido de píxeles.
7. **Asignación de `paint_number`**: cada zona geométrica (`zone_id`) recibe
   un número entre 1 y `num_colors` (ver sección 6).
8. **Supresión de etiquetas en fragmentos chicos**: las zonas demasiado
   chicas para sostener un número legible heredan el `paint_number` de una
   zona cercana y se marcan con `suppress_label=true`. Siguen coloreadas y
   forman parte del dibujo, pero no agregan un número propio que ensucie la
   lámina.
9. **Posicionamiento de números**: sobre cada zona final etiquetable (ver
   sección 8).

Este pipeline corre 100% en CPU — no depende de GPU, aunque esté disponible.

## 3. Alternativas evaluadas

| Técnica | Rol recomendado |
|---|---|
| Connected components / flood fill | **Motor principal** para extraer regiones cerradas. Simple, determinístico, rápido. Depende de que las líneas estén cerradas (por eso el paso de cierre morfológico previo). |
| Cortes curvos con Shapely | **Motor principal actual** para subdividir regiones grandes en modo line-art. Produce bordes más parecidos a láminas comerciales que SLIC en imágenes reales. |
| Watershed | Alternativa para el paso de subdivisión (semilla por distance transform), pero sin gradiente real (línea binaria) no aportó una ventaja clara. |
| Superpixels (SLIC) | Fallback técnico disponible. En pruebas reales tendió a celdas hexagonales/Voronoi, por eso dejó de ser la estrategia principal. |
| Superpixels (Felzenszwalb) | Mejor para segmentación natural de fotos a color (no da control fino de cantidad de celdas); no recomendado para este paso. |
| Contour extraction | No es un método de segmentación en sí, es el paso de vectorización final — se usa siempre. |
| SAM / FastSAM / MobileSAM | Se mantiene como motor para `mode: "color"` (fotos), donde sí hay señal semántica real. Se **descarta explícitamente** para `mode: "bw"`. |
| Vectorización de línea (potrace) | Útil para prolijar el trazo original en el SVG final (líneas más limpias al imprimir), es un problema aparte de la detección de zonas. |

## 4. Librerías recomendadas

- **OpenCV**: binarización, morfología, `connectedComponentsWithStats`,
  `findContours`, `approxPolyDP`.
- **scikit-image**: `skimage.segmentation.slic` queda como fallback de subdivisión.
- **Shapely**: construcción de polígonos, `simplify()`, `representative_point()`, y reuso de la lógica de unión de la Fase 5 para fusionar microzonas.
- **NumPy**: soporte general de arrays.
- **scipy.ndimage** (`distance_transform_edt`): para posicionamiento robusto de números (sección 8).
- Opcional: **potrace** (vía binding Python), solo para pulir el trazo de línea en el render final, no para detectar zonas.

## 5. Parámetros

**Expuestos al usuario**: `num_colors`, `detail_level`.

**Internos, derivados de `detail_level`**: tamaño de kernel de cierre de
líneas, `component_min_area_px` para detectar regiones sin dejar huecos,
`min_area_px` para subdivisión/fusión, tamaño objetivo de subzona curva y
tolerancia de simplificación de polígonos.

## 6. Estrategia para `paint_number`

- **Modo color**: se mantiene lo ya construido — color real promedio de
  cada zona, agrupado por k-means en `num_colors` clusters, y
  `paint_number = cluster_id + 1`.
- **Modo line-art (sin color de origen)**: no hay señal de color para
  agrupar. Se modela como un problema de **coloreo de grafos**: se arma un
  grafo de adyacencia entre zonas (comparten borde visual dentro de una
  tolerancia de trazo), y se aplica un algoritmo greedy de coloreo limitado
  a `num_colors` colores, priorizando que zonas vecinas no compartan número.
  Si `num_colors` es muy chico respecto a la cantidad de zonas, es esperable
  (y normal en láminas comerciales) que haya colisiones entre zonas no
  contiguas — el objetivo es minimizar colisiones entre vecinas directas, no
  eliminarlas del todo.
- Las zonas por debajo del umbral de legibilidad de etiqueta heredan el
  `paint_number` de una zona cercana y no muestran número propio. Esto evita
  que texturas densas del line-art generen una nube de números dentro de una
  misma zona visual.

## 7. Subdivisión de regiones grandes

La estrategia principal actual usa cortes curvos iterativos sobre las zonas
grandes: se elige la pieza de mayor área y se intenta partirla con una línea
curva hasta acercarse al nivel de detalle objetivo. SLIC queda como fallback,
pero las pruebas visuales mostraron que tiende a un patrón hexagonal que no
coincide con la estética buscada. Subdivisión por grilla regular se descarta:
el resultado se ve artificial, no como una lámina comercial.

## 8. Posicionamiento de números

Implementado en `processing-service/app/pdf_export.py`: se mantiene
`representative_point()` de Shapely como primer intento barato. Si el punto
no queda dentro del polígono erosionado por el radio necesario para el
número, se usa **"pole of inaccessibility"** (el punto más alejado posible
de cualquier borde del polígono) calculado con
`scipy.ndimage.distance_transform_edt` sobre la máscara de la zona. Si ni
así entra un número legible, se reduce el tamaño de fuente progresivamente
y se reportan métricas de placement para inspección.

## 9. Riesgos y mitigaciones

- **Líneas abiertas**: el cierre morfológico previo lo resuelve, pero un
  kernel muy grande puede fusionar regiones que en realidad son distintas
  — requiere ajuste fino por `detail_level` y validación visual.
- **Dibujos muy densos** (mandalas o bosques con mucho detalle): pueden
  generar muchas microzonas. Se prioriza no dejar regiones sin zona; la
  densidad se controla después con `detail_level` y futuras reglas de
  simplificación/fusión visual.
- **Microzonas**: se fusionan con la vecina más cercana, no se descartan
  (descartarlas dejaría un hueco visual en el dibujo).
- **Texto/firma/marca de agua**: puede tratarse como una región aislada más
  — no se resuelve algorítmicamente en esta primera versión, queda como
  caso a revisar manualmente si aparece.
- **Zonas extremadamente finas** (ej. un bigote): el posicionamiento por
  pole-of-inaccessibility ayuda, pero pintar una tira de 2px es poco
  práctico en la impresión real, independientemente del algoritmo —
  conviene advertir al usuario si detecta zonas por debajo de un ancho
  mínimo, no forzar una solución perfecta.
- **Imagen escaneada con ruido**: se mitiga con denoise + umbral adaptativo
  antes del cierre de líneas.

## 10. Plan de implementación incremental

1. **Extracción de regiones cerradas** (sin subdivisión ni números todavía).
   Archivos probables: `processing-service/app/lineart.py` (nuevo).
   Estado: implementado. Hecho cuando: las regiones detectadas coinciden
   visualmente con la estructura del dibujo original, sin ruido excesivo.
2. **Filtrado de microzonas + cierre de gaps parametrizado por `detail_level`**.
   Hecho cuando: mover `detail_level` cambia visiblemente cantidad/tamaño
   de zonas sin deformar el dibujo.
3. **Subdivisión curva de regiones grandes**.
   Estado: implementado. Criterio interno actual: una zona se subdivide si
   supera un umbral derivado del área mediana de las zonas detectadas, el
   `min_area_px` derivado de `detail_level` y un multiplicador interno. La
   cantidad de subzonas por región es proporcional a su área y está limitada
   por `slic_max_segments_per_zone` por compatibilidad interna de parámetros.
   Hecho cuando: una región grande queda partida en celdas orgánicas, sin
   patrón de grilla evidente.
4. **Fusión de fragmentos chicos post-subdivisión** (reusando `geometry.py`
   de la Fase 5).
   Estado: implementado. Las fusiones internas usan `merge_zones()` con un
   `snap_tolerance` pequeño para absorber gaps de 1–2 px introducidos por la
   vectorización de subzonas. Si una subzona no tiene vecino fusionable,
   no se inventa una regla nueva: se deja en el resultado y se reporta en
   `small_fragment_unmerged_details`.
   Hecho cuando: no quedan zonas por debajo del área mínima tras subdividir,
   salvo casos documentados como no fusionables.
5. **Asignación de `paint_number`** vía coloreo de grafos, y actualización
   del contrato de `/segment` para exponer `zone_id` y `paint_number` como
   campos separados.
   Estado: implementado para `mode="bw"`. Se construye un grafo de
   adyacencias entre zonas finales post-subdivisión/fusión usando una tolerancia de
   10 px para cubrir el grosor del trazo negro entre regiones pintables, y se
   aplica un coloreo greedy limitado a `num_colors`, priorizando que zonas
   vecinas no compartan número. Con `num_colors=10`, las imágenes reales de
   validación generan más de 150 zonas con `paint_number` en el rango 1-10.
   Hecho cuando: con `num_colors=10` se generan 100-300 zonas con
   `paint_number` en el rango 1-10, sin romper la paleta existente.
6. **Posicionamiento robusto de números** (pole of inaccessibility).
   Estado: implementado en `pdf_export.py`. El PDF y el SVG numerado usan
   `representative_point()` cuando alcanza, y fallback a distance transform
   más reducción progresiva de fuente para zonas finas/cóncavas.
   Hecho cuando: ningún número queda cortado o fuera de su zona en el PDF,
   incluso en zonas finas/cóncavas.
7. **Actualizar documentación** (`FUNCTIONAL_SPEC.md`, `API_CONTRACT.md`)
   reflejando la separación `num_colors` / `detail_level` / `zone_id` /
   `paint_number`.

## 11. Cambio de contrato (importante)

Esto ya está reflejado en `API_CONTRACT.md`: `suggested_palettes.colors` se
indexa por `paint_number` (1..num_colors), y cada zona en `zones_geojson`
incluye ambos campos: `zone_id` (identificador geométrico único, usado por
`/zones/merge` y `/zones/split`) y `paint_number` (el número visible para
pintar). Así merge/split siguen operando sobre geometría sin acoplarse a la
cantidad de colores elegida por el usuario.
