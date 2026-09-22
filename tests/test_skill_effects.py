import hashlib
import json
import shutil

import pytest

from backend.game import Arena, GameConfig
from backend.game import characters
from backend.game.characters import with_characters
from backend.game.skills import dash, move, rest
from backend.replay.recording import decode_recording, encode_recording, record_match

CHOICES = {"p1": "crimson_blade", "p2": "frost_bell"}


@pytest.fixture
def package(tmp_path, monkeypatch):
    root = tmp_path / "characters"
    shutil.copytree(characters.CHARACTER_CONFIG_ROOT, root)
    monkeypatch.setattr(characters, "CHARACTER_CONFIG_ROOT", root)
    return root / "crimson_blade/character.json"


def configure(package, skill, **changes):
    data = json.loads(package.read_text(encoding="utf-8"))
    data["skills"][skill].update(changes)
    package.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("active_ms", [250, 1000])
def test_character_recovery_totals_apply_only_during_active_phase(package, active_ms):
    configure(package, "rest", stamina_restore=24, mana_restore=32,
              windup_ms=100, active_ms=active_ms, recovery_ms=150)
    arena = Arena(with_characters(GameConfig(decision_ms=50), CHOICES))
    for fighter in arena.fighters.values():
        fighter.stamina = fighter.mana = 0
    metadata = arena.available_tools("p1")["available"]["rest"]["metadata"]
    assert metadata["stamina_restore"] == 24
    assert metadata["mana_restore"] == 32
    arena.advance({"p1": rest(), "p2": rest()})
    assert arena.fighters["p1"].mana == 0
    while arena.time_ms < 100 + active_ms + 150:
        arena.advance({})
        progress = min(1, max(0, (arena.time_ms - 100) / active_ms))
        assert arena.fighters["p1"].stamina == pytest.approx(24 * progress)
        assert arena.fighters["p1"].mana == pytest.approx(32 * progress)
    opponent_rest = arena.config.characters["p2"].skills["rest"]
    expected_stamina = opponent_rest.stamina_restore
    expected_mana = opponent_rest.mana_restore
    if expected_stamina is None:
        expected_stamina = arena.config.rest_stamina_per_second * opponent_rest.active_ms / 1000
    if expected_mana is None:
        expected_mana = arena.config.rest_mana_per_second * opponent_rest.active_ms / 1000
    assert arena.fighters["p2"].stamina == expected_stamina
    assert arena.fighters["p2"].mana == expected_mana


def test_zero_recovery_overrides_global_rates_and_caps_still_apply(package):
    configure(package, "rest", stamina_restore=0, mana_restore=50)
    arena = Arena(with_characters(GameConfig(), CHOICES))
    mana_limit = arena.config.resource_limit("p1", "mana")
    arena.fighters["p1"].stamina = 40
    arena.fighters["p1"].mana = mana_limit - 20
    arena.advance({"p1": rest(), "p2": rest()})
    assert arena.fighters["p1"].stamina == 40
    assert arena.fighters["p1"].mana == mana_limit


@pytest.mark.parametrize("skill,action,active_ms,speed", [
    ("move", move, 500, 3), ("dash", dash, 250, 8),
])
def test_character_movement_settings_match_unobstructed_distance(package, skill, action, active_ms, speed):
    configure(package, skill, speed=speed, active_ms=active_ms)
    arena = Arena(with_characters(GameConfig(), CHOICES))
    arena.advance({"p1": action(), "p2": rest()})
    assert arena.fighters["p1"].position - 3 == pytest.approx(speed * active_ms / 1000)


def test_existing_format_three_replay_without_skill_restore_fields_loads():
    recording = record_match(config=GameConfig(time_limit_ms=500))
    lines = [json.loads(line) for line in encode_recording(recording).splitlines()]
    data = lines[0]["data"]["config"]
    for spec in data["skills"].values():
        spec.pop("teleport")
        spec.pop("stamina_restore")
        spec.pop("mana_restore")
    lines[0]["data"]["config_hash"] = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert decode_recording("\n".join(json.dumps(line) for line in lines)) == recording


def test_character_recovery_settings_persist_in_replay_and_reload_for_new_matches(package):
    configure(package, "rest", stamina_restore=9, mana_restore=7)
    original = with_characters(GameConfig(time_limit_ms=500), CHOICES)
    configure(package, "rest", stamina_restore=25, mana_restore=30)
    assert with_characters(GameConfig(), CHOICES).characters["p1"].skills["rest"].mana_restore == 30
    recording = decode_recording(encode_recording(record_match(config=original)))
    assert recording.config.characters["p1"].skills["rest"].mana_restore == 7


@pytest.mark.parametrize("skill,field,value", [
    ("rest", "stamina_restore", -1), ("rest", "mana_restore", -1),
    ("jab", "stamina_restore", 1), ("move", "mana_restore", 1),
])
def test_invalid_recovery_config_is_rejected(skill, field, value):
    data = GameConfig().model_dump()
    data["skills"][skill][field] = value
    with pytest.raises(ValueError):
        GameConfig.model_validate(data)
