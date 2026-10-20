import asyncio
import json

import httpx
import pytest
from model_provider import current_observation

from backend.agents.deepseek import (
    MAX_CONTEXT_BYTES, DeepSeekAgent, DeepSeekSettings, ProviderError, build_tools, prompt_messages,
)
from backend.agents.pi import PiBattleAgent, PiRuntime
from backend.game import Arena, GameConfig
from backend.game.characters import with_characters
from backend.matches.live import record_live_match
from backend.replay.store import ReplayStore


def response(name="rest", **arguments):
    return httpx.Response(200, json={
        "usage": {"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140,
                  "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 40},
        "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{
            "type": "function", "function": {
                "name": name, "arguments": json.dumps({"decision_summary": "观察并行动。", **arguments}),
            },
        }]}}],
    })


def test_tool_waits_for_settlement_and_does_not_spend_request_timeout_while_paused():
    async def run():
        requests = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: requests.append(json.loads(request.content)) or response(),
        )) as client:
            async with PiRuntime() as runtime:
                agent = PiBattleAgent(runtime, "p1", DeepSeekAgent(
                    DeepSeekSettings(api_key="test-only", timeout_seconds=0.05), client,
                ))
                arena = Arena(GameConfig(time_limit_ms=2000))
                first = await agent.decide(arena.observe("p1"))
                assert first.action.skill == "rest"
                await asyncio.sleep(0.15)
                assert len(requests) == 1
                arena.advance({"p1": first.action})
                second = await agent.decide(arena.observe("p1"))
                assert second.info.source == "llm"
                assert len(requests) == 2
                assert requests[1]["messages"][-1]["role"] == "tool"
                assert current_observation(requests[1])["simulation_time"] == 0.5
            assert runtime.process.returncode == 0
    asyncio.run(run())


def test_long_match_bounds_history_preserves_pairs_and_reports_cache_hits():
    bodies = []

    def provider(request):
        bodies.append(json.loads(request.content))
        return response()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match(
                "deepseek", "deepseek", GameConfig(time_limit_ms=8000),
                settings=DeepSeekSettings(api_key="test-only"), client=client,
            )

    recording = asyncio.run(run())
    assert len(bodies) == 32
    assert recording.agent_metadata["p1"]["agent_runtime"] == "pi-agent-core"
    assert recording.summary["model_usage"]["p1"]["prompt_cache_hit_tokens"] == 16 * 80
    for body in bodies:
        view = current_observation(body)
        assert len(view["recent_turns"]) <= 4
        assert len(body["messages"]) <= 10
        assert len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()) <= MAX_CONTEXT_BYTES
        messages = body["messages"]
        for index, message in enumerate(messages):
            if message["role"] == "tool":
                assert messages[index - 1]["role"] == "assistant"
                assert messages[index - 1]["tool_calls"][0]["id"] == message["tool_call_id"]
            for call in message.get("tool_calls", []):
                assert "decision_summary" not in json.loads(call["function"]["arguments"])
        # Old snapshots are compacted; the full state appears only once.
        assert sum('"self"' in (message.get("content") or "") for message in messages
                   if message["role"] in ("user", "tool")) == 1
    assert current_observation(bodies[-1])["opponent_history"]["observed_decisions"] > 0


def test_waits_only_for_players_that_can_act_and_preserves_simultaneous_observation():
    views = []

    def provider(request):
        view = current_observation(json.loads(request.content))
        views.append(view)
        return response("heavy_punch") if view["self"]["fighter_id"] == "p1" else response()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await asyncio.wait_for(record_live_match(
                "deepseek", "deepseek", GameConfig(decision_ms=50, time_limit_ms=1000),
                settings=DeepSeekSettings(api_key="test-only"), client=client,
            ), 10)

    recording = asyncio.run(run())
    assert recording.summary["result"]["simulation_time"] == 1
    assert all(view["self"]["can_decide"] for view in views)
    assert any(set(decision.actions) != {"p1", "p2"} for decision in recording.decisions)
    initial = [view for view in views if view["simulation_time"] == 0]
    assert len(initial) == 2 and all(view["opponent"]["action"] is None for view in initial)


def test_cancellation_releases_a_waiting_tool_and_cancels_other_provider():
    async def run():
        waiting = asyncio.Event()
        cancelled = asyncio.Event()

        async def provider(request):
            view = current_observation(json.loads(request.content))
            if view["self"]["fighter_id"] == "p1":
                return response()
            waiting.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            async with PiRuntime() as runtime:
                settings = DeepSeekSettings(api_key="test-only")
                agents = [PiBattleAgent(runtime, player, DeepSeekAgent(settings, client))
                          for player in ("p1", "p2")]
                arena = Arena()
                tasks = [asyncio.create_task(agent.decide(arena.observe(agent.player))) for agent in agents]
                await asyncio.wait_for(waiting.wait(), 5)
                await asyncio.wait_for(tasks[0], 2)
                tasks[1].cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            assert cancelled.is_set()
            assert runtime.process.returncode == 0
    asyncio.run(run())


