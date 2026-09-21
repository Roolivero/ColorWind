from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))

import cv2
import numpy as np
import torch
from ultralytics import FastSAM
from ultralytics.utils.downloads import attempt_download_asset


LOGGER = logging.getLogger("segment-image")


@dataclass(frozen=True)
class SegmentParams:
    device: str = "cuda:0"
    imgsz: int = 640
    conf: float = 0.35
    iou: float = 0.9
    max_det: int = 256
    min_area_px: int = 350
    min_area_ratio: float = 0.001
    max_area_ratio: float = 0.92
    epsilon_ratio: float = 0.003
    duplicate_overlap_ratio: float = 0.82
    duplicate_area_ratio: float = 0.65
    max_zones: int | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Segment an image with FastSAM on GPU and export polygon zones.",
    )
    parser.add_argument(
        "images",
        nargs="+",
        type=Path,
        help="Input image path(s). JPG and PNG are expected.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs",
        help="Directory for line preview PNGs and polygon JSON files.",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=ROOT / "models" / "FastSAM-s.pt",
        help="FastSAM weights path. Missing official weights are downloaded.",
    )
    parser.add_argument("--device", default="cuda:0", help="Torch device, default: cuda:0.")
    parser.add_argument("--imgsz", type=int, default=640, help="FastSAM inference size.")
    parser.add_argument("--conf", type=float, default=0.35, help="FastSAM confidence threshold.")
    parser.add_argument("--iou", type=float, default=0.9, help="FastSAM IoU threshold.")
    parser.add_argument("--max-det", type=int, default=256, help="Maximum mask detections.")
    parser.add_argument(
        "--min-area-px",
        type=int,
        default=350,
        help="Discard contours smaller than this pixel area.",
    )
    parser.add_argument(
        "--min-area-ratio",
        type=float,
        default=0.001,
        help="Also discard contours smaller than this fraction of the image.",
    )
    parser.add_argument(
        "--max-area-ratio",
        type=float,
        default=0.92,
        help="Discard single contours larger than this fraction of the image.",
    )
    parser.add_argument(
        "--epsilon-ratio",
        type=float,
        default=0.003,
        help="Polygon simplification ratio relative to contour perimeter.",
    )
    parser.add_argument(
        "--duplicate-overlap-ratio",
        type=float,
        default=0.82,
        help="Drop similar-size zones when overlap/min(area) is above this ratio.",
    )
    parser.add_argument(
        "--duplicate-area-ratio",
        type=float,
        default=0.65,
        help="Only dedupe overlapping zones when min(area)/max(area) is above this ratio.",
    )
    parser.add_argument(
        "--line-thickness",
        type=int,
        default=2,
        help="Line thickness for preview image.",
    )
    parser.add_argument(
        "--label-zones",
        action="store_true",
        help="Draw zone ids in the preview image. The JSON always includes ids.",
    )
    return parser.parse_args()


def require_cuda(device: str) -> torch.device:
    torch_device = torch.device(device)
    if torch_device.type != "cuda":
        raise RuntimeError(f"Fase 1 requires GPU execution; got device={device!r}.")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available to PyTorch. Refusing to fall back to CPU.")
    index = torch_device.index or 0
    if index >= torch.cuda.device_count():
        raise RuntimeError(f"CUDA device index {index} is not available.")
    LOGGER.info("device=%s gpu=%s", torch_device, torch.cuda.get_device_name(index))
    return torch_device


def load_model(weights_path: Path) -> FastSAM:
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    resolved = Path(attempt_download_asset(str(weights_path)))
    LOGGER.info("model=%s", resolved)
    return FastSAM(str(resolved))


