from __future__ import annotations

import math
from typing import Any

from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon
from shapely.ops import snap, split, unary_union
from shapely.validation import make_valid


NEUTRAL_GRAY = "#B8B8B8"
AREA_CONFIRMATION_THRESHOLD = 0.2


class GeometryError(ValueError):
    pass


def _zone_id(zone: dict[str, Any]) -> str:
    return str(zone["id"])


def _paint_number(zone: dict[str, Any]) -> str:
    return str(zone.get("paint_number") or zone["id"])


def _polygon_parts(geometry: Any) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        parts: list[Polygon] = []
        for item in geometry.geoms:
            parts.extend(_polygon_parts(item))
        return parts
    return []


def _as_single_polygon(geometry: Any, *, error: str) -> Polygon:
    geometry = make_valid(geometry)
    parts = [part for part in _polygon_parts(geometry) if part.area > 1]
    if len(parts) != 1:
        raise GeometryError(error)
    return parts[0]


def _zone_to_polygon(zone: dict[str, Any]) -> Polygon:
    polygon = Polygon(zone["polygon"])
    return _as_single_polygon(polygon, error=f"Zona {_zone_id(zone)} no tiene geometria valida.")


def _zone_from_polygon(
    polygon: Polygon,
    zone_id: int | str,
    *,
    source_zone: dict[str, Any] | None = None,
    requires_manual_color: bool = False,
) -> dict[str, Any]:
    clean = _as_single_polygon(polygon, error="La geometria resultante no es un poligono simple.")
    coordinates = []
    for x, y in clean.exterior.coords[:-1]:
        point = [int(round(x)), int(round(y))]
        if not coordinates or coordinates[-1] != point:
            coordinates.append(point)

    if len(coordinates) < 3:
        raise GeometryError("La geometria resultante es demasiado pequena.")

    min_x, min_y, max_x, max_y = clean.bounds
    next_zone = {
        "id": int(zone_id),
        "area_px": round(float(clean.area), 2),
        "bbox": [
            int(math.floor(min_x)),
            int(math.floor(min_y)),
            int(math.ceil(max_x - min_x)),
            int(math.ceil(max_y - min_y)),
        ],
        "polygon": coordinates,
    }
    if source_zone and source_zone.get("mask_index") is not None:
        next_zone["mask_index"] = source_zone["mask_index"]
    for metadata_key in ("source", "slic_parent_id"):
        if source_zone and source_zone.get(metadata_key) is not None:
            next_zone[metadata_key] = source_zone[metadata_key]
    if source_zone and source_zone.get("paint_number") is not None:
        next_zone["paint_number"] = str(source_zone["paint_number"])
    if requires_manual_color:
        next_zone["requires_manual_color"] = True
    elif source_zone and source_zone.get("requires_manual_color"):
        next_zone["requires_manual_color"] = True
    return next_zone


