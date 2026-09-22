"""Author the Sakura Court background in a reproducible, layered pixel palette."""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

OUTPUT = Path(__file__).resolve().parents[1] / "frontend/public/assets/arena.png"


def stage():
    rng = random.Random(914)
    image = Image.new("RGB", (600, 240))
    d = ImageDraw.Draw(image)

    def gradient(top, bottom, start, end):
        for y in range(start, end):
            t = (y - start) / max(1, end - start - 1)
            color = tuple(round(a + (b-a)*t) for a, b in zip(top, bottom))
            d.line((0, y, 600, y), fill=color)

    gradient((88, 101, 157), (241, 191, 191), 0, 150)
    # A pale moon, wind-stretched clouds and atmospheric mountain silhouettes.
    d.ellipse((387, 18, 420, 51), fill="#fff3df")
    d.ellipse((391, 21, 418, 48), fill="#fff8e8")
    for x, y, w in [(80, 32, 90), (245, 22, 65), (325, 64, 104), (158, 70, 80)]:
        for j, color in enumerate(("#b6accb", "#d3bad1", "#e3c4d6")):
            yy = y + j*3
            d.polygon([(x-10, yy+3), (x+10, yy), (x+w//3, yy),
                       (x+w//2, yy-3), (x+w-18, yy+1), (x+w, yy+4)], fill=color)
    d.polygon([(0, 127), (36, 103), (64, 111), (121, 70), (163, 99),
               (199, 88), (240, 124), (296, 104), (344, 71), (390, 113),
               (434, 99), (472, 119), (530, 82), (600, 119), (600, 170), (0, 170)], fill="#9799ba")
    d.polygon([(88, 95), (121, 70), (153, 93), (129, 84), (119, 88), (113, 84)], fill="#e7d9e1")
    d.polygon([(316, 91), (344, 71), (371, 96), (346, 84), (337, 88)], fill="#e7d9e1")
    d.polygon([(0, 136), (56, 117), (109, 141), (178, 110), (237, 140),
               (300, 128), (369, 145), (438, 113), (492, 132), (560, 115),
               (600, 132), (600, 172), (0, 172)], fill="#7c87a3")
    gradient((133, 156, 180), (172, 178, 191), 144, 174)
    for _ in range(100):
        x, y = rng.randrange(600), rng.randrange(148, 174)
        d.line((x, y, x+rng.randrange(3, 22), y), fill=rng.choice(["#c5bacb", "#aeb6ca", "#929fb8"]))
    # Distant garden: evergreen silhouettes keep the fighting plane quiet.
    for x in list(range(0, 155, 9)) + list(range(440, 601, 9)):
        y = rng.randrange(125, 151)
        d.line((x, y, x, 174), fill="#586d83", width=2)
        for j in range(4):
            yy = y + j*7
            d.polygon([(x, yy-7), (x-7-j*2, yy+8), (x+7+j*2, yy+8)], fill="#647c90")

    # Small shrine across the water, with tiled eaves and warm paper windows.
    d.rectangle((438, 120, 486, 160), fill="#7a566a")
    d.rectangle((445, 125, 478, 157), fill="#d4a994")
    for x in (447, 465):
        d.rectangle((x, 129, x+11, 150), fill="#f6d6a7")
        d.line((x+5, 129, x+5, 150), fill="#a6757b")
        d.line((x, 139, x+11, 139), fill="#a6757b")
    d.polygon([(428, 122), (439, 113), (461, 101), (482, 113), (499, 121),
               (488, 125), (439, 125)], fill="#424760")
    d.line([(429, 121), (449, 117), (461, 108), (477, 118), (498, 121)], fill="#9c8ca0", width=2)
    for x in range(442, 486, 6):
        d.line((461+(x-461)*.4, 109, x, 121), fill="#696680")
    d.rectangle((434, 156, 491, 160), fill="#9292a3")

    # Vermilion torii, deliberately behind the fighters and off center.
    for x in (371, 413):
        d.polygon([(x, 96), (x+7, 96), (x+9, 170), (x-2, 170)], fill="#8c5168")
        d.line((x+2, 101, x+2, 163), fill="#d99493", width=2)
        d.rectangle((x-3, 164, x+9, 171), fill="#555469")
    d.polygon([(359, 91), (381, 95), (409, 95), (433, 90), (430, 96),
               (410, 100), (380, 100), (361, 97)], fill="#42455f")
    d.line([(360, 94), (382, 98), (411, 98), (431, 94)], fill="#ce8c8d", width=2)
    d.rectangle((365, 108, 429, 112), fill="#b87280")
    d.rectangle((390, 99, 400, 116), fill="#d4ac8c")
    d.rectangle((393, 102, 397, 112), fill="#6a566c")
    d.arc((376, 106, 418, 124), 0, 180, fill="#ead3b5", width=1)
    for x in (382, 393, 406):
        d.polygon([(x, 120), (x+2, 123), (x, 126), (x+3, 128)], fill="#f5e3ce")

    # Stone balustrade and a broad, uninterrupted dueling terrace at y=183.
    d.rectangle((0, 169, 600, 176), fill="#535b77")
    for x in range(8, 600, 34):
        d.rectangle((x, 151, x+5, 173), fill="#8c889b")
        d.rectangle((x, 151, x+1, 173), fill="#b6a9b3")
        d.rectangle((x-1, 149, x+6, 152), fill="#d0bec6")
    d.rectangle((0, 154, 600, 157), fill="#bbaab7")
    d.line((0, 154, 600, 154), fill="#e1cbd0")
    d.rectangle((0, 171, 600, 175), fill="#ddd0cc")
    d.rectangle((0, 176, 600, 179), fill="#6b6a85")
    gradient((158, 148, 166), (94, 91, 122), 180, 240)
    for y in (187, 201, 224):
        d.line((0, y, 600, y), fill="#706e8c")
        d.line((0, y+1, 600, y+1), fill="#aaa0b3")
    for x in range(-180, 800, 72):
        d.line((300+(x-300)*.58, 180, x, 240), fill="#77738f")
    d.line((12, 182, 588, 182), fill="#ebd6c8")
    # A subdued engraved flower crest on the stone, below the combat line.
    d.ellipse((254, 201, 346, 225), outline="#b2a2b6", width=1)
    d.ellipse((260, 203, 340, 223), outline="#b2a2b6", width=1)
    for i in range(8):
        a = i*math.tau/8
        x, y = 300+math.cos(a)*31, 213+math.sin(a)*7
        d.line((300, 213, x, y), fill="#b2a2b6")

    def lantern(x, y):
        d.rectangle((x-3, y+9, x+3, y+33), fill="#72718a")
        d.rectangle((x-9, y+30, x+9, y+34), fill="#aaa0ad")
        d.rectangle((x-7, y, x+7, y+12), fill="#69667f")
        d.rectangle((x-4, y+2, x+4, y+9), fill="#ffdbac")
        d.line((x, y+2, x, y+9), fill="#b98d84")
        d.polygon([(x-12, y), (x-7, y-4), (x, y-7), (x+7, y-4), (x+12, y)], fill="#a99aa7")
        d.line((x-11, y, x+11, y), fill="#e3c8c8")
        d.ellipse((x-2, y-11, x+2, y-7), fill="#aaa0ad")

    lantern(27, 145)
    lantern(570, 145)

    def sakura(mirror=False):
        def points(items):
            return [(600-x if mirror else x, y) for x, y in items]
        def branch(items, width, color):
            d.line(points(items), fill=color, width=width)
        branch([(9, 180), (20, 133), (17, 86), (37, 45), (72, 17)], 17, "#4c405e")
        branch([(17, 142), (26, 90), (49, 68), (106, 50), (142, 21)], 9, "#4c405e")
        branch([(25, 99), (9, 62), (0, 48)], 9, "#4c405e")
        branch([(21, 111), (25, 80), (42, 52)], 3, "#977285")
        branch([(42, 68), (80, 60), (113, 37)], 3, "#ac7a8e")
        centers = [(0, 18, 44), (44, 17, 37), (83, 15, 33), (119, 10, 30),
                   (18, 50, 27), (62, 42, 32), (100, 38, 24), (143, 24, 22)]
        if mirror:
            centers = centers[:7]
        for x, y, r in centers:
            for _ in range(38):
                a, distance = rng.random()*math.tau, rng.random()*r
                xx, yy = x+math.cos(a)*distance, y+math.sin(a)*distance*.65
                if mirror:
                    xx = 600-xx
                size = rng.randrange(3, 9)
                color = rng.choice(["#b86e9a", "#d489af", "#eaa1bd"] if yy > y+5 else ["#e9a3c1", "#f2b7cf", "#f9d1dd"])
                d.ellipse((xx-size, yy-size*.6, xx+size, yy+size*.6), fill=color)
                if rng.random() < .45:
                    d.line((xx-2, yy-2, xx+1, yy-2), fill="#ffe1e8")
        for _ in range(85):
            x, y = rng.randrange(0, 145), rng.randrange(0, 60)
            if mirror:
                x = 600-x
            d.rectangle((x, y, x+1, y+1), fill=rng.choice(["#ffe4e9", "#edabc6", "#f7cada"]))

    sakura()
    sakura(True)
    for _ in range(65):
        x, y = rng.randrange(12, 588), rng.randrange(183, 239)
        d.line((x, y, x+2, y-1), fill=rng.choice(["#e3abc4", "#f3c7d5", "#bc91b1"]))
    for x, y in [(113, 94), (190, 68), (269, 119), (341, 47), (466, 81), (528, 111), (71, 127)]:
        d.polygon([(x, y), (x+4, y-2), (x+3, y+1), (x+1, y+2)], fill="#ffe0e9")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.resize((1200, 480), Image.Resampling.NEAREST).save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    print(stage())
