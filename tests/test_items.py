import json
import shutil

import pytest
from fastapi.testclient import TestClient

from backend.agents.character_tools import available_bindings, model_observation
from backend.agents.deepseek import parse_tool_response
from backend.agents.scripted import TestAgent
from backend.api.app import create_app
from backend.game import Arena, GameConfig
from backend.game import items
from backend.game.items import item_catalog, with_items
from backend.game.models import Action
from backend.game.skills import heavy_punch, rest


def configured(p1, p2=(), **changes):
    return Arena(with_items(GameConfig(**changes), {"p1": list(p1), "p2": list(p2)}))


def use(item_id):
    return Action(skill="use_item", item_id=item_id)


def test_item_catalog_has_six_independent_consumables():
    catalog = item_catalog()
    assert set(catalog) == {
        "healing_potion", "stamina_drink", "mana_crystal",
        "guardian_charm", "time_sand", "smoke_bomb",
    }
    assert catalog["healing_potion"].name == "樱露"
    assert all(item.charges == 1 for item in catalog.values())


def test_item_loadout_allows_at_most_two_unique_items():
    with pytest.raises(ValueError, match="two unique items"):
        configured(["healing_potion", "stamina_drink", "mana_crystal"])
    with pytest.raises(ValueError, match="two unique items"):
        configured(["healing_potion", "healing_potion"])


def test_json_edits_are_reloaded_and_visible_to_agent(tmp_path, monkeypatch):
    root = tmp_path / "items"
    shutil.copytree(items.ITEM_CONFIG_ROOT, root)
    monkeypatch.setattr(items, "ITEM_CONFIG_ROOT", root)
    original = configured(["healing_potion"])
    path = root / "healing_potion.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["charges"] = 2
    data["effects"]["health_restore"] = 41
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    updated = configured(["healing_potion"])
    assert original.config.items["p1"][0].effects.health_restore == 25
    assert updated.fighters["p1"].items["healing_potion"] == 2
    observation = model_observation(updated.observe("p1"))
    assert observation["rules"]["items"]["p1"]["healing_potion"]["effects"]["health_restore"] == 41


def test_resource_items_restore_to_caps_and_consume_charges():
    cases = [
        ("healing_potion", "health", 25),
        ("stamina_drink", "stamina", 35),
        ("mana_crystal", "mana", 35),
    ]
    for item_id, resource, amount in cases:
        arena = configured([item_id])
        fighter = arena.fighters["p1"]
        setattr(fighter, resource, 10)
        arena.advance({"p1": use(item_id), "p2": rest()})
        assert getattr(fighter, resource) == 10 + amount
        assert fighter.items[item_id] == 0
        assert item_id not in arena.available_tools("p1")["items"]["available"]
        event = next(event for event in arena.events if event.status == "item_used")
        assert event.effects[f"{resource}_restore"] == amount


def test_guardian_charm_absorbs_damage_and_expires_after_two_turns():
    arena = configured(["guardian_charm"], starting_positions=(3, 4))
    initial = arena.fighters["p1"].health
    shield = item_catalog()["guardian_charm"].effects.shield
    arena.advance({"p1": use("guardian_charm"), "p2": heavy_punch()})
    hit = next(event for event in arena.events if event.status == "hit")
    absorbed = min(shield, 25)
    assert hit.effects["shield_absorbed"] == absorbed
    assert hit.effects["health_damage"] == 25 - absorbed
    assert arena.fighters["p1"].health == initial - (25 - absorbed)
    assert arena.fighters["p1"].shield == shield - absorbed

    expiry = configured(["guardian_charm"])
    expiry.advance({"p1": use("guardian_charm"), "p2": rest()})
    assert expiry.fighters["p1"].shield == shield
    assert expiry.playback_frame()["fighters"]["p1"]["shield_turns"] == 1
    expiry.advance({"p1": rest(), "p2": rest()})
    assert expiry.fighters["p1"].shield == 0


def test_time_sand_reduces_all_active_skill_cooldowns():
    arena = configured(["time_sand"], starting_positions=(3, 9))
    arena.advance({"p1": heavy_punch(), "p2": rest()})
    assert arena.fighters["p1"].cooldowns["heavy_punch"] == 3
    arena.advance({"p2": rest()})
    arena.advance({"p1": use("time_sand"), "p2": rest()})
    assert "heavy_punch" not in arena.fighters["p1"].cooldowns
    event = next(event for event in arena.events if event.status == "item_used")
    assert event.effects["cooldowns_reduced"] == {"heavy_punch": 1}


def test_smoke_bomb_moves_backward_and_respects_arena_boundary():
    arena = configured(["smoke_bomb"], starting_positions=(1, 9))
    arena.advance({"p1": use("smoke_bomb"), "p2": rest()})
    assert arena.fighters["p1"].position == arena.config.fighter_radius
    event = next(event for event in arena.events if event.status == "item_used")
    assert event.effects["origin"] == 1
    assert event.effects["destination"] == arena.config.fighter_radius


def test_items_are_available_to_agent_with_raw_effects_and_remaining_charges():
    arena = configured(["healing_potion", "smoke_bomb"])
    arena.fighters["p1"].health = 50
    observation = arena.observe("p1")
    binding = available_bindings(observation)["use_item"]
    assert binding["parameters"]["properties"]["item_id"]["enum"] == ["healing_potion", "smoke_bomb"]
    model = model_observation(observation)
    assert model["self"]["items"] == {"healing_potion": 1, "smoke_bomb": 1}
    assert model["rules"]["items"]["p1"]["healing_potion"]["effects"]["health_restore"] == 25
    assert "asset" not in model["rules"]["items"]["p1"]["healing_potion"]
    response = {"choices": [{"message": {"tool_calls": [{
        "type": "function", "function": {
            "name": "use_item",
            "arguments": '{"item_id":"healing_potion","decision_summary":"恢复生命。"}',
        },
    }]}}]}
    action, summary = parse_tool_response(response, observation)
    assert action == use("healing_potion")
    assert summary == "恢复生命。"


def test_test_agent_cycles_through_every_skill_and_equipped_item():
    arena = configured(["healing_potion", "mana_crystal"])
    arena.fighters["p1"].health = arena.fighters["p1"].mana = 50
    observation = arena.observe("p1")
    agent = TestAgent()
    actions = [agent.decide(observation) for _ in range(9)]
    assert [action.skill for action in actions] == [
        "jab", "use_item", "use_item", "heavy_punch", "kick", "guard", "dash", "move", "rest",
    ]
    assert [action.item_id for action in actions if action.skill == "use_item"] == [
        "healing_potion", "mana_crystal",
    ]


def test_item_api_preview_and_match_snapshot_do_not_mix_with_weapons(tmp_path):
    choices = {"p1": ["healing_potion", "time_sand"], "p2": ["mana_crystal"]}
    with TestClient(create_app(tmp_path)) as client:
        catalog = client.get("/api/items")
        assert catalog.status_code == 200
        assert len(catalog.json()) == 6
        preview = client.post("/api/preview", json={"items": choices})
        assert preview.status_code == 200
        assert preview.json()["frame"]["fighters"]["p1"]["items"]["healing_potion"] == 1
        assert client.get("/api/replays").json() == []
        match = client.post("/api/matches", json={"items": choices, "config": {"time_limit_ms": 500}})
        assert match.status_code == 201
        assert [item["item_id"] for item in match.json()["config"]["items"]["p1"]] == [
            "healing_potion", "time_sand",
        ]
