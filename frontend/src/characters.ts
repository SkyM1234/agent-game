import type { CharacterId, GameConfig, Player, PromptVariant, Skill } from './types';

export const CHARACTER_ART: Record<CharacterId, { name: string; title: string; color: number; hex: string }> = {
  crimson_blade: { name: '绯刃', title: '剑士', color: 0xff7588, hex: '#ff7588' },
  frost_bell: { name: '霜铃', title: '法师', color: 0x86ddff, hex: '#86ddff' },
};
export const DEFAULT_CHARACTERS: Record<Player, CharacterId> = { p1: 'crimson_blade', p2: 'frost_bell' };
export function promptVariant(config: GameConfig, player: Player): PromptVariant {
  // Saved character profiles identify aggressive prompts with a version suffix.
  return /-aggressive-v\d+$/.test(config.characters?.[player]?.agent?.version ?? '') ? 'aggressive' : 'neutral';
}
export function characterId(config: GameConfig, player: Player): CharacterId {
  return config.characters?.[player]?.character_id ?? DEFAULT_CHARACTERS[player];
}
export function skillName(config: GameConfig | undefined, player: Player, skill: Skill, fallback: string): string {
  return config?.characters?.[player]?.skills[skill]?.display_name ?? fallback;
}
export function assetPath(id: CharacterId, file: string): string {
  return `/assets/characters/${id}/${file}`;
}
