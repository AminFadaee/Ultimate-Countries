import pathlib
from typing import BinaryIO

from PIL import Image

MAP_WIDTH = 1000
MAP_COLORS = 64


def save_compact(source: BinaryIO | pathlib.Path, target: pathlib.Path) -> None:
    with Image.open(source) as image:
        height = round(image.height * MAP_WIDTH / image.width)
        small = image.convert("RGB").resize((MAP_WIDTH, height), Image.LANCZOS)
    small.quantize(colors=MAP_COLORS, method=Image.Quantize.MEDIANCUT).save(target, optimize=True)
