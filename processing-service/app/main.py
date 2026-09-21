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
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.palette import suggested_palettes
from segment_image import ROOT, SegmentParams, load_model, require_cuda, segment_path


LOGGER = logging.getLogger("processing-service")
DEVICE = "cuda:0"
MODEL_PATH = ROOT / "models" / "FastSAM-s.pt"
RUNTIME_DIR = ROOT / "runtime"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
PROJECTS: dict[str, dict[str, Any]] = {}
MODEL_LOCK = threading.Lock()


class SegmentRequest(BaseModel):
    image_base64: str
    mode: Literal["color", "bw"]
    num_zones: int = Field(ge=1, le=256)
    min_zone_area_px: int = Field(ge=1)


class PaletteResponse(BaseModel):
    name: str
    colors: dict[str, str]


class SegmentResponse(BaseModel):
    project_id: str
    zones_svg: str
    zones_geojson: dict[str, Any]
    suggested_palettes: list[PaletteResponse]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    require_cuda(DEVICE)
    app.state.model = load_model(MODEL_PATH)
    LOGGER.info("FastAPI startup device=%s gpu=%s", DEVICE, torch.cuda.get_device_name(0))
    yield


app = FastAPI(title="ColorWind Processing Service", version="0.2.0", lifespan=lifespan)


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
        paths.append(
            f'<path id="zone-{zone_id}" data-zone-id="{zone_id}" d="{path_data}" '
            'fill="none" stroke="#000000" stroke-width="2" vector-effect="non-scaling-stroke"/>'
        )
    body = "\n  ".join(paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">\n'
        f'  {body}\n'
        "</svg>"
    )


def zones_to_geojson(width: int, height: int, mode: str, zones: list[dict[str, Any]]) -> dict[str, Any]:
    features = []
    for zone in zones:
        ring = [[int(x), int(y)] for x, y in zone["polygon"]]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        zone_id = str(zone["id"])
        features.append(
            {
                "type": "Feature",
                "id": zone_id,
                "properties": {
                    "zone_id": zone_id,
                    "area_px": zone["area_px"],
                    "bbox": zone["bbox"],
                },
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
        },
        "features": features,
    }


@app.post("/segment", response_model=SegmentResponse)
def segment(request: SegmentRequest) -> SegmentResponse:
    image_bgr = decode_image(request.image_base64)
    temp_path = write_temp_image(image_bgr, request.mode)

    params = SegmentParams(
        device=DEVICE,
        min_area_px=request.min_zone_area_px,
        max_zones=request.num_zones,
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
    zones = payload["zones"]
    project_id = str(uuid.uuid4())
    zones_svg = zones_to_svg(width, height, zones)
    zones_geojson = zones_to_geojson(width, height, request.mode, zones)
    palettes = suggested_palettes(image_bgr, len(zones))

    PROJECTS[project_id] = {
        "project_id": project_id,
        "mode": request.mode,
        "image": image_info,
        "zones": zones,
        "zones_svg": zones_svg,
        "zones_geojson": zones_geojson,
        "suggested_palettes": palettes,
        "created_at": datetime.now(UTC).isoformat(),
        "metrics": payload["metrics"],
        "device": payload["device"],
        "gpu": payload["gpu"],
    }
    LOGGER.info(
        "project_id=%s device=%s gpu=%s zones=%s inference=%ss",
        project_id,
        payload["device"],
        payload["gpu"],
        len(zones),
        payload["metrics"]["inference_seconds"],
    )
    return SegmentResponse(
        project_id=project_id,
        zones_svg=zones_svg,
        zones_geojson=zones_geojson,
        suggested_palettes=palettes,
    )
