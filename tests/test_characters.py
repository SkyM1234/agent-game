import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.game import Arena, GameConfig
from backend.game.characters import character_catalog, load_character, with_characters
from backend.game.skills import dash, guard, jab, rest
from backend.matches.live import record_live_match
from backend.replay.recording import decode_recording, encode_recording, record_match


CHOICES = {"p1": "crimson_blade", "p2": "frost_bell"}


def character_arena(**changes):
    return Arena(with_characters(GameConfig(decision_ms=50, **changes), CHOICES))


def tick_until(arena, end):
    while arena.time_ms < end:
        arena.advance({p: rest() for p, f in arena.fighters.items() if f.active is None})


def test_each_character_exposes_its_own_skill_rules():
    arena = character_arena()
    blade = arena.observe("p1")
    frost = arena.observe("p2")
    blade_jab = arena.config.characters["p1"].skills["jab"]
    frost_jab = arena.config.characters["p2"].skills["jab"]
    assert blade["tools"]["available"]["jab"]["metadata"]["reach"] == blade_jab.reach
    assert frost["tools"]["available"]["jab"]["metadata"]["reach"] == frost_jab.reach
    assert frost["rules"]["skills"]["jab"]["projectile_speed"] == frost_jab.projectile_speed
    assert frost_jab.projectile_speed > 0
    arena.fighters["p2"].mana = 0
    assert ("jab" in arena.available_tools("p2")["available"]) == (frost_jab.mana_cost == 0)
    assert "jab" in arena.available_tools("p1")["available"]




def test_projectile_travels_before_dealing_damage_and_hits_once():
    arena = character_arena(starting_positions=(3, 6))
    initial_health = arena.fighters["p1"].health
    spec = arena.config.characters["p2"].skills["jab"]
    damage = spec.health_damage
    arena.advance({"p1": rest(), "p2": jab()})
    tick_until(arena, spec.windup_ms + arena.config.step_ms)
    assert arena.fighters["p1"].health == initial_health
    assert len([e for e in arena.events if e.status == "projectile"]) == 1
    tick_until(arena, spec.duration_ms + arena.config.step_ms)
    assert arena.fighters["p1"].health == initial_health - damage
    tick_until(arena, 1100)
    assert len([e for e in arena.events if e.status == "hit"]) == 1


def test_projectile_does_not_hit_outside_its_range():
    arena = character_arena(starting_positions=(3, 8))
    initial_health = arena.fighters["p1"].health
    arena.advance({"p1": rest(), "p2": jab()})
    tick_until(arena, 1100)
    assert arena.fighters["p1"].health == initial_health
    assert any(e.status == "missed" and e.actor == "p2" for e in arena.events)


def test_projectile_hits_a_fighter_crossing_its_path_between_ticks():
    arena = character_arena(starting_positions=(3, 6))
    initial_health = arena.fighters["p1"].health
    damage = arena.config.characters["p2"].skills["jab"].health_damage
    arena.advance({"p1": rest(), "p2": jab()})
    tick_until(arena, 500)
    arena.advance({"p1": dash()})
    tick_until(arena, 600)
    # At 600ms the target has moved past this tick's projectile endpoint.
    # Their swept paths still intersect, so a point-only check would miss it.
    assert arena.fighters["p1"].health == initial_health - damage


def test_guard_blocks_projectile():
    arena = character_arena(starting_positions=(3, 4))
    initial_health = arena.fighters["p1"].health
    damage = arena.config.characters["p2"].skills["jab"].health_damage
    arena.advance({"p1": guard(), "p2": jab()})
    tick_until(arena, 400)
    assert arena.fighters["p1"].health == pytest.approx(
        initial_health - damage * arena.config.guard_damage_multiplier
    )




