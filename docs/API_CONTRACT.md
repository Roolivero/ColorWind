# Contrato de API — Servicio de Procesamiento

Base URL (local): `http://localhost:8000`

## POST /segment

Segmenta una imagen y devuelve zonas + paleta sugerida.

**Request**
```json
{
  "image_base64": "string",
  "mode": "color",
  "num_zones": 12,
  "min_zone_area_px": 500
}
```
`mode` es `"color"` o `"bw"`.

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
  conserva número/color, para el caso en que la diferencia de área entre
  ambas sea menor al 20% (ver regla en `docs/FUNCTIONAL_SPEC.md`, paso 4).
  Si no se envía y la diferencia de área es <20%, el backend responde
  pidiendo esta confirmación en vez de decidir solo (ver más abajo). Si la
  diferencia es ≥20%, este campo se ignora y gana la zona de mayor área.

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

De las dos piezas resultantes, la de **mayor área** conserva el número y
color de la zona original; la de menor área es la nueva zona, y recibe el
siguiente número disponible junto con un color no usado de la paleta
sugerida original (o un gris neutro si no queda ninguno libre, marcado para
asignación manual — ver `docs/FUNCTIONAL_SPEC.md`, paso 4).

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