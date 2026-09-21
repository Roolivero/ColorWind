from __future__ import annotations

import colorsys
from typing import Any

import cv2
import numpy as np


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def _hsv_to_hex(hue: float, saturation: float, value: float) -> str:
    red, green, blue = colorsys.hsv_to_rgb(hue % 1.0, np.clip(saturation, 0.0, 1.0), np.clip(value, 0.0, 1.0))
    return _rgb_to_hex((round(red * 255), round(green * 255), round(blue * 255)))


def _hex_to_hsv(hex_color: str) -> tuple[float, float, float]:
    rgb = tuple(int(hex_color[index : index + 2], 16) for index in (1, 3, 5))
    return colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))


def _fallback_seed_colors(image_bgr: np.ndarray, count: int) -> list[str]:
    gray_mean = float(np.mean(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY))) / 255.0
    seed_hue = (0.58 + gray_mean * 0.22) % 1.0
    return [_hsv_to_hex(seed_hue + offset, 0.55, 0.82) for offset in (0.0, 0.08, 0.17, 0.33, 0.50, 0.67)][:count]


def dominant_colors(image_bgr: np.ndarray, count: int) -> list[str]:
    target_count = max(1, min(count, 8))
    image = image_bgr
    max_side = 360
    height, width = image.shape[:2]
    scale = min(1.0, max_side / max(height, width))
    if scale < 1.0:
        image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    color_mask = (saturation > 35) & (value > 35) & (value < 245)
    pixels = image[color_mask]

    if len(pixels) < 50:
        return _fallback_seed_colors(image_bgr, target_count)

    samples = pixels.reshape(-1, 3).astype(np.float32)
    cluster_count = min(target_count, len(samples))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(samples, cluster_count, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.flatten(), minlength=cluster_count)
    ordered = centers[np.argsort(-counts)]

    colors = []
    for bgr in ordered:
        blue, green, red = [int(round(channel)) for channel in bgr]
        colors.append(_rgb_to_hex((red, green, blue)))
    return colors or _fallback_seed_colors(image_bgr, target_count)


def _palette_from_shifts(base_colors: list[str], zone_count: int, shifts: tuple[float, ...], sat_mul: float, val_mul: float) -> dict[str, str]:
    if zone_count <= 0:
        return {}

    colors: dict[str, str] = {}
    for index in range(zone_count):
        hue, saturation, value = _hex_to_hsv(base_colors[index % len(base_colors)])
        shift = shifts[index % len(shifts)]
        colors[str(index + 1)] = _hsv_to_hex(hue + shift, saturation * sat_mul, value * val_mul)
    return colors


def suggested_palettes(image_bgr: np.ndarray, zone_count: int) -> list[dict[str, Any]]:
    base = dominant_colors(image_bgr, max(zone_count, 3))
    return [
        {
            "name": "Análoga",
            "colors": _palette_from_shifts(base, zone_count, (-1 / 24, 0.0, 1 / 24), 0.9, 1.05),
        },
        {
            "name": "Complementaria",
            "colors": _palette_from_shifts(base, zone_count, (0.0, 0.5), 1.0, 1.0),
        },
        {
            "name": "Triádica",
            "colors": _palette_from_shifts(base, zone_count, (0.0, 1 / 3, 2 / 3), 0.95, 1.0),
        },
    ]
