from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Direction = Literal["forward", "backward"]
Skill = Literal["jab", "heavy_punch", "kick", "guard", "dash", "move", "rest"]
ActionName = Literal["jab", "heavy_punch", "kick", "guard", "dash", "move", "rest", "idle", "use_item"]
CharacterId = Literal["crimson_blade", "frost_bell"]
Profession = Literal["swordsman", "mage"]
ATTACKS = ("jab", "heavy_punch", "kick")
SKILLS: tuple[Skill, ...] = (*ATTACKS, "guard", "dash", "move", "rest")


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False,
                              revalidate_instances="always")


class Action(Schema):
    skill: ActionName
    direction: Direction | None = None
    position: float | None = Field(default=None, strict=True)
    item_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @model_validator(mode="after")
    def validate_parameters(self) -> Action:
        if self.skill == "use_item":
            if self.item_id is None or self.direction is not None or self.position is not None:
                raise ValueError("item use requires only item_id")
        elif self.item_id is not None:
            raise ValueError("only item use accepts item_id")
        elif self.skill == "dash":
            if (self.direction is None) == (self.position is None):
                raise ValueError("dash requires either direction or teleport position")
        elif self.skill == "move":
            if self.direction is None or self.position is not None:
                raise ValueError("movement requires only direction")
        elif self.direction is not None or self.position is not None:
            raise ValueError("only movement accepts direction or position")
        return self


class SkillSpec(Schema):
    display_name: str | None = None
    cooldown_turns: int = Field(default=0, ge=0, strict=True)
    projectile_speed: float = Field(default=0, ge=0)
    stamina_cost: float = Field(default=0, ge=0)
    mana_cost: float = Field(default=0, ge=0)
    windup_ms: int = Field(default=0, ge=0, strict=True)
    active_ms: int = Field(gt=0, strict=True)
    recovery_ms: int = Field(default=0, ge=0, strict=True)
    reach: float = Field(default=0, ge=0)
    health_damage: float = Field(default=0, ge=0)
    speed: float = Field(default=0, ge=0)
    teleport: bool = False
    # Total recovery over this skill's active phase; None uses the global rest rate.
    stamina_restore: float | None = Field(default=None, ge=0)
    mana_restore: float | None = Field(default=None, ge=0)

    @property
    def duration_ms(self) -> int:
        # Active effects and recovery run in parallel after windup.
        return self.windup_ms + max(self.active_ms, self.recovery_ms)

    @property
    def lock_ms(self) -> int:
        return self.windup_ms + self.recovery_ms


def default_skills() -> dict[Skill, SkillSpec]:
    return {
        "jab": SkillSpec(stamina_cost=8, windup_ms=100,
                         active_ms=100, recovery_ms=300, reach=1.3,
                         health_damage=10),
        "heavy_punch": SkillSpec(stamina_cost=20, cooldown_turns=2,
                                 windup_ms=400, active_ms=100, recovery_ms=500,
                                 reach=1.5, health_damage=25),
        "kick": SkillSpec(stamina_cost=16, windup_ms=250, cooldown_turns=1,
                          active_ms=100, recovery_ms=650, reach=2.1,
                          health_damage=16),
        "guard": SkillSpec(stamina_cost=6,
                           active_ms=500),
        "dash": SkillSpec(stamina_cost=10, cooldown_turns=1,
                          active_ms=250, recovery_ms=250, speed=6),
        "move": SkillSpec(active_ms=500, speed=2),
        "rest": SkillSpec(active_ms=500),
    }


class CharacterTool(Schema):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    action: Skill
    description: str = Field(min_length=1, max_length=1000)
    parameters: dict[str, list[str] | dict[str, Literal["number"]]]

    @model_validator(mode="after")
    def validate_parameters(self):
        if self.action == "dash" and self.parameters == {"position": {"type": "number"}}:
            return self
        expected = {"direction": {"forward", "backward"}} if self.action in ("move", "dash") else {}
        if set(self.parameters) != set(expected):
            raise ValueError("tool parameters do not match its engine action")
        for name, values in self.parameters.items():
            if not isinstance(values, list) or not values or len(values) != len(set(values)) or not set(values) <= expected[name]:
                raise ValueError("invalid tool parameter choices")
        return self


