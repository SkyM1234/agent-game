"""Regenerate the Sakura Court background and legacy robot assets."""

from pathlib import Path

from PIL import Image, ImageDraw

OUTPUT = Path(__file__).resolve().parents[1] / "frontend" / "public" / "assets"


def robot(filename, accent, light):
    image = Image.new("RGBA", (176, 64))
    draw = ImageDraw.Draw(image)
    edge, metal, bright = "#141c21", "#8a969c", "#dce8e8"

    def box(bounds, fill, outline=edge, width=2):
        draw.rectangle(bounds, fill=fill, outline=outline, width=width)

    # Torso, head, upper arm, fist, thigh and boot have stable atlas rectangles.
    box((2, 2, 45, 53), metal)
    box((6, 5, 41, 31), accent)
    box((9, 8, 38, 12), light, light)
    box((13, 18, 34, 28), edge)
    box((17, 21, 30, 24), light, light)
    box((10, 34, 37, 46), "#3f4c52")
    for y in (36, 40, 44):
        draw.line((14, y, 33, y), fill=metal, width=1)
    box((7, 51, 40, 59), edge)
    box((50, 3, 77, 29), accent)
    box((53, 1, 74, 5), bright)
    box((51, 11, 78, 21), edge)
    draw.rectangle((54, 13, 75, 16), fill=light)
    draw.rectangle((54, 17, 59, 18), fill="#ffffff")
    box((56, 25, 71, 30), metal)
    box((82, 2, 93, 28), metal)
    box((82, 4, 93, 13), accent)
    box((84, 17, 91, 27), bright)
    box((97, 1, 118, 23), accent)
    box((100, 4, 115, 10), light, light)
    for x in (102, 107, 112):
        draw.line((x, 14, x, 21), fill=edge)
    box((122, 1, 135, 30), metal)
    box((123, 2, 134, 13), accent)
    box((124, 16, 133, 27), "#4c5a60")
    box((140, 1, 158, 13), accent)
    box((140, 10, 165, 18), metal)
    draw.rectangle((142, 16, 163, 18), fill=edge)
    image.save(OUTPUT / filename)


def stage():
    if __package__:
        from .draw_arena import stage as draw_sakura_court
    else:
        from draw_arena import stage as draw_sakura_court

    draw_sakura_court()


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    robot("robot-mint.png", "#43bda9", "#9bffe6")
    robot("robot-coral.png", "#de7565", "#ffb49b")
    stage()
    print(f"Generated 3 original bitmap assets in {OUTPUT}")
