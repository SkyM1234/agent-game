import { Graphics } from 'pixi.js';
import type { CharacterId, Skill } from '../types';

// The fighters use 4 x 4 world pixels. All effect geometry shares this grid.
const PIXEL = 4;
const snap = (n: number) => Math.round(n / PIXEL) * PIXEL;
const bound = (n: number) => Math.max(0, Math.min(1, n));
const ROSE = [0x814b79, 0xf28dab, 0xfff3da];
const ICE = [0x648fbc, 0x9ae5f5, 0xe3faff];

function block(g: Graphics, x: number, y: number, color: number, alpha = 1, size = PIXEL) {
  g.rect(snap(x), snap(y), size, size).fill({ color, alpha });
}

function pixelLine(g: Graphics, x1: number, y1: number, x2: number, y2: number,
                   color: number, alpha = 1, thickness = 1) {
  let x = Math.round(x1 / PIXEL), y = Math.round(y1 / PIXEL);
  const endX = Math.round(x2 / PIXEL), endY = Math.round(y2 / PIXEL);
  const dx = Math.abs(endX - x), dy = -Math.abs(endY - y);
  const sx = x < endX ? 1 : -1, sy = y < endY ? 1 : -1;
  let error = dx + dy;
  for (;;) {
    block(g, x * PIXEL, y * PIXEL, color, alpha, thickness * PIXEL);
    if (x === endX && y === endY) break;
    const twice = error * 2;
    if (twice >= dy) { error += dy; x += sx; }
    if (twice <= dx) { error += dx; y += sy; }
  }
}

function stamp(g: Graphics, x: number, y: number, rows: string[], palette: number[], alpha = 1, facing = 1, scale = 1) {
  const cx = (rows[0].length - 1) / 2, cy = (rows.length - 1) / 2;
  rows.forEach((row, j) => [...row].forEach((cell, i) => {
    if (cell !== '.') block(g, x + facing * (i - cx) * PIXEL * scale, y + (j - cy) * PIXEL * scale,
      palette[Number(cell)], alpha, PIXEL * scale);
  }));
}

function spark(g: Graphics, x: number, y: number, palette: number[], alpha = 1) {
  stamp(g, x, y, ['..1..', '..2..', '12221', '..2..', '..1..'], palette, alpha);
}

function ring(g: Graphics, x: number, y: number, rx: number, ry: number, color: number, alpha = 1) {
  // Rasterize a stepped ellipse; never draw a smooth vector outline.
  const cols = Math.max(2, Math.round(rx / PIXEL)), rows = Math.max(1, Math.round(ry / PIXEL));
  for (let j = -rows; j <= rows; j++) for (let i = -cols; i <= cols; i++) {
    const inside = (a: number, b: number) => a * a / (cols * cols) + b * b / (rows * rows) <= 1;
    if (inside(i, j) && (!inside(i - 1, j) || !inside(i + 1, j) || !inside(i, j - 1) || !inside(i, j + 1))) {
      block(g, x + i * PIXEL, y + j * PIXEL, color, alpha);
    }
  }
}

function shard(g: Graphics, x: number, base: number, height: number, alpha = 1) {
  const rows = Math.max(3, Math.round(height / PIXEL));
  for (let j = 0; j < rows; j++) {
    const width = Math.min(4, Math.floor(j / 3));
    for (let i = -width; i <= width; i++) {
      block(g, x + i * PIXEL, base - (rows - j) * PIXEL,
        i === -width ? ICE[2] : i <= 0 ? ICE[1] : ICE[0], alpha);
    }
  }
}

