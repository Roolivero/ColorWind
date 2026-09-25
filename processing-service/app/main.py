from __future__ import annotations

import base64
import logging
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
import torch
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from app.geometry import GeometryError, merge_zones, should_confirm_merge, split_zone
from app.lineart import LineArtParams, assign_graph_paint_numbers, save_region_overlay, segment_lineart
from app.palette import suggested_palettes
from app.pdf_export import generate_pdf
from segment_image import ROOT, SegmentParams, load_model, require_cuda, segment_path


LOGGER = logging.getLogger("processing-service")
DEVICE = "cuda:0"
MODEL_PATH = ROOT / "models" / "FastSAM-s.pt"
RUNTIME_DIR = ROOT / "runtime"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
LINEART_OVERLAY_DIR = RUNTIME_DIR / "lineart-overlays"
PROJECTS: dict[str, dict[str, Any]] = {}
MODEL_LOCK = threading.Lock()
DEFAULT_NUM_COLORS = 12
DEFAULT_DETAIL_LEVEL = 0.5
DETAIL_MIN_AREA_RATIOS = (0.012, 0.0012)
DETAIL_MAX_ZONES = (35, 180)


class SegmentRequest(BaseModel):
    image_base64: str
    mode: Literal["color", "bw"]
    num_colors: int | None = Field(default=None, ge=1, le=30)
    detail_level: float | None = Field(default=None, ge=0.0, le=1.0)
    # Legacy Fase 3/4 fields kept so older curl snippets fail gracefully less often.
    num_zones: int | None = Field(default=None, ge=1, le=256)
    min_zone_area_px: int | None = Field(default=None, ge=1)


class PaletteResponse(BaseModel):
    name: str
    colors: dict[str, str]


class SegmentResponse(BaseModel):
    project_id: str
    zones_svg: str
    zones_geojson: dict[str, Any]
    suggested_palettes: list[PaletteResponse]


class MergeRequest(BaseModel):
    project_id: str
    zone_ids: list[str] = Field(min_length=2, max_length=2)
    palette_colors: dict[str, str] | None = None
    keep_zone_id: str | None = None


class SplitRequest(BaseModel):
    project_id: str
    zone_id: str
    split_line: list[list[float]] = Field(min_length=2)
    palette_colors: dict[str, str] | None = None


class MergeConfirmationResponse(BaseModel):
    requires_confirmation: bool
    reason: Literal["area_difference_below_threshold"]
    candidates: list[str]


class ExportPdfRequest(BaseModel):
    project_id: str
    palette: dict[str, str]
    paper_size: Literal["A4", "Letter"]
    orientation: Literal["portrait", "landscape"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    LINEART_OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
    require_cuda(DEVICE)
    app.state.model = load_model(MODEL_PATH)
    LOGGER.info("FastAPI startup device=%s gpu=%s", DEVICE, torch.cuda.get_device_name(0))
    yield


app = FastAPI(title="ColorWind Processing Service", version="0.3.0", lifespan=lifespan)


def decode_image(image_base64: str) -> np.ndarray:
    encoded = image_base64.strip()
    if encoded.startswith("data:") and "," in encoded:
        encoded = encoded.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="image_base64 is not valid base64") from exc

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="image_base64 is not a readable image")
    return image


def write_temp_image(image_bgr: np.ndarray, mode: str) -> Path:
    if mode == "bw":
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        image_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    handle = tempfile.NamedTemporaryFile(prefix="segment-", suffix=".png", dir=UPLOAD_DIR, delete=False)
    handle.close()
    path = Path(handle.name)
    if not cv2.imwrite(str(path), image_bgr):
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not prepare image for segmentation")
    return path


def image_for_mode(image_bgr: np.ndarray, mode: str) -> np.ndarray:
    if mode != "bw":
        return image_bgr
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def normalized_num_colors(request: SegmentRequest) -> int:
    requested = request.num_colors if request.num_colors is not None else request.num_zones
    return max(1, min(int(requested or DEFAULT_NUM_COLORS), 30))


def normalized_detail_level(request: SegmentRequest) -> float:
    if request.detail_level is None:
        return DEFAULT_DETAIL_LEVEL
    return max(0.0, min(float(request.detail_level), 1.0))


