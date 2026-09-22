"""Risk-weighted counterfactual rollouts over isolated authoritative arenas."""

from __future__ import annotations

from statistics import fmean
from typing import Literal

from pydantic import Field

from backend.game.engine import Arena
from backend.game.models import ATTACKS, Action, ItemSpec, Schema, parameter_matches

Player = Literal["p1", "p2"]


class UtilityWeights(Schema):
    damage_dealt: float = 1.0
    damage_received: float = 1.2
    stamina: float = 0.06
    mana: float = 0.09
    shield: float = 0.65
    position: float = 0.45
    cooldown: float = 0.8
    pending_effect: float = 0.55
    initiative: float = 1.5
    item_reserve: float = 0.25
    rejection: float = 20.0
    risk_aversion: float = Field(default=0.7, ge=0, le=1)


class BranchMetrics(Schema):
    damage_dealt: float
    damage_received: float
    stamina_delta: float
    mana_delta: float
    shield_delta: float
    distance_before: float
    distance_after: float
    position_value_delta: float
    cooldown_burden_delta: float
    pending_effect_delta: float
    initiative: int
    elapsed_ms: int
    ready_players: list[str]
    ongoing_actions: dict[str, int]
    items_spent: dict[str, int]
    opponent_items_spent: dict[str, int]
    rejected_actions: int


class BranchOutcome(Schema):
    own_action: Action
    opponent_action: Action | None
    score: float
    metrics: BranchMetrics
    terminal_result: Literal["win", "loss", "draw"] | None = None
    terminal_reason: str | None = None


class ActionEvaluation(Schema):
    action: Action
    risk_weighted_score: float
    worst_score: float
    mean_score: float
    best_score: float
    worst_response: Action | None
    worst_metrics: BranchMetrics
    terminal_outcomes: dict[str, int] = Field(default_factory=dict)
    terminal_reasons: dict[str, int] = Field(default_factory=dict)


class RolloutAnalysis(Schema):
    player: str
    generated_by: str = "engine_counterfactual_rollout"
    horizon: str = "next_decision_event"
    risk_aversion: float
    selected_action: Action
    evaluations: list[ActionEvaluation]
    payoff_matrix: list[BranchOutcome]

    @property
    def selected_evaluation(self) -> ActionEvaluation:
        return next(item for item in self.evaluations if item.action == self.selected_action)

    @staticmethod
    def terminal_status(item: ActionEvaluation) -> str:
        outcomes = item.terminal_outcomes
        total = sum(outcomes.values())
        if not total or outcomes.get("non_terminal") == total:
            return "none"
        for result in ("win", "loss", "draw"):
            if outcomes.get(result) == total:
                return f"forced_{result}"
        return "mixed"

    @property
    def selected_terminal_status(self) -> str:
        return self.terminal_status(self.selected_evaluation)

    @property
    def selected_is_forced_knockout(self) -> bool:
        evaluation = self.selected_evaluation
        total = sum(evaluation.terminal_outcomes.values())
        knockout_count = sum(
            count for reason, count in evaluation.terminal_reasons.items()
            if reason in ("knockout", "double_knockout")
        )
        return self.selected_terminal_status.startswith("forced_") and knockout_count == total

    def prompt_summary(self, detail_limit: int = 3, *, model_facing: bool = False) -> dict:
        ranked = sorted(self.evaluations, key=evaluation_key, reverse=True)

        def compact(action: Action | None):
            return action.model_dump(exclude_none=True) if action else {"skill": "continue"}

        def compact_metrics(metrics: BranchMetrics) -> dict:
            values = {
                "damage_dealt": metrics.damage_dealt,
                "damage_received": metrics.damage_received,
                "stamina_delta": metrics.stamina_delta,
                "mana_delta": metrics.mana_delta,
                "shield_delta": metrics.shield_delta,
                "distance_after": metrics.distance_after,
                "position_value_delta": metrics.position_value_delta,
                "cooldown_burden_delta": metrics.cooldown_burden_delta,
                "pending_effect_delta": metrics.pending_effect_delta,
                "initiative": metrics.initiative,
                "rejected_actions": metrics.rejected_actions,
            }
            return {key: round(value, 3) for key, value in values.items()
                    if value or key in ("damage_dealt", "damage_received", "distance_after")}

        summary = {
            "generated_by": self.generated_by,
            "horizon": self.horizon,
            "higher_score_is_better": True,
            "risk_aversion": self.risk_aversion,
            "recommended_action": compact(self.selected_action),
            "recommended_risk_weighted_score": round(
                self.selected_evaluation.risk_weighted_score, 3,
            ),
            "recommended_terminal_status": self.selected_terminal_status,
            "candidates": [{
                "action": compact(item.action),
                "risk_weighted_score": round(item.risk_weighted_score, 3),
                "worst_score": round(item.worst_score, 3),
                "mean_score": round(item.mean_score, 3),
                "worst_response": compact(item.worst_response),
                "terminal_status": self.terminal_status(item),
                **({"terminal_outcomes": {
                    key: value for key, value in item.terminal_outcomes.items()
                    if value
                }} if any(key != "non_terminal" and value
                          for key, value in item.terminal_outcomes.items()) else {}),
            } for item in ranked],
            "detailed_worst_cases": [{
                "action": compact(item.action),
                "metrics": compact_metrics(item.worst_metrics),
            } for item in ranked[:detail_limit]],
        }
        if len(ranked) > 1:
            summary["recommended_score_gap"] = round(
                ranked[0].risk_weighted_score - ranked[1].risk_weighted_score, 3,
            )
        if model_facing:
            minimum = min(item.risk_weighted_score for item in ranked)
            maximum = max(item.risk_weighted_score for item in ranked)

            def preference(item: ActionEvaluation) -> float:
                if maximum == minimum:
                    return 100.0
                return round(100 * (item.risk_weighted_score - minimum) / (maximum - minimum), 1)

            summary = {
                "generated_by": self.generated_by,
                "horizon": self.horizon,
                "higher_preference_is_better": True,
                "preference_is_relative_to_current_candidates": True,
                "risk_aversion": self.risk_aversion,
                "candidates": [{
                    "action": compact(item.action),
                    "preference": preference(item),
                    "worst_response": compact(item.worst_response),
                    "terminal_status": self.terminal_status(item),
                    **({"terminal_outcomes": {
                        key: value for key, value in item.terminal_outcomes.items() if value
                    }} if any(key != "non_terminal" and value
                              for key, value in item.terminal_outcomes.items()) else {}),
                } for item in sorted(self.evaluations, key=lambda item: action_key(item.action))],
                "detailed_worst_cases": summary["detailed_worst_cases"],
            }
        return summary


