import math

import pytest
from pydantic import ValidationError

from backend.game import Action, Arena, GameConfig
from backend.game.skills import dash, guard, heavy_punch, jab, kick, move, rest


def close_arena(**overrides):
    return Arena(GameConfig(starting_positions=(3, 4), decision_ms=50, **overrides))


def advance_to(arena, time_ms):
    while arena.time_ms < time_ms and arena.result is None:
        arena.advance({key: rest() for key, value in arena.fighters.items()
                       if value.active is None})


def events(arena, status, actor=None):
    return [event for event in arena.events
            if event.status == status and (actor is None or event.actor == actor)]








def test_guard_reduces_frontal_health_damage():
    arena = close_arena()
    arena.advance({"p1": jab(), "p2": guard()})
    advance_to(arena, 150)
    assert arena.fighters["p2"].health == 97
    assert arena.fighters["p2"].active.request.skill == "guard"
    assert events(arena, "hit")[0].effects["health_damage"] == 3


def test_guard_has_no_effect_from_behind():
    arena = close_arena()
    arena.advance({"p1": jab(), "p2": guard()})
    arena.fighters["p2"].active.facing = 1
    advance_to(arena, 150)
    assert arena.fighters["p2"].health == 90






def test_insufficient_stamina_rejects_without_applying_fallback_effects():
    arena = close_arena()
    arena.fighters["p1"].stamina = 7
    arena.advance({"p1": jab(), "p2": rest()})
    assert events(arena, "rejected")[0].reason == "insufficient_stamina"
    assert arena.fighters["p1"].stamina == 7
    assert arena.fighters["p2"].stamina == 100








@pytest.mark.parametrize("factory,reach,damage", [
    (jab, 1.3, 10), (heavy_punch, 1.5, 25), (kick, 2.1, 16),
])
@pytest.mark.parametrize("offset", [0, 0.000001])
def test_attack_reach_boundary(factory, reach, damage, offset):
    arena = Arena(GameConfig(starting_positions=(3, 3 + reach + offset)))
    arena.advance({"p1": factory(), "p2": rest()})
    assert arena.fighters["p2"].health == (100 - damage if offset == 0 else 100)
    assert len(events(arena, "hit")) == (1 if offset == 0 else 0)
    assert len(events(arena, "missed")) == (0 if offset == 0 else 1)
    assert arena.fighters["p1"].stamina < 100


def test_single_attack_cannot_hit_twice_during_long_active_window():
    data = GameConfig(starting_positions=(3, 4)).model_dump()
    data["skills"]["jab"]["active_ms"] = 600
    arena = Arena(GameConfig.model_validate(data))
    arena.advance({"p1": jab(), "p2": rest()})
    arena.advance({"p2": rest()})
    assert len(events(arena, "hit", "p1")) == 1
    assert arena.fighters["p2"].health == 90


def test_range_is_checked_at_impact_not_at_submission():
    arena = Arena(GameConfig(starting_positions=(3, 4.35)))
    arena.advance({"p1": jab(), "p2": move()})
    assert arena.fighters["p2"].health == 90


def test_simultaneous_knockout_is_draw_and_stops_early():
    arena = Arena(GameConfig(starting_positions=(3, 4), max_health=10))
    arena.advance({"p2": jab(), "p1": jab()})
    assert arena.result.winner is None
    assert arena.result.reason == "double_knockout"
    assert arena.time_ms == 150
    assert len(events(arena, "hit")) == 2
    with pytest.raises(RuntimeError, match="finished"):
        arena.advance({})




def test_knockout_has_priority_over_time_limit():
    arena = Arena(GameConfig(starting_positions=(3, 4), max_health=10, time_limit_ms=150))
    arena.advance({"p1": jab(), "p2": rest()})
    assert arena.result.winner == "p1"
    assert arena.result.reason == "knockout"


@pytest.mark.parametrize("health,winner", [(100, None), (90, "p2")])
def test_time_limit_uses_health_and_can_draw(health, winner):
    arena = close_arena(time_limit_ms=100)
    arena.fighters["p1"].health = health
    advance_to(arena, 100)
    assert arena.result.reason == "time_limit"
    assert arena.result.winner == winner
    assert arena.time_ms == 100


