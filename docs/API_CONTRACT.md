# Contrato de API — Servicio de Procesamiento

Base URL (local): `http://localhost:8000`

## POST /segment

Segmenta una imagen y devuelve zonas + paleta sugerida.

**Request**
```json
{
  "image_base64": "string",
  "mode": "color",
  "num_colors": 12,
  "detail_level": 0.5
}
```
`mode` es `"color"` o `"bw"`. `num_colors` es la cantidad de números/colores
de pintura (5–30 en la UI), y el backend intenta usar todos cuando hay
suficientes zonas geométricas para repartirlos. `detail_level` es cualitativo
(`0` = bajo, `0.5` = medio, `1` = alto): controla cuántas zonas geométricas
intenta conservar el post-procesado, pero no promete una cantidad exacta de
zonas.

Implementación actual:

- `mode="color"` usa FastSAM con GPU.
- `mode="bw"` usa el pipeline line-art CPU de regiones cerradas
  (`processing-service/app/lineart.py`): binarización adaptativa, cierre
  morfológico de líneas, `connectedComponentsWithStats`, subdivisión SLIC
  de zonas grandes, fusión interna de fragmentos chicos post-SLIC y
  asignación de `paint_number` por coloreo de grafos.

**Response**
```json
{
  "project_id": "uuid",
  "zones_svg": "string (SVG)",
  "zones_geojson": { "...": "..." },
  "suggested_palettes": [
    { "name": "Análoga", "colors": { "1": "#RRGGBB", "2": "#RRGGBB" } },
    { "name": "Vibrante", "colors": { "1": "#RRGGBB", "2": "#RRGGBB" } }
  ]
}
```
Cada feature de `zones_geojson.features` tiene dos identificadores distintos:

- `properties.zone_id`: id geométrico interno de la zona, usado para
  fusionar/dividir.
- `properties.paint_number`: número visible para pintar. Varios `zone_id`
  pueden compartir el mismo `paint_number`.

`suggested_palettes[].colors` se indexa por `paint_number`, no por
`zone_id`.

## POST /zones/merge

**Request**
```json
{
  "project_id": "uuid",
  "zone_ids": ["3", "7"],
  "palette_colors": { "1": "#RRGGBB", "2": "#RRGGBB" },
  "keep_zone_id": "3"
}
```
- `palette_colors` (opcional): paleta actualmente activa en el frontend
  (puede diferir de `suggested_palettes` si el usuario editó colores en el
  editor de paleta). Si se envía, el backend la usa como base en vez de la
  paleta original, y solo recalcula lo necesario por el merge.
- `keep_zone_id` (opcional): resuelve manualmente cuál de las dos zonas
  conserva el `paint_number`, para el caso en que la diferencia de área
  entre ambas sea menor al 20% y las zonas tengan números distintos (ver
  regla en `docs/FUNCTIONAL_SPEC.md`, paso 4). Si no se envía y la
  diferencia es <20%, el backend responde pidiendo esta confirmación en vez
  de decidir solo (ver más abajo). Si la diferencia es ≥20%, este campo se
  ignora y gana la zona de mayor área. Si ambas zonas ya comparten
  `paint_number`, no se pide confirmación.
- **Identificador resultante**: la zona fusionada conserva como `zone_id`
  el valor de `keep_zone_id` (el que ganó, ya sea por área o por elección
  manual del usuario tras la confirmación). El otro `zone_id` deja de
  existir. Esto es lo que el resto del sistema (undo, referencias en la
  DB) debe usar para identificar la zona resultante — no se genera un
  `zone_id` nuevo en el merge.

**Response (caso normal)**: mismo shape que `/segment` (zonas renumeradas +
paleta actualizada, respetando `palette_colors` si vino en el request).

**Response (requiere confirmación, diferencia de área <20% y no vino
`keep_zone_id`)**
```json
{
  "requires_confirmation": true,
  "reason": "area_difference_below_threshold",
  "candidates": ["3", "7"]
}
```
El frontend debe mostrar el diálogo de confirmación y reintentar el mismo
request agregando `keep_zone_id` con la elección del usuario.

## POST /zones/split

**Request**
```json
{
  "project_id": "uuid",
  "zone_id": "5",
  "split_line": [[0, 0], [10, 10]],
  "palette_colors": { "1": "#RRGGBB", "2": "#RRGGBB" }
}
```
`split_line` es una lista de puntos `[x, y]` que definen la línea trazada
por el usuario. `palette_colors` (opcional): misma función que en
`/zones/merge`.

De las dos piezas resultantes, la pieza de **mayor área** conserva el
`zone_id` original. Ambas piezas conservan el mismo `paint_number` de la zona
original, porque dividir geometría no cambia por sí solo la cantidad de
números/colores elegida por el usuario. La pieza de menor área recibe un
`zone_id` nuevo.

**Response**: mismo shape que `/segment`.

## POST /export/pdf

**Request**
```json
{
  "project_id": "uuid",
  "palette": { "1": "#RRGGBB", "2": "#RRGGBB" },
  "paper_size": "A4",
  "orientation": "portrait"
}
```
`paper_size` es `"A4"` o `"Letter"`. `orientation` es `"portrait"` o
`"landscape"`.

**Response**: PDF binario (`application/pdf`).

## Notas

- `project_id` identifica el estado actual del proyecto en el servicio de
  procesamiento (zonas + historial), para no tener que reenviar toda la
  imagen en cada operación.
- Los colores siempre viajan en formato hex (`#RRGGBB`).
