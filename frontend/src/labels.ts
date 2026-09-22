import type { Action, ActionName, CombatEvent, GameConfig, Player, Skill } from './types';
import { CHARACTER_ART, characterId, skillName } from './characters';
import { itemById } from './items';

export const SKILLS: Record<Skill, string> = {
  jab: '刺拳', heavy_punch: '重拳', kick: '踢击', guard: '格挡',
  dash: '冲刺', move: '移动', rest: '休息',
};
export const AGENTS: Record<string, string> = {
  heuristic: '规则机器人', deepseek: '纯 DeepSeek',
  test: '测试', counterfactual: '反事实推演',
  counterfactual_deepseek: '反事实推演 + DeepSeek',
};
export const PHASES = { windup: '前摇', active: '生效中', recovery: '恢复中' };
export const actorName = (actor: string | null, config?: GameConfig) => {
  if (actor !== 'p1' && actor !== 'p2') return '擂台';
  return config ? `${CHARACTER_ART[characterId(config, actor)].name} · ${actor === 'p1' ? 'A' : 'B'}` : actor === 'p1' ? 'A-01' : 'B-02';
};
export function actionText(action: Action | null, config?: GameConfig, player: Player = 'p1') {
  if (!action) return '待命';
  if (action.skill === 'idle') return '无动作';
  if (action.skill === 'use_item') {
    const item = config && itemById(config, player, action.item_id);
    return `使用 ${item?.name ?? action.item_id ?? '道具'}`;
  }
  const detail = action.position != null ? `落点 ${Number(action.position.toFixed(3))}` : action.direction === 'forward' ? '接近' : action.direction === 'backward' ? '后退' : '';
  return `${skillName(config, player, action.skill, SKILLS[action.skill])}${detail ? ` · ${detail}` : ''}`;
}
export function eventText(event: CombatEvent, config?: GameConfig) {
  const effect = event.effects;
  const player = event.actor ?? 'p1';
  const name = (skill: ActionName) => skill === 'use_item' ? '使用道具' : skill === 'idle' ? '无动作' : skillName(config, player, skill, SKILLS[skill]);
  switch (event.status) {
    case 'hit': return `${effect.skill ? name(effect.skill) + ' · ' : ''}命中 ${actorName(effect.defender ?? null, config)}`;
    case 'started': return actionText({ skill: effect.skill!, item_id: effect.item_id, direction: effect.direction as Action['direction'], position: effect.position }, config, player);
    case 'item_used': {
      const item = config && itemById(config, player, effect.item_id);
      return `${item?.name ?? '道具'}生效`;
    }
    case 'teleported': return `${name(effect.skill!)} · ${Number((effect.origin ?? 0).toFixed(3))} → ${Number((effect.destination ?? 0).toFixed(3))}`;
    case 'projectile': return `${name(effect.skill!)}释放`;
    case 'missed': return '攻击落空 · 超出射程';
    case 'fallback': return effect.skill === 'idle' ? '非法动作 · 本回合无动作' : '执行保底动作 · 休息';
    case 'rejected': return '动作被拒绝';
    case 'match_finished': return effect.winner ? `${actorName(effect.winner, config)} 获胜` : '对局结束 · 平局';
    default: return '动作完成';
  }
}

export function frameAt(frames: { simulation_time: number }[], time: number) {
  let lo = 0, hi = frames.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (frames[mid].simulation_time <= time + 1e-8) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}
