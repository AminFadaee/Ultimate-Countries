import pathlib
from typing import BinaryIO

from PIL import Image, ImageColor

MAP_WIDTH = 1000
MAP_COLORS = 64
PALETTE_SIZE = 256


def save_compact(source: BinaryIO | pathlib.Path, target: pathlib.Path, keep: tuple[str, ...] = ()) -> None:
    with Image.open(source) as image:
        height = round(image.height * MAP_WIDTH / image.width)
        small = image.convert("RGB").resize((MAP_WIDTH, height), Image.LANCZOS)
    adaptive = small.quantize(colors=MAP_COLORS - len(keep), method=Image.Quantize.MEDIANCUT)
    palette = adaptive.getpalette()[: 3 * (MAP_COLORS - len(keep))]
    for color in keep:
        palette += list(ImageColor.getrgb(color))
    palette += [0] * (3 * PALETTE_SIZE - len(palette))
    reference = Image.new("P", (1, 1))
    reference.putpalette(palette)
    small.quantize(palette=reference, dither=Image.Dither.NONE).save(target, optimize=True)
