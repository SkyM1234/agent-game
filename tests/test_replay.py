from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from backend.agents import TestAgent
from backend.api.app import create_app
from backend.game import Arena, GameConfig
from backend.matches import run_match
from backend.replay.recording import (
    Recording, config_digest, decode_recording, encode_recording, record_match,
)
from backend.replay.store import ReplayStore


@pytest.fixture(scope="module")
def recorded():
    return record_match()


def test_recording_does_not_change_combat_results(recorded):
    original = run_match(Arena(), {"p1": TestAgent(), "p2": TestAgent()})
    assert recorded.summary == original
    assert recorded.frames[0].tick == 0
    assert recorded.frames[0].fighters["p1"].stamina == 100
    assert recorded.frames[0].fighters["p1"].action is None
    assert len(recorded.decisions) > 1
    assert recorded.frames[-1].simulation_time == original["result"]["simulation_time"]


def test_hash_is_stable_across_pydantic_and_json_normalization():
    config = GameConfig()
    loaded = GameConfig.model_validate_json(config.model_dump_json())
    assert config_digest(config) == config_digest(loaded)


def test_jsonl_round_trip_preserves_all_actions_events_and_frames(recorded):
    loaded = decode_recording(encode_recording(recorded))
    assert loaded == recorded
    assert loaded.decisions[0].actions["p1"].skill == "jab"
    assert loaded.frames[-1].event_count == len(loaded.events)


def test_every_tick_and_damage_transition_are_captured(recorded):
    assert set(frame.tick for frame in recorded.frames) == set(range(recorded.frames[-1].tick + 1))
    hit = next(event for event in recorded.events if event.status == "hit")
    player = hit.effects["defender"]
    before = [frame for frame in recorded.frames if frame.simulation_time < hit.simulation_time][-1]
    after = next(frame for frame in recorded.frames if frame.simulation_time == hit.simulation_time)
    assert before.fighters[player].health - after.fighters[player].health == hit.effects["health_damage"]
    assert recorded.frames[0].turn == 1
    assert all(a.turn <= b.turn for a, b in zip(recorded.frames, recorded.frames[1:]))


def test_store_reopens_and_loads_without_agents_or_engine(tmp_path, recorded, monkeypatch):
    store = ReplayStore(tmp_path)
    store.save(recorded)

    def forbidden(*args, **kwargs):
        raise AssertionError("replay must not execute agents or engine")

    monkeypatch.setattr(TestAgent, "decide", forbidden)
    monkeypatch.setattr(Arena, "__init__", forbidden)
    reopened = ReplayStore(tmp_path)
    assert reopened.load(recorded.replay_id) == recorded
    assert reopened.list()[0]["replay_id"] == recorded.replay_id
    assert reopened.list()[0]["frame_count"] == len(recorded.frames)


@pytest.mark.parametrize("mutation", ["hash", "version", "frames", "events", "summary", "fighters"])
def test_invalid_recordings_are_rejected(recorded, mutation):
    data = recorded.model_dump(mode="json")
    if mutation == "hash": data["config_hash"] = "wrong"
    if mutation == "version": data["format_version"] = 99
    if mutation == "frames": data["frames"][-1]["simulation_time"] = 0
    if mutation == "events": data["events"] = []
    if mutation == "summary": data["summary"]["result"]["winner"] = "p2" if recorded.summary["result"]["winner"] != "p2" else "p1"
    if mutation == "fighters": del data["frames"][0]["fighters"]["p1"]
    with pytest.raises(ValidationError):
        Recording.model_validate(data)


def test_store_refuses_paths_and_overwrites(tmp_path, recorded):
    store = ReplayStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load("../outside")
    store.save(recorded)
    with pytest.raises(FileExistsError):
        store.save(recorded)
    assert store.load(recorded.replay_id) == recorded