def _renumber(
    zones: list[dict[str, Any]],
    palette_colors: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    next_zones: list[dict[str, Any]] = []

    for index, zone in enumerate(zones, start=1):
        next_zone = dict(zone)
        next_zone["id"] = index
        next_zones.append(next_zone)

    return next_zones, palette_colors


def _by_zone_id(zones: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_zone_id(zone): zone for zone in zones}


def _normalize_palette(palette_colors: dict[str, str] | None) -> dict[str, str]:
    return {str(key): value for key, value in (palette_colors or {}).items()}


def _with_palette_defaults(
    zones: list[dict[str, Any]],
    palette_colors: dict[str, str] | None,
    fallback_colors: dict[str, str],
) -> dict[str, str]:
    current = _normalize_palette(palette_colors)
    fallback = _normalize_palette(fallback_colors)
    keys = set(current) | set(fallback) | {_paint_number(zone) for zone in zones}
    return {key: current.get(key, fallback.get(key, NEUTRAL_GRAY)) for key in _sorted_numeric(keys)}


def _sorted_numeric(values: set[str] | list[str]) -> list[str]:
    return sorted(values, key=lambda value: int(value) if str(value).isdigit() else str(value))


def should_confirm_merge(zones: list[dict[str, Any]], zone_ids: list[str]) -> bool:
    zone_map = _by_zone_id(zones)
    try:
        first = _zone_to_polygon(zone_map[str(zone_ids[0])])
        second = _zone_to_polygon(zone_map[str(zone_ids[1])])
    except KeyError as exc:
        raise GeometryError("Zona inexistente para fusionar.") from exc

    _as_single_polygon(
        unary_union([first, second]),
        error="Las zonas elegidas no forman una region continua. Elegi dos zonas vecinas.",
    )
    if _paint_number(zone_map[str(zone_ids[0])]) == _paint_number(zone_map[str(zone_ids[1])]):
        return False
    larger = max(first.area, second.area)
    if larger <= 0:
        raise GeometryError("No se puede fusionar una zona sin area.")
    return abs(first.area - second.area) / larger < AREA_CONFIRMATION_THRESHOLD


def merge_zones(
    zones: list[dict[str, Any]],
    zone_ids: list[str],
    *,
    palette_colors: dict[str, str] | None,
    fallback_colors: dict[str, str],
    keep_zone_id: str | None,
    snap_tolerance: float = 0.0,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if len(zone_ids) != 2 or len({str(zone_id) for zone_id in zone_ids}) != 2:
        raise GeometryError("Se deben fusionar exactamente dos zonas distintas.")

    requested_ids = [str(zone_id) for zone_id in zone_ids]
    zone_map = _by_zone_id(zones)
    if any(zone_id not in zone_map for zone_id in requested_ids):
        raise GeometryError("Zona inexistente para fusionar.")

    first_zone = zone_map[requested_ids[0]]
    second_zone = zone_map[requested_ids[1]]
    first_polygon = _zone_to_polygon(first_zone)
    second_polygon = _zone_to_polygon(second_zone)
    if snap_tolerance > 0:
        second_polygon = snap(second_polygon, first_polygon, snap_tolerance)
        first_polygon = snap(first_polygon, second_polygon, snap_tolerance)
    merged_polygon = _as_single_polygon(
        unary_union([first_polygon, second_polygon]),
        error="Las zonas elegidas no forman una region continua. Elegi dos zonas vecinas.",
    )

    larger_zone = first_zone if first_polygon.area >= second_polygon.area else second_zone
    larger_area = max(first_polygon.area, second_polygon.area)
    area_difference = abs(first_polygon.area - second_polygon.area) / larger_area if larger_area else 0
    same_paint_number = _paint_number(first_zone) == _paint_number(second_zone)
    if area_difference >= AREA_CONFIRMATION_THRESHOLD or same_paint_number:
        keep_id = _zone_id(larger_zone)
    elif keep_zone_id and str(keep_zone_id) in requested_ids:
        keep_id = str(keep_zone_id)
    else:
        raise GeometryError("La fusion requiere confirmar que color conservar.")

    current_palette = _with_palette_defaults(zones, palette_colors, fallback_colors)
    merged_requires_manual = bool(zone_map[keep_id].get("requires_manual_color"))
    merged_zone = _zone_from_polygon(
        merged_polygon,
        keep_id,
        source_zone=zone_map[keep_id],
        requires_manual_color=merged_requires_manual,
    )

    next_zones: list[dict[str, Any]] = []
    inserted = False
    for zone in zones:
        zone_id = _zone_id(zone)
        if zone_id not in requested_ids:
            next_zones.append(zone)
            continue
        if not inserted:
            next_zones.append(merged_zone)
            inserted = True

    next_palette = dict(current_palette)

    return _renumber(next_zones, next_palette)


def _extend_line(points: list[list[float]], bounds: tuple[float, float, float, float]) -> list[list[float]]:
    if len(points) < 2:
        raise GeometryError("La linea de division necesita al menos dos puntos.")

    min_x, min_y, max_x, max_y = bounds
    margin = max(max_x - min_x, max_y - min_y, 1) * 3
    extended = [[float(x), float(y)] for x, y in points]

    def extend_endpoint(endpoint: list[float], neighbor: list[float]) -> list[float]:
        dx = endpoint[0] - neighbor[0]
        dy = endpoint[1] - neighbor[1]
        length = math.hypot(dx, dy)
        if length < 0.001:
            raise GeometryError("La linea de division necesita puntos distintos.")
        return [endpoint[0] + (dx / length) * margin, endpoint[1] + (dy / length) * margin]

    extended[0] = extend_endpoint(extended[0], extended[1])
    extended[-1] = extend_endpoint(extended[-1], extended[-2])
    return extended


def split_zone(
    zones: list[dict[str, Any]],
    zone_id: str,
    split_line: list[list[float]],
    *,
    palette_colors: dict[str, str] | None,
    fallback_colors: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    current_id = str(zone_id)
    zone_map = _by_zone_id(zones)
    if current_id not in zone_map:
        raise GeometryError("Zona inexistente para dividir.")

    zone = zone_map[current_id]
    polygon = _zone_to_polygon(zone)
    extended_line = LineString(_extend_line(split_line, polygon.bounds))
    pieces = [piece for piece in _polygon_parts(split(polygon, extended_line)) if piece.area > 1]

    if len(pieces) < 2:
        raise GeometryError("La linea no dividio la zona. Traza una linea que cruce la region.")
    if len(pieces) > 2:
        raise GeometryError("La linea genero mas de dos piezas. Traza una linea simple.")

    pieces.sort(key=lambda item: item.area, reverse=True)
    larger_piece, smaller_piece = pieces
    current_palette = _with_palette_defaults(zones, palette_colors, fallback_colors)
    new_id = str(max(int(_zone_id(item)) for item in zones if _zone_id(item).isdigit()) + 1)

    original_zone = _zone_from_polygon(
        larger_piece,
        current_id,
        source_zone=zone,
        requires_manual_color=bool(zone.get("requires_manual_color")),
    )
    new_zone = _zone_from_polygon(
        smaller_piece,
        new_id,
        source_zone=zone,
        requires_manual_color=bool(zone.get("requires_manual_color")),
    )

    next_zones = [original_zone if _zone_id(item) == current_id else item for item in zones]
    next_zones.append(new_zone)

    next_palette = dict(current_palette)

    return _renumber(next_zones, next_palette)