def detail_segmentation_limits(
    width: int,
    height: int,
    detail_level: float,
    legacy_min_area_px: int | None,
) -> tuple[int, int]:
    min_ratio_low, min_ratio_high = DETAIL_MIN_AREA_RATIOS
    min_area_ratio = min_ratio_low + (min_ratio_high - min_ratio_low) * detail_level
    min_area_px = legacy_min_area_px or int(round(width * height * min_area_ratio))
    max_zones_low, max_zones_high = DETAIL_MAX_ZONES
    max_zones = int(round(max_zones_low + (max_zones_high - max_zones_low) * detail_level))
    return max(50, min_area_px), max_zones


def assign_paint_numbers(
    image_bgr: np.ndarray,
    zones: list[dict[str, Any]],
    num_colors: int,
) -> list[dict[str, Any]]:
    if not zones:
        return zones

    samples: list[list[float]] = []
    areas: list[float] = []
    for zone in zones:
        mask = np.zeros(image_bgr.shape[:2], dtype=np.uint8)
        contour = np.array(zone["polygon"], dtype=np.int32).reshape(-1, 1, 2)
        cv2.fillPoly(mask, [contour], 255)
        blue, green, red, _ = cv2.mean(image_bgr, mask=mask)
        samples.append([blue, green, red])
        areas.append(max(float(zone.get("area_px") or 1.0), 1.0))

    cluster_count = min(max(1, num_colors), len(samples))
    if cluster_count == 1:
        for zone in zones:
            zone["paint_number"] = "1"
        return zones

    bgr_samples = np.array(samples, dtype=np.uint8).reshape(-1, 1, 3)
    lab_samples = cv2.cvtColor(bgr_samples, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    try:
        _, labels, _ = cv2.kmeans(lab_samples, cluster_count, None, criteria, 5, cv2.KMEANS_PP_CENTERS)
    except cv2.error:
        for index, zone in enumerate(zones):
            zone["paint_number"] = str((index % num_colors) + 1)
        return zones

    labels_flat = labels.flatten()
    area_by_cluster = np.zeros(cluster_count, dtype=np.float64)
    for label, area in zip(labels_flat, areas, strict=True):
        area_by_cluster[int(label)] += area
    ordered_clusters = np.argsort(-area_by_cluster)
    cluster_to_number = {int(cluster): str(index + 1) for index, cluster in enumerate(ordered_clusters)}

    for zone, label in zip(zones, labels_flat, strict=True):
        zone["paint_number"] = cluster_to_number[int(label)]

    target_numbers = [str(index + 1) for index in range(min(num_colors, len(zones)))]
    counts = {number: 0 for number in target_numbers}
    for zone in zones:
        current_number = zone_paint_number(zone)
        if current_number in counts:
            counts[current_number] += 1

    missing_numbers = [number for number in target_numbers if counts[number] == 0]
    if missing_numbers:
        zone_indexes = sorted(
            range(len(zones)),
            key=lambda index: float(zones[index].get("area_px") or 0),
            reverse=True,
        )
        for missing_number in missing_numbers:
            donor_index = next(
                (
                    index
                    for index in zone_indexes
                    if counts.get(zone_paint_number(zones[index]), 0) > 1
                ),
                None,
            )
            if donor_index is None:
                break
            donor_number = zone_paint_number(zones[donor_index])
            counts[donor_number] -= 1
            zones[donor_index]["paint_number"] = missing_number
            counts[missing_number] = 1
    return zones


def zone_paint_number(zone: dict[str, Any]) -> str:
    return str(zone.get("paint_number") or zone["id"])


def polygon_path(points: list[list[int]]) -> str:
    if not points:
        return ""
    first, *rest = points
    commands = [f"M {first[0]} {first[1]}"]
    commands.extend(f"L {point[0]} {point[1]}" for point in rest)
    commands.append("Z")
    return " ".join(commands)


def zones_to_svg(width: int, height: int, zones: list[dict[str, Any]]) -> str:
    paths = []
    for zone in zones:
        path_data = polygon_path(zone["polygon"])
        zone_id = str(zone["id"])
        paint_number = zone_paint_number(zone)
        paths.append(
            f'<path id="zone-{zone_id}" data-zone-id="{zone_id}" data-paint-number="{paint_number}" d="{path_data}" '
            'fill="none" stroke="#000000" stroke-width="2" vector-effect="non-scaling-stroke"/>'
        )
    body = "\n  ".join(paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">\n'
        f'  {body}\n'
        "</svg>"
    )


def zones_to_geojson(
    width: int,
    height: int,
    mode: str,
    zones: list[dict[str, Any]],
    *,
    num_colors: int | None = None,
    detail_level: float | None = None,
) -> dict[str, Any]:
    features = []
    for zone in zones:
        ring = [[int(x), int(y)] for x, y in zone["polygon"]]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        zone_id = str(zone["id"])
        paint_number = zone_paint_number(zone)
        properties = {
            "zone_id": zone_id,
            "paint_number": paint_number,
            "area_px": zone["area_px"],
            "bbox": zone["bbox"],
        }
        if zone.get("requires_manual_color"):
            properties["requires_manual_color"] = True
        if zone.get("suppress_label"):
            properties["suppress_label"] = True
        features.append(
            {
                "type": "Feature",
                "id": zone_id,
                "properties": properties,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [ring],
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "properties": {
            "width": width,
            "height": height,
            "mode": mode,
            "zone_count": len(zones),
            "num_colors": num_colors,
            "detail_level": detail_level,
        },
        "features": features,
    }


def project_or_404(project_id: str) -> dict[str, Any]:
    project = PROJECTS.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project_id not found")
    return project


def original_palette(project: dict[str, Any]) -> dict[str, str]:
    palettes = project.get("original_suggested_palettes") or project.get("suggested_palettes") or []
    if not palettes:
        return {}
    return {str(key): value for key, value in palettes[0].get("colors", {}).items()}


def fallback_palette(project: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): value
        for key, value in (project.get("palette_colors") or original_palette(project)).items()
    }


def operation_palettes(project: dict[str, Any], palette_colors: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"name": "Paleta actual", "colors": palette_colors},
        *(project.get("original_suggested_palettes") or []),
    ]