def test_character_api_selection_roundtrip_and_validation(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        expected_ids = set(character_catalog())
        returned_ids = {item["character_id"] for item in client.get("/api/characters").json()}
        assert returned_ids == expected_ids
        response = client.post("/api/matches", json={"characters": CHOICES})
        assert response.status_code == 201
        data = response.json()
        assert data["config"]["characters"]["p2"]["character_id"] == "frost_bell"
        exported = client.get(f"/api/replays/{data['replay_id']}/export").text
        loaded = decode_recording(exported)
        assert (loaded.config.characters["p2"].skills["jab"].projectile_speed
                == character_catalog()["frost_bell"].skills["jab"].projectile_speed)
        assert not [e for e in loaded.events if e.status == "rejected"]
        assert client.post("/api/matches", json={"characters": {"p1": "unknown", "p2": "frost_bell"}}).status_code == 422
        assert client.post("/api/matches", json={"characters": {"p1": "crimson_blade"}}).status_code == 422


def test_live_updates_include_character_rules_and_cooldowns():
    updates = []

    async def receive(message):
        updates.append(message)

    recording = asyncio.run(record_live_match("test", "test",
        with_characters(GameConfig(time_limit_ms=3000), CHOICES), on_update=receive))
    assert updates[0]["replay"]["config"]["characters"]["p1"]["name"] == "绯刃"
    assert any(frame.fighters["p1"].cooldowns for frame in recording.frames)
    assert decode_recording(encode_recording(recording)) == recording


@pytest.mark.parametrize("variants", [None, {"p1": "aggressive"}, {"p2": "aggressive"},
                                     {"p1": "aggressive", "p2": "aggressive"}])
def test_prompt_selection_preview_match_and_replay(tmp_path, variants):
    body = {"characters": CHOICES, "config": {"time_limit_ms": 500}}
    if variants is not None:
        body["prompt_variants"] = variants
    expected = {player: load_character(character, prompt_variant=(variants or {}).get(player, "neutral"))
                for player, character in CHOICES.items()}
    with TestClient(create_app(tmp_path)) as client:
        preview = client.post("/api/preview", json=body)
        assert preview.status_code == 200
        response = client.post("/api/matches", json=body)
        assert response.status_code == 201
        data = response.json()
        assert preview.json()["config"] == data["config"]
        exported = client.get(f"/api/replays/{data['replay_id']}/export").text
        assert decode_recording(exported).config.characters == expected
        imported = client.post("/api/replays/import", content=exported)
        assert imported.status_code == 201
        assert imported.json()["config"] == data["config"]


@pytest.mark.parametrize("body", [
    {"characters": CHOICES, "prompt_variants": {"p1": "unknown"}},
    {"characters": CHOICES, "prompt_variants": {"p3": "aggressive"}},
    {"prompt_variants": {"p1": "aggressive"}},
])
def test_prompt_selection_validation_on_http_and_websocket(tmp_path, body):
    with TestClient(create_app(tmp_path)) as client:
        for endpoint in ("/api/preview", "/api/matches"):
            assert client.post(endpoint, json=body).status_code == 422
        with client.websocket_connect("/api/live") as socket:
            socket.send_json(body)
            assert socket.receive_json()["code"] == "invalid_request"


def test_prompt_selection_live_match_snapshot(tmp_path):
    expected = with_characters(GameConfig(time_limit_ms=500), CHOICES,
                               prompt_variants={"p1": "aggressive", "p2": "neutral"})
    with TestClient(create_app(tmp_path)) as client:
        with client.websocket_connect("/api/live") as socket:
            socket.send_json({"characters": CHOICES, "prompt_variants": {"p1": "aggressive", "p2": "neutral"},
                              "config": {"time_limit_ms": 500}})
            started = socket.receive_json()
            assert started["type"] == "started"
            assert started["replay"]["config"] == expected.model_dump(mode="json")
            while True:
                message = socket.receive_json()
                if message["type"] == "cycle":
                    socket.send_json({"type": "next"})
                elif message["type"] == "finished":
                    data = message["replay"]
                    break
                else:
                    assert message["type"] == "waiting"
        saved = client.get(f"/api/replays/{data['replay_id']}").json()
        assert saved["config"] == expected.model_dump(mode="json")
