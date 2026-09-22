"""Draw deterministic, transparent pixel animation atlases from authored poses.

Visual references and their credits are in references/sources.json. This renderer
draws each pose from pixel geometry; it does not filter or trace the source images.
"""

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

from pixel_renderer import FOOT_Y, HEIGHT, PALETTES, PORTRAIT_BOX, WIDTH, draw_pose

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "frontend/public/assets/characters"
SIZE = HEIGHT
WHITE = "#fff4e5"
MODES = ["idle", "move", "windup", "jab", "heavy_punch", "kick", "guard", "dash", "rest", "hurt", "down"]


def make_effects(character):
    """Eight crisp impact poses, authored at 32px and shown at 4x in the arena."""
    sheet = Image.new("RGBA", (64*8, 64))
    mage = character == "frost_bell"
    palette = PALETTES[character]
    for frame in range(8):
        image = Image.new("RGBA", (32, 32))
        d = ImageDraw.Draw(image)
        if frame < 4:
            radius = [4, 8, 10, 7][frame]
            if mage:
                for ray in range(6):
                    a = ray * math.tau / 6
                    x, y = round(16+math.cos(a)*radius), round(16+math.sin(a)*radius)
                    d.line((16, 16, x, y), fill=palette['bright'], width=3)
                    d.line((16, 16, x, y), fill=WHITE, width=1)
                d.rectangle((14, 14, 18, 18), fill=WHITE)
            else:
                points = []
                for ray in range(16):
                    a = ray * math.tau / 16
                    r = radius if ray % 2 == 0 else radius * .3
                    points.append((round(16+math.cos(a)*r), round(16+math.sin(a)*r)))
                d.polygon(points, fill=palette['bright'], outline=palette['shade'])
                d.line((16-radius+2, 16, 16+radius-2, 16), fill=WHITE, width=2)
                d.line((16, 16-radius+2, 16, 16+radius-2), fill=WHITE, width=2)
        for ray in range(6 if mage else 8):
            a = ray / (6 if mage else 8) * math.tau
            radius = min(13, 5+frame*1.4)
            x, y = round(16+math.cos(a)*radius), round(16+math.sin(a)*radius)
            if frame >= 6 and ray % 2 != frame % 2:
                continue
            color = WHITE if ray % 3 == 0 else palette['bright']
            if mage and frame < 6:
                d.polygon([(x, y-2), (x+1, y), (x, y+2), (x-1, y)], fill=color)
            else:
                size = 2 if frame < 5 else 1
                d.rectangle((x, y, x+size-1, y+size-1), fill=color)
        sheet.alpha_composite(image.resize((64, 64), Image.Resampling.NEAREST), (frame*64, 0))
    return sheet


def main():
    preview = Image.new("RGB", (WIDTH*8, SIZE*2), "#34333c")
    for row, character in enumerate(PALETTES):
        directory = OUTPUT / character
        (directory / "sprites").mkdir(parents=True, exist_ok=True)
        (directory / "effects").mkdir(exist_ok=True)
        sheet = Image.new("RGBA", (WIDTH*4, SIZE*len(MODES)))
        animations = {}
        for mode_index, mode in enumerate(MODES):
            animations[mode] = dict(frames=list(range(mode_index*4, mode_index*4+4)),
                                    fps=10 if mode in ("move", "dash") else 3 if mode == "idle" else 8,
                                    loop=mode in ("idle", "move", "rest", "guard", "dash"))
            for frame in range(4):
                pose = draw_pose(character, mode, frame)
                sheet.alpha_composite(pose, (frame*WIDTH, mode_index*SIZE))
            if mode_index < 8:
                preview.paste(draw_pose(character, mode, 1), (mode_index*WIDTH, row*SIZE), draw_pose(character, mode, 1))
        sheet.save(directory / "sprites/atlas.png")
        portrait = draw_pose(character, "idle", 0).crop(PORTRAIT_BOX)
        portrait.save(directory / "portrait.png")
        make_effects(character).save(directory / "effects/impact.png")
        (directory / "animations.json").write_text(json.dumps(dict(
            version=2, width=WIDTH, height=SIZE, columns=4, scale=1, anchor=[.5, FOOT_Y/SIZE],
            animations=animations), indent=2), encoding="utf-8")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "manifest.json").write_text(json.dumps(dict(version=2,
        characters=[dict(id=name, atlas=f"{name}/sprites/atlas.png",
                         animations=f"{name}/animations.json", portrait=f"{name}/portrait.png",
                         impact=f"{name}/effects/impact.png") for name in PALETTES]), indent=2), encoding="utf-8")
    preview.save(Path(__file__).parent / "pixel-preview.png")
    print("Created two character atlases (44 poses each), portraits, impact atlases and manifests.")


if __name__ == "__main__":
    main()