def update_project_geometry(
    project: dict[str, Any],
    zones: list[dict[str, Any]],
    palette_colors: dict[str, str],
) -> SegmentResponse:
    image_info = project["image"]
    width = int(image_info["width"])
    height = int(image_info["height"])
    zones_svg = zones_to_svg(width, height, zones)
    zones_geojson = zones_to_geojson(
        width,
        height,
        project["mode"],
        zones,
        num_colors=project.get("num_colors"),
        detail_level=project.get("detail_level"),
    )
    palettes = operation_palettes(project, palette_colors)

    project.update(
        {
            "zones": zones,
            "palette_colors": palette_colors,
            "zones_svg": zones_svg,
            "zones_geojson": zones_geojson,
            "suggested_palettes": palettes,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    return SegmentResponse(
        project_id=project["project_id"],
        zones_svg=zones_svg,
        zones_geojson=zones_geojson,
        suggested_palettes=palettes,
    )


@app.post("/segment", response_model=SegmentResponse)
def segment(request: SegmentRequest) -> SegmentResponse:
    image_bgr = decode_image(request.image_base64)
    processing_image = image_for_mode(image_bgr, request.mode)
    height, width = processing_image.shape[:2]
    num_colors = normalized_num_colors(request)
    detail_level = normalized_detail_level(request)
    pipeline_name = "lineart_connected_components"
    if request.mode == "bw":
        try:
            payload = segment_lineart(
                processing_image,
                LineArtParams(
                    detail_level=detail_level,
                    min_area_px=request.min_zone_area_px,
                ),
            )
        except Exception as exc:
            LOGGER.exception("Line-art segmentation failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    else:
        pipeline_name = "fastsam"
        temp_path = write_temp_image(image_bgr, request.mode)
        min_area_px, max_zones = detail_segmentation_limits(
            width,
            height,
            detail_level,
            request.min_zone_area_px,
        )
        params = SegmentParams(
            device=DEVICE,
            min_area_px=min_area_px,
            max_zones=max_zones,
        )

        try:
            with MODEL_LOCK:
                payload = segment_path(app.state.model, temp_path, params)
        except Exception as exc:
            LOGGER.exception("Segmentation failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            temp_path.unlink(missing_ok=True)

    image_info = payload["image"]
    width = int(image_info["width"])
    height = int(image_info["height"])
    if request.mode == "bw":
        label_min_area_px = float(payload.get("parameters", {}).get("min_area_px") or 0) * 1.2
        zones, paint_number_metrics = assign_graph_paint_numbers(
            payload["zones"],
            num_colors,
            label_min_area_px=label_min_area_px,
        )
    else:
        zones = assign_paint_numbers(processing_image, payload["zones"], num_colors)
        paint_number_metrics = {
            "paint_number_strategy": "color_kmeans",
        }
    payload["metrics"] = {**payload["metrics"], **paint_number_metrics}
    project_id = str(uuid.uuid4())
    zones_svg = zones_to_svg(width, height, zones)
    zones_geojson = zones_to_geojson(
        width,
        height,
        request.mode,
        zones,
        num_colors=num_colors,
        detail_level=detail_level,
    )
    palettes = suggested_palettes(processing_image, num_colors)
    palette_colors = palettes[0]["colors"] if palettes else {}
    debug_outputs: dict[str, str] = {}
    if request.mode == "bw":
        overlay_path = LINEART_OVERLAY_DIR / f"{project_id}.regions.png"
        save_region_overlay(processing_image, zones, overlay_path)
        debug_outputs["lineart_overlay_png"] = str(overlay_path)

    PROJECTS[project_id] = {
        "project_id": project_id,
        "mode": request.mode,
        "pipeline": pipeline_name,
        "num_colors": num_colors,
        "detail_level": detail_level,
        "image": image_info,
        "zones": zones,
        "zones_svg": zones_svg,
        "zones_geojson": zones_geojson,
        "suggested_palettes": palettes,
        "original_suggested_palettes": palettes,
        "palette_colors": palette_colors,
        "created_at": datetime.now(UTC).isoformat(),
        "metrics": payload["metrics"],
        "debug_outputs": debug_outputs,
        "device": payload["device"],
        "gpu": payload["gpu"],
    }
    LOGGER.info(
        "project_id=%s pipeline=%s device=%s gpu=%s zones=%s colors=%s detail=%.2f parameters=%s inference=%ss debug_outputs=%s",
        project_id,
        pipeline_name,
        payload["device"],
        payload["gpu"],
        len(zones),
        num_colors,
        detail_level,
        payload.get("parameters"),
        payload["metrics"]["inference_seconds"],
        debug_outputs,
    )
    return SegmentResponse(
        project_id=project_id,
        zones_svg=zones_svg,
        zones_geojson=zones_geojson,
        suggested_palettes=palettes,
    )


@app.post("/zones/merge", response_model=SegmentResponse | MergeConfirmationResponse)
def merge(request: MergeRequest) -> SegmentResponse | MergeConfirmationResponse:
    project = project_or_404(request.project_id)
    zone_ids = [str(zone_id) for zone_id in request.zone_ids]

    try:
        if should_confirm_merge(project["zones"], zone_ids):
            if not request.keep_zone_id:
                return MergeConfirmationResponse(
                    requires_confirmation=True,
                    reason="area_difference_below_threshold",
                    candidates=zone_ids,
                )
            if str(request.keep_zone_id) not in zone_ids:
                raise GeometryError("keep_zone_id debe ser una de las zonas a fusionar.")

        zones, palette_colors = merge_zones(
            project["zones"],
            zone_ids,
            palette_colors=request.palette_colors,
            fallback_colors=fallback_palette(project),
            keep_zone_id=request.keep_zone_id,
        )
    except GeometryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    LOGGER.info("project_id=%s merge=%s zones=%s", request.project_id, zone_ids, len(zones))
    return update_project_geometry(project, zones, palette_colors)


@app.post("/zones/split", response_model=SegmentResponse)
def split_zone_endpoint(request: SplitRequest) -> SegmentResponse:
    project = project_or_404(request.project_id)
    try:
        zones, palette_colors = split_zone(
            project["zones"],
            request.zone_id,
            request.split_line,
            palette_colors=request.palette_colors,
            fallback_colors=fallback_palette(project),
        )
    except GeometryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    LOGGER.info("project_id=%s split=%s zones=%s", request.project_id, request.zone_id, len(zones))
    return update_project_geometry(project, zones, palette_colors)


@app.post("/export/pdf")
def export_pdf(request: ExportPdfRequest) -> Response:
    project = project_or_404(request.project_id)
    image_info = project["image"]

    try:
        result = generate_pdf(
            palette={str(key): value for key, value in request.palette.items()},
            image_width=int(image_info["width"]),
            image_height=int(image_info["height"]),
            zones=project["zones"],
            paper_size=request.paper_size,
            orientation=request.orientation,
        )
    except Exception as exc:
        LOGGER.exception("PDF export failed")
        raise HTTPException(status_code=500, detail="Could not generate PDF") from exc

    project["numbered_svg"] = result.numbered_svg
    project["pdf_placement_metrics"] = result.placement_metrics
    LOGGER.info(
        "project_id=%s export_pdf paper=%s orientation=%s zones=%s seconds=%s placements=%s",
        request.project_id,
        request.paper_size,
        request.orientation,
        len(project["zones"]),
        result.elapsed_seconds,
        result.placement_metrics,
    )
    return Response(
        content=result.pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="colorwind-{request.project_id[:8]}.pdf"',
            "X-Generation-Seconds": str(result.elapsed_seconds),
        },
    )
