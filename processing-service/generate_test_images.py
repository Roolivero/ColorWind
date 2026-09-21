from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "test-images"


def write_realistic_placeholder(path: Path) -> None:
    width, height = 640, 480
    yy, xx = np.mgrid[0:height, 0:width]
    sky = np.zeros((height, width, 3), dtype=np.uint8)
    sky[..., 0] = np.clip(210 - yy * 0.12, 80, 255)
    sky[..., 1] = np.clip(170 + yy * 0.06, 120, 230)
    sky[..., 2] = np.clip(110 + xx * 0.06, 90, 220)

    image = sky.copy()
    cv2.ellipse(image, (160, 310), (120, 85), -10, 0, 360, (80, 145, 75), -1)
    cv2.ellipse(image, (330, 330), (170, 95), 5, 0, 360, (55, 110, 65), -1)
    cv2.ellipse(image, (520, 300), (105, 75), 15, 0, 360, (70, 130, 80), -1)
    cv2.circle(image, (500, 105), 46, (60, 205, 245), -1)
    cv2.rectangle(image, (230, 250), (420, 405), (110, 120, 140), -1)
    cv2.fillPoly(image, [np.array([[210, 250], [325, 155], [440, 250]], dtype=np.int32)], (60, 70, 95))
    cv2.rectangle(image, (300, 320), (350, 405), (75, 60, 55), -1)
    cv2.rectangle(image, (252, 285), (292, 325), (190, 220, 235), -1)
    cv2.rectangle(image, (364, 285), (402, 325), (190, 220, 235), -1)

    noise = np.random.default_rng(3).normal(0, 5, image.shape).astype(np.int16)
    image = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    cv2.imwrite(str(path), image)


def write_flat_illustration(path: Path) -> None:
    image = np.full((512, 512, 3), (245, 238, 222), dtype=np.uint8)
    cv2.circle(image, (170, 175), 88, (92, 169, 214), -1)
    cv2.circle(image, (340, 170), 76, (233, 164, 72), -1)
    cv2.rectangle(image, (95, 305), (260, 430), (122, 186, 105), -1)
    cv2.rectangle(image, (282, 285), (430, 428), (205, 95, 100), -1)
    cv2.line(image, (60, 272), (460, 272), (52, 52, 58), 9)
    cv2.fillPoly(
        image,
        [np.array([[260, 345], [335, 250], [415, 345]], dtype=np.int32)],
        (145, 105, 190),
    )
    cv2.imwrite(str(path), image)


def write_line_art(path: Path) -> None:
    image = np.full((512, 512, 3), 255, dtype=np.uint8)
    black = (20, 20, 20)
    cv2.rectangle(image, (72, 82), (438, 430), black, 4)
    cv2.circle(image, (176, 185), 76, black, 4)
    cv2.circle(image, (344, 185), 76, black, 4)
    cv2.line(image, (252, 86), (252, 430), black, 4)
    cv2.line(image, (76, 300), (438, 300), black, 4)
    cv2.ellipse(image, (258, 355), (86, 42), 0, 0, 360, black, 4)
    cv2.imwrite(str(path), image)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_realistic_placeholder(OUT_DIR / "realistic-placeholder.png")
    write_flat_illustration(OUT_DIR / "flat-illustration.png")
    write_line_art(OUT_DIR / "line-art-bw.png")
    print(f"Wrote test images to {OUT_DIR}")


if __name__ == "__main__":
    main()