def test_checkpoint_restores_completed_tool_history_with_fresh_observation(tmp_path):
    store = ReplayStore(tmp_path)
    bodies = []

    def provider(request):
        bodies.append(json.loads(request.content))
        return response()

    class Interrupted(Exception):
        pass

    count = 0

    async def pause():
        nonlocal count
        count += 1
        if count == 3:
            raise Interrupted

    async def run(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match(
                "deepseek", "test", GameConfig(time_limit_ms=2500), store=store,
                settings=DeepSeekSettings(api_key="test-only"), client=client, **kwargs,
            )

    with pytest.raises(Interrupted):
        asyncio.run(run(wait_for_next=pause))
    replay_id = store.list_incomplete()[0]["replay_id"]
    saved = store.load_checkpoint(replay_id)["checkpoint"]["agent_states"]["p1"]["pi_history"]
    assert any(message["role"] == "toolResult" for message in saved)
    assert "test-only" not in json.dumps(saved)
    bodies.clear()
    recording = asyncio.run(run(resume_id=replay_id))
    assert current_observation(bodies[0])["simulation_time"] == 1.5
    assert any(message["role"] == "assistant" for message in bodies[0]["messages"])
    for body in bodies:
        ids = [call["id"] for message in body["messages"] for call in message.get("tool_calls", [])]
        assert len(ids) == len(set(ids))
    assert recording.summary["result"]["simulation_time"] == 2.5


def test_context_budget_rejects_oversized_current_state_without_sending_api_request():
    async def run():
        def forbidden(_request):
            raise AssertionError("oversized context must not reach the provider")

        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            observation = Arena().observe("p1")
            observation["too_large"] = "战" * MAX_CONTEXT_BYTES
            decision = await DeepSeekAgent(DeepSeekSettings(api_key="test-only"), client).decide(observation)
        assert decision.info.source == "fallback"
        assert decision.info.errors == ["context_too_large"]
        assert decision.info.attempts == 0
    asyncio.run(run())


def test_near_budget_rebases_history_without_losing_current_observation():
    async def run():
        bodies = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: bodies.append(json.loads(request.content)) or response(),
        )) as client:
            async with PiRuntime() as runtime:
                agent = PiBattleAgent(runtime, "p1", DeepSeekAgent(DeepSeekSettings(api_key="test-only"), client))
                arena = Arena(GameConfig(time_limit_ms=2000))
                for _ in range(3):
                    view = arena.observe("p1")
                    baseline = {"messages": prompt_messages(view), "tools": build_tools(view)}
                    size = len(json.dumps(baseline, ensure_ascii=False, separators=(",", ":")).encode())
                    view["padding"] = "x" * (MAX_CONTEXT_BYTES - 2500 - size)
                    decision = await agent.decide(view)
                    assert decision.info.source == "llm"
                    arena.advance({"p1": decision.action})
        assert [current_observation(body)["simulation_time"] for body in bodies] == [0, 0.5, 1]
        assert all(len(body["messages"]) == 2 for body in bodies)
        assert all(len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())
                   <= MAX_CONTEXT_BYTES for body in bodies)
    asyncio.run(run())


def test_cancelling_startup_still_reaps_the_node_process():
    async def run():
        async with PiRuntime() as runtime:
            starting = asyncio.create_task(runtime.start())
            await asyncio.sleep(0)
            starting.cancel()
            result = await asyncio.gather(starting, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError)
        assert runtime.process.returncode == 0
    asyncio.run(run())


def test_provider_failure_can_submit_rest_when_recovery_tool_is_unavailable():
    async def run():
        bodies = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: bodies.append(json.loads(request.content)) or httpx.Response(503),
        )) as client:
            async with PiRuntime() as runtime:
                agent = PiBattleAgent(runtime, "p1", DeepSeekAgent(DeepSeekSettings(api_key="test-only"), client))
                arena = Arena(with_characters(GameConfig(), {"p1": "frost_bell", "p2": "crimson_blade"}))
                arena.fighters["p1"].cooldowns["rest"] = 2
                decision = await agent.decide(arena.observe("p1"))
                assert decision.info.source == "fallback"
                assert decision.action.skill == "rest"
                assert decision.info.errors == ["provider_error"]
        assert all(tool["function"]["name"] not in ("meditate", "__arena_fallback")
                   for tool in bodies[0]["tools"])
    asyncio.run(run())


def test_missing_pi_runtime_reports_clear_error(monkeypatch):
    monkeypatch.setattr("backend.agents.pi.shutil.which", lambda _: None)

    async def run():
        async with httpx.AsyncClient() as client:
            await record_live_match("deepseek", "test", GameConfig(time_limit_ms=500),
                                    settings=DeepSeekSettings(api_key="private-key"), client=client)

    with pytest.raises(ProviderError) as captured:
        asyncio.run(run())
    assert captured.value.code == "agent_runtime_unavailable"
    assert "private-key" not in str(captured.value)
