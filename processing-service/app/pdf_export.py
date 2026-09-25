from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import html
import math
import time
from typing import Any, Literal

import cv2
import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER, landscape, portrait
from reportlab.pdfgen import canvas
from scipy.ndimage import distance_transform_edt
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon
from shapely.validation import make_valid


PaperSize = Literal["A4", "Letter"]
Orientation = Literal["portrait", "landscape"]


@dataclass(frozen=True)
class LabelPlacement:
    zone_id: str
    x: float
    y: float
    font_size_image: float
    method: str
    clearance_px: float
    required_radius_px: float


@dataclass(frozen=True)
class PdfExport:
    pdf_bytes: bytes
    numbered_svg: str
    elapsed_seconds: float
    placement_metrics: dict[str, Any]


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


def _zone_polygon(zone: dict[str, Any]) -> Polygon:
    polygon = make_valid(Polygon(zone["polygon"]))
    parts = [part for part in _polygon_parts(polygon) if part.area > 1]
    if not parts:
        return Polygon(zone["polygon"])
    return max(parts, key=lambda item: item.area)


def _paint_number(zone: dict[str, Any]) -> str:
    return str(zone.get("paint_number") or zone["id"])


def _label_font_size(zone: dict[str, Any], polygon: Polygon) -> float:
    bbox = zone.get("bbox") or [0, 0, 0, 0]
    bbox_width = max(float(bbox[2]), 1.0)
    bbox_height = max(float(bbox[3]), 1.0)
    area_size = math.sqrt(max(float(zone.get("area_px") or polygon.area), 1.0))
    return max(5.0, min(28.0, area_size * 0.28, bbox_width * 0.55, bbox_height * 0.55))


def _text_required_radius(paint_number: str, font_size: float) -> float:
    # Conservative image-space estimate for a bold sans-serif label centered
    # in the zone. A circle with this radius should contain the text box.
    text_width = max(1, len(paint_number)) * font_size * 0.62
    return max(2.0, math.hypot(text_width / 2, font_size * 0.48))


def _point_clearance(polygon: Polygon, point: Point) -> float:
    if polygon.is_empty or not polygon.covers(point):
        return 0.0
    return max(0.0, float(point.distance(polygon.boundary)))


def _point_fits_label(polygon: Polygon, point: Point, required_radius: float) -> bool:
    if required_radius <= 0:
        return polygon.covers(point)
    eroded = polygon.buffer(-required_radius)
    if eroded.is_empty:
        return False
    return eroded.covers(point)


def _pole_of_inaccessibility(polygon: Polygon, required_radius: float) -> tuple[Point, float]:
    min_x, min_y, max_x, max_y = polygon.bounds
    padding = max(3, int(math.ceil(required_radius)) + 2)
    x0 = math.floor(min_x) - padding
    y0 = math.floor(min_y) - padding
    x1 = math.ceil(max_x) + padding
    y1 = math.ceil(max_y) + padding
    width = max(1, int(x1 - x0 + 1))
    height = max(1, int(y1 - y0 + 1))

    exterior = np.array(
        [[int(round(x - x0)), int(round(y - y0))] for x, y in polygon.exterior.coords],
        dtype=np.int32,
    )
    mask = np.zeros((height, width), dtype=np.uint8)
    if len(exterior) >= 3:
        cv2.fillPoly(mask, [exterior.reshape(-1, 1, 2)], 1)
    for interior in polygon.interiors:
        hole = np.array(
            [[int(round(x - x0)), int(round(y - y0))] for x, y in interior.coords],
            dtype=np.int32,
        )
        if len(hole) >= 3:
            cv2.fillPoly(mask, [hole.reshape(-1, 1, 2)], 0)

    if int(mask.max()) == 0:
        point = polygon.representative_point()
        return point, _point_clearance(polygon, point)

    distances = distance_transform_edt(mask.astype(bool))
    row, col = np.unravel_index(int(np.argmax(distances)), distances.shape)
    point = Point(float(x0 + col), float(y0 + row))
    if not polygon.covers(point):
        point = polygon.representative_point()
        return point, _point_clearance(polygon, point)
    return point, float(distances[row, col])


