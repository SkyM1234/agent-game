import json
import shutil

import pytest

from backend.game import Arena, GameConfig
from backend.game import characters
from backend.game.characters import CharacterConfigError, load_character, with_characters
from backend.game.skills import rest
from backend.replay.recording import Recording, decode_recording, encode_recording, record_match


CHOICES = {"p1": "crimson_blade", "p2": "frost_bell"}


@pytest.fixture
def packages(tmp_path, monkeypatch):
    root = tmp_path / "characters"
    shutil.copytree(characters.CHARACTER_CONFIG_ROOT, root)
    monkeypatch.setattr(characters, "CHARACTER_CONFIG_ROOT", root)
    return root


def set_limits(packages, character_id, **limits):
    path = packages / character_id / "character.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(limits)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_each_character_uses_own_limits_for_initial_state_recovery_and_replay(packages):
    set_limits(packages, "crimson_blade", max_health=120, max_stamina=70, max_mana=30)
    set_limits(packages, "frost_bell", max_health=80, max_stamina=130, max_mana=55)
    config = with_characters(GameConfig(decision_ms=50, time_limit_ms=500), CHOICES)
    arena = Arena(config)
    assert [(f.health, f.stamina, f.mana) for f in arena.fighters.values()] == [
        (120, 70, 30), (80, 130, 55),
    ]
    assert arena.observe("p1")["rules"]["max_mana"] == 30
    assert arena.observe("p2")["rules"]["max_stamina"] == 130

    for fighter in arena.fighters.values():
        fighter.stamina -= 1
        fighter.mana -= 1
    arena.advance({"p1": rest(), "p2": rest()})
    assert [(f.stamina, f.mana) for f in arena.fighters.values()] == [(70, 30), (130, 55)]

    recording = decode_recording(encode_recording(record_match(config=config)))
    assert recording.frames[0].fighters["p1"].health == 120
    assert recording.frames[0].fighters["p2"].mana == 55
    set_limits(packages, "crimson_blade", max_health=200)
    assert recording.config.characters["p1"].max_health == 120
    invalid = recording.model_dump()
    invalid["frames"][0]["fighters"]["p2"]["mana"] = 56
    with pytest.raises(ValueError, match="invalid fighter resources"):
        Recording.model_validate(invalid)


def test_missing_character_limits_fall_back_to_global_config(packages):
    for character_id in CHOICES.values():
        path = packages / character_id / "character.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for field in ("max_health", "max_stamina", "max_mana"):
            del data[field]
        path.write_text(json.dumps(data), encoding="utf-8")
    config = with_characters(GameConfig(max_health=90, max_stamina=80, max_mana=70,
                                        time_limit_ms=500), CHOICES)
    assert all((f.health, f.stamina, f.mana) == (90, 80, 70)
               for f in Arena(config).fighters.values())
    assert decode_recording(encode_recording(record_match(config=config))).config == config


@pytest.mark.parametrize("field,value", [
    ("max_health", 0), ("max_stamina", -1), ("max_mana", 0),
])
def test_invalid_character_limits_are_rejected(packages, field, value):
    set_limits(packages, "crimson_blade", **{field: value})
    with pytest.raises(CharacterConfigError):
        load_character("crimson_blade")