class CharacterAgentProfile(Schema):
    version: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=8000)
    tools: list[CharacterTool] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_tools(self):
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("character tool names must be unique")
        if len({tool.action for tool in self.tools}) != len(self.tools):
            raise ValueError("each engine action must have one character tool")
        if {tool.action for tool in self.tools} != set(SKILLS):
            raise ValueError("character tools must cover the seven supported engine actions")
        return self


class CharacterLoadout(Schema):
    character_id: CharacterId
    name: str
    title: str
    profession: Profession | None = None
    max_health: float | None = Field(default=None, gt=0)
    max_stamina: float | None = Field(default=None, gt=0)
    max_mana: float | None = Field(default=None, gt=0)
    skills: dict[Skill, SkillSpec]
    agent: CharacterAgentProfile | None = None


class ResourceModifiers(Schema):
    max_health: float = 0
    max_stamina: float = 0
    max_mana: float = 0


class SkillModifiers(Schema):
    cooldown_turns: int | None = Field(default=None, strict=True)
    projectile_speed: float | None = None
    stamina_cost: float | None = None
    mana_cost: float | None = None
    windup_ms: int | None = Field(default=None, strict=True)
    active_ms: int | None = Field(default=None, strict=True)
    recovery_ms: int | None = Field(default=None, strict=True)
    reach: float | None = None
    health_damage: float | None = None
    speed: float | None = None
    stamina_restore: float | None = None
    mana_restore: float | None = None


class WeaponSpec(Schema):
    weapon_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=80)
    profession: Profession
    description: str = Field(min_length=1, max_length=500)
    asset: str = Field(pattern=r"^[a-z0-9_/-]+\.png$")
    resource_modifiers: ResourceModifiers = Field(default_factory=ResourceModifiers)
    skill_modifiers: dict[Skill, SkillModifiers] = Field(default_factory=dict)

    def apply_to_skill(self, name: Skill, spec: SkillSpec) -> SkillSpec:
        modifier = self.skill_modifiers.get(name)
        if modifier is None:
            return spec
        values = spec.model_dump()
        for field_name, amount in modifier.model_dump(exclude_none=True).items():
            base = values[field_name]
            values[field_name] = (0 if base is None else base) + amount
        return SkillSpec.model_validate(values)


class ItemTiming(Schema):
    windup_ms: int = Field(default=150, ge=0, strict=True)
    active_ms: int = Field(default=50, gt=0, strict=True)
    recovery_ms: int = Field(default=300, ge=0, strict=True)

    @property
    def duration_ms(self) -> int:
        # Item effects use the same overlapping active/recovery timeline as skills.
        return self.windup_ms + max(self.active_ms, self.recovery_ms)

    @property
    def lock_ms(self) -> int:
        return self.windup_ms + self.recovery_ms


class ItemEffects(Schema):
    health_restore: float = Field(default=0, ge=0)
    stamina_restore: float = Field(default=0, ge=0)
    mana_restore: float = Field(default=0, ge=0)
    shield: float = Field(default=0, ge=0)
    shield_duration_turns: int = Field(default=0, ge=0, strict=True)
    cooldown_reduction_turns: int = Field(default=0, ge=0, strict=True)
    backward_move: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_effect(self):
        values = self.model_dump()
        if not any(values.values()):
            raise ValueError("item must have an effect")
        if bool(self.shield) != bool(self.shield_duration_turns):
            raise ValueError("shield and duration must be configured together")
        return self


