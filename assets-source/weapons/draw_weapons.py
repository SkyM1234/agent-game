"""Draw original 32px weapon icons from authored pixel geometry."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "frontend" / "public" / "assets" / "weapons"
SCALE = 4
INK = "#30263e"


def canvas():
    image = Image.new("RGBA", (32, 32))
    return image, ImageDraw.Draw(image)


def polygon(draw, points, fill, outline=INK):
    draw.polygon(points, fill=fill)
    if outline:
        draw.line([*points, points[0]], fill=outline, width=1)


def stone_sword():
    image, draw = canvas()
    polygon(draw, [(15, 2), (18, 5), (18, 20), (16, 23), (14, 20), (14, 5)], "#f6e7bf")
    draw.line((16, 5, 16, 20), fill="#ffffff")
    draw.line((17, 6, 17, 19), fill="#b8a987")
    polygon(draw, [(7, 21), (14, 20), (16, 22), (18, 20), (25, 21), (23, 24), (17, 23), (15, 23), (9, 24)], "#edc36e")
    draw.rectangle((14, 23, 17, 29), fill="#66517d", outline=INK)
    draw.line((15, 24, 16, 28), fill="#f1d98f")
    polygon(draw, [(14, 29), (18, 29), (17, 31), (15, 31)], "#edc36e")
    draw.rectangle((15, 21, 17, 23), fill="#9a6bc0")
    return image


def lake_sword():
    image, draw = canvas()
    polygon(draw, [(15, 2), (18, 5), (18, 19), (20, 22), (17, 24), (14, 22), (14, 5)], "#475061")
    draw.line((16, 4, 16, 20), fill="#dce38b")
    draw.line((17, 5, 17, 19), fill="#8f9852")
    polygon(draw, [(5, 21), (12, 19), (15, 21), (17, 21), (20, 19), (27, 21), (23, 24), (18, 23), (14, 23), (9, 24)], "#aab0aa")
    draw.rectangle((14, 23, 17, 29), fill="#3d3545", outline=INK)
    for y in (24, 27):
        draw.line((14, y, 17, y + 2), fill="#d2b96c")
    polygon(draw, [(13, 29), (18, 29), (19, 31), (12, 31)], "#777e78")
    return image


def dragonwrath_staff():
    image, draw = canvas()
    draw.line((16, 12, 16, 30), fill=INK, width=3)
    draw.line((16, 13, 16, 29), fill="#6b506f")
    polygon(draw, [(16, 2), (20, 6), (18, 12), (14, 12), (12, 6)], "#738fda")
    polygon(draw, [(16, 3), (18, 7), (16, 10), (14, 7)], "#bff8ff", None)
    draw.rectangle((15, 5, 16, 7), fill="#ffffff")
    polygon(draw, [(13, 8), (8, 7), (10, 11), (14, 13)], "#9d7a54")
    polygon(draw, [(19, 8), (24, 7), (22, 11), (18, 13)], "#9d7a54")
    draw.rectangle((12, 12, 20, 14), fill="#493957", outline=INK)
    for x in (11, 21):
        draw.line((x, 12, x, 17), fill="#c7a35f")
        draw.rectangle((x - 1, 16, x + 1, 18), fill="#78e7f0", outline=INK)
    polygon(draw, [(13, 29), (16, 27), (19, 29), (18, 31), (14, 31)], "#6a78bd")
    return image


def twilight_scepter():
    image, draw = canvas()
    draw.line((16, 10, 16, 29), fill=INK, width=4)
    draw.line((16, 11, 16, 28), fill="#e8c34f", width=2)
    polygon(draw, [(16, 2), (21, 5), (20, 10), (16, 13), (12, 10), (11, 5)], "#d7b444")
    polygon(draw, [(16, 4), (19, 6), (18, 9), (16, 10), (14, 9), (13, 6)], "#fff0a3", None)
    draw.rectangle((15, 5, 16, 7), fill="#ffffff")
    polygon(draw, [(16, 1), (18, 4), (16, 5), (14, 4)], "#c45686")
    draw.rectangle((12, 11, 20, 13), fill="#e8c34f", outline=INK)
    polygon(draw, [(12, 28), (16, 26), (20, 28), (18, 31), (14, 31)], "#bd4978")
    draw.line((14, 28, 18, 30), fill="#ed92ad")
    return image


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    weapons = {
        "stone_sword": stone_sword,
        "lake_sword": lake_sword,
        "dragonwrath_staff": dragonwrath_staff,
        "twilight_scepter": twilight_scepter,
    }
    preview = Image.new("RGBA", (128 * len(weapons), 128), "#f7edf4")
    for index, (name, renderer) in enumerate(weapons.items()):
        icon = renderer().resize((128, 128), Image.Resampling.NEAREST)
        icon.save(OUTPUT / f"{name}.png")
        preview.alpha_composite(icon, (index * 128, 0))
    preview.convert("RGB").save(Path(__file__).parent / "pixel-preview.png")
    print(f"Created {len(weapons)} weapon icons in {OUTPUT}")


if __name__ == "__main__":
    main()
