import { useEffect, useRef, useState } from 'react';
import { Application, Assets, Container, Graphics, Rectangle, Sprite, Text, Texture } from 'pixi.js';
import { assetPath, CHARACTER_ART, characterId } from '../characters';
import type { CharacterId, CombatEvent, Fighter, Frame, GameConfig, Item, Player, Weapon } from '../types';
import { effectiveSkills, weaponAsset } from '../weapons';
import { itemAsset, itemById } from '../items';

import { drawProjectileEffect, drawSkillEffect, drawTeleportEffect } from './pixelEffects';

const W = 1200, H = 480;
const PLAYERS: Player[] = ['p1', 'p2'];
interface Animation { frames: number[]; fps: number; loop?: boolean }
interface Atlas { width: number; height: number; columns: number; scale?: number; anchor: [number, number]; animations: Record<string, Animation> }
interface Art { frames: Texture[]; impact: Texture[]; atlas: Atlas }
interface Props { frame: Frame; nextFrame: Frame; config: GameConfig; time: number; events: CombatEvent[] }
const clamp = (n: number, min = 0, max = 1) => Math.max(min, Math.min(max, n));
const worldX = (position: number, config: GameConfig) => 44 + position / config.arena_width * 1112;

function animationFor(fighter: Fighter, hit: boolean): string {
  if (fighter.health <= 0) return 'down';
  if (hit) return 'hurt';
  const action = fighter.action;
  if (!action) return 'idle';
  if (action.skill === 'use_item') return 'rest';
  if (action.position != null) return 'guard';
  if (action.phase === 'windup') return 'windup';
  if (action.phase === 'recovery') return 'idle';
  return action.skill;
}

function weaponPose(fighter: Fighter, progress: number): { x: number; y: number; rotation: number } {
  if (fighter.health <= 0) return { x: 4, y: 58, rotation: 1.45 };
  const action = fighter.action;
  if (!action) return { x: 12, y: 41, rotation: .08 };
  if (action.skill === 'use_item') return { x: 5, y: 42, rotation: -.12 };
  if (action.position != null) return { x: 10, y: 34, rotation: -.42 };
  if (action.phase === 'windup') return { x: 10, y: 31, rotation: -.72 };
  if (action.phase === 'active' && ['jab', 'heavy_punch'].includes(action.skill)) {
    return { x: 13 + progress * 5, y: 33 + progress * 5, rotation: -.15 + progress * 1.25 };
  }
  if (action.skill === 'dash') return { x: 12, y: 40, rotation: .55 };
  if (action.skill === 'rest') return { x: 5, y: 42, rotation: -.12 };
  return { x: 12, y: 39, rotation: .28 };
}

