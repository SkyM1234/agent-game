"""Chunky Q-style fighters on an 80 x 64 grid, exported as solid 4 x 4 pixels."""

from PIL import Image, ImageDraw

WIDTH, HEIGHT = 320, 256
GRID, ZOOM = (80, 64), 4
FOOT_Y = 58 * ZOOM
PORTRAIT_BOX = (24 * ZOOM, 3 * ZOOM, 55 * ZOOM, 38 * ZOOM)
INK = '#30263e'
SKIN, CHEEK, CREAM, GOLD = '#ffe2bd', '#ed9ba6', '#fff3da', '#edc36e'
PALETTES = {
    'crimson_blade': dict(hair='#c96e9c', shade='#814b79', light='#f8b1c4',
                          dress='#b84e79', dark='#563552', bright='#f28dab', eye='#934367'),
    'frost_bell': dict(hair='#9bd8ee', shade='#648fbc', light='#e3faff',
                      dress='#5d83bd', dark='#354769', bright='#9ae5f5', eye='#577fb5'),
}


def draw_pose(character, mode, frame):
    p = PALETTES[character]
    mage = character == 'frost_bell'
    image = Image.new('RGBA', GRID)
    d = ImageDraw.Draw(image)

    def poly(points, color, edge=INK):
        points = [(round(x), round(y)) for x, y in points]
        d.polygon(points, fill=color)
        if edge:
            d.line([*points, points[0]], fill=edge, width=1)

    def line(points, color, width=1):
        d.line([(round(x), round(y)) for x, y in points], fill=color, width=width)

    def box(x, y, w, h, color):
        d.rectangle((round(x), round(y), round(x+w-1), round(y+h-1)), fill=color)

    def arm(start, elbow, end, color):
        line([start, elbow, end], INK, 5)
        line([start, elbow, end], color, 3)
        box(end[0]-1, end[1]-1, 3, 3, SKIN)

    attack = mode in ('jab', 'heavy_punch', 'kick')
    bob = [0, 0, -1, 0][frame] if mode in ('idle', 'rest', 'move') else 0
    lean = 3 if mode == 'dash' else [0, 2, 3, 1][frame] if attack else -2 if mode == 'hurt' else 0
    cx, head = 39+lean, 18+bob
    sway = [0, 1, 0, -1][frame] * (2 if mode in ('move', 'dash') else 1)

    # Distinct back silhouettes: two rose tails versus one wavy blue fall.
    if mage:
        poly([(cx-9, head), (cx+9, head), (cx+11, 31), (cx+9, 36),
              (cx+13+sway, 41), (cx+10+sway, 46), (cx+5, 45), (cx+3, 42),
              (cx-5, 48), (cx-12-sway, 44), (cx-10, 38), (cx-12, 31)], p['shade'])
        poly([(cx-8, 27+bob), (cx-5, 31), (cx-7, 39), (cx-10-sway, 42),
              (cx-6, 44), (cx-10, 44), (cx-11, 41), (cx-8, 36)], p['hair'], None)
        poly([(cx+6, 28+bob), (cx+9, 29), (cx+8, 36), (cx+12+sway, 41),
              (cx+10+sway, 44), (cx+7, 42), (cx+9, 41), (cx+6, 36)], p['hair'], None)
    else:
        for side in (-1, 1):
            x = cx+side*9
            poly([(x-side*2, head-4), (x+side*4, head), (x+side*5, 32),
                  (x+side*(8+sway), 42), (x+side*(11+sway), 41), (x+side*(9+sway), 46),
                  (x+side*(4+sway), 45), (x+side*1, 38), (x-side*2, 29)], p['shade'])
            poly([(x, head+2), (x+side*2, head+4), (x+side*3, 33),
                  (x+side*(6+sway), 41), (x+side*(8+sway), 43), (x+side*(5+sway), 42),
                  (x+side*1, 34)], p['hair'], None)
            line([(x+side, 26), (x+side*2, 33), (x+side*(5+sway), 40)], p['light'])
            box(x-1, head+7, 3, 2, GOLD)

    stride = [0, 3, 0, -3][frame] if mode == 'move' else 0
    legs = [((cx-3, 43), (cx-4-stride, 55)), ((cx+4, 43), (cx+5+stride, 55))]
    if mode == 'dash':
        legs = [((cx-3, 43), (cx-13, 53)), ((cx+4, 43), (cx+10, 55))]
    if mode == 'kick' and not mage:
        legs[1] = ((cx+4, 43), (cx+16, 41+frame))
    for hip, foot in legs:
        line([hip, foot], INK, 5)
        line([hip, foot], p['dark'], 3)
        line([(hip[0]-1, hip[1]+4), (foot[0]-1, foot[1]-2)], p['shade'])
        fx, fy = foot
        poly([(fx-2, fy-1), (fx+2, fy-1), (fx+2, fy), (fx+4, fy),
              (fx+4, fy+2), (fx-2, fy+2)], p['dark'])
        box(fx-1, fy, 3, 1, GOLD)

    if mage:
        # White split hem and one bell carry the costume's identity.
        poly([(cx-6, 31+bob), (cx+6, 31+bob), (cx+7, 40+bob),
              (cx+11, 49), (cx+5, 47), (cx, 43), (cx-7, 49), (cx-10, 47), (cx-6, 39)], p['dress'])
        poly([(cx-4, 31+bob), (cx+4, 31+bob), (cx+5, 39+bob),
              (cx+3, 44), (cx-4, 44), (cx-5, 38)], p['dark'])
        poly([(cx-6, 39+bob), (cx+6, 41+bob), (cx+8, 46), (cx+4, 45),
              (cx, 42), (cx-6, 47), (cx-6, 43)], CREAM)
        box(cx-5, 39+bob, 10, 2, '#c06b87')
        box(cx+5, 41+bob, 2, 5, '#c06b87')
        box(cx-2, 32+bob, 4, 3, GOLD)
        box(cx-1, 35+bob, 2, 1, INK)
        box(cx+4, 40+bob, 2, 2, p['bright'])
    else:
        # Broad pink skirt, ivory collar and one bow; no tiny embroidery.
        poly([(cx-5, 30+bob), (cx+5, 30+bob), (cx+6, 38+bob),
              (cx+10, 45), (cx+5, 47), (cx-6, 47), (cx-10, 44), (cx-5, 37)], p['dress'])
        poly([(cx-4, 31+bob), (cx, 33+bob), (cx+4, 31+bob),
              (cx+4, 35+bob), (cx-3, 35+bob)], CREAM, None)
        poly([(cx+2, 36+bob), (cx+5, 35+bob), (cx+8, 44),
              (cx+4, 45), (cx, 39)], p['shade'], None)
        line([(cx-6, 44), (cx-2, 45), (cx+3, 45)], p['light'], 2)
        line([(cx+4, 39), (cx+6, 44)], GOLD)
        poly([(cx-4, 37+bob), (cx-9, 36+bob), (cx-8, 40+bob),
              (cx-4, 39+bob), (cx-2, 42+bob), (cx, 39+bob)], p['light'])
        box(cx-5, 38+bob, 2, 2, GOLD)

    rear_elbow, rear_hand = (cx-8, 37+bob), (cx-9, 41+bob)
    elbow, hand = (cx+9, 37+bob), (cx+12, 41+bob)
    if mode == 'move':
        hand, rear_hand = (cx+11+stride, 40+bob), (cx-10-stride, 40+bob)
    elif mode == 'windup':
        elbow, hand = (cx+9, 35), (cx+12, 29)
    elif attack:
        elbow = (cx+10, 35)
        hand = (cx+[13, 17, 18, 14][frame], [31, 33, 37, 39][frame])
        if mode == 'heavy_punch':
            hand = (cx+[11, 15, 18, 13][frame], [28, 31, 38, 40][frame])
    elif mode == 'guard':
        elbow, hand, rear_hand = (cx+9, 37), (cx+11, 30), (cx+4, 38)
    elif mode == 'rest':
        elbow, hand, rear_hand = (cx+8, 38+bob), (cx+3, 39+bob), (cx-2, 39+bob)
    elif mode == 'hurt':
        elbow, hand = (cx+9, 36), (cx+12, 32)
    elif mode == 'dash':
        rear_hand, elbow, hand = (cx-13, 37), (cx+7, 37), (cx+11, 39)
    arm((cx-5, 33+bob), rear_elbow, rear_hand, p['dress'])
    arm((cx+5, 33+bob), elbow, hand, p['light'] if mage else SKIN)
    box(hand[0]-2, hand[1]-3, 3, 2, p['dress'])

    # Big stepped head and simple eyes at native resolution.
    poly([(cx-8, head-7), (cx+7, head-7), (cx+10, head-3),
          (cx+10, head+6), (cx+7, head+11), (cx+3, head+13),
          (cx-3, head+12), (cx-8, head+9), (cx-10, head+3), (cx-10, head-3)], p['shade'])
    poly([(cx-7, head-1), (cx+7, head-2), (cx+8, head+7),
          (cx+5, head+11), (cx-1, head+11), (cx-6, head+8)], SKIN)
    line([(cx-6, head+6), (cx-5, head+8), (cx-2, head+10)], '#e3ae9c')
    for ex in (cx-4, cx+4):
        if mode in ('rest', 'down') or (mode == 'idle' and frame == 3):
            line([(ex, head+5), (ex+1, head+6), (ex+3, head+5)], INK)
        elif mode == 'hurt':
            line([(ex, head+3), (ex+2, head+5), (ex, head+6)], INK)
        else:
            box(ex, head+3, 4, 4, CREAM)
            box(ex+1, head+3, 2, 4, p['eye'])
            box(ex+1, head+3, 2, 2, INK)
            box(ex+1, head+3, 1, 1, CREAM)
            line([(ex-1, head+2), (ex+2, head+2 if not attack else head+3)], INK)
    box(cx-5, head+8, 2, 1, CHEEK)
    box(cx+6, head+8, 2, 1, CHEEK)
    box(cx+1, head+9, 2, 1, '#b97986')

    if mage:
        poly([(cx-10, head+4), (cx-12, head), (cx-11, head-5), (cx-7, head-8),
              (cx+2, head-9), (cx+8, head-6), (cx+11, head-1), (cx+10, head+5),
              (cx+8, head+7), (cx+8, head), (cx+5, head+3), (cx+2, head+4),
              (cx+3, head-2), (cx-1, head+3), (cx-4, head+4), (cx-4, head-1),
              (cx-7, head+3), (cx-7, head+8), (cx-10, head+6)], p['hair'])
        poly([(cx-9, head-3), (cx-6, head-6), (cx, head-7),
              (cx-3, head-4), (cx-5, head-4), (cx-7, head-1)], p['light'], None)
        poly([(cx+2, head-7), (cx+7, head-5), (cx+9, head-2), (cx+5, head-3)], p['light'], None)
        line([(cx-2, head-3), (cx-3, head+1)], p['shade'])
        for side in (-1, 1):
            x = cx+side*8
            poly([(x-side*2, head-7), (x-side, head-12), (x+side*2, head-12),
                  (x+side*4, head-6), (x+side*5, head-5), (x+side*2, head-5), (x, head-9)], '#674356')
            box(x if side < 0 else x+1, head-10, 1, 3, '#c4738a')
        line([(cx, head-9), (cx-1, head-12), (cx+1, head-14), (cx+4, head-13)], p['hair'], 2)
        box(cx-9, head+7, 2, 4, p['hair'])
        box(cx+9, head+6, 2, 4, p['hair'])
    else:
        poly([(cx-9, head+7), (cx-11, head+2), (cx-11, head-4),
              (cx-7, head-8), (cx+1, head-9), (cx+8, head-6), (cx+10, head-2),
              (cx+9, head+5), (cx+7, head+6), (cx+7, head-1), (cx+3, head+2),
              (cx, head+3), (cx+1, head-3), (cx-4, head+2), (cx-6, head+3),
              (cx-6, head), (cx-7, head+8)], p['hair'])
        poly([(cx-9, head-3), (cx-6, head-6), (cx, head-7),
              (cx-3, head-4), (cx-5, head-4), (cx-7, head-1)], p['light'], None)
        line([(cx+3, head-6), (cx+6, head-5), (cx+7, head-3)], p['light'], 2)
        poly([(cx-9, head-5), (cx-11, head-13), (cx-4, head-8)], p['shade'])
        poly([(cx+6, head-7), (cx+11, head-13), (cx+11, head-4)], p['shade'])
        line([(cx-9, head-10), (cx-7, head-8)], p['light'])
        line([(cx+9, head-9), (cx+10, head-11)], GOLD)
        box(cx+8, head-5, 3, 1, CREAM)
        box(cx+9, head-6, 1, 3, CREAM)
        box(cx+9, head-5, 1, 1, GOLD)

    if mode == 'down':
        fallen = image.crop(image.getbbox()).rotate(90, expand=True)
        image = Image.new('RGBA', GRID)
        image.alpha_composite(fallen, ((GRID[0]-fallen.width)//2, 58-fallen.height))
    return image.resize((WIDTH, HEIGHT), Image.Resampling.NEAREST)
