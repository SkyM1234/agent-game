import json
import random
import subprocess
import sys

import pytest

from backend.agents.scripted import TestAgent
from backend.game import Action, Arena, GameConfig
from backend.game.skills import rest
from backend.matches import run_match


@pytest.mark.parametrize("positions", [(3, 9), (9, 3), (5, 6), (0.4, 11.6)])
def test_script_match_completes_with_legal_actions(positions):
    arena = Arena(GameConfig(starting_positions=positions))
    result = run_match(arena, {"p1": TestAgent(), "p2": TestAgent()})
    assert arena.result is not None
    assert arena.time_ms <= arena.config.time_limit_ms
    assert result["event_counts"].get("rejected", 0) == 0
    assert result["event_counts"].get("fallback", 0) == 0
    for fighter in arena.fighters.values():
        assert 0 <= fighter.health <= arena.config.max_health
        assert 0 <= fighter.stamina <= arena.config.max_stamina
        assert 0 <= fighter.mana <= arena.config.max_mana




def test_repeating_same_script_match_is_deterministic():
    results = []
    for _ in range(2):
        arena = Arena()
        summary = run_match(arena, {"p1": TestAgent(), "p2": TestAgent()})
        results.append((summary, arena.events))
    assert results[0] == results[1]


def test_agents_receive_same_time_without_pending_opponent_action():
    views = []

    class SpyAgent:
        def decide(self, observation):
            views.append(observation)
            return rest()

    arena = Arena(GameConfig(time_limit_ms=500))
    run_match(arena, {"p1": SpyAgent(), "p2": SpyAgent()})
    assert len(views) == 2
    assert views[0]["simulation_time"] == views[1]["simulation_time"] == 0
    assert all(view["opponent"]["action"] is None for view in views)


def test_agents_decide_every_turn_when_windup_plus_recovery_fits_one_turn():
    data = GameConfig(decision_ms=50, time_limit_ms=150, starting_positions=(3, 9)).model_dump()
    data["skills"]["jab"].update(windup_ms=0, active_ms=100, recovery_ms=50)
    config = GameConfig.model_validate(data)
    phases = {"p1": [], "p2": []}

    class PhaseAgent:
        def __init__(self, player):
            self.player = player

        def decide(self, observation):
            action = observation["self"]["action"]
            phases[self.player].append(action["phase"] if action else None)
            return Action(skill="jab") if action is None else rest()

    arena = Arena(config)
    run_match(arena, {player: PhaseAgent(player) for player in arena.fighters})

    assert phases == {"p1": [None, "active", "active"], "p2": [None, "active", "active"]}
    assert not [event for event in arena.events if event.status == "rejected"]


class RandomLegalAgent:
    def __init__(self, seed):
        self.random = random.Random(seed)

    def decide(self, observation):
        tools = observation["tools"]["available"]
        skill = self.random.choice(list(tools))
        params = {
            name: self.random.choice(schema["enum"])
            for name, schema in tools[skill]["parameters"]["properties"].items()
        }
        return Action(skill=skill, **params)


@pytest.mark.parametrize("seed", range(8))
def test_seeded_random_actions_preserve_invariants(seed):
    arena = Arena(GameConfig(starting_positions=(5, 6), decision_ms=100))

    def check_state(_events):
        left, right = sorted(arena.fighters.values(), key=lambda fighter: fighter.position)
        assert right.position - left.position >= 2 * arena.config.fighter_radius - 1e-9
        assert left.position >= arena.config.fighter_radius - 1e-9
        assert right.position <= arena.config.arena_width - arena.config.fighter_radius + 1e-9
        for fighter in (left, right):
            assert 0 <= fighter.stamina <= arena.config.max_stamina
            assert 0 <= fighter.health <= arena.config.max_health

    summary = run_match(arena, {"p1": RandomLegalAgent(seed), "p2": RandomLegalAgent(seed + 20)}, check_state)
    assert summary["event_counts"].get("rejected", 0) == 0


def test_script_finishes_with_empty_resources():
    arena = Arena(GameConfig(time_limit_ms=1000))
    for fighter in arena.fighters.values():
        fighter.stamina = fighter.mana = 0
    summary = run_match(arena, {"p1": TestAgent(), "p2": TestAgent()})
    assert summary["result"]["reason"] == "time_limit"
    assert summary["result"]["winner"] is None
    assert summary["event_counts"].get("rejected", 0) == 0


def test_cli_json_output_and_config():
    completed = subprocess.run(
        [sys.executable, "-m", "backend.matches", "--config", "configs/default.json", "--json"],
        check=True, capture_output=True, text=True,
    )
    summary = json.loads(completed.stdout)
    assert summary["result"]["reason"] in ("knockout", "double_knockout", "time_limit")
    assert summary["event_counts"].get("rejected", 0) == 0


def test_runner_requires_both_agents():
    with pytest.raises(ValueError, match="exactly one"):
        run_match(Arena(), {"p1": TestAgent()})
