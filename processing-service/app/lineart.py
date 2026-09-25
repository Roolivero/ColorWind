from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import time
from typing import Any

import cv2
import numpy as np
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.validation import make_valid
from skimage.segmentation import slic

from app.geometry import GeometryError, merge_zones


GRAPH_ADJACENCY_TOLERANCE_PX = 2.0


@dataclass(frozen=True)
class LineArtParams:
    detail_level: float = 0.5
    min_area_px: int | None = None
    min_area_ratio_low_detail: float = 0.0025
    min_area_ratio_high_detail: float = 0.00035
    close_kernel_low_detail: int = 5
    close_kernel_high_detail: int = 3
    adaptive_block_size: int = 35
    adaptive_c: int = 9
    epsilon_ratio_low_detail: float = 0.005
    epsilon_ratio_high_detail: float = 0.0018
    max_area_ratio: float = 0.92
    enable_slic: bool = True
    slic_large_zone_multiplier_low_detail: float = 3.2
    slic_large_zone_multiplier_high_detail: float = 2.0
    slic_target_area_multiplier_low_detail: float = 3.4
    slic_target_area_multiplier_high_detail: float = 1.2
    slic_compactness_low_detail: float = 24.0
    slic_compactness_high_detail: float = 12.0
    slic_max_segments_per_zone: int = 64
    merge_small_slic_fragments: bool = True
    small_fragment_max_iterations_multiplier: int = 3


def _odd(value: int) -> int:
    value = max(3, int(value))
    return value if value % 2 else value + 1


def _clamp_detail(detail_level: float) -> float:
    return max(0.0, min(float(detail_level), 1.0))


def _interpolate(low_detail_value: float, high_detail_value: float, detail_level: float) -> float:
    return low_detail_value + (high_detail_value - low_detail_value) * _clamp_detail(detail_level)


def derived_lineart_params(width: int, height: int, params: LineArtParams) -> dict[str, Any]:
    detail_level = _clamp_detail(params.detail_level)
    min_area_ratio = _interpolate(
        params.min_area_ratio_low_detail,
        params.min_area_ratio_high_detail,
        detail_level,
    )
    min_area_px = params.min_area_px or int(round(width * height * min_area_ratio))
    close_kernel = _odd(
        round(
            _interpolate(
                params.close_kernel_low_detail,
                params.close_kernel_high_detail,
                detail_level,
            )
        )
    )
    epsilon_ratio = _interpolate(
        params.epsilon_ratio_low_detail,
        params.epsilon_ratio_high_detail,
        detail_level,
    )
    return {
        "detail_level": detail_level,
        "min_area_px": max(20, min_area_px),
        "close_kernel": close_kernel,
        "adaptive_block_size": _odd(params.adaptive_block_size),
        "adaptive_c": params.adaptive_c,
        "epsilon_ratio": epsilon_ratio,
        "max_area_px": width * height * params.max_area_ratio,
        "slic_large_zone_multiplier": _interpolate(
            params.slic_large_zone_multiplier_low_detail,
            params.slic_large_zone_multiplier_high_detail,
            detail_level,
        ),
        "slic_target_area_multiplier": _interpolate(
            params.slic_target_area_multiplier_low_detail,
            params.slic_target_area_multiplier_high_detail,
            detail_level,
        ),
        "slic_compactness": _interpolate(
            params.slic_compactness_low_detail,
            params.slic_compactness_high_detail,
            detail_level,
        ),
        "slic_max_segments_per_zone": params.slic_max_segments_per_zone,
        "merge_small_slic_fragments": params.merge_small_slic_fragments,
        "small_fragment_max_iterations_multiplier": params.small_fragment_max_iterations_multiplier,
    }


