import asyncio
import json

import httpx
import pytest

from backend.agents.deepseek import (
    DeepSeekSettings, build_tools, parse_tool_response, retry_instruction,
)
from backend.agents.scripted import TestAgent
from backend.game import Action, Arena, GameConfig
from backend.game.characters import with_characters
from backend.game.models import parameter_matches
from backend.game.skills import dash, heavy_punch, move, rest, teleport
from backend.matches import run_match
from backend.matches.live import record_live_match
from backend.replay.recording import decode_recording, encode_recording


def arena_for(mirror=False, **config):
    return Arena(with_characters(GameConfig(**config), {
        "p1": "frost_bell" if mirror else "crimson_blade", "p2": "frost_bell"}))


def arena_with_teleport_windup(windup_ms):
    config = with_characters(GameConfig(), {"p1": "crimson_blade", "p2": "frost_bell"})
    data = config.model_dump()
    data["characters"]["p2"]["skills"]["dash"]["windup_ms"] = windup_ms
    return Arena(GameConfig.model_validate(data))


def mirror_arena_with_teleport_windups(p1_windup_ms, p2_windup_ms):
    config = with_characters(GameConfig(), {"p1": "frost_bell", "p2": "frost_bell"})
    data = config.model_dump()
    data["characters"]["p1"]["skills"]["dash"]["windup_ms"] = p1_windup_ms
    data["characters"]["p2"]["skills"]["dash"]["windup_ms"] = p2_windup_ms
    return Arena(GameConfig.model_validate(data))


def response(**arguments):
    return {"choices": [{"message": {"tool_calls": [{"type": "function", "function": {
        "name": "frost_teleport", "arguments": json.dumps({"decision_summary": "传送到对手身后。", **arguments})}}]}}]}


def test_teleport_tools_expose_coordinate_ranges_and_reject_old_direction():
    arena = arena_for()
    tools = {t["function"]["name"]: t["function"] for t in build_tools(arena.observe("p2"))}
    assert "frost_slide" not in tools
    schema = tools["frost_teleport"]["parameters"]["properties"]["position"]
    assert schema["type"] == "number"
    assert schema["minimum"] == .4 and schema["maximum"] == 11.6
    assert parameter_matches(1.5, schema)
    assert not parameter_matches(3, schema)
    action, _ = parse_tool_response(response(position=1.5), arena.observe("p2"))
    assert action == teleport(1.5)
    with pytest.raises(ValueError):
        parse_tool_response(response(direction="backward"), arena.observe("p2"))


def test_retry_explains_current_teleport_coordinate_ranges():
    observation = arena_for().observe("p2")

    occupied = retry_instruction(
        response(position=3), "invalid_target_or_direction", observation,
    )
    missing = retry_instruction(
        response(), "invalid_arguments", observation,
    )

    assert "position=3 is invalid" in occupied
    assert "a finite number; in [0.4, 11.6]" in occupied
    assert "in one allowed interval: <= 2.2 or >= 3.8" in occupied
    assert "missing position" in missing


def test_teleport_crosses_opponent_after_windup_with_no_swept_movement_or_extra_charge():
    arena = arena_for()
    spec = arena.config.characters["p2"].skills["dash"]
    initial_mana = arena.config.resource_limit("p2", "mana")
    initial_stamina = arena.config.resource_limit("p2", "stamina")
    frames = []
    arena.advance({"p1": rest(), "p2": teleport(1.5)}, on_frame=frames.append)
    assert frames[0]["simulation_time"] == 0
    assert frames[0]["fighters"]["p2"]["position"] == 9
    assert all(frame["fighters"]["p2"]["position"] == 9
               for frame in frames if frame["simulation_time"] <= spec.windup_ms / 1000)
    landing = next(frame for frame in frames if frame["fighters"]["p2"]["position"] == 1.5)
    assert landing["simulation_time"] == (spec.windup_ms + arena.config.step_ms) / 1000
    assert all(frame["fighters"]["p2"]["position"] == 1.5
               for frame in frames if frame["simulation_time"] >= landing["simulation_time"])
    assert arena.fighters["p1"].position == 3
    assert arena.fighters["p2"].facing == 1
    assert arena.fighters["p2"].mana == initial_mana - spec.mana_cost
    assert arena.fighters["p2"].stamina == initial_stamina - spec.stamina_cost
    events = [e for e in arena.events if e.status == "teleported"]
    assert len(events) == 1
    assert events[0].simulation_time == landing["simulation_time"]
    assert events[0].effects == {"skill": "dash", "origin": 9, "destination": 1.5}
    assert arena.available_tools("p2")["unavailable"]["dash"] == "cooldown"
    for _ in range(spec.cooldown_turns):
        arena.advance({"p1": rest(), "p2": rest()})
    assert "dash" in arena.available_tools("p2")["available"]


