import asyncio

import pytest

from backend.agents.character_tools import model_observation, tool_names
from backend.game import Arena, GameConfig
from backend.game.characters import with_characters
from backend.game.skills import guard, heavy_punch, jab, move, rest
from backend.matches.live import record_live_match
from backend.replay.recording import Recording, decode_recording, encode_recording, record_match


CHOICES = {"p1": "crimson_blade", "p2": "frost_bell"}


def test_mana_is_paid_once_on_start_even_if_attack_misses():
    arena = Arena(with_characters(GameConfig(), CHOICES))
    fighter = arena.fighters["p2"]
    mana_limit = arena.config.resource_limit("p2", "mana")
    mana_cost = arena.config.characters["p2"].skills["jab"].mana_cost
    assert fighter.mana == mana_limit
    arena.advance({"p1": rest(), "p2": jab()})
    assert fighter.mana == mana_limit - mana_cost
    arena.advance({"p1": rest(), "p2": jab()})
    assert fighter.mana >= mana_limit - mana_cost
    assert any(e.reason == "cooldown" for e in arena.events)
    assert any(e.status == "missed" and e.actor == "p2" for e in arena.events)
    starts = [e for e in arena.events if e.status == "started" and e.actor == "p2"
              and e.effects["skill"] == "jab"]
    assert len(starts) == 1
    assert starts[0].effects["mana_cost"] == mana_cost


def test_insufficient_mana_is_filtered_and_rejected_without_charging_or_cooldown():
    arena = Arena(with_characters(GameConfig(), CHOICES))
    fighter = arena.fighters["p2"]
    skills = arena.config.characters["p2"].skills
    mana_cost = skills["jab"].mana_cost
    assert mana_cost > 0
    starting_mana = mana_cost / 2
    fighter.mana = starting_mana
    assert arena.available_tools("p2")["unavailable"]["jab"] == "insufficient_mana"
    arena.advance({"p1": rest(), "p2": jab()})
    assert any(e.reason == "insufficient_mana" and e.status == "rejected" for e in arena.events)
    assert any(e.reason == "insufficient_mana" and e.status == "fallback"
               and e.effects["skill"] == "idle" for e in arena.events)
    assert fighter.stamina == arena.config.resource_limit("p2", "stamina")
    assert fighter.mana == starting_mana
    assert "jab" not in fighter.cooldowns
    tools = arena.available_tools("p2")
    assert ("jab" in tools["available"]) == (fighter.mana >= mana_cost)


def test_exact_mana_cost_is_accepted_for_each_character():
    arena = Arena(with_characters(GameConfig(), CHOICES))
    blade_cost = arena.config.characters["p1"].skills["jab"].mana_cost
    frost_cost = arena.config.characters["p2"].skills["jab"].mana_cost
    arena.fighters["p1"].mana = blade_cost
    arena.fighters["p2"].mana = frost_cost
    arena.advance({"p1": jab(), "p2": jab()})
    assert all(f.mana == 0 for f in arena.fighters.values())
    assert not [e for e in arena.events if e.status in ("rejected", "fallback")]


def test_rest_recovers_both_resources_to_configured_caps_and_move_does_not():
    arena = Arena(GameConfig(max_mana=70, max_stamina=80))
    fighter = arena.fighters["p1"]
    fighter.mana = fighter.stamina = 0
    arena.advance({"p1": rest(), "p2": rest()})
    assert (fighter.mana, fighter.stamina) == (20, 15)
    arena.advance({"p1": move(), "p2": rest()})
    assert (fighter.mana, fighter.stamina) == (20, 15)
    for _ in range(6):
        arena.advance({"p1": rest(), "p2": rest()})
    assert (fighter.mana, fighter.stamina) == (70, 80)


def test_rest_can_have_a_turn_cooldown():
    data = GameConfig().model_dump()
    data["skills"]["rest"]["cooldown_turns"] = 1
    arena = Arena(GameConfig.model_validate(data))
    arena.fighters["p1"].mana = arena.fighters["p1"].stamina = 0

    arena.advance({"p1": rest(), "p2": move()})
    assert arena.available_tools("p1")["unavailable"]["rest"] == "cooldown"
    arena.advance({"p1": move(), "p2": move()})
    assert "rest" in arena.available_tools("p1")["available"]


