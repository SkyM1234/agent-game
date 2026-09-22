import type { GameConfig, Item, Player } from './types';

export const DEFAULT_ITEMS: Record<Player, [string, string]> = {
  p1: ['healing_potion', 'stamina_drink'],
  p2: ['mana_crystal', 'guardian_charm'],
};

export function itemAsset(item: Item): string {
  return `/assets/items/${item.asset}`;
}

export function itemIds(config: GameConfig, player: Player): [string, string] {
  const ids = config.items?.[player]?.map(item => item.item_id) ?? [];
  return [ids[0] ?? '', ids[1] ?? ''];
}

export function itemEffect(item: Item): string {
  const effects = item.effects;
  if (effects.health_restore) return `生命 +${effects.health_restore}`;
  if (effects.stamina_restore) return `体力 +${effects.stamina_restore}`;
  if (effects.mana_restore) return `魔力 +${effects.mana_restore}`;
  if (effects.shield) return `护盾 ${effects.shield} / ${effects.shield_duration_turns} 回合`;
  if (effects.cooldown_reduction_turns) return `冷却 -${effects.cooldown_reduction_turns} 回合`;
  if (effects.backward_move) return `后撤 ${effects.backward_move}`;
  return '无效果';
}

export function itemById(config: GameConfig, player: Player, itemId: string | null | undefined): Item | undefined {
  return config.items?.[player]?.find(item => item.item_id === itemId);
}