def _label_point(
    polygon: Polygon,
    paint_number: str,
    font_size: float,
) -> tuple[Point, str, float, float, float]:
    required_radius = _text_required_radius(paint_number, font_size)
    point = polygon.representative_point()
    representative_clearance = _point_clearance(polygon, point)
    if _point_fits_label(polygon, point, required_radius):
        return point, "representative_point", representative_clearance, required_radius, font_size

    pole, pole_clearance = _pole_of_inaccessibility(polygon, required_radius)
    if pole_clearance + 0.001 >= required_radius:
        return pole, "pole_of_inaccessibility", pole_clearance, required_radius, font_size

    reduced_font_size = font_size
    reduced_required_radius = required_radius
    while reduced_font_size > 3.5:
        reduced_font_size = max(3.5, reduced_font_size * 0.9)
        reduced_required_radius = _text_required_radius(paint_number, reduced_font_size)
        if pole_clearance + 0.001 >= reduced_required_radius:
            return (
                pole,
                "pole_of_inaccessibility_reduced_font",
                pole_clearance,
                reduced_required_radius,
                reduced_font_size,
            )

    return pole, "pole_of_inaccessibility_tight", pole_clearance, reduced_required_radius, reduced_font_size


def label_placements(zones: list[dict[str, Any]]) -> list[LabelPlacement]:
    placements: list[LabelPlacement] = []
    for zone in zones:
        if zone.get("suppress_label"):
            continue
        polygon = _zone_polygon(zone)
        paint_number = _paint_number(zone)
        font_size = _label_font_size(zone, polygon)
        point, method, clearance, required_radius, adjusted_font_size = _label_point(polygon, paint_number, font_size)
        placements.append(
            LabelPlacement(
                zone_id=str(zone["id"]),
                x=float(point.x),
                y=float(point.y),
                font_size_image=adjusted_font_size,
                method=method,
                clearance_px=round(clearance, 3),
                required_radius_px=round(required_radius, 3),
            )
        )
    return placements


def placement_metrics(placements: list[LabelPlacement]) -> dict[str, Any]:
    pole_count = sum(1 for placement in placements if placement.method.startswith("pole_of_inaccessibility"))
    reduced_count = sum(1 for placement in placements if placement.method == "pole_of_inaccessibility_reduced_font")
    tight_count = sum(
        1
        for placement in placements
        if placement.clearance_px + 0.001 < placement.required_radius_px
    )
    return {
        "placement_count": len(placements),
        "representative_point_count": len(placements) - pole_count,
        "pole_of_inaccessibility_count": pole_count,
        "reduced_font_count": reduced_count,
        "tight_label_count": tight_count,
        "min_clearance_px": round(min((placement.clearance_px for placement in placements), default=0.0), 3),
        "min_clearance_ratio": round(
            min(
                (
                    placement.clearance_px / placement.required_radius_px
                    for placement in placements
                    if placement.required_radius_px > 0
                ),
                default=0.0,
            ),
            3,
        ),
    }


def _polygon_path(points: list[list[int]]) -> str:
    if not points:
        return ""
    first, *rest = points
    commands = [f"M {first[0]} {first[1]}"]
    commands.extend(f"L {point[0]} {point[1]}" for point in rest)
    commands.append("Z")
    return " ".join(commands)


def numbered_svg(
    width: int,
    height: int,
    zones: list[dict[str, Any]],
    placements: list[LabelPlacement] | None = None,
) -> str:
    placement_map = {
        placement.zone_id: placement
        for placement in (placements if placements is not None else label_placements(zones))
    }
    elements: list[str] = []
    for zone in zones:
        zone_id = str(zone["id"])
        paint_number = _paint_number(zone)
        elements.append(
            f'<path id="zone-{html.escape(zone_id)}" data-zone-id="{html.escape(zone_id)}" '
            f'data-paint-number="{html.escape(paint_number)}" '
            f'd="{html.escape(_polygon_path(zone["polygon"]))}" fill="none" '
            'stroke="#000000" stroke-width="2" vector-effect="non-scaling-stroke"/>'
        )
    for zone in zones:
        zone_id = str(zone["id"])
        if zone_id not in placement_map:
            continue
        paint_number = _paint_number(zone)
        placement = placement_map[zone_id]
        elements.append(
            f'<text x="{placement.x:.2f}" y="{placement.y:.2f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="{placement.font_size_image:.2f}" '
            f'font-weight="700" fill="#000000">{html.escape(paint_number)}</text>'
        )
    body = "\n  ".join(elements)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">\n  {body}\n</svg>'
    )


def _page_size(paper_size: PaperSize, orientation: Orientation) -> tuple[float, float]:
    size = A4 if paper_size == "A4" else LETTER
    return landscape(size) if orientation == "landscape" else portrait(size)


def _hex_color(value: str, fallback: str = "#B8B8B8") -> str:
    color = value.strip() if isinstance(value, str) else fallback
    if len(color) == 7 and color.startswith("#"):
        try:
            int(color[1:], 16)
            return color.upper()
        except ValueError:
            return fallback
    return fallback


def _reportlab_color(value: str) -> colors.Color:
    return colors.HexColor(_hex_color(value))


def _sorted_palette_numbers(zones: list[dict[str, Any]], palette: dict[str, str]) -> list[str]:
    numbers = set(str(number) for number in palette)
    numbers.update(_paint_number(zone) for zone in zones)
    return sorted(numbers, key=lambda value: int(value) if value.isdigit() else value)