def renumber_zones(zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, zone in enumerate(zones, start=1):
        zone["id"] = index
    return zones


def contour_to_polygon(contour: np.ndarray, epsilon_ratio: float) -> list[list[int]]:
    perimeter = cv2.arcLength(contour, True)
    epsilon = max(1.0, epsilon_ratio * perimeter)
    approx = cv2.approxPolyDP(contour, epsilon, True)
    return approx.reshape(-1, 2).astype(int).tolist()


def masks_to_zones(
    masks: torch.Tensor,
    width: int,
    height: int,
    *,
    min_area_px: int,
    min_area_ratio: float,
    max_area_ratio: float,
    epsilon_ratio: float,
    duplicate_overlap_ratio: float,
    duplicate_area_ratio: float,
) -> list[dict[str, Any]]:
    min_area = max(float(min_area_px), float(width * height) * min_area_ratio)
    max_area = float(width * height) * max_area_ratio
    candidates: list[dict[str, Any]] = []

    masks_np = masks.detach().to("cpu").numpy()
    for mask_index, raw_mask in enumerate(masks_np):
        mask = (raw_mask > 0.5).astype(np.uint8) * 255
        if mask.shape[:2] != (height, width):
            mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < min_area or area > max_area:
                continue

            polygon = contour_to_polygon(contour, epsilon_ratio)
            if len(polygon) < 3:
                continue

            x, y, w, h = cv2.boundingRect(contour)
            candidates.append(
                {
                    "id": 0,
                    "mask_index": int(mask_index),
                    "area_px": round(area, 2),
                    "bbox": [int(x), int(y), int(w), int(h)],
                    "polygon": polygon,
                }
            )

    candidates.sort(key=lambda z: z["area_px"], reverse=True)
    zones: list[dict[str, Any]] = []
    accepted_masks: list[tuple[np.ndarray, float]] = []

    for candidate in candidates:
        candidate_mask = np.zeros((height, width), dtype=np.uint8)
        candidate_contour = np.array(candidate["polygon"], dtype=np.int32).reshape(-1, 1, 2)
        cv2.fillPoly(candidate_mask, [candidate_contour], 255)
        candidate_pixels = float(cv2.countNonZero(candidate_mask))
        if candidate_pixels <= 0:
            continue

        duplicate = False
        for accepted_mask, accepted_pixels in accepted_masks:
            area_ratio = min(candidate_pixels, accepted_pixels) / max(candidate_pixels, accepted_pixels)
            if area_ratio < duplicate_area_ratio:
                continue
            intersection = cv2.countNonZero(cv2.bitwise_and(candidate_mask, accepted_mask))
            overlap_ratio = float(intersection) / min(candidate_pixels, accepted_pixels)
            if overlap_ratio >= duplicate_overlap_ratio:
                duplicate = True
                break

        if duplicate:
            continue
        zones.append(candidate)
        accepted_masks.append((candidate_mask, candidate_pixels))

    return renumber_zones(zones)


def draw_line_preview(
    width: int,
    height: int,
    zones: list[dict[str, Any]],
    output_path: Path,
    *,
    thickness: int,
    label_zones: bool,
) -> None:
    preview = np.full((height, width, 3), 255, dtype=np.uint8)
    font_scale = max(0.45, min(width, height) / 950.0)

    for zone in zones:
        contour = np.array(zone["polygon"], dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(preview, [contour], True, (0, 0, 0), thickness, cv2.LINE_AA)

        if not label_zones:
            continue
        moments = cv2.moments(contour)
        if moments["m00"]:
            cx = int(moments["m10"] / moments["m00"])
            cy = int(moments["m01"] / moments["m00"])
            label = str(zone["id"])
            cv2.putText(
                preview,
                label,
                (cx - 8, cy + 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                (0, 0, 0),
                max(1, thickness),
                cv2.LINE_AA,
            )

    cv2.imwrite(str(output_path), preview)


def segment_path(model: FastSAM, image_path: Path, params: SegmentParams) -> dict[str, Any]:
    if not image_path.exists():
        raise FileNotFoundError(image_path)

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {image_path}")
    height, width = image.shape[:2]

    started = time.perf_counter()
    results = model.predict(
        source=str(image_path),
        device=params.device,
        imgsz=params.imgsz,
        conf=params.conf,
        iou=params.iou,
        max_det=params.max_det,
        retina_masks=True,
        verbose=False,
    )
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    inference_seconds = time.perf_counter() - started

    result = results[0]
    if result.masks is None or result.masks.data is None:
        zones: list[dict[str, Any]] = []
        raw_masks = 0
    else:
        raw_masks = int(len(result.masks.data))
        zones = masks_to_zones(
            result.masks.data,
            width,
            height,
            min_area_px=params.min_area_px,
            min_area_ratio=params.min_area_ratio,
            max_area_ratio=params.max_area_ratio,
            epsilon_ratio=params.epsilon_ratio,
            duplicate_overlap_ratio=params.duplicate_overlap_ratio,
            duplicate_area_ratio=params.duplicate_area_ratio,
        )

    if params.max_zones is not None:
        zones = renumber_zones(zones[: params.max_zones])

    return {
        "source_image": str(image_path),
        "device": params.device,
        "gpu": torch.cuda.get_device_name(torch.device(params.device).index or 0),
        "image": {"width": width, "height": height},
        "parameters": asdict(params),
        "metrics": {
            "raw_masks": raw_masks,
            "zones": len(zones),
            "inference_seconds": round(inference_seconds, 4),
        },
        "zones": zones,
    }


def segment_one(
    model: FastSAM,
    image_path: Path,
    output_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    params = SegmentParams(
        device=args.device,
        imgsz=args.imgsz,
        conf=args.conf,
        iou=args.iou,
        max_det=args.max_det,
        min_area_px=args.min_area_px,
        min_area_ratio=args.min_area_ratio,
        max_area_ratio=args.max_area_ratio,
        epsilon_ratio=args.epsilon_ratio,
        duplicate_overlap_ratio=args.duplicate_overlap_ratio,
        duplicate_area_ratio=args.duplicate_area_ratio,
    )
    payload = segment_path(model, image_path, params)
    width = payload["image"]["width"]
    height = payload["image"]["height"]
    zones = payload["zones"]

    stem = image_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = output_dir / f"{stem}.zones.png"
    json_path = output_dir / f"{stem}.polygons.json"
    draw_line_preview(
        width,
        height,
        zones,
        preview_path,
        thickness=args.line_thickness,
        label_zones=args.label_zones,
    )

    payload["model"] = str(args.model)
    payload["parameters"]["label_zones"] = args.label_zones
    payload["outputs"] = {
        "preview_png": str(preview_path),
        "polygons_json": str(json_path),
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    LOGGER.info(
        "%s raw_masks=%s zones=%s inference=%.3fs preview=%s json=%s",
        image_path.name,
        payload["metrics"]["raw_masks"],
        len(zones),
        payload["metrics"]["inference_seconds"],
        preview_path,
        json_path,
    )
    return payload


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    require_cuda(args.device)

    load_started = time.perf_counter()
    model = load_model(args.model)
    LOGGER.info("model_load_seconds=%.3f", time.perf_counter() - load_started)

    summaries = [segment_one(model, image, args.output_dir, args) for image in args.images]
    slow = [item for item in summaries if item["metrics"]["inference_seconds"] >= 10.0]
    if slow:
        names = ", ".join(Path(item["source_image"]).name for item in slow)
        raise RuntimeError(f"Images exceeded 10s GPU processing target: {names}")


if __name__ == "__main__":
    main()
