"""Draw the six consumable item icons as authored 32px pixel art."""

import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "frontend/public/assets/items"
SCALE = 4
INK = "#33283f"
LIGHT = "#fff2dc"


def icon(draw_item):
    image = Image.new("RGBA", (32, 32))
    draw_item(ImageDraw.Draw(image))
    return image.resize((128, 128), Image.Resampling.NEAREST)


def bottle(d, liquid, shine):
    d.rectangle((12, 3, 19, 6), fill=INK)
    d.rectangle((13, 3, 18, 5), fill="#d9c6df")
    d.rectangle((10, 6, 21, 9), fill=INK)
    d.rectangle((12, 8, 19, 11), fill=LIGHT)
    d.polygon([(10, 10), (21, 10), (24, 15), (23, 27), (20, 30), (11, 30), (8, 27), (7, 15)], fill=INK)
    d.polygon([(11, 11), (20, 11), (22, 16), (21, 27), (19, 28), (12, 28), (10, 26), (9, 16)], fill=liquid)
    d.rectangle((10, 14, 21, 16), fill=shine)
    d.rectangle((11, 12, 13, 14), fill=LIGHT)
    d.rectangle((12, 17, 13, 22), fill="#ffffff")


def healing(d):
    bottle(d, "#df6688", "#ff9eb4")
    d.rectangle((14, 18, 17, 26), fill=LIGHT)
    d.rectangle((11, 21, 20, 23), fill=LIGHT)


def stamina(d):
    bottle(d, "#dcae45", "#ffe07b")
    d.polygon([(17, 15), (12, 22), (16, 22), (14, 28), (21, 19), (17, 19)], fill=LIGHT)


def mana(d):
    d.polygon([(16, 2), (25, 13), (22, 25), (16, 31), (9, 25), (6, 13)], fill=INK)
    d.polygon([(16, 4), (23, 14), (20, 24), (16, 28), (11, 24), (8, 14)], fill="#6f89e8")
    d.polygon([(16, 5), (19, 15), (16, 25), (11, 22), (9, 14)], fill="#9de8ff")
    d.polygon([(16, 7), (16, 17), (12, 19), (10, 14)], fill=LIGHT)


def charm(d):
    d.rectangle((8, 3, 23, 29), fill=INK)
    d.rectangle((10, 4, 21, 27), fill="#f4d59a")
    d.rectangle((9, 4, 22, 8), fill="#d15f75")
    d.rectangle((12, 11, 19, 13), fill="#a74c67")
    d.rectangle((14, 9, 17, 23), fill="#a74c67")
    d.rectangle((11, 18, 20, 20), fill="#a74c67")
    d.polygon([(10, 27), (13, 24), (16, 28), (19, 24), (21, 27)], fill="#fff0c4")


def hourglass(d):
    d.rectangle((7, 3, 24, 6), fill=INK)
    d.rectangle((9, 6, 22, 9), fill="#e8d7ed")
    d.polygon([(10, 8), (21, 8), (19, 14), (17, 16), (20, 19), (22, 25), (9, 25), (11, 19), (14, 16), (12, 14)], fill=INK)
    d.polygon([(12, 9), (19, 9), (17, 14), (15, 16), (13, 14)], fill="#72c7d4")
    d.polygon([(15, 17), (18, 20), (19, 23), (12, 23), (13, 20)], fill="#e4b95f")
    d.rectangle((7, 25, 24, 28), fill=INK)
    d.rectangle((9, 28, 22, 30), fill="#b78762")


def smoke(d):
    d.ellipse((6, 9, 25, 29), fill=INK)
    d.ellipse((8, 11, 23, 27), fill="#555167")
    d.rectangle((13, 6, 19, 11), fill=INK)
    d.rectangle((15, 3, 17, 7), fill="#d5b45d")
    d.line([(17, 3), (20, 1), (22, 3)], fill="#e56f67", width=2)
    d.rectangle((10, 16, 13, 19), fill="#908ba0")
    d.rectangle((18, 20, 21, 23), fill="#777287")
    d.rectangle((13, 24, 16, 26), fill="#aaa5b4")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    drawings = {
        "healing_potion": healing,
        "stamina_drink": stamina,
        "mana_crystal": mana,
        "guardian_charm": charm,
        "time_sand": hourglass,
        "smoke_bomb": smoke,
    }
    for item_id, draw_item in drawings.items():
        icon(draw_item).save(OUTPUT / f"{item_id}.png")
    (OUTPUT / "manifest.json").write_text(json.dumps({
        "version": 1,
        "items": [{"id": item_id, "asset": f"{item_id}.png"} for item_id in drawings],
    }, indent=2), encoding="utf-8")
    print("Created six consumable item icons.")


if __name__ == "__main__":
    main()
