import json
import shutil

import pytest
from fastapi.testclient import TestClient

from backend.agents.character_tools import model_observation
from backend.api.app import create_app
from backend.game import Arena, GameConfig
from backend.game.characters import with_characters
from backend.game import weapons
from backend.game.weapons import load_weapon, weapon_catalog, with_weapons


CHARACTERS = {"p1": "crimson_blade", "p2": "frost_bell"}
WEAPONS = {"p1": "stone_sword", "p2": "dragonwrath_staff"}


def equipped_config(**changes):
    config = with_characters(GameConfig(**changes), CHARACTERS)
    return with_weapons(config, WEAPONS)


def test_weapon_catalog_is_independent_and_enforces_professions():
    catalog = weapon_catalog()
    assert set(catalog) == {
        "stone_sword", "lake_sword", "dragonwrath_staff", "twilight_scepter",
    }
    assert {weapon.profession for weapon in catalog.values()} == {"swordsman", "mage"}
    with pytest.raises(ValueError, match="cannot be equipped"):
        with_weapons(with_characters(GameConfig(), CHARACTERS), {
            "p1": "dragonwrath_staff", "p2": "stone_sword",
        })


def test_weapon_modifiers_change_resources_skills_and_agent_observation():
    config = equipped_config()
    arena = Arena(config)
    blade, frost = config.characters["p1"], config.characters["p2"]
    sword, staff = config.equipment["p1"], config.equipment["p2"]
    blade_health = blade.max_health + sword.resource_modifiers.max_health
    blade_stamina = blade.max_stamina + sword.resource_modifiers.max_stamina
    frost_mana = frost.max_mana + staff.resource_modifiers.max_mana
    blade_jab_damage = blade.skills["jab"].health_damage + sword.skill_modifiers["jab"].health_damage
    frost_burst_damage = (frost.skills["heavy_punch"].health_damage
                          + staff.skill_modifiers["heavy_punch"].health_damage)
    assert config.resource_limit("p1", "health") == blade_health
    assert config.resource_limit("p1", "stamina") == blade_stamina
    assert config.resource_limit("p2", "mana") == frost_mana
    assert arena.fighters["p1"].health == blade_health
    assert arena.skills_for(arena.fighters["p1"])["jab"].health_damage == blade_jab_damage
    assert arena.skills_for(arena.fighters["p2"])["heavy_punch"].health_damage == frost_burst_damage

    view = model_observation(arena.observe("p1"))
    assert view["self"]["weapon"]["weapon_id"] == "stone_sword"
    assert view["opponent"]["weapon"]["weapon_id"] == "dragonwrath_staff"
    assert set(view["self"]["weapon"]) == {"weapon_id", "name"}
    assert view["self"]["max_health"] == blade_health
    assert view["self"]["max_stamina"] == blade_stamina
    assert view["opponent"]["max_mana"] == frost_mana
    assert view["rules"]["skills"]["blade_flurry"]["health_damage"] == blade_jab_damage
    assert view["rules"]["opponent_skills"]["frost_burst"]["health_damage"] == frost_burst_damage


def test_json_edits_are_reloaded_for_new_matches_and_visible_to_agent(tmp_path, monkeypatch):
    root = tmp_path / "weapons"
    shutil.copytree(weapons.WEAPON_CONFIG_ROOT, root)
    monkeypatch.setattr(weapons, "WEAPON_CONFIG_ROOT", root)

    original = equipped_config()
    path = root / "stone_sword.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["resource_modifiers"]["max_health"] = 37
    data["skill_modifiers"]["jab"]["health_damage"] = 9
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    updated = equipped_config()
    base_health = original.characters["p1"].max_health
    base_jab_damage = original.characters["p1"].skills["jab"].health_damage
    assert original.resource_limit("p1", "health") == (
        base_health + original.equipment["p1"].resource_modifiers.max_health
    )
    assert updated.resource_limit("p1", "health") == base_health + 37
    observation = model_observation(Arena(updated).observe("p1"))
    assert observation["self"]["max_health"] == base_health + 37
    assert (observation["rules"]["skills"]["blade_flurry"]["health_damage"]
            == base_jab_damage + 9)


def test_weapon_api_catalog_selection_and_validation(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        returned = client.get("/api/weapons")
        assert returned.status_code == 200
        assert {item["weapon_id"] for item in returned.json()} == set(weapon_catalog())

        response = client.post("/api/matches", json={
            "characters": CHARACTERS,
            "weapons": WEAPONS,
            "config": {"time_limit_ms": 500},
        })
        assert response.status_code == 201
        payload = response.json()
        expected = equipped_config()
        assert payload["config"]["equipment"]["p1"]["weapon_id"] == "stone_sword"
        assert (payload["frames"][0]["fighters"]["p2"]["mana"]
                == expected.resource_limit("p2", "mana"))

        invalid = client.post("/api/matches", json={
            "characters": CHARACTERS,
            "weapons": {"p1": "dragonwrath_staff", "p2": "stone_sword"},
        })
        assert invalid.status_code == 422


def test_loadout_preview_uses_match_rules_without_saving_replay(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/preview", json={
            "characters": CHARACTERS,
            "weapons": WEAPONS,
        })
        assert response.status_code == 200
        payload = response.json()
        expected = equipped_config()
        assert payload["config"]["equipment"]["p1"]["weapon_id"] == "stone_sword"
        assert (payload["frame"]["fighters"]["p1"]["health"]
                == expected.resource_limit("p1", "health"))
        assert (payload["frame"]["fighters"]["p2"]["mana"]
                == expected.resource_limit("p2", "mana"))
        assert payload["frame"]["tools"]["p1"]["available"]
        assert client.get("/api/replays").json() == []