class ItemSpec(Schema):
    item_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    asset: str = Field(pattern=r"^[a-z0-9_/-]+\.png$")
    allowed_professions: list[Profession] = Field(default_factory=list, max_length=2)
    charges: int = Field(default=1, ge=1, le=9, strict=True)
    timing: ItemTiming = Field(default_factory=ItemTiming)
    effects: ItemEffects

    @model_validator(mode="after")
    def validate_professions(self):
        if len(self.allowed_professions) != len(set(self.allowed_professions)):
            raise ValueError("allowed professions must be unique")
        return self


class GameConfig(Schema):
    characters: dict[Literal["p1", "p2"], CharacterLoadout] = Field(default_factory=dict)
    equipment: dict[Literal["p1", "p2"], WeaponSpec] = Field(default_factory=dict)
    items: dict[Literal["p1", "p2"], list[ItemSpec]] = Field(default_factory=dict)
    step_ms: int = Field(default=50, gt=0, strict=True)
    decision_ms: int = Field(default=500, gt=0, strict=True)
    time_limit_ms: int = Field(default=60_000, gt=0, strict=True)
    arena_width: float = Field(default=12, gt=0)
    fighter_radius: float = Field(default=0.4, gt=0)
    starting_positions: tuple[float, float] = (3, 9)
    max_health: float = Field(default=100, gt=0)
    max_stamina: float = Field(default=100, gt=0)
    max_mana: float = Field(default=100, gt=0)
    guard_damage_multiplier: float = Field(default=0.3, ge=0, le=1)
    rest_stamina_per_second: float = Field(default=30, gt=0)
    rest_mana_per_second: float = Field(default=40, gt=0)
    skills: dict[Skill, SkillSpec] = Field(default_factory=default_skills)

    def resource_limit(self, player: Literal["p1", "p2"], resource: Literal["health", "stamina", "mana"]) -> float:
        field = f"max_{resource}"
        character = self.characters.get(player)
        character_limit = getattr(character, field) if character else None
        base = character_limit if character_limit is not None else getattr(self, field)
        weapon = self.equipment.get(player)
        return base + (getattr(weapon.resource_modifiers, field) if weapon else 0)

    def effective_skills(self, player: Literal["p1", "p2"]) -> dict[Skill, SkillSpec]:
        character = self.characters.get(player)
        skills = character.skills if character else self.skills
        weapon = self.equipment.get(player)
        return ({name: weapon.apply_to_skill(name, spec) for name, spec in skills.items()}
                if weapon else skills)

    @model_validator(mode="after")
    def validate_rules(self) -> GameConfig:
        if self.decision_ms % self.step_ms or self.time_limit_ms % self.step_ms:
            raise ValueError("decision_ms and time_limit_ms must align to step_ms")
        if set(self.skills) != set(SKILLS):
            raise ValueError("configuration must define all seven skills")
        if self.characters and set(self.characters) != {"p1", "p2"}:
            raise ValueError("characters must contain both players")
        if self.equipment and set(self.equipment) != {"p1", "p2"}:
            raise ValueError("equipment must contain both players")
        if self.equipment and not self.characters:
            raise ValueError("equipment requires character selections")
        if self.items and set(self.items) != {"p1", "p2"}:
            raise ValueError("items must contain both players")
        for player, loadout in self.items.items():
            if len(loadout) > 2 or len({item.item_id for item in loadout}) != len(loadout):
                raise ValueError("players may carry up to two unique items")
            profession = self.characters.get(player).profession if self.characters.get(player) else None
            if any(item.allowed_professions and profession not in item.allowed_professions for item in loadout):
                raise ValueError("item cannot be used by the selected profession")
            for item in loadout:
                if any(duration % self.step_ms for duration in (
                    item.timing.windup_ms, item.timing.active_ms, item.timing.recovery_ms
                )):
                    raise ValueError(f"{item.item_id} durations must align to step_ms")
        for player, weapon in self.equipment.items():
            if self.characters[player].profession != weapon.profession:
                raise ValueError(f"{weapon.weapon_id} cannot be equipped by {self.characters[player].character_id}")
            if any(self.resource_limit(player, resource) <= 0 for resource in ("health", "stamina", "mana")):
                raise ValueError("weapon modifiers must leave resource limits positive")
        for skills in [self.skills, *(item.skills for item in self.characters.values())]:
            self._validate_skills(skills)
        for player in self.equipment:
            self._validate_skills(self.effective_skills(player))
        for character in self.characters.values():
            if character.agent:
                dash_tool = next(tool for tool in character.agent.tools if tool.action == "dash")
                if character.skills["dash"].teleport != ("position" in dash_tool.parameters):
                    raise ValueError("teleport skill and position tool parameters must match")
        left, right = sorted(self.starting_positions)
        radius = self.fighter_radius
        if left < radius or right > self.arena_width - radius:
            raise ValueError("starting positions must be inside the arena")
        if right - left < 2 * radius - 1e-9:
            raise ValueError("fighters must start without overlap")
        if any(not (-float("inf") < x < float("inf"))
               for x in self.starting_positions):
            raise ValueError("starting positions must be finite")
        return self

    def _validate_skills(self, skills: dict[Skill, SkillSpec]) -> None:
        if set(skills) != set(SKILLS):
            raise ValueError("configuration must define all seven skills")
        for name, spec in skills.items():
            if spec.teleport and (name != "dash" or spec.speed != 0):
                raise ValueError("teleport must be a dash with zero speed")
            if name != "rest" and (spec.stamina_restore is not None or spec.mana_restore is not None):
                raise ValueError("only rest skills support resource restoration")
            if any(t % self.step_ms for t in
                   (spec.windup_ms, spec.active_ms, spec.recovery_ms)):
                raise ValueError(f"{name} durations must align to step_ms")
            if spec.projectile_speed and (name not in ATTACKS or spec.projectile_speed * spec.active_ms / 1000 < spec.reach):
                raise ValueError("projectiles must be attacks with enough active time to cover reach")
        if skills["rest"].stamina_cost != 0 or skills["rest"].mana_cost != 0:
            raise ValueError("rest must not consume stamina or mana")
        if skills["move"].cooldown_turns != 0:
            raise ValueError("move must not have a cooldown")


