export type Player = 'p1' | 'p2';
export type CharacterId = 'crimson_blade' | 'frost_bell';
export type PromptVariant = 'neutral' | 'aggressive';
export type Profession = 'swordsman' | 'mage';
export interface Character {
  character_id: CharacterId;
  name: string;
  title: string;
  profession?: Profession | null;
  max_health?: number | null;
  max_stamina?: number | null;
  max_mana?: number | null;
  skills: Record<Skill, SkillSpec>;
  agent?: { version: string; prompt: string };
}
export interface Weapon {
  weapon_id: string;
  name: string;
  profession: Profession;
  description: string;
  asset: string;
  resource_modifiers: { max_health: number; max_stamina: number; max_mana: number };
  skill_modifiers: Partial<Record<Skill, Partial<Record<keyof SkillSpec, number>>>>;
}
export type Skill = 'jab' | 'heavy_punch' | 'kick' | 'guard' | 'dash' | 'move' | 'rest';
export type ActionName = Skill | 'idle' | 'use_item';
export interface ItemEffects {
  health_restore: number;
  stamina_restore: number;
  mana_restore: number;
  shield: number;
  shield_duration_turns: number;
  cooldown_reduction_turns: number;
  backward_move: number;
}
export interface Item {
  item_id: string;
  name: string;
  description: string;
  asset: string;
  allowed_professions: Profession[];
  charges: number;
  timing: { windup_ms: number; active_ms: number; recovery_ms: number };
  effects: ItemEffects;
}
export interface Action {
  skill: ActionName;
  direction?: 'forward' | 'backward' | null;
  position?: number | null;
  item_id?: string | null;
}
export interface CurrentAction extends Action {
  action_id: string;
  phase: 'windup' | 'active' | 'recovery';
  elapsed_ms: number;
  remaining_ms: number;
}
export interface Fighter {
  fighter_id: Player;
  position: number;
  facing: number;
  health: number;
  stamina: number;
  mana: number;
  action: CurrentAction | null;
  ongoing_actions?: CurrentAction[];
  cooldowns?: Partial<Record<Skill, number>>;
  items?: Record<string, number>;
  shield?: number;
  shield_turns?: number;
}
export interface Result {
  winner: Player | null;
  reason: string;
  simulation_time: number;
}
export interface Frame {
  turn: number;
  tick: number;
  simulation_time: number;
  fighters: Record<Player, Fighter>;
  tools: Record<Player, {
    available: ActionName[];
    unavailable: Partial<Record<ActionName, string>>;
    items?: { available: Record<string, Item>; unavailable: Record<string, string> };
  }>;
  event_count: number;
  result: Result | null;
}
export interface MatchPreview {
  config: GameConfig;
  frame: Frame;
}
export interface SkillSpec {
  display_name?: string | null;
  cooldown_turns: number;
  mana_cost: number;
  projectile_speed?: number;
  health_damage: number;
  stamina_cost: number;
  speed: number;
  teleport?: boolean;
  stamina_restore?: number | null;
  mana_restore?: number | null;
  reach: number;
  windup_ms: number;
  active_ms: number;
  recovery_ms: number;
}
export interface GameConfig {
  characters?: Partial<Record<Player, Character>>;
  equipment?: Partial<Record<Player, Weapon>>;
  items?: Partial<Record<Player, Item[]>>;
  arena_width: number;
  fighter_radius: number;
  max_health: number;
  max_stamina: number;
  max_mana: number;
  guard_damage_multiplier: number;
  rest_stamina_per_second: number;
  rest_mana_per_second: number;
  step_ms: number;
  decision_ms: number;
  time_limit_ms: number;
  skills: Record<Skill, SkillSpec>;
}
export interface CombatEvent {
  simulation_time: number;
  actor: Player | null;
  action_id: string | null;
  status: string;
  reason: string | null;
  effects: {
    skill?: ActionName; item_id?: string; direction?: string; defender?: Player;
    health_damage?: number; stamina_cost?: number; mana_cost?: number;
    health_restore?: number; stamina_restore?: number; mana_restore?: number;
    shield?: number; shield_absorbed?: number; shield_duration_turns?: number;
    cooldown_reduction_turns?: number;
    winner?: Player | null;
    origin?: number; destination?: number; position?: number; facing?: number; speed?: number; reach?: number; launched_at?: number;
  };
}
export interface Replay {
  replay_id: string;
  created_at: string;
  format_version: number;
  rules_version: string;
  config_hash: string;
  agents: Record<Player, string>;
  agent_metadata?: Partial<Record<Player, { provider: string; model?: string; prompt_version?: string }>>;
  config: GameConfig;
  frames: Frame[];
  events: CombatEvent[];
  decisions: { tick: number; simulation_time: number; actions: Partial<Record<Player, Action>>; details?: Partial<Record<Player, DecisionInfo>> }[];
  summary: { result: Result | null };
}
export interface DecisionInfo {
  source: string; summary: string; attempts: number; latency_ms: number;
  total_tokens: number; requested_model: string | null; returned_model: string | null;
  errors: string[]; available_tools: string[];
  selected_tool?: string | null; character_id?: CharacterId | null;
  character_prompt_version?: string | null; finish_reason?: string | null;
  rollout_recommendation?: Action | null; risk_weighted_score?: number | null;
}
export interface AgentOption {
  id: string; label: string; available: boolean; model?: string; reason?: string | null;
}
export interface ReplayItem {
  replay_id: string;
  created_at: string;
  agents: Record<Player, string>;
  result: Result;
  frame_count: number;
}
export interface IncompleteMatch {
  replay_id: string;
  created_at: string;
  updated_at: string;
  agents: Record<Player, string>;
  turn_count: number;
  status: 'running';
}