def test_store_delete_removes_file_and_index(tmp_path, recorded):
    store = ReplayStore(tmp_path)
    store.save(recorded)
    path = store.path(recorded.replay_id)
    assert path.is_file()
    store.delete(recorded.replay_id)
    assert not path.exists()
    assert ReplayStore(tmp_path).list() == []
    with pytest.raises(FileNotFoundError):
        store.load(recorded.replay_id)
    with pytest.raises(FileNotFoundError):
        store.delete(recorded.replay_id)
    with pytest.raises(FileNotFoundError):
        store.delete("../outside")


def test_api_delete_replay_removes_record_and_export(tmp_path, recorded):
    app = create_app(tmp_path)
    app.state.store.save(recorded)
    url = f"/api/replays/{recorded.replay_id}"
    with TestClient(app) as client:
        assert client.delete(url).status_code == 204
        assert client.get("/api/replays").json() == []
        assert client.get(url).status_code == 404
        assert client.get(f"{url}/export").status_code == 404
        assert client.delete(url).status_code == 404
        assert client.delete("/api/replays/not-an-id").status_code == 404


def test_frame_observer_cannot_modify_engine_state():
    arena = Arena()
    baseline = deepcopy(arena.snapshot())

    def mutate(frame):
        frame["fighters"]["p1"]["health"] = 0
        frame["tools"]["p1"]["available"].clear()

    from backend.game.skills import rest
    arena.advance({"p1": rest(), "p2": rest()}, on_frame=mutate)
    assert arena.fighters["p1"].health == baseline["fighters"]["p1"]["health"]


def test_api_create_export_import_and_reopen(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/replays").json() == []
        created = client.post("/api/matches", json={"p1": "test", "p2": "test"})
        assert created.status_code == 201
        data = created.json()
        replay_id = data["replay_id"]
        assert client.get(f"/api/replays/{replay_id}").json() == data
        exported = client.get(f"/api/replays/{replay_id}/export")
        assert exported.status_code == 200
        assert ".jsonl" in exported.headers["content-disposition"]
        imported = client.post("/api/replays/import", content=exported.content)
        assert imported.status_code == 201
        assert imported.json()["replay_id"] != replay_id
        assert imported.json()["frames"] == data["frames"]
        assert len(client.get("/api/replays").json()) == 2
    with TestClient(create_app(tmp_path)) as reopened:
        assert reopened.get(f"/api/replays/{replay_id}").json()["summary"] == data["summary"]


@pytest.mark.parametrize("payload", [b"", b"not-json", b"[]\n", b'{"type":"header","data":{}}\n',
    b'{"type":"header","data":1}\n{"type":"summary","data":{}}\n'])
def test_api_rejects_invalid_imports(tmp_path, payload):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/replays/import", content=payload)
        assert response.status_code == 422
        assert client.get("/api/replays").json() == []


def test_api_rejects_unknown_agents_oversized_simulation_and_missing_replay(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        options = client.get("/api/agents").json()
        assert [option["id"] for option in options] == [
            "test", "heuristic", "counterfactual", "deepseek", "counterfactual_deepseek",
        ]
        assert options[2]["label"] == "反事实推演"
        assert options[4]["label"] == "反事实推演 + DeepSeek"
        assert client.post("/api/matches", json={"p1": "aggressive"}).status_code == 422
        assert client.post("/api/matches", json={"p2": "counter"}).status_code == 422
        assert client.post("/api/matches", json={"p1": "unknown"}).status_code == 422
        assert client.post("/api/matches", json={"config": {"time_limit_ms": 99999900}}).status_code == 422
        assert client.get("/api/replays/not-an-id").status_code == 404
        assert client.get(f"/api/replays/{'a' * 32}/export").status_code == 404


def test_broken_stored_file_reports_error(tmp_path, recorded):
    app = create_app(tmp_path)
    app.state.store.save(recorded)
    path: Path = app.state.store.path(recorded.replay_id)
    path.write_text("broken", encoding="utf-8")
    with TestClient(app) as client:
        assert client.get(f"/api/replays/{recorded.replay_id}").status_code == 422