def _draw_drawing_page(
    pdf: canvas.Canvas,
    page_width: float,
    page_height: float,
    image_width: int,
    image_height: int,
    zones: list[dict[str, Any]],
    placements: list[LabelPlacement],
) -> None:
    margin = 36.0
    scale = min((page_width - margin * 2) / image_width, (page_height - margin * 2) / image_height)
    drawing_width = image_width * scale
    drawing_height = image_height * scale
    origin_x = (page_width - drawing_width) / 2
    origin_y = (page_height - drawing_height) / 2
    placement_map = {placement.zone_id: placement for placement in placements}

    def transform(point: list[int]) -> tuple[float, float]:
        return origin_x + point[0] * scale, origin_y + (image_height - point[1]) * scale

    pdf.setLineWidth(max(1.0, 2 * scale))
    pdf.setStrokeColor(colors.black)
    for zone in zones:
        path = pdf.beginPath()
        points = zone["polygon"]
        if not points:
            continue
        first_x, first_y = transform(points[0])
        path.moveTo(first_x, first_y)
        for point in points[1:]:
            x, y = transform(point)
            path.lineTo(x, y)
        path.close()
        pdf.drawPath(path, stroke=1, fill=0)

    pdf.setFillColor(colors.black)
    for zone in zones:
        zone_id = str(zone["id"])
        if zone_id not in placement_map:
            continue
        paint_number = _paint_number(zone)
        placement = placement_map[zone_id]
        x = origin_x + placement.x * scale
        y = origin_y + (image_height - placement.y) * scale
        bbox = zone.get("bbox") or [0, 0, 1, 1]
        max_width = max(4.0, float(bbox[2]) * scale * 0.85)
        max_height = max(4.0, float(bbox[3]) * scale * 0.85)
        font_size = min(placement.font_size_image * scale, 18.0, max_height)
        text_width = pdf.stringWidth(paint_number, "Helvetica-Bold", font_size)
        if text_width > max_width:
            font_size = max(3.5, font_size * (max_width / text_width))
        else:
            font_size = max(3.5, font_size)
        pdf.setFont("Helvetica-Bold", font_size)
        pdf.drawCentredString(x, y - font_size * 0.32, paint_number)


def _draw_legend_page(
    pdf: canvas.Canvas,
    page_width: float,
    page_height: float,
    zones: list[dict[str, Any]],
    palette: dict[str, str],
) -> None:
    margin = 42.0
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(margin, page_height - margin, "Leyenda de colores")

    palette_numbers = _sorted_palette_numbers(zones, palette)
    row_height = 24.0
    header_gap = 34.0
    usable_height = page_height - margin * 2 - header_gap
    rows_per_column = max(1, int(usable_height // row_height))
    columns = max(1, math.ceil(len(palette_numbers) / rows_per_column))
    column_width = (page_width - margin * 2) / columns
    swatch_size = 14.0

    pdf.setFont("Helvetica", 10)
    for index, paint_number in enumerate(palette_numbers):
        column = index // rows_per_column
        row = index % rows_per_column
        x = margin + column * column_width
        y = page_height - margin - header_gap - row * row_height
        color_hex = _hex_color(palette.get(paint_number, "#B8B8B8"))

        pdf.setFillColor(_reportlab_color(color_hex))
        pdf.rect(x, y - swatch_size + 3, swatch_size, swatch_size, fill=1, stroke=0)
        pdf.setStrokeColor(colors.black)
        pdf.rect(x, y - swatch_size + 3, swatch_size, swatch_size, fill=0, stroke=1)
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(x + swatch_size + 8, y - 8, paint_number)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(x + swatch_size + 36, y - 8, color_hex)


def generate_pdf(
    *,
    image_width: int,
    image_height: int,
    zones: list[dict[str, Any]],
    palette: dict[str, str],
    paper_size: PaperSize,
    orientation: Orientation,
) -> PdfExport:
    started = time.perf_counter()
    page_width, page_height = _page_size(paper_size, orientation)
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(page_width, page_height), pageCompression=1)
    pdf.setTitle("ColorWind pintar por numeros")
    placements = label_placements(zones)

    _draw_drawing_page(pdf, page_width, page_height, image_width, image_height, zones, placements)
    pdf.showPage()
    _draw_legend_page(pdf, page_width, page_height, zones, palette)
    pdf.showPage()
    pdf.save()

    return PdfExport(
        pdf_bytes=buffer.getvalue(),
        numbered_svg=numbered_svg(image_width, image_height, zones, placements),
        elapsed_seconds=round(time.perf_counter() - started, 4),
        placement_metrics=placement_metrics(placements),
    )
