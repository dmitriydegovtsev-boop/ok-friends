"""Иконка программы: оранжевый круг с двумя силуэтами. Создаёт assets/icon.png и assets/icon.ico."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets"


def draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size * 0.04
    d.ellipse([pad, pad, size - pad, size - pad], fill=(238, 130, 8, 255))
    white = (255, 255, 255, 255)
    # две фигурки: головы и плечи
    for cx, cy, r in ((size * 0.38, size * 0.40, size * 0.11), (size * 0.64, size * 0.44, size * 0.09)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=white)
    d.pieslice([size * 0.20, size * 0.52, size * 0.56, size * 0.92], 180, 360, fill=white)
    d.pieslice([size * 0.50, size * 0.56, size * 0.80, size * 0.90], 180, 360, fill=white)
    return img


def main():
    OUT.mkdir(exist_ok=True)
    base = draw(256)
    base.save(OUT / "icon.png")
    base.save(OUT / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ok:", OUT / "icon.ico")


if __name__ == "__main__":
    sys.exit(main())