export function drawSkillEffect(g: Graphics, id: CharacterId, skill: Skill, phase: string,
  x: number, facing: number, progress: number, reach: number, time: number, targetX: number, phaseAge: number) {
  const mage = id === 'frost_bell', palette = mage ? ICE : ROSE;
  const handX = x + facing * 48, y = 276;
  const attack = ['jab', 'heavy_punch', 'kick'].includes(skill);
  const step = Math.floor(bound(progress) * 4) / 4;
  if (phase === 'windup' && attack) {
    const radius = 36 - step * 24;
    for (let i = 0; i < 4; i++) {
      const a = i * Math.PI / 2 + Math.floor(time * 12) * .2;
      block(g, handX + Math.cos(a) * radius, y + Math.sin(a) * radius,
        palette[i % 2 + 1], .85, i % 2 ? 4 : 8);
    }
    if (progress > .45) spark(g, handX, y, palette);
    return;
  }
  // Short attacks retain a fading silhouette into recovery, driven by replay time.
  const afterglow = phase === 'recovery' && attack && phaseAge < .12;
  if (phase !== 'active' && !afterglow) return;
  const alpha = afterglow ? (1 - phaseAge / .12) * .55 : 1;
  const pose = afterglow ? 1 : step;
  const range = Math.max(0, reach);
  if (skill === 'guard') {
    const sx = x + facing * 60;
    if (mage) {
      stamp(g, sx, 280, [
        '...22222...', '..2111112..', '.211111112.', '21101110112',
        '21110101112', '21111211112', '21122222112', '21111211112',
        '21110101112', '.211111112.', '..2111112..', '...21112...', '....222....',
      ], ICE, .65, 1, 2);
      if (Math.floor(time * 6) % 2 === 0) spark(g, sx - facing * 8, 260, ICE, .85);
    } else {
      // Brackets around the raised sword distinguish parrying from the ice shield.
      for (const side of [-1, 1]) {
        pixelLine(g, sx + side * 16, 250, sx + side * 24, 258, ROSE[1]);
        pixelLine(g, sx + side * 24, 258, sx + side * 24, 292, ROSE[1]);
        pixelLine(g, sx + side * 24, 292, sx + side * 16, 300, ROSE[2]);
      }
      spark(g, sx, 248, ROSE, .9);
    }
  } else if (skill === 'dash') {
    for (let i = 0; i < 4; i++) {
      const tail = x - facing * (48 + i * 20);
      pixelLine(g, tail, 286 + i % 2 * 20, tail - facing * 24, 286 + i % 2 * 20,
        palette[i % 2 + 1], .8 - i * .14, i === 0 ? 2 : 1);
    }
    stamp(g, x - facing * 44, 356, ['..00..', '.0110.', '012210', '001100'], palette, .6);
  } else if (skill === 'rest') {
    ring(g, x, 360, 32, 8, palette[1], .5);
    for (let i = 0; i < 3; i++) {
      const age = (Math.floor(time * 10) / 10 + i / 3) % 1;
      const px = x - 32 + i * 32, py = 338 - age * 52;
      if (mage) spark(g, px, py, ICE, 1 - age * .7);
      else stamp(g, px, py, ['.2.', '222', '.2.', '.1.'], ROSE, 1 - age * .7);
    }
  } else if (attack && range > 0) {
    if (!mage) {
      const extent = Math.min(range, 236), start = Math.min(28, extent * .25);
      if (skill === 'jab') {
        // A quick diagonal cut with a second short glint for the flurry.
        const endY = 256 + pose * 44;
        pixelLine(g, x + facing * start, 260, x + facing * (extent - 4), endY, ROSE[0], alpha * .6, 2);
        pixelLine(g, x + facing * start, 252, x + facing * (extent - 4), endY - 8, ROSE[1], alpha, 2);
        pixelLine(g, x + facing * (start + 12), 252, x + facing * (extent - 4), endY - 8, ROSE[2], alpha);
        if (pose >= .5) pixelLine(g, x + facing * extent * .55, endY + 12,
          x + facing * (extent - 4), endY - 8, ROSE[2], alpha * .8);
      } else if (skill === 'heavy_punch') {
        // A broad horizontal draw-slash, gold core, rose wake, pointed front.
        for (let i = 0; i < 5; i++) {
          const offset = Math.abs(i - 2);
          pixelLine(g, x + facing * (start + offset * 12), y - 8 + i * 4,
            x + facing * (extent - offset * 12 - 4), y - 8 + i * 4,
            i === 2 ? ROSE[2] : i % 2 ? ROSE[1] : ROSE[0], alpha);
        }
        spark(g, x + facing * Math.max(start, extent - 20), y, ROSE, alpha);
      } else {
        // Wide stepped crescent, with an open center so the opposing fighter reads.
        for (let j = -14; j <= 14; j++) {
          const yy = j * PIXEL;
          const edge = Math.sqrt(Math.max(0, 1 - (j / 15) ** 2));
          const px = x + facing * (start + (extent - start - 12) * edge);
          block(g, px - facing * 8, y + yy, ROSE[0], alpha * .6, 8);
          block(g, px - facing * 4, y + yy, ROSE[1], alpha, 8);
          block(g, px, y + yy, ROSE[2], alpha);
        }
      }
    } else if (skill === 'kick') {
      const count = Math.max(1, Math.min(6, Math.floor(range / 40)));
      const visible = Math.max(1, Math.ceil((pose + .3) * count));
      for (let i = 0; i < Math.min(count, visible); i++) {
        const px = x + facing * range * (i + 1) / (count + 1);
        const height = 32 + (i % 3) * 12 + pose * 24;
        shard(g, px, 360, height, alpha);
        block(g, px + facing * 12, 344 - height, ICE[1], alpha, 8);
      }
    } else if (skill === 'heavy_punch') {
      const center = x + facing * Math.min(range, Math.abs(targetX - x));
      ring(g, center, y, 20 + pose * 48, 20 + pose * 40, ICE[1], alpha * .8);
      spark(g, center, y, ICE, alpha);
      for (let i = 0; i < 6; i++) {
        const angle = i * Math.PI / 3;
        const px = center + Math.cos(angle) * (24 + pose * 48), py = y + Math.sin(angle) * (24 + pose * 40);
        stamp(g, px, py, ['..2..', '.221.', '22110', '.110.', '..0..'], ICE, alpha);
      }
    } else if (!afterglow && pose < .5) {
      spark(g, handX, y, ICE, 1 - pose);
    }
  }
}

export function drawTeleportEffect(g: Graphics, x: number, age: number, arriving: boolean) {
  const step = Math.floor(bound(age / .45) * 6) / 6;
  const alpha = 1 - step;
  ring(g, x, 358, 28 + step * 24, 8 + step * 4, ICE[2], alpha);
  ring(g, x, 284, 32 + step * 20, 60 - step * 16, ICE[1], alpha * .75);
  for (let i = 0; i < 8; i++) {
    const column = i % 4, rise = step * (arriving ? 64 : -64);
    const px = x - 40 + column * 24, py = 228 + Math.floor(i / 4) * 84 + rise;
    block(g, px, py, i % 2 ? ICE[1] : ICE[2], alpha, i % 3 === 0 ? 8 : 4);
  }
}

export function drawProjectileEffect(g: Graphics, x: number, facing: number, age: number) {
  const tick = Math.floor(age * 12);
  for (let i = 4; i > 0; i--) {
    const px = x - facing * (16 + i * 12), py = 276 + ((i + tick) % 3 - 1) * 4;
    pixelLine(g, px, py, px - facing * 8, py, i % 2 ? ICE[1] : ICE[2], .7 - i * .1);
  }
  stamp(g, x, 276, [
    '...11....', '..1221...', '.122221..', '122222210', '.011110..', '..010....',
  ], ICE, 1, facing);
}
