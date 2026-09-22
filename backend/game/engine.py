from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict

from pydantic import ValidationError

from .models import (
    ATTACKS, Action, ActiveAction, Event, Fighter, GameConfig,
    ItemSpec, MatchResult, SkillSpec, parameter_matches,
)
from .skills import idle


class Arena:
    """Fixed-step, simultaneous combat with no dependency on agents or rendering."""

    def __init__(self, config: GameConfig | None = None):
        self.config = (config or GameConfig()).model_copy(deep=True)
        self.tick = 0
        self.turn_index = 0
        self.result: MatchResult | None = None
        self.events: list[Event] = []
        self._recent_events: list[Event] = []
        self._action_sequence = 0
        positions = self.config.starting_positions
        self.fighters = {
            fighter_id: Fighter(
                fighter_id=fighter_id, position=positions[index],
                facing=1 if positions[index] < positions[1 - index] else -1,
                health=self.config.resource_limit(fighter_id, "health"),
                stamina=self.config.resource_limit(fighter_id, "stamina"),
                mana=self.config.resource_limit(fighter_id, "mana"),
                items={item.item_id: item.charges for item in self.config.items.get(fighter_id, [])},
            )
            for index, fighter_id in enumerate(("p1", "p2"))
        }

    def clone(self) -> Arena:
        """Return an isolated combat branch with the complete in-flight world state."""
        return deepcopy(self)

    def export_state(self) -> dict:
        """Serialize the authoritative state needed to continue this arena."""
        def active_state(active: ActiveAction | None) -> dict | None:
            if active is None:
                return None
            data = asdict(active)
            data["request"] = active.request.model_dump(mode="json")
            return data

        def fighter_state(fighter: Fighter) -> dict:
            data = asdict(fighter)
            data["active"] = active_state(fighter.active)
            data["lingering"] = [active_state(item) for item in fighter.lingering]
            return data

        return {
            "tick": self.tick,
            "turn_index": self.turn_index,
            "result": asdict(self.result) if self.result else None,
            "events": [asdict(event) for event in self.events],
            "recent_events": [asdict(event) for event in self._recent_events],
            "action_sequence": self._action_sequence,
            "fighters": {player: fighter_state(fighter) for player, fighter in self.fighters.items()},
        }

    @classmethod
    def from_state(cls, config: GameConfig, state: dict) -> Arena:
        """Restore a state emitted by :meth:`export_state`."""
        arena = cls(config)

        def active(value: dict | None) -> ActiveAction | None:
            if value is None:
                return None
            request = Action.model_validate(value["request"])
            legacy_teleport = request.position is not None and "teleport_resolved" not in value
            return ActiveAction(**{
                **value, "request": request,
                **({"teleport_resolved": True} if legacy_teleport else {}),
            })

        arena.tick = int(state["tick"])
        arena.turn_index = int(state["turn_index"])
        arena.result = MatchResult(**state["result"]) if state.get("result") else None
        arena.events = [Event(**event) for event in state.get("events", [])]
        arena._recent_events = [Event(**event) for event in state.get("recent_events", [])]
        arena._action_sequence = int(state.get("action_sequence", 0))
        arena.fighters = {
            player: Fighter(
                **{key: value for key, value in fighter.items()
                   if key not in ("active", "lingering")},
                active=active(fighter.get("active")),
                lingering=[active(item) for item in fighter.get("lingering", [])],
            )
            for player, fighter in state["fighters"].items()
        }
        if set(arena.fighters) != {"p1", "p2"} or arena.tick < 0 or arena.turn_index < 0:
            raise ValueError("invalid arena checkpoint")
        return arena

    @property
    def time_ms(self) -> int:
        return self.tick * self.config.step_ms

    @property
    def simulation_time(self) -> float:
        return self.time_ms / 1000

    def opponent(self, fighter_id: str) -> Fighter:
        if fighter_id not in self.fighters:
            raise KeyError(fighter_id)
        return self.fighters["p2" if fighter_id == "p1" else "p1"]

    def _emit(self, status: str, actor: str | None = None,
              action_id: str | None = None, reason: str | None = None,
              **effects) -> None:
        self.events.append(Event(self.simulation_time, actor, action_id,
                                 status, reason, effects))

    def skills_for(self, fighter: Fighter) -> dict[str, SkillSpec]:
        return self.config.effective_skills(fighter.fighter_id)

    def items_for(self, fighter: Fighter) -> dict[str, ItemSpec]:
        return {item.item_id: item for item in self.config.items.get(fighter.fighter_id, [])}

    def action_spec(self, fighter: Fighter, action: Action):
        if action.skill == "idle":
            return SkillSpec(active_ms=self.config.decision_ms)
        return (self.items_for(fighter)[action.item_id].timing
                if action.skill == "use_item" else self.skills_for(fighter)[action.skill])

    def can_decide(self, fighter_id: str) -> bool:
        fighter = self.fighters[fighter_id]
        if fighter.active is None:
            return True
        spec = self.action_spec(fighter, fighter.active.request)
        return fighter.active.elapsed_ms >= spec.lock_ms

    @staticmethod
    def _actions(fighter: Fighter) -> list[ActiveAction]:
        return ([fighter.active] if fighter.active else []) + fighter.lingering

    def _action_view(self, fighter: Fighter, active: ActiveAction) -> dict:
        spec = self.action_spec(fighter, active.request)
        return {
            "action_id": active.action_id,
            **active.request.model_dump(exclude_none=True),
            "phase": active.phase(spec),
            "elapsed_ms": active.elapsed_ms,
            "remaining_ms": spec.duration_ms - active.elapsed_ms,
        }

    def _unavailable_reason(self, fighter: Fighter, spec: SkillSpec, name: str) -> str | None:
        if self.result is not None:
            return "match_finished"
        if not self.can_decide(fighter.fighter_id):
            return "action_locked"
        if fighter.cooldowns.get(name, 0) > self.turn_index:
            return "cooldown"
        if fighter.stamina + 1e-9 < spec.stamina_cost:
            return "insufficient_stamina"
        if fighter.mana + 1e-9 < spec.mana_cost:
            return "insufficient_mana"
        return None

    def _item_unavailable_reason(self, fighter: Fighter, item: ItemSpec) -> str | None:
        if self.result is not None:
            return "match_finished"
        if not self.can_decide(fighter.fighter_id):
            return "action_locked"
        if fighter.items.get(item.item_id, 0) <= 0:
            return "depleted"
        effects = item.effects
        if effects.health_restore and fighter.health >= self.config.resource_limit(fighter.fighter_id, "health"):
            return "health_full"
        if effects.stamina_restore and fighter.stamina >= self.config.resource_limit(fighter.fighter_id, "stamina"):
            return "stamina_full"
        if effects.mana_restore and fighter.mana >= self.config.resource_limit(fighter.fighter_id, "mana"):
            return "mana_full"
        if effects.shield and fighter.shield > 0:
            return "shield_active"
        if effects.cooldown_reduction_turns and not any(until > self.turn_index for until in fighter.cooldowns.values()):
            return "no_cooldown"
        if effects.backward_move:
            boundary = (fighter.position - self.config.fighter_radius if fighter.facing > 0 else
                        self.config.arena_width - self.config.fighter_radius - fighter.position)
            if boundary <= 1e-9:
                return "no_room"
        return None

    def _movement_directions(self, fighter: Fighter) -> list[str]:
        minimum = self.config.fighter_radius
        maximum = self.config.arena_width - self.config.fighter_radius
        directions = []
        for direction in ("forward", "backward"):
            movement_direction = fighter.facing * (1 if direction == "forward" else -1)
            if movement_direction < 0 and fighter.position <= minimum + 1e-9:
                continue
            if movement_direction > 0 and fighter.position >= maximum - 1e-9:
                continue
            directions.append(direction)
        return directions

    def available_tools(self, fighter_id: str) -> dict:
        fighter = self.fighters[fighter_id]
        available, unavailable = {}, {}
        for name, spec in self.skills_for(fighter).items():
            reason = self._unavailable_reason(fighter, spec, name)
            if reason:
                unavailable[name] = reason
                continue
            properties = {}
            if spec.teleport:
                radius = self.config.fighter_radius
                enemy = self.opponent(fighter_id).position
                properties["position"] = {
                    "type": "number", "minimum": radius, "maximum": self.config.arena_width - radius,
                    "anyOf": [{"maximum": enemy - 2 * radius}, {"minimum": enemy + 2 * radius}],
                    "description": "Absolute horizontal landing coordinate. Can cross the opponent; cannot overlap them.",
                }
            elif name in ("move", "dash"):
                properties["direction"] = {
                    "type": "string", "enum": self._movement_directions(fighter),
                }
            character = self.config.characters.get(fighter_id)
            if character and character.agent:
                definition = next(tool for tool in character.agent.tools if tool.action == name)
                for key, values in definition.parameters.items():
                    if isinstance(values, list):
                        properties[key]["enum"] = [value for value in properties[key]["enum"] if value in values]
                if any("enum" in schema and not schema["enum"] for schema in properties.values()):
                    unavailable[name] = "no_valid_target"
                    continue
            available[name] = {
                "name": name,
                "parameters": {
                    "type": "object", "properties": properties,
                    "required": list(properties), "additionalProperties": False,
                },
                "metadata": spec.model_dump(),
            }
        available_items, unavailable_items = {}, {}
        for item in self.items_for(fighter).values():
            reason = self._item_unavailable_reason(fighter, item)
            if reason:
                unavailable_items[item.item_id] = reason
            else:
                available_items[item.item_id] = item.model_dump()
        if available_items:
            available["use_item"] = {
                "name": "use_item",
                "parameters": {
                    "type": "object",
                    "properties": {"item_id": {
                        "type": "string", "enum": list(available_items),
                        "description": "Consumable item to use.",
                    }},
                    "required": ["item_id"], "additionalProperties": False,
                },
                "metadata": {"items": available_items},
            }
        elif fighter.items:
            unavailable["use_item"] = "no_available_items"
        return {"available": available, "unavailable": unavailable,
                "items": {"available": available_items, "unavailable": unavailable_items}}

    def _fighter_view(self, fighter: Fighter) -> dict:
        active = fighter.active
        return {
            "fighter_id": fighter.fighter_id,
            "position": fighter.position, "facing": fighter.facing,
            "health": fighter.health, "stamina": fighter.stamina, "mana": fighter.mana,
            "action": self._action_view(fighter, active) if active else None,
            "ongoing_actions": [self._action_view(fighter, action) for action in fighter.lingering],
            "cooldowns": {name: min(self.skills_for(fighter)[name].cooldown_turns, until - self.turn_index)
                          for name, until in fighter.cooldowns.items()
                          if until > self.turn_index},
            "items": dict(fighter.items),
            "shield": fighter.shield,
            "shield_turns": max(0, fighter.shield_until_turn - self.turn_index) if fighter.shield else 0,
        }

    def _incoming_threats(self, fighter_id: str) -> list[dict]:
        target = self.fighters[fighter_id]
        attacker = self.opponent(fighter_id)
        threats = []
        for active in self._actions(attacker):
            if active.request.skill not in ATTACKS or active.attack_resolved:
                continue
            spec = self.skills_for(attacker)[active.request.skill]
            origin = active.projectile_origin if active.projectile_origin is not None else attacker.position
            distance = (target.position - origin) * active.facing
            in_path = 0 <= distance <= spec.reach + 1e-9
            impact_eta_ms = None
            if in_path:
                until_active = max(0, spec.windup_ms - active.elapsed_ms)
                if spec.projectile_speed:
                    progress = (0 if active.projectile_origin is None else
                                max(0.0, spec.projectile_speed
                                    * (active.elapsed_ms - spec.windup_ms) / 1000))
                    travel_ms = max(0.0, distance - progress) / spec.projectile_speed * 1000
                    impact_eta_ms = round(until_active + travel_ms)
                else:
                    impact_eta_ms = until_active
            threats.append({
                "actor": attacker.fighter_id,
                "action_id": active.action_id,
                "skill": active.request.skill,
                "phase": active.phase(spec),
                "damage": spec.health_damage,
                "reach": spec.reach,
                "distance_along_attack": round(distance, 3),
                "target_in_path": in_path,
                "earliest_impact_ms": impact_eta_ms,
                "projectile": bool(spec.projectile_speed),
            })
        return threats

    def observe(self, fighter_id: str) -> dict:
        fighter = self.fighters[fighter_id]
        opponent = self.opponent(fighter_id)
        return {
            "simulation_time": self.simulation_time,
            "turn": self.turn_index + 1,
            "self": self._fighter_view(fighter),
            "opponent": self._fighter_view(opponent),
            "distance": abs(fighter.position - opponent.position),
            "incoming_threats": self._incoming_threats(fighter_id),
            "rules": {
                **self.config.model_dump(),
                **{f"max_{resource}": self.config.resource_limit(fighter_id, resource)
                   for resource in ("health", "stamina", "mana")},
                "resource_limits": {
                    player: {resource: self.config.resource_limit(player, resource)
                             for resource in ("health", "stamina", "mana")}
                    for player in self.fighters
                },
                "skills": {
                    name: spec.model_dump()
                    for name, spec in self.skills_for(fighter).items()
                },
                "opponent_skills": {
                    name: spec.model_dump()
                    for name, spec in self.skills_for(opponent).items()
                },
            },
            "tools": self.available_tools(fighter_id),
            "events": [asdict(event) for event in self._recent_events
                       if event.status != "rejected" or event.actor == fighter_id],
        }

    def snapshot(self) -> dict:
        return {
            "tick": self.tick, "simulation_time": self.simulation_time,
            "turn": self.turn_index + 1,
            "fighters": {key: self._fighter_view(value)
                         for key, value in self.fighters.items()},
            "result": asdict(self.result) if self.result else None,
        }

    def _validate_action(self, fighter: Fighter, action: Action) -> str | None:
        if action.skill == "idle":
            return "invalid_parameters"
        if action.skill == "use_item":
            item = self.items_for(fighter).get(action.item_id)
            if item is None:
                return "item_not_equipped"
            return self._item_unavailable_reason(fighter, item)
        reason = self._unavailable_reason(fighter, self.skills_for(fighter)[action.skill], action.skill)
        if reason:
            return reason
        spec = self.skills_for(fighter)[action.skill]
        if spec.teleport != (action.position is not None):
            return "invalid_parameters"
        if spec.teleport:
            schema = self.available_tools(fighter.fighter_id)["available"][action.skill]["parameters"]["properties"]["position"]
            if not parameter_matches(action.position, schema):
                return "invalid_position"
        elif action.skill in ("move", "dash") and action.direction not in self._movement_directions(fighter):
            return "no_room"
        character = self.config.characters.get(fighter.fighter_id)
        if character and character.agent:
            definition = next(tool for tool in character.agent.tools if tool.action == action.skill)
            if any(isinstance(values, list) and getattr(action, key) not in values for key, values in definition.parameters.items()):
                return "invalid_target_or_direction"
        return None

    def advance(self, actions: Mapping[str, Action | dict],
                on_frame: Callable[[dict], None] | None = None,
                fallback_reasons: Mapping[str, str] | None = None) -> list[Event]:
        """Submit one decision batch, then advance at most one decision interval.

        Missing and invalid decisions fall back to an internal no-effect action. A new action can start
        once windup plus recovery has elapsed; any longer-lived effect continues
        independently. Every decision is validated before it is started.
        """
        if self.result is not None:
            raise RuntimeError("match is already finished")
        unknown = set(actions) - set(self.fighters)
        if unknown:
            raise ValueError(f"unknown fighters: {sorted(unknown)}")
        event_start = len(self.events)
        accepted: dict[str, Action] = {}
        cooldown_exempt: set[str] = set()
        for fighter_id, fighter in self.fighters.items():
            if not self.can_decide(fighter_id):
                if fighter_id in actions:
                    self._emit("rejected", fighter_id, reason="action_locked")
                continue
            if fighter.active is not None and fighter_id not in actions:
                continue
            action = None
            reason = "missing_action"
            if fighter_id in actions:
                try:
                    action = Action.model_validate(actions[fighter_id])
                    reason = self._validate_action(fighter, action)
                except (ValidationError, TypeError):
                    reason = "invalid_parameters"
            if reason:
                self._emit("rejected", fighter_id, reason=reason)
                action = idle()
                cooldown_exempt.add(fighter_id)
                self._emit("fallback", fighter_id, reason=reason, skill="idle")
            accepted[fighter_id] = action
        teleports = {player: action for player, action in accepted.items()
                     if action.position is not None}
        simultaneous_conflict = (
            len(teleports) == 2
            and len({self.action_spec(self.fighters[player], action).windup_ms
                     for player, action in teleports.items()}) == 1
            and abs(teleports["p1"].position - teleports["p2"].position)
            < 2 * self.config.fighter_radius - 1e-9
        )
        if simultaneous_conflict:
            # Resolve simultaneous landing conflicts symmetrically before charging either player.
            for player in teleports:
                self._emit("rejected", player, reason="teleport_conflict")
                self._emit("fallback", player, reason="teleport_conflict", skill="idle")
                accepted[player] = idle()
                cooldown_exempt.add(player)
        for fighter_id, action in accepted.items():
            if fallback_reasons and fighter_id in fallback_reasons:
                self._emit("fallback", fighter_id, reason=fallback_reasons[fighter_id], skill=action.skill)
            self._start(self.fighters[fighter_id], action,
                        apply_cooldown=fighter_id not in cooldown_exempt)
        self._resolve_teleports()
        if on_frame:
            on_frame(self.playback_frame())
        for _ in range(self.config.decision_ms // self.config.step_ms):
            self._step()
            if on_frame:
                on_frame(self.playback_frame())
            if self.result is not None:
                break
        # A completed decision cycle consumes exactly one cooldown turn, regardless
        # of simulation step count, wall-clock model latency, or playback speed.
        if self.result is None:
            self.turn_index += 1
            for fighter in self.fighters.values():
                if fighter.shield and self.turn_index >= fighter.shield_until_turn:
                    fighter.shield = 0
            if on_frame:
                on_frame(self.playback_frame())
        self._recent_events = self.events[event_start:]
        return deepcopy(self._recent_events)

    def playback_frame(self) -> dict:
        tools = {}
        for fighter_id in self.fighters:
            current = self.available_tools(fighter_id)
            tools[fighter_id] = {
                "available": list(current["available"]),
                "unavailable": current["unavailable"],
                "items": current["items"],
            }
        return {**self.snapshot(), "tools": tools, "event_count": len(self.events)}

    def _start(self, fighter: Fighter, action: Action, *, apply_cooldown: bool = True) -> None:
        if fighter.active is not None:
            fighter.lingering.append(fighter.active)
        self._action_sequence += 1
        facing = 1 if self.opponent(fighter.fighter_id).position > fighter.position else -1
        fighter.facing = facing
        direction = 0
        if action.direction:
            direction = facing * (1 if action.direction == "forward" else -1)
        spec = self.action_spec(fighter, action)
        stamina_cost = mana_cost = 0
        if action.skill == "idle":
            pass
        elif action.skill == "use_item":
            fighter.items[action.item_id] -= 1
        else:
            stamina_cost, mana_cost = spec.stamina_cost, spec.mana_cost
            fighter.stamina = max(0, fighter.stamina - stamina_cost)
            fighter.mana = max(0, fighter.mana - mana_cost)
            if apply_cooldown and spec.cooldown_turns:
                fighter.cooldowns[action.skill] = self.turn_index + spec.cooldown_turns + 1
        fighter.active = ActiveAction(
            action_id=f"a{self._action_sequence}", request=action,
            facing=facing, movement_direction=direction,
        )
        self._emit("started", fighter.fighter_id, fighter.active.action_id,
                   skill=action.skill, direction=action.direction,
                   **({"position": action.position} if action.position is not None else {}),
                   **({"item_id": action.item_id} if action.item_id else {}),
                   stamina_cost=stamina_cost, mana_cost=mana_cost)

    def _move_fighters(self) -> None:
        left, right = sorted(self.fighters.values(), key=lambda fighter: fighter.position)
        deltas = {}
        dt = self.config.step_ms / 1000
        for fighter in (left, right):
            delta = 0.0
            for active in self._actions(fighter):
                spec = self.action_spec(fighter, active.request)
                if active.phase(spec) == "active" and active.request.skill in ("move", "dash"):
                    delta += active.movement_direction * spec.speed * dt
            destination = min(self.config.arena_width - self.config.fighter_radius,
                              max(self.config.fighter_radius, fighter.position + delta))
            deltas[fighter.fighter_id] = destination - fighter.position
        dl, dr = deltas[left.fighter_id], deltas[right.fighter_id]
        gap = right.position - left.position
        minimum = 2 * self.config.fighter_radius
        if dl > dr and gap + dr - dl < minimum:
            # Move to contact together, then allow only shared non-pushing motion.
            fraction = max(0.0, min(1.0, (gap - minimum) / (dl - dr)))
            shared = dr if dl >= 0 and dr >= 0 else dl if dl <= 0 and dr <= 0 else 0
            dl = dl * fraction + shared * (1 - fraction)
            dr = dr * fraction + shared * (1 - fraction)
        left.position += dl
        right.position += dr

    def _resolve_teleports(self) -> None:
        pending = {}
        for player, fighter in self.fighters.items():
            for active in self._actions(fighter):
                if active.request.position is None or active.teleport_resolved:
                    continue
                spec = self.action_spec(fighter, active.request)
                if active.phase(spec) != "active":
                    continue
                active.teleport_resolved = True
                pending[player] = (fighter, active, active.request.position)
        if not pending:
            return

        destinations = {
            player: pending[player][2] if player in pending else fighter.position
            for player, fighter in self.fighters.items()
        }
        if abs(destinations["p1"] - destinations["p2"]) < 2 * self.config.fighter_radius - 1e-9:
            for player, (_, active, _) in pending.items():
                self._emit("rejected", player, active.action_id, "teleport_conflict")
            return

        origins = {player: fighter.position for player, fighter in self.fighters.items()}
        for player, (fighter, _, destination) in pending.items():
            fighter.position = destination
        for player, (fighter, active, destination) in pending.items():
            fighter.facing = 1 if self.opponent(player).position > fighter.position else -1
            active.facing = fighter.facing
            self._emit("teleported", player, active.action_id, skill=active.request.skill,
                       origin=origins[player], destination=destination)

    def _collect_hits(self, previous_positions: dict[str, float]) -> list[tuple[Fighter, Fighter, ActiveAction, float]]:
        hits = []
        for attacker in self.fighters.values():
            for active in self._actions(attacker):
                if active.request.skill not in ATTACKS or active.attack_resolved:
                    continue
                spec = self.skills_for(attacker)[active.request.skill]
                if active.phase(spec) != "active":
                    continue
                defender = self.opponent(attacker.fighter_id)
                if spec.projectile_speed and active.projectile_origin is None:
                    active.projectile_origin = attacker.position
                    self._emit("projectile", attacker.fighter_id, active.action_id,
                               skill=active.request.skill, origin=attacker.position,
                               facing=active.facing, speed=spec.projectile_speed, reach=spec.reach,
                               launched_at=(self.time_ms - self.config.step_ms) / 1000)
                if spec.projectile_speed:
                    # Sweep the projectile's fixed-step path to avoid tunneling between frames.
                    age = active.elapsed_ms - spec.windup_ms
                    start = spec.projectile_speed * age / 1000
                    end = min(spec.reach, spec.projectile_speed * (age + self.config.step_ms) / 1000)
                    distance = (defender.position - active.projectile_origin) * active.facing
                    before = (previous_positions[defender.fighter_id] - active.projectile_origin) * active.facing
                    if start > spec.reach or before < start - 1e-9 or distance > end + 1e-9:
                        continue
                else:
                    distance = (defender.position - attacker.position) * active.facing
                    if distance < 0 or distance > spec.reach + 1e-9:
                        continue
                health_damage = spec.health_damage
                guarding = next((action for action in self._actions(defender)
                                 if action.request.skill == "guard"
                                 and action.phase(self.skills_for(defender)["guard"]) == "active"
                                 and (attacker.position - defender.position) * action.facing > 0), None)
                if guarding:
                    health_damage *= self.config.guard_damage_multiplier
                active.hit = True
                active.attack_resolved = True
                hits.append((attacker, defender, active, health_damage))
        return hits

    def _step(self) -> None:
        self.tick += 1
        for fighter in self.fighters.values():
            if fighter.shield and self.turn_index >= fighter.shield_until_turn:
                fighter.shield = 0
            for active in self._actions(fighter):
                if (active.request.skill == "use_item" and not active.item_applied
                    and active.phase(self.action_spec(fighter, active.request)) == "active"):
                    self._apply_item(fighter, self.items_for(fighter)[active.request.item_id], active)
        self._resolve_teleports()
        previous_positions = {key: fighter.position for key, fighter in self.fighters.items()}
        self._move_fighters()
        for fighter in self.fighters.values():
            for active in self._actions(fighter):
                if (active.request.skill == "rest"
                    and active.phase(self.skills_for(fighter)["rest"]) == "active"):
                    spec = self.skills_for(fighter)["rest"]
                    fraction = self.config.step_ms / spec.active_ms
                    stamina_restore = (spec.stamina_restore if spec.stamina_restore is not None
                                       else self.config.rest_stamina_per_second * spec.active_ms / 1000)
                    mana_restore = (spec.mana_restore if spec.mana_restore is not None
                                    else self.config.rest_mana_per_second * spec.active_ms / 1000)
                    fighter.stamina = min(
                        self.config.resource_limit(fighter.fighter_id, "stamina"),
                        fighter.stamina + stamina_restore * fraction,
                    )
                    fighter.mana = min(self.config.resource_limit(fighter.fighter_id, "mana"), fighter.mana
                        + mana_restore * fraction)
        # Collect all hits against the same pre-damage state before applying any.
        hits = self._collect_hits(previous_positions)
        for attacker, defender, active, health_damage in hits:
            shield_absorbed = min(defender.shield, health_damage)
            defender.shield -= shield_absorbed
            health_damage -= shield_absorbed
            defender.health = max(0, defender.health - health_damage)
            self._emit("hit", attacker.fighter_id, active.action_id,
                       skill=active.request.skill,
                       defender=defender.fighter_id, health_damage=health_damage,
                       shield_absorbed=shield_absorbed)
        for fighter in self.fighters.values():
            for active in self._actions(fighter):
                spec = self.action_spec(fighter, active.request)
                active.elapsed_ms += self.config.step_ms
                if (active.request.skill in ATTACKS and not active.attack_resolved
                    and active.elapsed_ms >= spec.windup_ms + spec.active_ms):
                    active.attack_resolved = True
                    self._emit("missed", fighter.fighter_id, active.action_id, "out_of_range")
                if active.elapsed_ms >= spec.duration_ms:
                    self._emit("completed", fighter.fighter_id, active.action_id,
                               skill=active.request.skill,
                               **({"item_id": active.request.item_id} if active.request.item_id else {}))
                    if fighter.active is active:
                        fighter.active = None
                    else:
                        fighter.lingering.remove(active)
        self._check_result()

    def _apply_item(self, fighter: Fighter, item: ItemSpec, active: ActiveAction) -> None:
        effects = item.effects
        changes = {"item_id": item.item_id}
        for resource in ("health", "stamina", "mana"):
            amount = getattr(effects, f"{resource}_restore")
            if not amount:
                continue
            before = getattr(fighter, resource)
            limit = self.config.resource_limit(fighter.fighter_id, resource)
            setattr(fighter, resource, min(limit, before + amount))
            changes[f"{resource}_restore"] = getattr(fighter, resource) - before
        if effects.shield:
            fighter.shield += effects.shield
            fighter.shield_until_turn = self.turn_index + effects.shield_duration_turns
            changes.update(shield=effects.shield, shield_duration_turns=effects.shield_duration_turns)
        if effects.cooldown_reduction_turns:
            reduced = {}
            for skill, until in list(fighter.cooldowns.items()):
                next_until = max(self.turn_index, until - effects.cooldown_reduction_turns)
                reduced[skill] = until - next_until
                if next_until <= self.turn_index:
                    del fighter.cooldowns[skill]
                else:
                    fighter.cooldowns[skill] = next_until
            changes["cooldown_reduction_turns"] = effects.cooldown_reduction_turns
            changes["cooldowns_reduced"] = reduced
        if effects.backward_move:
            origin = fighter.position
            destination = origin - fighter.facing * effects.backward_move
            fighter.position = min(self.config.arena_width - self.config.fighter_radius,
                                   max(self.config.fighter_radius, destination))
            changes.update(origin=origin, destination=fighter.position)
        active.item_applied = True
        self._emit("item_used", fighter.fighter_id, active.action_id, **changes)

    def _check_result(self) -> None:
        alive = [fighter for fighter in self.fighters.values() if fighter.health > 0]
        winner = None
        reason = None
        if len(alive) < 2:
            winner = alive[0].fighter_id if alive else None
            reason = "knockout" if alive else "double_knockout"
        elif self.time_ms >= self.config.time_limit_ms:
            p1, p2 = self.fighters["p1"], self.fighters["p2"]
            if abs(p1.health - p2.health) > 1e-9:
                winner = p1.fighter_id if p1.health > p2.health else p2.fighter_id
            reason = "time_limit"
        if reason:
            self.result = MatchResult(winner, reason, self.simulation_time)
            self._emit("match_finished", reason=reason, winner=winner)