def test_locked_action_cannot_be_replaced_or_charged_again():
    arena = close_arena()
    arena.advance({"p1": heavy_punch(), "p2": rest()})
    first = arena.fighters["p1"].active
    assert arena.available_tools("p1")["available"] == {}
    arena.advance({"p1": move("backward")})
    assert arena.fighters["p1"].active is first
    assert arena.fighters["p1"].stamina == 80
    assert first.elapsed_ms == 100
    assert events(arena, "rejected", "p1")[-1].reason == "action_locked"


def test_lock_expiry_starts_a_new_action_without_interrupting_current_effect():
    data = GameConfig(starting_positions=(3, 6), decision_ms=50).model_dump()
    data["skills"]["jab"].update(windup_ms=50, active_ms=250, recovery_ms=100)
    arena = Arena(GameConfig.model_validate(data))
    arena.advance({"p1": jab(), "p2": rest()})
    current = arena.fighters["p1"].active
    assert current.phase(arena.config.skills["jab"]) == "active"
    assert not arena.can_decide("p1")
    arena.advance({})
    arena.advance({})
    assert arena.can_decide("p1")

    arena.advance({"p1": move("backward")})

    assert arena.fighters["p1"].active.request == move("backward")
    assert current in arena.fighters["p1"].lingering
    while current in arena.fighters["p1"].lingering:
        arena.advance({})
    assert len(events(arena, "missed", "p1")) == 1
    assert not events(arena, "rejected", "p1")


def test_windup_and_recovery_phases_still_lock_decisions():
    arena = close_arena()
    arena.advance({"p1": jab(), "p2": rest()})
    assert arena.fighters["p1"].active.phase(arena.config.skills["jab"]) == "windup"
    assert not arena.can_decide("p1")
    arena.advance({})
    assert arena.fighters["p1"].active.phase(arena.config.skills["jab"]) == "active"
    assert not arena.can_decide("p1")
    arena.advance({})
    assert arena.fighters["p1"].active.phase(arena.config.skills["jab"]) == "active"
    assert not arena.can_decide("p1")
    arena.advance({})
    assert arena.fighters["p1"].active.phase(arena.config.skills["jab"]) == "recovery"
    assert not arena.can_decide("p1")
    while arena.fighters["p1"].active is not None:
        arena.advance({})
    assert arena.can_decide("p1")


def test_movement_cannot_cross_fighters_or_push_stationary_opponent():
    arena = Arena(GameConfig(starting_positions=(3, 4)))
    arena.advance({"p1": dash(), "p2": rest()})
    assert arena.fighters["p2"].position == 4
    assert arena.fighters["p1"].position == pytest.approx(3.2)
    both = Arena(GameConfig(starting_positions=(3, 4)))
    both.advance({"p1": dash(), "p2": dash()})
    assert both.fighters["p1"].position == pytest.approx(3.1)
    assert both.fighters["p2"].position == pytest.approx(3.9)


def test_contact_allows_both_fighters_to_move_in_same_direction():
    arena = Arena(GameConfig(starting_positions=(3, 3.8)))
    arena.advance({"p1": move(), "p2": move("backward")})
    assert arena.fighters["p1"].position == pytest.approx(4)
    assert arena.fighters["p2"].position == pytest.approx(4.8)


def test_movement_stays_inside_walls():
    arena = Arena(GameConfig(starting_positions=(0.5, 11.5)))
    arena.advance({"p1": dash("backward"), "p2": dash("backward")})
    assert arena.fighters["p1"].position == 0.4
    assert arena.fighters["p2"].position == 11.6


def test_movement_toward_an_adjacent_wall_is_unavailable_and_rejected():
    arena = Arena(GameConfig(starting_positions=(0.4, 11.6)))
    for fighter in arena.fighters.values():
        fighter.stamina = 50
        fighter.mana = 50

    for player in ("p1", "p2"):
        tools = arena.available_tools(player)["available"]
        assert tools["move"]["parameters"]["properties"]["direction"]["enum"] == ["forward"]
        assert tools["dash"]["parameters"]["properties"]["direction"]["enum"] == ["forward"]

    arena.advance({"p1": move("backward"), "p2": move("backward")})

    assert arena.fighters["p1"].position == 0.4
    assert arena.fighters["p2"].position == 11.6
    assert all(fighter.stamina == 50 and fighter.mana == 50 for fighter in arena.fighters.values())
    assert [event.reason for event in events(arena, "rejected")] == ["no_room", "no_room"]
    assert len(events(arena, "fallback")) == 2
    assert all(event.effects["skill"] == "idle" for event in events(arena, "fallback"))


