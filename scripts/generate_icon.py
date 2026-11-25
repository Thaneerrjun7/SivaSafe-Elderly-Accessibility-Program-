"""
Draw the SivaSafe app icon.

Windows shows the sending application's icon on every toast. Without one the
reminder arrives against a blank grey square, so the icon is generated here
once and written into assets/ alongside the chime.

Run from the project root:  python scripts/generate_icon.py
"""

import os
from PIL import Image, ImageDraw, ImageFont

# SivaSafe design tokens (ui.py, class C)
GREEN = (55, 103, 87, 255)      # C.PRIMARY
GOLD = (212, 175, 55, 255)      # C.TERTIARY
CREAM = (242, 240, 235, 255)    # C.SURFACE

SIZE = 256
SUPERSAMPLE = 4                 # draw big, shrink down — cheap antialiasing


def _font(px: int):
    """Segoe UI Bold if Windows has it, otherwise whatever PIL can find."""
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except Exception:
            continue
    return ImageFont.load_default()


def generate_icon(png_path: str, ico_path: str):
    s = SIZE * SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded square body
    radius = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=GREEN)

    # Gold inset ring — the same accent the app uses for highlights
    inset = int(s * 0.075)
    d.rounded_rectangle(
        [inset, inset, s - 1 - inset, s - 1 - inset],
        radius=radius - int(inset * 0.6),
        outline=GOLD,
        width=max(1, int(s * 0.022)),
    )

    # Centred "S"
    font = _font(int(s * 0.52))
    box = d.textbbox((0, 0), "S", font=font)
    d.text(
        ((s - (box[2] - box[0])) / 2 - box[0],
         (s - (box[3] - box[1])) / 2 - box[1]),
        "S", font=font, fill=CREAM,
    )

    img = img.resize((SIZE, SIZE), Image.LANCZOS)

    os.makedirs(os.path.dirname(png_path), exist_ok=True)
    img.save(png_path, "PNG")
    img.save(ico_path, "ICO",
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    generate_icon("assets/sivasafe.png", "assets/sivasafe.ico")
    print("Generated assets/sivasafe.png and assets/sivasafe.ico")