export function ArenaView(props: Props) {
  const host = useRef<HTMLDivElement>(null);
  const drawRef = useRef<((data: Props) => void) | null>(null);
  const latest = useRef(props); latest.current = props;
  const [error, setError] = useState(false);

  useEffect(() => {
    let disposed = false, initialized = false, destroyed = false;
    const app = new Application();
    const dispose = () => {
      if (initialized && !destroyed) { destroyed = true; app.destroy(true, { children: true }); }
    };
    async function setup() {
      try {
        await app.init({ width: W, height: H, backgroundAlpha: 0, antialias: false,
          resolution: Math.min(window.devicePixelRatio || 1, 2), autoDensity: true,
          autoStart: false, preference: 'webgl', preserveDrawingBuffer: true });
        initialized = true;
        if (disposed) { dispose(); return; }
        const ids = Object.keys(CHARACTER_ART) as CharacterId[];
        const art = {} as Record<CharacterId, Art>;
        const [background, weaponResponse, itemResponse] = await Promise.all([
          Assets.load<Texture>('/assets/arena.png'), fetch('/api/weapons'), fetch('/api/items'),
        ]);
        if (!weaponResponse.ok) throw new Error('Missing weapon catalog');
        if (!itemResponse.ok) throw new Error('Missing item catalog');
        const weaponCatalog: Weapon[] = await weaponResponse.json();
        const itemCatalog: Item[] = await itemResponse.json();
        const weaponTextures: Record<string, Texture> = {};
        const itemTextures: Record<string, Texture> = {};
        await Promise.all(weaponCatalog.map(async weapon => {
          const texture = await Assets.load<Texture>(weaponAsset(weapon));
          texture.source.scaleMode = 'nearest';
          weaponTextures[weapon.weapon_id] = texture;
        }));
        await Promise.all(itemCatalog.map(async item => {
          const texture = await Assets.load<Texture>(itemAsset(item));
          texture.source.scaleMode = 'nearest';
          itemTextures[item.item_id] = texture;
        }));
        await Promise.all(ids.map(async id => {
          const [texture, impact, response] = await Promise.all([
            Assets.load<Texture>(assetPath(id, 'sprites/atlas.png')),
            Assets.load<Texture>(assetPath(id, 'effects/impact.png')), fetch(assetPath(id, 'animations.json')),
          ]);
          if (!response.ok) throw new Error(`Missing animation atlas: ${id}`);
          const atlas: Atlas = await response.json();
          texture.source.scaleMode = 'nearest';
          const count = Math.floor(texture.height / atlas.height) * atlas.columns;
          const frames = Array.from({ length: count }, (_, i) => new Texture({ source: texture.source,
            frame: new Rectangle(i % atlas.columns * atlas.width, Math.floor(i / atlas.columns) * atlas.height, atlas.width, atlas.height) }));
          impact.source.scaleMode = 'nearest';
          art[id] = { frames, atlas, impact: Array.from({ length: 8 }, (_, i) => new Texture({ source: impact.source,
            frame: new Rectangle(i * 64, 0, 64, 64) })) };
        }));
        if (disposed) { dispose(); return; }
        background.source.scaleMode = 'nearest';
        app.canvas.setAttribute('aria-label', '二次元像素人物战斗擂台');
        app.canvas.setAttribute('role', 'img');
        host.current?.appendChild(app.canvas);
        app.stage.addChild(new Sprite(background));
        const shadows = new Graphics(), effects = new Graphics();
        const people = new Container();
        app.stage.addChild(shadows, people, effects);
        const fighters = {} as Record<Player, { sprite: Sprite; ghost: Sprite; weapon: Sprite; item: Sprite; label: Text }>;
        for (const player of PLAYERS) {
          const ghost = new Sprite(), sprite = new Sprite(), weapon = new Sprite(), item = new Sprite();
          weapon.anchor.set(.5, .8);
          item.anchor.set(.5);
          const label = new Text({ text: '', style: { fontFamily: 'Microsoft YaHei, sans-serif', fontSize: 15, fill: 0xffffff,
            stroke: { color: 0x342b49, width: 4 } } });
          label.anchor.set(.5, 0);
          people.addChild(ghost, sprite, weapon, item, label);
          fighters[player] = { sprite, ghost, weapon, item, label };
        }
        const impactSprites = Array.from({ length: 8 }, () => {
          const sprite = new Sprite(); sprite.anchor.set(.5); sprite.scale.set(2); app.stage.addChild(sprite); return sprite;
        });
        const damageLabels = Array.from({ length: 8 }, () => {
          const label = new Text({ text: '', style: { fontFamily: 'Consolas', fontSize: 22, fontWeight: 'bold', fill: 0xffffff,
            stroke: { color: 0x171820, width: 4 } } });
          label.anchor.set(.5); app.stage.addChild(label); return label;
        });
        drawRef.current = ({ frame, nextFrame, config, time, events }) => {
          shadows.clear(); effects.clear(); damageLabels.forEach(label => { label.visible = false; });
          impactSprites.forEach(sprite => { sprite.visible = false; });
          const interval = nextFrame.simulation_time - frame.simulation_time;
          const mix = interval > 0 ? clamp((time - frame.simulation_time) / interval) : 0;
          for (const player of PLAYERS) {
            const fighter = frame.fighters[player], { sprite, ghost, weapon, item, label } = fighters[player];
            const id = characterId(config, player), data = art[id], character = CHARACTER_ART[id];
            const upcoming = nextFrame.fighters[player];
            const jumping = upcoming.action?.position != null && upcoming.position !== fighter.position;
            const x = worldX(fighter.position + (upcoming.position - fighter.position) * (jumping ? 0 : mix), config);
            const hit = events.some(event => event.status === 'hit' && event.effects.defender === player && time - event.simulation_time >= 0 && time - event.simulation_time < .15);
            const action = fighter.action;
            const animationName = animationFor(fighter, hit);
            const animation = data.atlas.animations[animationName] ?? data.atlas.animations.idle;
            const elapsed = action ? (action.elapsed_ms / 1000 + time - frame.simulation_time) : time;
            const usedItem = action?.skill === 'use_item' ? itemById(config, player, action.item_id) : undefined;
            const skillSpec = action && action.skill !== 'use_item' && action.skill !== 'idle'
              ? effectiveSkills(config, player)[action.skill] : null;
            const spec = usedItem?.timing ?? skillSpec;
            const phaseStart = !spec ? 0 : action?.phase === 'recovery' ? spec.windup_ms + spec.active_ms
              : action?.phase === 'active' ? spec.windup_ms : 0;
            const phaseElapsed = Math.max(0, elapsed * 1000 - phaseStart);
            const phaseDuration = !spec ? 1 : action?.phase === 'windup' ? spec.windup_ms
              : action?.phase === 'recovery' ? spec.recovery_ms - spec.active_ms : spec.active_ms;
            const progress = clamp(phaseElapsed / Math.max(1, phaseDuration));
            const frameNumber = action && !animation.loop && !hit && fighter.health > 0
              ? Math.floor(progress * animation.frames.length) : Math.floor(Math.max(0, time) * animation.fps);
            const index = animation.loop ? frameNumber % animation.frames.length : Math.min(frameNumber, animation.frames.length - 1);
            sprite.texture = data.frames[animation.frames[index]];
            sprite.anchor.set(...data.atlas.anchor);
            const scale = data.atlas.scale ?? 2;
            sprite.scale.set(fighter.facing * scale, scale);
            sprite.position.set(Math.round(x), 366);
            sprite.tint = hit ? 0xffb4be : fighter.health <= 0 ? 0xa8a2b5 : 0xffffff;
            ghost.texture = sprite.texture; ghost.anchor.copyFrom(sprite.anchor); ghost.scale.copyFrom(sprite.scale);
            ghost.position.set(sprite.x - fighter.facing * 24, sprite.y); ghost.tint = character.color; ghost.alpha = .22;
            ghost.visible = action?.skill === 'dash' && action.phase === 'active' && !skillSpec?.teleport;
            const equipped = config.equipment?.[player];
            const pose = weaponPose(fighter, progress);
            weapon.visible = Boolean(equipped && weaponTextures[equipped.weapon_id]);
            if (weapon.visible) {
              weapon.texture = weaponTextures[equipped!.weapon_id];
              weapon.position.set(Math.round(x + fighter.facing * pose.x * 4), 366 - (58 - pose.y) * 4);
              weapon.scale.set(fighter.facing * .7, .7);
              weapon.rotation = fighter.facing * pose.rotation;
              weapon.tint = hit ? 0xffb4be : fighter.health <= 0 ? 0xa8a2b5 : 0xffffff;
            }
            item.visible = Boolean(usedItem && itemTextures[usedItem.item_id] && fighter.health > 0);
            if (item.visible) {
              item.texture = itemTextures[usedItem!.item_id];
              item.position.set(Math.round(x - fighter.facing * 56), 286 - Math.sin(time * 8) * 4);
              item.scale.set(.48);
              item.alpha = action?.phase === 'recovery' ? .65 : 1;
            }
            if ((fighter.shield ?? 0) > 0) {
              effects.circle(x, 302, 57).stroke({ color: character.color, width: 5, alpha: .72 });
              effects.circle(x, 302, 64).stroke({ color: 0xfff0cb, width: 2, alpha: .48 });
            }
            shadows.ellipse(x, 367, fighter.health <= 0 ? 65 : 32, 6).fill({ color: 0x080a12, alpha: .5 });
            label.text = `${player === 'p1' ? 'A' : 'B'}  ${character.name}`;
            label.style.fill = character.color;
            label.position.set(clamp(x, 66, W - 66), 388);
            if (action && action.skill !== 'use_item' && action.skill !== 'idle'
              && fighter.health > 0 && !skillSpec?.teleport) {
              drawSkillEffect(effects, id, action.skill, action.phase, x, fighter.facing, progress,
                skillSpec!.reach / config.arena_width * 1112, time,
                worldX(frame.fighters[player === 'p1' ? 'p2' : 'p1'].position, config), phaseElapsed / 1000);
            }
          }
          let damageIndex = 0;
          for (const event of events) {
            const age = time - event.simulation_time;
            if (age < -.001) continue;
            if (event.status === 'teleported' && age < .45) {
              for (const [position, arriving] of [[event.effects.origin, false], [event.effects.destination, true]] as const) {
                if (position !== undefined) drawTeleportEffect(effects, worldX(position, config), age, arriving);
              }
            }
            if (event.status === 'projectile' && event.actor) {
              const { origin, facing, speed, reach } = event.effects;
              const travelAge = time - (event.effects.launched_at ?? event.simulation_time);
              if (origin === undefined || !facing || !speed || !reach || travelAge < 0 || travelAge > reach / speed) continue;
              const ended = events.some(other => other.action_id === event.action_id && ['hit', 'missed'].includes(other.status) && other.simulation_time <= time);
              if (ended || frame.result) continue;
              const px = worldX(origin + facing * speed * travelAge, config);
              drawProjectileEffect(effects, px, facing, travelAge);
            }
            if (event.status !== 'hit' || !event.effects.defender || age > .45) continue;
            const target = fighters[event.effects.defender].sprite;
            const x = target.x, y = 276;
            const alpha = clamp(1 - age / .45);
            if (damageIndex < damageLabels.length) {
              const impact = impactSprites[damageIndex]; impact.visible = true;
              impact.texture = art[characterId(config, event.actor ?? 'p1')].impact[Math.min(7, Math.floor(age / .45 * 8))];
              impact.position.set(x, y); impact.alpha = alpha;
              const label = damageLabels[damageIndex++]; label.visible = true;
              label.text = `-${(event.effects.health_damage ?? 0).toFixed(0)}`;
              label.position.set(x, y - 50 - age * 60); label.alpha = alpha;
            }
          }
          app.render();
        };
        drawRef.current(latest.current);
      } catch (reason) {
        if (!disposed) { console.error(reason); setError(true); }
      }
    }
    void setup();
    return () => { disposed = true; drawRef.current = null; dispose(); };
  }, []);

  useEffect(() => { drawRef.current?.(props); }, [props]);
  return <div className="arena-canvas" ref={host} data-testid="arena-canvas">
    {error && <div className="canvas-error" role="alert">人物素材加载失败，请刷新页面重试。</div>}
  </div>;
}