def test_position_and_facing_are_mirrored_fairly():
    left = Arena(GameConfig(starting_positions=(3, 4)))
    right = Arena(GameConfig(starting_positions=(9, 8)))
    for arena in (left, right):
        arena.advance({"p1": jab(), "p2": move()})
    for fighter_id in left.fighters:
        a, b = left.fighters[fighter_id], right.fighters[fighter_id]
        assert a.position == pytest.approx(12 - b.position)
        assert a.facing == -b.facing
        assert a.health == b.health


@pytest.mark.parametrize("tool_output", [
    {"skill": "teleport"}, {"skill": "move"},
    {"skill": "jab", "target": "head"},
    {"skill": "rest", "target": "torso"},
    {"skill": "move", "direction": "up"},
    {"skill": "jab", "target": "torso", "damage": 999},
    {"skill": "jab", "target": "torso", "direction": "forward"},
    None, "jab(torso)",
])
def test_invalid_tool_output_falls_back_without_damaging_opponent(tool_output):
    arena = close_arena()
    arena.fighters["p1"].stamina = 50
    arena.fighters["p1"].mana = 50
    arena.advance({"p1": tool_output, "p2": rest()})
    assert events(arena, "rejected", "p1")[0].reason == "invalid_parameters"
    assert len(events(arena, "fallback", "p1")) == 1
    assert events(arena, "fallback", "p1")[0].effects["skill"] == "idle"
    assert arena.fighters["p1"].stamina == 50
    assert arena.fighters["p1"].mana == 50
    assert arena.fighters["p2"].health == 100


def test_unknown_fighter_rejected_before_any_state_mutation():
    arena = Arena()
    before = arena.snapshot()
    with pytest.raises(ValueError, match="unknown fighters"):
        arena.advance({"p1": jab(), "p3": rest()})
    assert arena.snapshot() == before
    assert arena.events == []


def test_missing_action_falls_back_without_applying_effects():
    arena = close_arena()
    for fighter in arena.fighters.values():
        fighter.stamina = 50
        fighter.mana = 50
    arena.advance({})
    assert len(events(arena, "fallback")) == 2
    assert all(event.reason == "missing_action" for event in events(arena, "rejected"))
    assert all(event.effects["skill"] == "idle" for event in events(arena, "fallback"))
    assert all(fighter.stamina == 50 and fighter.mana == 50 for fighter in arena.fighters.values())


def test_observation_and_returned_events_do_not_expose_mutable_state():
    arena = close_arena()
    view = arena.observe("p1")
    view["self"]["mana"] = 0
    view["rules"]["skills"]["jab"]["health_damage"] = 999
    view["tools"]["available"].clear()
    assert arena.fighters["p1"].mana == 100
    assert arena.config.skills["jab"].health_damage == 10
    batch = arena.advance({"p1": rest(), "p2": rest()})
    batch[0].effects["skill"] = "unknown"
    assert arena.events[0].effects["skill"] == "rest"


@pytest.mark.parametrize("updates", [
    {"step_ms": 0}, {"decision_ms": 75}, {"time_limit_ms": 125},
    {"starting_positions": (0, 9)}, {"starting_positions": (3, 3.5)},
    {"starting_positions": (math.inf, 9)}, {"max_health": math.nan},
    {"arena_width": 0}, {"guard_damage_multiplier": 2}, {"unknown": 1},
])
def test_invalid_config_fails_early(updates):
    with pytest.raises(ValidationError):
        GameConfig(**updates)


@pytest.mark.parametrize("skill,field,value", [
    ("jab", "active_ms", 75), ("rest", "stamina_cost", 1),
    ("move", "cooldown_turns", 1),
    ("jab", "mana_cost", -1), ("jab", "cooldown_turns", -1), ("jab", "cooldown_turns", 1.5),
])
def test_invalid_skill_config_fails_early(skill, field, value):
    data = GameConfig().model_dump()
    data["skills"][skill][field] = value
    with pytest.raises(ValidationError):
        GameConfig.model_validate(data)


def test_skill_config_requires_complete_registry():
    data = GameConfig().model_dump()
    del data["skills"]["rest"]
    with pytest.raises(ValidationError):
        GameConfig.model_validate(data)


def test_action_schema_rejects_extra_parameters():
    with pytest.raises(ValidationError):
        Action(skill="guard", damage=100)
