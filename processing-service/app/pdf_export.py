from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import html
import math
import time
from typing import Any, Literal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER, landscape, portrait
from reportlab.pdfgen import canvas
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


@dataclass(frozen=True)
class PdfExport:
    pdf_bytes: bytes
    numbered_svg: str
    elapsed_seconds: float


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


def _label_point(polygon: Polygon) -> Point:
    centroid = polygon.centroid
    if polygon.covers(centroid):
        return centroid
    return polygon.representative_point()


def _label_font_size(zone: dict[str, Any], polygon: Polygon) -> float:
    bbox = zone.get("bbox") or [0, 0, 0, 0]
    bbox_width = max(float(bbox[2]), 1.0)
    bbox_height = max(float(bbox[3]), 1.0)
    area_size = math.sqrt(max(float(zone.get("area_px") or polygon.area), 1.0))
    return max(5.0, min(28.0, area_size * 0.28, bbox_width * 0.55, bbox_height * 0.55))


def label_placements(zones: list[dict[str, Any]]) -> list[LabelPlacement]:
    placements: list[LabelPlacement] = []
    for zone in zones:
        polygon = _zone_polygon(zone)
        point = _label_point(polygon)
        placements.append(
            LabelPlacement(
                zone_id=str(zone["id"]),
                x=float(point.x),
                y=float(point.y),
                font_size_image=_label_font_size(zone, polygon),
            )
        )
    return placements


def _polygon_path(points: list[list[int]]) -> str:
    if not points:
        return ""
    first, *rest = points
    commands = [f"M {first[0]} {first[1]}"]
    commands.extend(f"L {point[0]} {point[1]}" for point in rest)
    commands.append("Z")
    return " ".join(commands)


def numbered_svg(width: int, height: int, zones: list[dict[str, Any]]) -> str:
    placements = {placement.zone_id: placement for placement in label_placements(zones)}
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
        paint_number = _paint_number(zone)
        placement = placements[zone_id]
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
) -> None:
    margin = 36.0
    scale = min((page_width - margin * 2) / image_width, (page_height - margin * 2) / image_height)
    drawing_width = image_width * scale
    drawing_height = image_height * scale
    origin_x = (page_width - drawing_width) / 2
    origin_y = (page_height - drawing_height) / 2
    placements = {placement.zone_id: placement for placement in label_placements(zones)}

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
        paint_number = _paint_number(zone)
        placement = placements[zone_id]
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

    _draw_drawing_page(pdf, page_width, page_height, image_width, image_height, zones)
    pdf.showPage()
    _draw_legend_page(pdf, page_width, page_height, zones, palette)
    pdf.showPage()
    pdf.save()

    return PdfExport(
        pdf_bytes=buffer.getvalue(),
        numbered_svg=numbered_svg(image_width, image_height, zones),
        elapsed_seconds=round(time.perf_counter() - started, 4),
    )