def test_zero_windup_teleport_remains_immediate():
    arena = arena_with_teleport_windup(0)
    frames = []
    arena.advance({"p1": rest(), "p2": teleport(1.5)}, on_frame=frames.append)
    assert frames[0]["simulation_time"] == 0
    assert frames[0]["fighters"]["p2"]["position"] == 1.5
    events = [event for event in arena.events if event.status == "teleported"]
    assert len(events) == 1
    assert events[0].simulation_time == 0


def test_delayed_teleport_checkpoint_resumes_before_and_after_landing():
    arena = arena_for(decision_ms=50)
    arena.advance({"p1": rest(), "p2": teleport(1.5)})
    assert arena.fighters["p2"].position == 9

    restored = Arena.from_state(arena.config, arena.export_state())
    for _ in range(4):
        restored.advance({})
    assert restored.fighters["p2"].position == 1.5
    assert len([event for event in restored.events if event.status == "teleported"]) == 1

    legacy_state = restored.export_state()
    del legacy_state["fighters"]["p2"]["active"]["teleport_resolved"]
    legacy = Arena.from_state(restored.config, legacy_state)
    legacy.advance({})
    assert legacy.fighters["p2"].position == 1.5
    assert len([event for event in legacy.events if event.status == "teleported"]) == 1


@pytest.mark.parametrize("position", [-1, 0, .399, 11.601, 15, 3, 2.5, 3.5, "1.5", True, float("nan"), float("inf")])
def test_illegal_coordinates_are_rejected_by_model_and_engine_without_payment(position):
    arena = arena_for()
    initial = arena.fighters["p2"]
    initial_state = (initial.position, initial.mana, initial.stamina)
    with pytest.raises(ValueError):
        parse_tool_response(response(position=position), arena.observe("p2"))
    arena.advance({"p1": rest(), "p2": {"skill": "dash", "position": position}})
    fighter = arena.fighters["p2"]
    assert (fighter.position, fighter.mana, fighter.stamina) == initial_state
    assert not fighter.cooldowns
    assert not any(e.status == "teleported" for e in arena.events)


@pytest.mark.parametrize("position", [.4, 11.6, 2.2, 3.8])
def test_exact_arena_and_occupancy_boundaries_are_legal(position):
    arena = arena_for()
    arena.advance({"p1": rest(), "p2": teleport(position)})
    assert arena.fighters["p2"].position == position
    assert any(e.status == "teleported" for e in arena.events)


def test_teleport_is_character_specific_and_requires_exclusive_position_parameter():
    for action in [dash(), {"skill": "dash", "position": 1.5, "direction": "forward"}]:
        arena = arena_for()
        arena.advance({"p1": rest(), "p2": action})
        assert arena.fighters["p2"].position == 9
    arena = arena_for()
    arena.advance({"p1": teleport(6), "p2": rest()})
    assert arena.fighters["p1"].position == 3
    assert any(e.status == "rejected" for e in arena.events)
    with pytest.raises(ValueError):
        Action(skill="move", position=6)


def test_teleport_dodges_a_committed_attack_without_invincibility():
    arena = arena_for(starting_positions=(3, 4))
    initial_health = arena.config.resource_limit("p2", "health")
    arena.advance({"p1": heavy_punch(), "p2": teleport(9)})
    assert arena.fighters["p2"].health == initial_health
    assert any(e.status == "missed" and e.actor == "p1" for e in arena.events)
    arena = arena_for()
    initial_health = arena.config.resource_limit("p2", "health")
    damage = arena.config.characters["p1"].skills["heavy_punch"].health_damage
    arena.advance({"p1": heavy_punch(), "p2": teleport(4)})
    assert arena.fighters["p2"].health == initial_health - damage


