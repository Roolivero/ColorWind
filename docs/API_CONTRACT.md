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
{ "project_id": "uuid", "zone_ids": ["3", "7"] }
```

**Response**: mismo shape que `/segment` (zonas renumeradas + paleta
actualizada).

## POST /zones/split

**Request**
```json
{
  "project_id": "uuid",
  "zone_id": "5",
  "split_line": [[0, 0], [10, 10]]
}
```
`split_line` es una lista de puntos `[x, y]` que definen la línea trazada
por el usuario.

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
