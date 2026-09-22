import type { GameConfig, Player, Skill, SkillSpec, Weapon } from './types';

export const DEFAULT_WEAPONS: Record<Player, string> = {
  p1: 'stone_sword',
  p2: 'dragonwrath_staff',
};

export function weaponId(config: GameConfig, player: Player): string | undefined {
  return config.equipment?.[player]?.weapon_id;
}

export function weaponAsset(weapon: Weapon): string {
  return `/assets/weapons/${weapon.asset}`;
}

export function resourceLimit(config: GameConfig, player: Player, resource: 'health' | 'stamina' | 'mana'): number {
  const field = `max_${resource}` as const;
  const base = config.characters?.[player]?.[field] ?? config[field];
  return base + (config.equipment?.[player]?.resource_modifiers[field] ?? 0);
}

export function effectiveSkills(config: GameConfig, player: Player): Record<Skill, SkillSpec> {
  const base = config.characters?.[player]?.skills ?? config.skills;
  const modifiers = config.equipment?.[player]?.skill_modifiers ?? {};
  return Object.fromEntries(Object.entries(base).map(([skill, spec]) => {
    const result = { ...spec } as SkillSpec & Record<string, unknown>;
    for (const [field, amount] of Object.entries(modifiers[skill as Skill] ?? {})) {
      const current = result[field];
      if (typeof amount === 'number') result[field] = (typeof current === 'number' ? current : 0) + amount;
    }
    return [skill, result];
  })) as unknown as Record<Skill, SkillSpec>;
}

export function modifierSummary(weapon: Weapon): string {
  const labels = { max_health: '生命', max_stamina: '体力', max_mana: '魔力' };
  const values = Object.entries(weapon.resource_modifiers)
    .filter(([, value]) => value !== 0)
    .map(([field, value]) => `${labels[field as keyof typeof labels]} ${value > 0 ? '+' : ''}${value}`);
  return values.join(' · ') || '无属性修正';
}
