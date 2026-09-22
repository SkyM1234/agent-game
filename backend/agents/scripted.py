from __future__ import annotations

from typing import Protocol

from backend.game.models import Action, parameter_matches
from backend.game.skills import rest, teleport
from .planning import CounterfactualRolloutAgent
from .heuristic import HeuristicAgent


class Agent(Protocol):
    def decide(self, observation: dict) -> Action | dict: ...


def teleport_for_test(observation: dict) -> Action:
    tool = observation["tools"]["available"]["dash"]
    schema = tool["parameters"]["properties"]["position"]
    own, enemy = observation["self"]["position"], observation["opponent"]["position"]
    facing = 1 if enemy > own else -1
    preferred = enemy - facing * 2.5
    preferred = min(schema["maximum"], max(schema["minimum"], preferred))
    for position in (preferred, own, schema["minimum"], schema["maximum"]):
        if parameter_matches(position, schema):
            return teleport(position)
    return rest()


def action_for_test(observation: dict, skill: str) -> Action:
    tool = observation["tools"]["available"][skill]
    properties = tool["parameters"]["properties"]
    if "position" in properties:
        return teleport_for_test(observation)
    if "direction" in properties:
        directions = properties["direction"]["enum"]
        direction = "forward" if "forward" in directions else directions[0]
        return Action(skill=skill, direction=direction)
    return Action(skill=skill)


class TestAgent:
    """Cycle through every configured skill and equipped item to exercise their effects."""

    __test__ = False

    def __init__(self):
        self._skills: list[str] = []
        self._pending_items: list[str] = []
        self._skill_cursor = 0
        self._try_items = False

    def export_state(self) -> dict:
        return {
            "skills": self._skills, "pending_items": self._pending_items,
            "skill_cursor": self._skill_cursor, "try_items": self._try_items,
        }

    def restore_state(self, state: dict) -> None:
        self._skills = list(state.get("skills", []))
        self._pending_items = list(state.get("pending_items", []))
        self._skill_cursor = int(state.get("skill_cursor", 0))
        self._try_items = bool(state.get("try_items", False))

    def decide(self, observation: dict) -> Action:
        if not self._skills:
            self._skills = list(observation["rules"]["skills"])
            self._pending_items = list(observation["self"]["items"])
        available = observation["tools"]["available"]
        available_items = observation["tools"].get("items", {}).get("available", {})
        if self._try_items:
            for item_id in self._pending_items:
                if item_id in available_items:
                    self._pending_items.remove(item_id)
                    return Action(skill="use_item", item_id=item_id)
            self._try_items = False
        for _ in self._skills:
            skill = self._skills[self._skill_cursor]
            self._skill_cursor = (self._skill_cursor + 1) % len(self._skills)
            if skill in available:
                self._try_items = True
                return action_for_test(observation, skill)
        return rest()


AGENTS = {"test": TestAgent, "heuristic": HeuristicAgent, "counterfactual": CounterfactualRolloutAgent}
