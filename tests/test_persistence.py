import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from backend.agents.deepseek import DeepSeekSettings
from backend.game import Arena, GameConfig
from backend.game.skills import heavy_punch, rest
from backend.matches import live
from backend.matches.live import record_live_match
from backend.replay.store import ReplayStore


def test_mysql_database_is_created_before_tables(tmp_path, monkeypatch):
    connections = []

    class OperationalError(Exception):
        pass

    class Cursor:
        rowcount = 1

        def __init__(self):
            self.statements = []

        def execute(self, statement, parameters=None):
            self.statements.append((statement, parameters))

        def close(self):
            pass

    class Connection:
        def __init__(self, parameters):
            self.parameters = parameters
            self.cursor_instance = Cursor()
            self.committed = self.rolled_back = self.closed = False

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    def connect(**parameters):
        if parameters.get("database") == "agent_arena" and not connections:
            connections.append(SimpleNamespace(parameters=parameters))
            raise OperationalError(1049, "Unknown database 'agent_arena'")
        connection = Connection(parameters)
        connections.append(connection)
        return connection

    monkeypatch.setitem(sys.modules, "pymysql", SimpleNamespace(
        connect=connect, err=SimpleNamespace(OperationalError=OperationalError),
    ))
    ReplayStore(tmp_path, "mysql+pymysql://arena:p%40ss@127.0.0.1:3306/agent_arena")

    assert connections[0].parameters["database"] == "agent_arena"
    assert "database" not in connections[1].parameters
    assert connections[1].parameters["password"] == "p@ss"
    assert connections[1].cursor_instance.statements == [(
        "CREATE DATABASE IF NOT EXISTS `agent_arena` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci", None,
    )]
    assert connections[1].committed and connections[1].closed
    assert connections[2].parameters["database"] == "agent_arena"
    assert any("CREATE TABLE IF NOT EXISTS combat_matches" in statement
               for statement, _ in connections[2].cursor_instance.statements)


def test_arena_checkpoint_restores_in_flight_authoritative_state():
    arena = Arena(GameConfig(decision_ms=50, time_limit_ms=1000))
    arena.advance({"p1": heavy_punch(), "p2": rest()})

    restored = Arena.from_state(arena.config, arena.export_state())

    assert restored.snapshot() == arena.snapshot()
    assert restored.events == arena.events
    assert restored._action_sequence == arena._action_sequence
    assert restored.fighters["p1"].active == arena.fighters["p1"].active


def test_each_turn_is_journaled_and_an_incomplete_match_can_resume(tmp_path):
    store = ReplayStore(tmp_path)

    class Interrupted(Exception):
        pass

    async def stop_after_first_turn():
        raise Interrupted

    async def interrupt():
        await record_live_match(
            "counterfactual", "test", GameConfig(time_limit_ms=1000),
            store=store, wait_for_next=stop_after_first_turn,
        )

    with pytest.raises(Interrupted):
        asyncio.run(interrupt())

    incomplete = store.list_incomplete()
    assert len(incomplete) == 1
    replay_id = incomplete[0]["replay_id"]
    checkpoint = store.load_checkpoint(replay_id)
    assert checkpoint["turn_count"] == 1
    assert checkpoint["checkpoint"]["behavior_history"]["through_turn"] == 0
    detail = checkpoint["turns"][0]["decision"]["details"]["p1"]
    assert "counterfactual_analysis" not in detail
    turn_log = store.turn_log_path(replay_id, 1).read_text(encoding="utf-8")
    assert "TURN 1" in turn_log
    assert "[P1] PAYOFF MATRIX" in turn_log
    assert "| Own / Opp | B1" in turn_log
    assert "Action legend:" in turn_log
    assert "A1 / B1 =" in turn_log
    assert "[P1] FULL ENGINE EVALUATION" in turn_log
    assert "Key candidates" in turn_log
    assert "LLM INPUT" not in turn_log

    resumed = asyncio.run(record_live_match(store=store, resume_id=replay_id))
    uninterrupted = asyncio.run(record_live_match(
        "counterfactual", "test", GameConfig(time_limit_ms=1000),
    ))

    assert resumed.replay_id == replay_id
    assert len(resumed.decisions) > 1
    assert resumed.summary == uninterrupted.summary
    assert [item.actions for item in resumed.decisions] == [
        item.actions for item in uninterrupted.decisions
    ]
    assert store.load(replay_id) == resumed
    assert store.list_incomplete() == []


def test_aggregated_behavior_history_survives_checkpoint_resume(tmp_path):
    store = ReplayStore(tmp_path)

    class Interrupted(Exception):
        pass

    waits = 0

    async def stop_after_six_turns():
        nonlocal waits
        waits += 1
        if waits == 6:
            raise Interrupted

    config = GameConfig(time_limit_ms=4000)

    async def interrupt():
        await record_live_match(
            "test", "test", config, store=store,
            wait_for_next=stop_after_six_turns,
        )

    with pytest.raises(Interrupted):
        asyncio.run(interrupt())
    replay_id = store.list_incomplete()[0]["replay_id"]
    checkpoint = store.load_checkpoint(replay_id)["checkpoint"]
    assert checkpoint["behavior_history"]["through_turn"] == 2
    assert checkpoint["behavior_history"]["players"]["p1"]["observed_decisions"] == 2
    assert len(checkpoint["recent_turns"]) == 4

    resumed = asyncio.run(record_live_match(store=store, resume_id=replay_id))
    uninterrupted = asyncio.run(record_live_match("test", "test", config))

    assert resumed.summary == uninterrupted.summary
    assert [decision.actions for decision in resumed.decisions] == [
        decision.actions for decision in uninterrupted.decisions
    ]


def test_resumed_model_match_uses_snapshotted_system_prompt(tmp_path, monkeypatch):
    store = ReplayStore(tmp_path)
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "tool_calls",
            "message": {"tool_calls": [{"type": "function", "function": {
                "name": "rest",
                "arguments": json.dumps({"decision_summary": "保留资源。"}),
            }}]},
        }]})

    class Interrupted(Exception):
        pass

    async def stop_after_first_turn():
        raise Interrupted

    settings = DeepSeekSettings(api_key="test-only")

    async def interrupt():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            await record_live_match(
                "counterfactual_deepseek", "test", GameConfig(time_limit_ms=1000),
                settings=settings, client=client, store=store,
                wait_for_next=stop_after_first_turn,
            )

    with pytest.raises(Interrupted):
        asyncio.run(interrupt())
    replay_id = store.list_incomplete()[0]["replay_id"]
    saved_prompt = store.load_checkpoint(replay_id)["header"]["agent_metadata"]["p1"]["system_prompt"]
    monkeypatch.setattr(live, "SYSTEM_PROMPT", "new prompt that must not affect a resumed match")
    requests.clear()

    async def resume():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match(
                settings=settings, client=client, store=store, resume_id=replay_id,
            )

    asyncio.run(resume())
    assert requests
    assert all(request["messages"][0]["content"].startswith(saved_prompt) for request in requests)