@dataclass
class ActiveAction:
    action_id: str
    request: Action
    facing: int
    movement_direction: int = 0
    elapsed_ms: int = 0
    hit: bool = False
    attack_resolved: bool = False
    projectile_origin: float | None = None
    item_applied: bool = False
    teleport_resolved: bool = False

    def phase(self, spec: SkillSpec | ItemTiming) -> str:
        if self.elapsed_ms < spec.windup_ms:
            return "windup"
        if self.elapsed_ms < spec.windup_ms + spec.active_ms:
            return "active"
        return "recovery"


@dataclass
class Fighter:
    fighter_id: str
    position: float
    facing: int
    health: float
    stamina: float
    mana: float
    active: ActiveAction | None = None
    lingering: list[ActiveAction] = field(default_factory=list)
    cooldowns: dict[str, int] = field(default_factory=dict)
    items: dict[str, int] = field(default_factory=dict)
    shield: float = 0
    shield_until_turn: int = 0


@dataclass
class MatchResult:
    winner: str | None
    reason: str
    simulation_time: float


@dataclass
class Event:
    simulation_time: float
    actor: str | None
    action_id: str | None
    status: str
    reason: str | None = None
    effects: dict = field(default_factory=dict)


def parameter_matches(value, schema: dict) -> bool:
    """Validate the enum or bounded numeric parameters exposed by engine tools."""
    if "enum" in schema:
        return value in schema["enum"]
    if schema.get("type") == "number":
        if type(value) not in (int, float) or not math.isfinite(value):
            return False
    if "minimum" in schema and value < schema["minimum"]:
        return False
    if "maximum" in schema and value > schema["maximum"]:
        return False
    return "anyOf" not in schema or any(parameter_matches(value, item) for item in schema["anyOf"])