@pytest.mark.parametrize("positions", [(6, 6), (6, 6.5)])
@pytest.mark.parametrize("reverse", [False, True])
def test_simultaneous_landing_conflicts_cancel_both_before_charging(positions, reverse):
    arena = arena_for(mirror=True)
    initial_resources = {
        player: (fighter.mana, fighter.stamina) for player, fighter in arena.fighters.items()
    }
    actions = {"p1": teleport(positions[0]), "p2": teleport(positions[1])}
    if reverse:
        actions = dict(reversed(list(actions.items())))
    arena.advance(actions)
    assert [f.position for f in arena.fighters.values()] == [3, 9]
    assert all((fighter.mana, fighter.stamina) == initial_resources[player] and not fighter.cooldowns
               for player, fighter in arena.fighters.items())
    assert len([e for e in arena.events if e.reason == "teleport_conflict" and e.status == "rejected"]) == 2
    fallbacks = [e for e in arena.events if e.reason == "teleport_conflict" and e.status == "fallback"]
    assert len(fallbacks) == 2
    assert all(event.effects["skill"] == "idle" for event in fallbacks)


def test_simultaneous_crossing_teleports_do_not_collide_along_paths():
    arena = arena_for(mirror=True)
    arena.advance({"p1": teleport(10), "p2": teleport(2)})
    assert [f.position for f in arena.fighters.values()] == [10, 2]
    assert [f.facing for f in arena.fighters.values()] == [-1, 1]


def test_same_destination_with_different_windups_is_resolved_at_each_landing_time():
    arena = mirror_arena_with_teleport_windups(0, 200)
    initial_p2 = (arena.fighters["p2"].stamina, arena.fighters["p2"].mana)
    p2_spec = arena.skills_for(arena.fighters["p2"])["dash"]
    arena.advance({"p1": teleport(6), "p2": teleport(6)})

    assert arena.fighters["p1"].position == 6
    assert arena.fighters["p2"].position == 9
    assert len([event for event in arena.events if event.status == "teleported"]) == 1
    rejected = [event for event in arena.events
                if event.actor == "p2" and event.reason == "teleport_conflict"]
    assert len(rejected) == 1
    assert arena.fighters["p2"].stamina == initial_p2[0] - p2_spec.stamina_cost
    assert arena.fighters["p2"].mana == initial_p2[1] - p2_spec.mana_cost


def test_movement_after_teleport_still_respects_collision():
    arena = arena_for()
    arena.advance({"p1": rest(), "p2": teleport(4)})
    arena.advance({"p1": move(), "p2": rest()})
    assert arena.fighters["p1"].position == pytest.approx(3.2)
    assert arena.fighters["p2"].position == 4


def test_teleport_fails_once_if_opponent_occupies_landing_position_during_windup():
    arena = arena_for()
    arena.advance({"p1": move(), "p2": teleport(4)})
    assert arena.fighters["p2"].position == 9
    rejected = [event for event in arena.events
                if event.actor == "p2" and event.reason == "teleport_conflict"]
    assert len(rejected) == 1
    assert not any(event.status == "teleported" and event.actor == "p2" for event in arena.events)


def test_scripted_mages_choose_legal_teleport_positions():
    arena = arena_for(time_limit_ms=10000)
    agent = TestAgent()
    actions = [agent.decide(arena.observe("p2")) for _ in range(5)]
    action = actions[-1]
    assert action.skill == "dash" and action.position is not None
    summary = run_match(arena, {"p1": TestAgent(), "p2": TestAgent()})
    assert summary["event_counts"].get("rejected", 0) == 0


def test_model_numeric_teleport_and_replay_roundtrip():
    calls = []
    updates = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=response(position=1.5))

    async def collect(message):
        updates.append(message)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match("test", "counterfactual_deepseek", arena_for(time_limit_ms=500).config,
                settings=DeepSeekSettings(api_key="test-only"), client=client, on_update=collect)

    recording = asyncio.run(run())
    assert recording.decisions[0].actions["p2"] == teleport(1.5)
    assert recording.decisions[0].details["p2"].selected_tool == "frost_teleport"
    assert recording.decisions[0].details["p2"].source == "llm"
    assert decode_recording(encode_recording(recording)) == recording
    assert updates[2]["frames"][0]["fighters"]["p2"]["position"] == 9
    assert any(frame["fighters"]["p2"]["position"] == 1.5 for frame in updates[2]["frames"])