def preprocess_line_mask(image_bgr: np.ndarray, params: LineArtParams) -> tuple[np.ndarray, dict[str, Any]]:
    height, width = image_bgr.shape[:2]
    derived = derived_lineart_params(width, height, params)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    denoised = cv2.medianBlur(gray, 3)
    line_mask = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        int(derived["adaptive_block_size"]),
        int(derived["adaptive_c"]),
    )

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (int(derived["close_kernel"]), int(derived["close_kernel"])),
    )
    closed = cv2.morphologyEx(line_mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    # A black frame prevents the outer page background from leaking into
    # regions that touch the image border.
    closed[0, :] = 255
    closed[-1, :] = 255
    closed[:, 0] = 255
    closed[:, -1] = 255
    return closed, derived


def _contour_to_polygon(contour: np.ndarray, epsilon_ratio: float) -> list[list[int]]:
    perimeter = cv2.arcLength(contour, True)
    epsilon = max(1.0, perimeter * epsilon_ratio)
    approx = cv2.approxPolyDP(contour, epsilon, True)
    return approx.reshape(-1, 2).astype(int).tolist()


def _zones_from_component_labels(
    labels: np.ndarray,
    stats: np.ndarray,
    *,
    min_area_px: int,
    max_area_px: float,
    epsilon_ratio: float,
) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for label in range(1, stats.shape[0]):
        x, y, width, height, area = [int(value) for value in stats[label]]
        if area < min_area_px or area > max_area_px:
            continue

        component_mask = (labels == label).astype(np.uint8) * 255
        contours, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue

        contour = max(contours, key=cv2.contourArea)
        contour_area = float(cv2.contourArea(contour))
        if contour_area < min_area_px:
            continue

        polygon = _contour_to_polygon(contour, epsilon_ratio)
        if len(polygon) < 3:
            continue

        zones.append(
            {
                "id": 0,
                "area_px": round(contour_area, 2),
                "bbox": [x, y, width, height],
                "polygon": polygon,
                "source": "lineart_connected_component",
            }
        )

    zones.sort(key=lambda zone: (zone["bbox"][1], zone["bbox"][0], -zone["area_px"]))
    for index, zone in enumerate(zones, start=1):
        zone["id"] = index
    return zones


def _mask_from_zone(zone: dict[str, Any], shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    contour = np.array(zone["polygon"], dtype=np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(mask, [contour], 255)
    return mask


def _noise_feature(height: int, width: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 1.0, (height, width)).astype(np.float32)
    blur_size = _odd(max(9, round(min(height, width) / 8)))
    noise = cv2.GaussianBlur(noise, (blur_size, blur_size), 0)
    cv2.normalize(noise, noise, 0.0, 1.0, cv2.NORM_MINMAX)
    return noise


def _slic_feature_image(image_bgr: np.ndarray, zone_id: int) -> np.ndarray:
    height, width = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    noise = _noise_feature(height, width, zone_id * 7919)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    xx = xx / max(width - 1, 1)
    yy = yy / max(height - 1, 1)
    return np.dstack([gray, noise, (xx + yy) * 0.5]).astype(np.float32)


def _zone_from_mask(
    mask: np.ndarray,
    *,
    zone_id: int,
    source: str,
    epsilon_ratio: float,
    min_area_px: int,
    extra_properties: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    if area < min_area_px:
        return None

    polygon = _contour_to_polygon(contour, epsilon_ratio)
    if len(polygon) < 3:
        return None

    x, y, width, height = cv2.boundingRect(contour)
    zone = {
        "id": zone_id,
        "area_px": round(area, 2),
        "bbox": [int(x), int(y), int(width), int(height)],
        "polygon": polygon,
        "source": source,
    }
    if extra_properties:
        zone.update(extra_properties)
    return zone


def _subdivide_zone_with_slic(
    image_bgr: np.ndarray,
    zone: dict[str, Any],
    *,
    next_zone_id: int,
    target_area_px: float,
    min_area_px: int,
    epsilon_ratio: float,
    compactness: float,
    max_segments_per_zone: int,
) -> list[dict[str, Any]]:
    area = max(float(zone.get("area_px") or 0), 1.0)
    segment_count = int(round(area / max(target_area_px, 1.0)))
    segment_count = max(2, min(segment_count, max_segments_per_zone))
    if segment_count < 2:
        return [zone]

    full_mask = _mask_from_zone(zone, image_bgr.shape[:2])
    x, y, width, height = [int(value) for value in zone["bbox"]]
    pad = 3
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(image_bgr.shape[1], x + width + pad)
    y1 = min(image_bgr.shape[0], y + height + pad)

    crop = image_bgr[y0:y1, x0:x1]
    mask_crop = full_mask[y0:y1, x0:x1] > 0
    if int(np.count_nonzero(mask_crop)) < min_area_px * 2:
        return [zone]

    feature_image = _slic_feature_image(crop, int(zone["id"]))
    labels = slic(
        feature_image,
        n_segments=segment_count,
        compactness=compactness,
        sigma=0.6,
        start_label=1,
        mask=mask_crop,
        channel_axis=-1,
        convert2lab=False,
        enforce_connectivity=True,
        min_size_factor=0.15,
        max_size_factor=4.0,
    )

    subzones: list[dict[str, Any]] = []
    for label in sorted(int(value) for value in np.unique(labels) if value > 0):
        label_mask = ((labels == label) & mask_crop).astype(np.uint8) * 255
        full_label_mask = np.zeros(image_bgr.shape[:2], dtype=np.uint8)
        full_label_mask[y0:y1, x0:x1] = label_mask
        subzone = _zone_from_mask(
            full_label_mask,
            zone_id=next_zone_id + len(subzones),
            source="lineart_slic_subzone",
            epsilon_ratio=max(0.0008, epsilon_ratio * 0.45),
            min_area_px=max(8, int(min_area_px * 0.15)),
            extra_properties={"slic_parent_id": str(zone["id"])},
        )
        if subzone is not None:
            subzones.append(subzone)

    if len(subzones) < 2:
        return [zone]
    return subzones


def subdivide_large_zones(
    image_bgr: np.ndarray,
    zones: list[dict[str, Any]],
    derived: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not zones:
        return zones, {
            "slic_enabled": True,
            "slic_input_zones": 0,
            "slic_subdivided_zones": 0,
            "slic_created_zones": 0,
        }

    areas = np.array([float(zone.get("area_px") or 0) for zone in zones], dtype=np.float64)
    median_area = float(np.median(areas)) if len(areas) else float(derived["min_area_px"])
    target_area_px = max(
        float(derived["min_area_px"]) * float(derived["slic_target_area_multiplier"]),
        median_area * float(derived["slic_target_area_multiplier"]),
    )
    large_zone_threshold_px = max(
        target_area_px * float(derived["slic_large_zone_multiplier"]),
        float(derived["min_area_px"]) * float(derived["slic_large_zone_multiplier"]) * 2.0,
    )

    next_zones: list[dict[str, Any]] = []
    subdivided_zones = 0
    created_zones = 0
    next_zone_id = 1
    for zone in zones:
        if float(zone.get("area_px") or 0) < large_zone_threshold_px:
            next_zone = dict(zone)
            next_zone["id"] = next_zone_id
            next_zones.append(next_zone)
            next_zone_id += 1
            continue

        subzones = _subdivide_zone_with_slic(
            image_bgr,
            zone,
            next_zone_id=next_zone_id,
            target_area_px=target_area_px,
            min_area_px=int(derived["min_area_px"]),
            epsilon_ratio=float(derived["epsilon_ratio"]),
            compactness=float(derived["slic_compactness"]),
            max_segments_per_zone=int(derived["slic_max_segments_per_zone"]),
        )
        if len(subzones) > 1:
            subdivided_zones += 1
            created_zones += len(subzones)
        for subzone in subzones:
            next_subzone = dict(subzone)
            next_subzone["id"] = next_zone_id
            next_zones.append(next_subzone)
            next_zone_id += 1

    metrics = {
        "slic_enabled": True,
        "slic_input_zones": len(zones),
        "slic_subdivided_zones": subdivided_zones,
        "slic_created_zones": created_zones,
        "slic_target_area_px": round(target_area_px, 2),
        "slic_large_zone_threshold_px": round(large_zone_threshold_px, 2),
    }
    return next_zones, metrics


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


def _zone_polygon(zone: dict[str, Any]) -> Polygon | None:
    try:
        geometry = make_valid(Polygon(zone["polygon"]))
    except Exception:
        return None
    parts = [part for part in _polygon_parts(geometry) if part.area > 1]
    if not parts:
        return None
    return max(parts, key=lambda part: part.area)


def _shared_border_score(
    first: Polygon,
    second: Polygon,
    *,
    tolerance_px: float = 1.5,
    include_vertex: bool = False,
) -> float:
    if first.is_empty or second.is_empty:
        return 0.0
    boundary_intersection = first.boundary.intersection(second.boundary)
    if boundary_intersection.length > 0.5:
        return float(boundary_intersection.length)
    if include_vertex and not boundary_intersection.is_empty:
        return 0.1

    # Polygon simplification can create tiny gaps along originally adjacent
    # raster regions. This tolerance keeps the adjacency test practical while
    # still requiring a meaningful shared edge.
    near_intersection = first.boundary.buffer(tolerance_px).intersection(second.boundary)
    if near_intersection.length > max(3.0, tolerance_px * 2) and first.buffer(tolerance_px).intersects(second):
        return float(near_intersection.length)
    if include_vertex:
        boundary_distance = first.boundary.distance(second.boundary)
        if boundary_distance <= tolerance_px:
            return max(0.1, tolerance_px - boundary_distance)
    return 0.0


def _expanded_bounds_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    tolerance_px: float,
) -> bool:
    first_min_x, first_min_y, first_max_x, first_max_y = first
    second_min_x, second_min_y, second_max_x, second_max_y = second
    return not (
        first_max_x + tolerance_px < second_min_x
        or second_max_x + tolerance_px < first_min_x
        or first_max_y + tolerance_px < second_min_y
        or second_max_y + tolerance_px < first_min_y
    )


def build_zone_adjacency_graph(
    zones: list[dict[str, Any]],
    *,
    tolerance_px: float = GRAPH_ADJACENCY_TOLERANCE_PX,
) -> dict[str, set[str]]:
    graph = {str(zone["id"]): set() for zone in zones}
    polygon_items: list[tuple[str, Polygon, tuple[float, float, float, float]]] = []
    for zone in zones:
        polygon = _zone_polygon(zone)
        if polygon is None:
            continue
        polygon_items.append((str(zone["id"]), polygon, polygon.bounds))

    for index, (first_id, first_polygon, first_bounds) in enumerate(polygon_items):
        for second_id, second_polygon, second_bounds in polygon_items[index + 1 :]:
            if not _expanded_bounds_overlap(first_bounds, second_bounds, tolerance_px):
                continue
            score = _shared_border_score(
                first_polygon,
                second_polygon,
                tolerance_px=tolerance_px,
                include_vertex=True,
            )
            if score <= 0:
                continue
            graph[first_id].add(second_id)
            graph[second_id].add(first_id)
    return graph


def paint_number_distribution(zones: list[dict[str, Any]], num_colors: int) -> dict[str, int]:
    distribution = {str(index): 0 for index in range(1, max(1, int(num_colors)) + 1)}
    for zone in zones:
        paint_number = str(zone.get("paint_number") or zone["id"])
        distribution[paint_number] = distribution.get(paint_number, 0) + 1
    return distribution


def count_adjacency_collisions(
    zones: list[dict[str, Any]],
    adjacency_graph: dict[str, set[str]],
) -> int:
    paint_numbers = {
        str(zone["id"]): str(zone.get("paint_number") or zone["id"])
        for zone in zones
    }
    collisions = 0
    for zone_id, neighbors in adjacency_graph.items():
        for neighbor_id in neighbors:
            if zone_id >= neighbor_id:
                continue
            if paint_numbers.get(zone_id) == paint_numbers.get(neighbor_id):
                collisions += 1
    return collisions


def _graph_component_sizes(adjacency_graph: dict[str, set[str]]) -> list[int]:
    seen: set[str] = set()
    sizes: list[int] = []
    for start_id in adjacency_graph:
        if start_id in seen:
            continue
        stack = [start_id]
        seen.add(start_id)
        size = 0
        while stack:
            zone_id = stack.pop()
            size += 1
            for neighbor_id in adjacency_graph[zone_id]:
                if neighbor_id in seen:
                    continue
                seen.add(neighbor_id)
                stack.append(neighbor_id)
        sizes.append(size)
    return sorted(sizes, reverse=True)


def assign_graph_paint_numbers(
    zones: list[dict[str, Any]],
    num_colors: int,
    *,
    tolerance_px: float = GRAPH_ADJACENCY_TOLERANCE_PX,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not zones:
        return zones, {
            "paint_number_strategy": "lineart_graph_coloring",
            "paint_number_adjacency_edges": 0,
            "paint_number_collisions": 0,
            "paint_number_distribution": {},
            "paint_number_isolated_zones": 0,
            "paint_number_component_count": 0,
            "paint_number_largest_component": 0,
            "paint_number_seconds": 0.0,
        }

    started = time.perf_counter()
    color_count = max(1, int(num_colors))
    paint_numbers = [str(index) for index in range(1, color_count + 1)]
    next_zones = [dict(zone) for zone in zones]
    adjacency_graph = build_zone_adjacency_graph(next_zones, tolerance_px=tolerance_px)
    zone_by_id = {str(zone["id"]): zone for zone in next_zones}
    zone_ids = list(zone_by_id)
    degrees = {zone_id: len(adjacency_graph.get(zone_id, set())) for zone_id in zone_ids}
    areas = {zone_id: float(zone_by_id[zone_id].get("area_px") or 0) for zone_id in zone_ids}
    usage = {paint_number: 0 for paint_number in paint_numbers}
    assignments: dict[str, str] = {}
    unassigned = set(zone_ids)

    while unassigned:
        zone_id = max(
            unassigned,
            key=lambda candidate_id: (
                len({assignments[neighbor_id] for neighbor_id in adjacency_graph[candidate_id] if neighbor_id in assignments}),
                degrees[candidate_id],
                areas[candidate_id],
                -int(candidate_id) if candidate_id.isdigit() else 0,
            ),
        )
        neighbor_colors = {
            assignments[neighbor_id]
            for neighbor_id in adjacency_graph[zone_id]
            if neighbor_id in assignments
        }
        available_colors = [paint_number for paint_number in paint_numbers if paint_number not in neighbor_colors]
        if available_colors:
            selected_color = min(available_colors, key=lambda paint_number: (usage[paint_number], int(paint_number)))
        else:
            selected_color = min(
                paint_numbers,
                key=lambda paint_number: (
                    sum(1 for neighbor_id in adjacency_graph[zone_id] if assignments.get(neighbor_id) == paint_number),
                    usage[paint_number],
                    int(paint_number),
                ),
            )
        assignments[zone_id] = selected_color
        usage[selected_color] += 1
        unassigned.remove(zone_id)

    # A few local passes clean up avoidable collisions while preserving the
    # expectation that every requested number is used when there are enough zones.
    preserve_all_numbers = len(next_zones) >= color_count
    for _ in range(4):
        changed = False
        colliding_ids = sorted(
            zone_ids,
            key=lambda candidate_id: (
                sum(1 for neighbor_id in adjacency_graph[candidate_id] if assignments[neighbor_id] == assignments[candidate_id]),
                degrees[candidate_id],
                areas[candidate_id],
            ),
            reverse=True,
        )
        for zone_id in colliding_ids:
            current_color = assignments[zone_id]
            current_collision_count = sum(
                1
                for neighbor_id in adjacency_graph[zone_id]
                if assignments[neighbor_id] == current_color
            )
            if current_collision_count == 0:
                continue
            if preserve_all_numbers and usage[current_color] <= 1:
                continue
            best_color = min(
                paint_numbers,
                key=lambda paint_number: (
                    sum(1 for neighbor_id in adjacency_graph[zone_id] if assignments[neighbor_id] == paint_number),
                    usage[paint_number],
                    int(paint_number),
                ),
            )
            best_collision_count = sum(
                1
                for neighbor_id in adjacency_graph[zone_id]
                if assignments[neighbor_id] == best_color
            )
            if best_color != current_color and best_collision_count < current_collision_count:
                assignments[zone_id] = best_color
                usage[current_color] -= 1
                usage[best_color] += 1
                changed = True
        if not changed:
            break

    for zone in next_zones:
        zone["paint_number"] = assignments[str(zone["id"])]

    component_sizes = _graph_component_sizes(adjacency_graph)
    edge_count = sum(len(neighbors) for neighbors in adjacency_graph.values()) // 2
    distribution = paint_number_distribution(next_zones, color_count)
    metrics = {
        "paint_number_strategy": "lineart_graph_coloring",
        "paint_number_adjacency_tolerance_px": tolerance_px,
        "paint_number_adjacency_edges": edge_count,
        "paint_number_collisions": count_adjacency_collisions(next_zones, adjacency_graph),
        "paint_number_distribution": distribution,
        "paint_number_unused_numbers": [
            paint_number
            for paint_number, count in distribution.items()
            if count == 0
        ],
        "paint_number_isolated_zones": sum(1 for neighbors in adjacency_graph.values() if not neighbors),
        "paint_number_component_count": len(component_sizes),
        "paint_number_largest_component": component_sizes[0] if component_sizes else 0,
        "paint_number_seconds": round(time.perf_counter() - started, 4),
    }
    return next_zones, metrics


def _small_slic_zone(zones: list[dict[str, Any]], min_area_px: int) -> dict[str, Any] | None:
    small_zones = [
        zone
        for zone in zones
        if zone.get("source") == "lineart_slic_subzone"
        and float(zone.get("area_px") or 0) < min_area_px
    ]
    if not small_zones:
        return None
    return min(small_zones, key=lambda zone: float(zone.get("area_px") or 0))


def _adjacent_neighbors(
    zones: list[dict[str, Any]],
    small_zone: dict[str, Any],
) -> list[tuple[str, float]]:
    small_polygon = _zone_polygon(small_zone)
    if small_polygon is None:
        return []

    small_id = str(small_zone["id"])
    parent_id = small_zone.get("slic_parent_id")
    candidates: list[tuple[float, float, str]] = []
    for zone in zones:
        zone_id = str(zone["id"])
        if zone_id == small_id:
            continue
        if parent_id is not None and zone.get("slic_parent_id") != parent_id:
            continue
        polygon = _zone_polygon(zone)
        if polygon is None:
            continue
        shared_border = _shared_border_score(small_polygon, polygon)
        if shared_border <= 0:
            continue
        candidates.append((float(zone.get("area_px") or polygon.area), shared_border, zone_id))

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [(zone_id, shared_border) for _, shared_border, zone_id in candidates]


def merge_small_slic_fragments(
    zones: list[dict[str, Any]],
    derived: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    min_area_px = int(derived["min_area_px"])
    max_iterations = max(
        1,
        len(zones) * int(derived.get("small_fragment_max_iterations_multiplier", 3)),
    )
    current_zones = zones
    merge_count = 0
    failed_fragments: list[dict[str, Any]] = []

    for _ in range(max_iterations):
        small_zone = _small_slic_zone(current_zones, min_area_px)
        if small_zone is None:
            break

        neighbors = _adjacent_neighbors(current_zones, small_zone)
        if not neighbors:
            failed_fragments.append(
                {
                    "zone_id": str(small_zone["id"]),
                    "area_px": small_zone.get("area_px"),
                    "reason": "no_adjacent_same_parent_neighbor",
                }
            )
            current_zones = [
                {**zone, "source": "lineart_slic_unmerged_small_fragment"}
                if str(zone["id"]) == str(small_zone["id"])
                else zone
                for zone in current_zones
            ]
            continue

        last_error: str | None = None
        merged = False
        for neighbor_id, shared_border in neighbors:
            try:
                current_zones, _ = merge_zones(
                    current_zones,
                    [str(small_zone["id"]), neighbor_id],
                    palette_colors=None,
                    fallback_colors={},
                    keep_zone_id=neighbor_id,
                    snap_tolerance=2.0,
                )
                merge_count += 1
                merged = True
                break
            except GeometryError as exc:
                last_error = str(exc)

        if not merged:
            failed_fragments.append(
                {
                    "zone_id": str(small_zone["id"]),
                    "area_px": small_zone.get("area_px"),
                    "candidate_count": len(neighbors),
                    "reason": last_error or "no_mergeable_adjacent_same_parent_neighbor",
                }
            )
            current_zones = [
                {**zone, "source": "lineart_slic_unmerged_small_fragment"}
                if str(zone["id"]) == str(small_zone["id"])
                else zone
                for zone in current_zones
            ]

    remaining_small = [
        {
            "zone_id": str(zone["id"]),
            "area_px": zone.get("area_px"),
            "source": zone.get("source"),
        }
        for zone in current_zones
        if zone.get("source") == "lineart_slic_subzone"
        and float(zone.get("area_px") or 0) < min_area_px
    ]
    metrics = {
        "small_fragment_merge_enabled": True,
        "small_fragment_merges": merge_count,
        "small_fragment_unmerged": len(failed_fragments) + len(remaining_small),
        "small_fragment_unmerged_details": [*failed_fragments, *remaining_small][:20],
        "small_fragment_max_iterations": max_iterations,
    }
    return current_zones, metrics


def segment_lineart(image_bgr: np.ndarray, params: LineArtParams) -> dict[str, Any]:
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("image_bgr is empty")

    height, width = image_bgr.shape[:2]
    started = time.perf_counter()
    line_mask, derived = preprocess_line_mask(image_bgr, params)
    paintable_mask = cv2.bitwise_not(line_mask)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        paintable_mask,
        connectivity=8,
    )
    zones = _zones_from_component_labels(
        labels,
        stats,
        min_area_px=int(derived["min_area_px"]),
        max_area_px=float(derived["max_area_px"]),
        epsilon_ratio=float(derived["epsilon_ratio"]),
    )
    pre_slic_zone_count = len(zones)
    if params.enable_slic:
        zones, slic_metrics = subdivide_large_zones(image_bgr, zones, derived)
    else:
        slic_metrics = {
            "slic_enabled": False,
            "slic_input_zones": len(zones),
            "slic_subdivided_zones": 0,
            "slic_created_zones": 0,
        }
    if params.enable_slic and params.merge_small_slic_fragments:
        zones, small_fragment_metrics = merge_small_slic_fragments(zones, derived)
    else:
        small_fragment_metrics = {
            "small_fragment_merge_enabled": False,
            "small_fragment_merges": 0,
            "small_fragment_unmerged": 0,
            "small_fragment_unmerged_details": [],
        }
    elapsed_seconds = time.perf_counter() - started

    return {
        "source_image": None,
        "device": "cpu",
        "gpu": "not_used",
        "image": {"width": width, "height": height},
        "parameters": {**asdict(params), **derived},
        "metrics": {
            "raw_components": int(component_count - 1),
            "pre_slic_zones": pre_slic_zone_count,
            "zones": len(zones),
            "inference_seconds": round(elapsed_seconds, 4),
            "line_pixels": int(cv2.countNonZero(line_mask)),
            **slic_metrics,
            **small_fragment_metrics,
        },
        "zones": zones,
        "line_mask": line_mask,
    }


def save_region_overlay(
    image_bgr: np.ndarray,
    zones: list[dict[str, Any]],
    output_path: Path,
    *,
    alpha: float = 0.42,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overlay = image_bgr.copy()
    color_layer = image_bgr.copy()
    rng = np.random.default_rng(12345)

    for zone in zones:
        contour = np.array(zone["polygon"], dtype=np.int32).reshape(-1, 1, 2)
        color = [int(channel) for channel in rng.integers(40, 230, size=3)]
        cv2.fillPoly(color_layer, [contour], color)

    overlay = cv2.addWeighted(color_layer, alpha, overlay, 1.0 - alpha, 0)
    for zone in zones:
        contour = np.array(zone["polygon"], dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(overlay, [contour], True, (20, 20, 20), 1, cv2.LINE_AA)

    if not cv2.imwrite(str(output_path), overlay):
        raise ValueError(f"Could not write overlay: {output_path}")