@pytest.mark.parametrize("decision_ms", [100, 500, 1000])
@pytest.mark.parametrize("cooldown", [0, 1, 2, 3])
def test_cooldown_blocks_exactly_next_n_turns_independent_of_simulation_time(decision_ms, cooldown):
    data = GameConfig(decision_ms=decision_ms).model_dump()
    data["skills"]["guard"].update(active_ms=50, cooldown_turns=cooldown)
    data["skills"]["move"]["active_ms"] = 50
    arena = Arena(GameConfig.model_validate(data))
    frames = []
    arena.advance({"p1": guard(), "p2": rest()}, on_frame=frames.append)
    assert arena.observe("p1")["turn"] == 2
    assert frames[0]["fighters"]["p1"]["cooldowns"].get("guard", 0) == cooldown
    for remaining in range(cooldown, 0, -1):
        frame = arena.playback_frame()
        assert frame["fighters"]["p1"]["cooldowns"]["guard"] == remaining
        assert arena.available_tools("p1")["unavailable"]["guard"] == "cooldown"
        # Repeated observation/rendering never consumes cooldown turns.
        assert arena.playback_frame() == frame
        arena.advance({"p1": move(), "p2": rest()})
    assert "guard" not in arena.snapshot()["fighters"]["p1"]["cooldowns"]
    assert "guard" in arena.available_tools("p1")["available"]


def test_cooldown_rejects_reuse_and_rest_does_not_skip_turns():
    data = GameConfig().model_dump()
    data["skills"]["guard"]["cooldown_turns"] = 2
    arena = Arena(GameConfig.model_validate(data))
    arena.advance({"p1": guard(), "p2": rest()})
    arena.advance({"p1": guard(), "p2": rest()})
    assert any(e.status == "rejected" and e.reason == "cooldown" for e in arena.events)
    assert arena.fighters["p1"].cooldowns["guard"] == 3
    assert arena.observe("p1")["self"]["cooldowns"]["guard"] == 1
    arena.advance({"p1": rest(), "p2": rest()})
    assert arena.observe("p1")["turn"] == 4
    assert "guard" in arena.available_tools("p1")["available"]
    arena.advance({"p1": guard(), "p2": rest()})
    assert len([e for e in arena.events if e.status == "started" and e.effects["skill"] == "guard"]) == 2


def test_action_lock_and_cooldown_expire_independently():
    data = GameConfig().model_dump()
    data["skills"]["heavy_punch"]["cooldown_turns"] = 2
    arena = Arena(GameConfig.model_validate(data))
    arena.advance({"p1": heavy_punch(), "p2": rest()})
    assert arena.available_tools("p1")["unavailable"]["heavy_punch"] == "action_locked"
    arena.advance({"p2": rest()})
    assert arena.available_tools("p1")["unavailable"]["heavy_punch"] == "cooldown"
    arena.advance({"p1": rest(), "p2": rest()})
    assert "heavy_punch" in arena.available_tools("p1")["available"]


def test_model_observation_uses_mana_and_character_turn_cooldowns_without_parts():
    arena = Arena(with_characters(GameConfig(), CHOICES))
    guard_spec = arena.config.characters["p2"].skills["guard"]
    guard_name = tool_names(arena.observe("p2"))["guard"]
    initial_mana = arena.config.resource_limit("p2", "mana")
    arena.advance({"p1": rest(), "p2": guard()})
    view = model_observation(arena.observe("p2"))
    assert view["turn"] == 2
    assert view["self"]["mana"] == initial_mana - guard_spec.mana_cost
    assert view["self"]["cooldowns"][guard_name] == guard_spec.cooldown_turns
    assert view["rules"]["skills"][guard_name]["mana_cost"] == guard_spec.mana_cost
    assert "parts" not in view["self"]
    assert "part_durability" not in view["rules"]
    assert all("target" not in t["parameters"]["properties"] for t in arena.available_tools("p2")["available"].values())


def test_live_cycles_and_replay_keep_same_turn_mana_and_cooldown_state():
    updates = []

    async def receive(message):
        updates.append(message)

    config = with_characters(GameConfig(time_limit_ms=5000), CHOICES)
    recorded = asyncio.run(record_live_match("test", "test", config, on_update=receive))
    assert recorded == decode_recording(encode_recording(recorded))
    cycles = [u for u in updates if u["type"] == "cycle"]
    assert cycles
    for cycle in cycles:
        final = cycle["frames"][-1]
        matches = [f for f in recorded.frames if f.tick == final["tick"] and f.turn == final["turn"]
                   and f.event_count == final["event_count"]]
        assert matches[-1].fighters["p2"].mana == final["fighters"]["p2"]["mana"]
        assert matches[-1].fighters["p2"].cooldowns == final["fighters"]["p2"]["cooldowns"]
    initial_mana = recorded.config.resource_limit("p2", "mana")
    assert any(f.fighters["p2"].mana < initial_mana for f in recorded.frames)
    assert recorded.frames[-1].turn == 10


@pytest.mark.parametrize("mutation", ["mana_negative", "mana_overflow", "turn_backwards"])
def test_replay_validates_mana_and_turns(mutation):
    data = record_match(config=GameConfig(time_limit_ms=1000)).model_dump()
    if mutation.startswith("mana"):
        data["frames"][0]["fighters"]["p1"]["mana"] = -1 if mutation == "mana_negative" else 101
    else:
        data["frames"][-1]["turn"] = 1
    with pytest.raises(ValueError):
        Recording.model_validate(data)