def action_key(action: Action | None) -> str:
    if action is None:
        return "continue"
    detail = action.item_id or action.direction
    if action.position is not None:
        detail = f"{action.position:.3f}"
    return action.skill + (f"({detail})" if detail else "")


def evaluation_key(item: ActionEvaluation) -> tuple:
    return (
        item.risk_weighted_score,
        item.mean_score,
        -len(action_key(item.action)),
        action_key(item.action),
    )


class CounterfactualRollout:
    def __init__(self, weights: UtilityWeights | None = None, *, max_rollout_ms: int = 5000):
        self.weights = weights or UtilityWeights()
        self.max_rollout_ms = max_rollout_ms

    def analyze(self, arena: Arena, player: Player) -> RolloutAnalysis:
        if not arena.can_decide(player):
            raise ValueError(f"{player} cannot decide in the current state")
        opponent = "p2" if player == "p1" else "p1"
        own_actions = self.candidate_actions(arena, player)
        opponent_actions = (self.candidate_actions(arena, opponent)
                            if arena.can_decide(opponent) else [None])
        evaluations = []
        payoff_matrix = []
        for own_action in own_actions:
            outcomes = [self.evaluate_branch(arena, player, own_action, response)
                        for response in opponent_actions]
            payoff_matrix.extend(outcomes)
            worst = min(outcomes, key=lambda item: (item.score, action_key(item.opponent_action)))
            scores = [item.score for item in outcomes]
            mean = fmean(scores)
            risk_weighted = (self.weights.risk_aversion * worst.score
                             + (1 - self.weights.risk_aversion) * mean)
            evaluations.append(ActionEvaluation(
                action=own_action,
                risk_weighted_score=risk_weighted,
                worst_score=worst.score,
                mean_score=mean,
                best_score=max(scores),
                worst_response=worst.opponent_action,
                worst_metrics=worst.metrics,
                terminal_outcomes={
                    result: sum((outcome.terminal_result or "non_terminal") == result
                                for outcome in outcomes)
                    for result in ("win", "loss", "draw", "non_terminal")
                },
                terminal_reasons={
                    reason: sum(outcome.terminal_reason == reason for outcome in outcomes)
                    for reason in ("knockout", "double_knockout", "time_limit")
                },
            ))
        # Position skills sample several coordinates, but expose only their strongest
        # risk-weighted result as one tactical action to both agent paths.
        best_positions: dict[str, ActionEvaluation] = {}
        fixed_evaluations = []
        for evaluation in evaluations:
            if evaluation.action.position is None:
                fixed_evaluations.append(evaluation)
                continue
            current = best_positions.get(evaluation.action.skill)
            if current is None or evaluation_key(evaluation) > evaluation_key(current):
                best_positions[evaluation.action.skill] = evaluation
        evaluations = sorted(
            [*fixed_evaluations, *best_positions.values()],
            key=lambda item: action_key(item.action),
        )
        selected = max(evaluations, key=evaluation_key)
        return RolloutAnalysis(
            player=player,
            risk_aversion=self.weights.risk_aversion,
            selected_action=selected.action,
            evaluations=evaluations,
            payoff_matrix=payoff_matrix,
        )

    def candidate_actions(self, arena: Arena, player: Player) -> list[Action]:
        tools = arena.available_tools(player)["available"]
        actions: list[Action] = []
        for skill, tool in tools.items():
            properties = tool["parameters"]["properties"]
            if "item_id" in properties:
                actions.extend(Action(skill="use_item", item_id=item_id)
                               for item_id in properties["item_id"]["enum"])
            elif "direction" in properties:
                actions.extend(Action(skill=skill, direction=direction)
                               for direction in properties["direction"]["enum"])
            elif "position" in properties:
                actions.extend(Action(skill=skill, position=position)
                               for position in self._position_candidates(arena, player, properties["position"]))
            else:
                actions.append(Action(skill=skill))
        unique = {action_key(action): action for action in actions}
        return [unique[key] for key in sorted(unique)]

    def evaluate_branch(self, arena: Arena, player: Player, own_action: Action,
                        opponent_action: Action | None) -> BranchOutcome:
        opponent = "p2" if player == "p1" else "p1"
        branch = arena.clone()
        before = arena.snapshot()
        event_start = len(branch.events)
        actions = {player: own_action}
        if opponent_action is not None:
            actions[opponent] = opponent_action
        start_time = branch.time_ms
        branch.advance(actions)
        while (branch.result is None
               and not any(branch.can_decide(actor) for actor in branch.fighters)
               and branch.time_ms - start_time < self.max_rollout_ms):
            branch.advance({})
        after = branch.snapshot()
        events = branch.events[event_start:]
        own_before, enemy_before = before["fighters"][player], before["fighters"][opponent]
        own_after, enemy_after = after["fighters"][player], after["fighters"][opponent]
        items_spent = {
            item_id: count - own_after["items"].get(item_id, 0)
            for item_id, count in own_before["items"].items()
            if count > own_after["items"].get(item_id, 0)
        }
        opponent_items_spent = {
            item_id: count - enemy_after["items"].get(item_id, 0)
            for item_id, count in enemy_before["items"].items()
            if count > enemy_after["items"].get(item_id, 0)
        }
        pending_before = self._pending_balance(arena, player)
        pending_after = self._pending_balance(branch, player)
        ready = [actor for actor in branch.fighters if branch.result is None and branch.can_decide(actor)]
        initiative = int(player in ready) - int(opponent in ready)
        metrics = BranchMetrics(
            damage_dealt=enemy_before["health"] - enemy_after["health"],
            damage_received=own_before["health"] - own_after["health"],
            stamina_delta=own_after["stamina"] - own_before["stamina"],
            mana_delta=own_after["mana"] - own_before["mana"],
            shield_delta=own_after["shield"] - own_before["shield"],
            distance_before=abs(own_before["position"] - enemy_before["position"]),
            distance_after=abs(own_after["position"] - enemy_after["position"]),
            position_value_delta=self._position_value(branch, player) - self._position_value(arena, player),
            cooldown_burden_delta=self._cooldown_burden(branch, player) - self._cooldown_burden(arena, player),
            pending_effect_delta=pending_after - pending_before,
            initiative=initiative,
            elapsed_ms=branch.time_ms - start_time,
            ready_players=ready,
            ongoing_actions={actor: len(branch.fighters[actor].lingering)
                             + int(branch.fighters[actor].active is not None)
                             for actor in branch.fighters},
            items_spent=items_spent,
            opponent_items_spent=opponent_items_spent,
            rejected_actions=sum(event.status == "rejected" and event.actor == player
                                 for event in events),
        )
        score = self._score(arena, branch, player, metrics)
        terminal_result = None
        if branch.result:
            terminal_result = ("win" if branch.result.winner == player else
                               "draw" if branch.result.winner is None else "loss")
        return BranchOutcome(own_action=own_action, opponent_action=opponent_action,
                             score=score, metrics=metrics, terminal_result=terminal_result,
                             terminal_reason=branch.result.reason if branch.result else None)

    def _score(self, before: Arena, after: Arena, player: Player, metrics: BranchMetrics) -> float:
        weights = self.weights
        opponent = "p2" if player == "p1" else "p1"
        own_health = before.fighters[player].health
        opponent_health = before.fighters[opponent].health
        damage_received_weight = self._health_scaled_weight(
            weights.damage_received,
            own_health,
            before.config.resource_limit(player, "health"),
        )
        damage_dealt_weight = self._health_scaled_weight(
            weights.damage_dealt,
            opponent_health,
            before.config.resource_limit(opponent, "health"),
        )
        score = (
            metrics.damage_dealt * damage_dealt_weight
            - metrics.damage_received * damage_received_weight
            + metrics.stamina_delta * weights.stamina
            + metrics.mana_delta * weights.mana
            + metrics.shield_delta * weights.shield
            + metrics.position_value_delta * weights.position
            - metrics.cooldown_burden_delta * weights.cooldown
            + metrics.pending_effect_delta * weights.pending_effect
            + metrics.initiative * weights.initiative
            - metrics.rejected_actions * weights.rejection
        )
        item_specs = after.items_for(after.fighters[player])
        score -= sum(self._item_value(item_specs[item_id]) * count * weights.item_reserve
                     for item_id, count in metrics.items_spent.items())
        opponent_item_specs = after.items_for(after.fighters[opponent])
        score += sum(self._item_value(opponent_item_specs[item_id]) * count * weights.item_reserve
                     for item_id, count in metrics.opponent_items_spent.items())
        if after.result:
            if after.result.winner == player:
                score += 10_000
            elif after.result.winner is not None:
                score -= 10_000
        return score

    @staticmethod
    def _health_scaled_weight(base_weight: float, health: float, maximum: float) -> float:
        health_ratio = min(1.0, max(0.0, health / maximum))
        return base_weight * (2.0 - health_ratio)

    def _position_candidates(self, arena: Arena, player: Player, schema: dict) -> list[float]:
        fighter = arena.fighters[player]
        enemy = arena.opponent(player)
        minimum, maximum = schema["minimum"], schema["maximum"]
        radius = arena.config.fighter_radius
        facing = 1 if enemy.position > fighter.position else -1
        values = [minimum, maximum, fighter.position]
        for interval in schema.get("anyOf", []):
            low = max(minimum, interval.get("minimum", minimum))
            high = min(maximum, interval.get("maximum", maximum))
            if low <= high:
                values.extend((low, high, (low + high) / 2))
        for spec in arena.skills_for(fighter).values():
            if spec.health_damage:
                values.append(enemy.position - facing * max(2 * radius, spec.reach * 0.9))
        values.append(enemy.position + facing * (2 * radius + 1e-6))
        legal = []
        for value in values:
            value = round(min(maximum, max(minimum, value)), 6)
            if parameter_matches(value, schema) and all(abs(value - other) > 1e-6 for other in legal):
                legal.append(value)
        legal.sort(key=lambda value: (abs(value - fighter.position), value))
        return legal[:5]

    def _position_value(self, arena: Arena, player: Player) -> float:
        fighter = arena.fighters[player]
        distance = abs(fighter.position - arena.opponent(player).position)
        attacks = [spec.reach for name, spec in arena.skills_for(fighter).items()
                   if name in ATTACKS and spec.health_damage > 0]
        preferred = max(attacks, default=distance)
        return -abs(distance - preferred)

    @staticmethod
    def _cooldown_burden(arena: Arena, player: Player) -> float:
        return sum(max(0, until - arena.turn_index)
                   for until in arena.fighters[player].cooldowns.values())

    def _pending_balance(self, arena: Arena, player: Player) -> float:
        opponent = "p2" if player == "p1" else "p1"
        return self._pending_pressure(arena, player) - self._pending_pressure(arena, opponent)

    @staticmethod
    def _pending_pressure(arena: Arena, player: Player) -> float:
        fighter = arena.fighters[player]
        enemy = arena.opponent(player)
        pressure = 0.0
        for active in arena._actions(fighter):
            if active.request.skill not in ATTACKS or active.attack_resolved:
                continue
            spec = arena.skills_for(fighter)[active.request.skill]
            phase = active.phase(spec)
            timing = 0.35 if phase == "windup" else 0.7 if phase == "active" else 0
            distance = (enemy.position - fighter.position) * active.facing
            spatial = 0.0 if distance < 0 else max(0.0, 1 - max(0.0, distance - spec.reach) / max(spec.reach, 1e-6))
            pressure += spec.health_damage * timing * spatial
        return pressure

    @staticmethod
    def _item_value(item: ItemSpec) -> float:
        effects = item.effects
        return (effects.health_restore
                + effects.stamina_restore * 0.06
                + effects.mana_restore * 0.09
                + effects.shield * 0.65
                + effects.cooldown_reduction_turns * 5
                + effects.backward_move * 1.5)


class CounterfactualRolloutAgent:
    def __init__(self, weights: UtilityWeights | None = None):
        self.rollout = CounterfactualRollout(weights)
        self.last_analysis: RolloutAnalysis | None = None

    def decide_in_arena(self, arena: Arena, player: Player) -> Action:
        self.last_analysis = self.rollout.analyze(arena, player)
        return self.last_analysis.selected_action

    def decide(self, observation: dict) -> Action:
        raise RuntimeError("CounterfactualRolloutAgent requires the authoritative arena")
