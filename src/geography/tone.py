import io

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)
NARROW_RANGE = 0.8
STRETCH_TO = (0.06, 0.94)
TARGET_MEAN = 0.45
MEAN_RANGE = (0.32, 0.66)
GAMMA_RANGE = (0.75, 1.3)
DULL_SATURATION = 0.18
JPEG_QUALITY = 92


def auto_tone(data: bytes) -> bytes:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    pixels = np.asarray(image, dtype=np.float32) / 255
    pixels = stretched(pixels)
    pixels = brightened(pixels)
    toned = Image.fromarray((pixels * 255).round().astype(np.uint8))
    if mean_saturation(toned) < DULL_SATURATION:
        toned = ImageEnhance.Color(toned).enhance(1.2)
    toned = toned.filter(ImageFilter.UnsharpMask(radius=1.5, percent=40, threshold=3))
    output = io.BytesIO()
    toned.save(output, "JPEG", quality=JPEG_QUALITY)
    return output.getvalue()


def stretched(pixels: np.ndarray) -> np.ndarray:
    low, high = np.percentile(pixels @ LUMA, [1, 99])
    if not low < high or high - low >= NARROW_RANGE:
        return pixels
    low, high = min(low, STRETCH_TO[0]), max(high, STRETCH_TO[1])
    return np.clip((pixels - low) / (high - low), 0, 1)


def brightened(pixels: np.ndarray) -> np.ndarray:
    mean = (pixels @ LUMA).mean()
    if MEAN_RANGE[0] <= mean <= MEAN_RANGE[1]:
        return pixels
    gamma = np.clip(np.log(TARGET_MEAN) / np.log(max(mean, 0.02)), *GAMMA_RANGE)
    return pixels ** gamma


def mean_saturation(image: Image.Image) -> float:
    return float(np.asarray(image.convert("HSV"), dtype=np.float32)[..., 1].mean() / 255)
