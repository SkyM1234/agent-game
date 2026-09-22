import asyncio
from copy import deepcopy
import json
import os
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.agents import TestAgent
from backend.agents import deepseek
from backend.agents.deepseek import DeepSeekAgent, DeepSeekSettings, ProviderError, build_tools
from backend.api.app import create_app
from backend.game import Arena, GameConfig
from backend.matches.live import record_live_match
from backend.replay.recording import decode_recording, encode_recording, record_match
from backend.replay.store import ReplayStore


def settings(**updates):
    return DeepSeekSettings(api_key="test-secret-never-persist", **updates)


def test_settings_read_only_dotenv_and_reload_changes(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(deepseek, "ENV_FILE", env_file)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "system-secret")
    monkeypatch.setenv("DEEPSEEK_MODEL", "system-model")
    monkeypatch.setenv("DEEPSEEK_TIMEOUT_SECONDS", "invalid")
    env_file.write_text('DEEPSEEK_API_KEY="file-secret"\nDEEPSEEK_MODEL=file-model\n', encoding="utf-8-sig")

    loaded = DeepSeekSettings.from_env()
    assert loaded.api_key.get_secret_value() == "file-secret"
    assert loaded.model == "file-model"
    assert loaded.timeout_seconds == 20
    assert os.environ["DEEPSEEK_API_KEY"] == "system-secret"

    env_file.write_text("DEEPSEEK_API_KEY=new-file-secret\n", encoding="utf-8")
    assert DeepSeekSettings.from_env().api_key.get_secret_value() == "new-file-secret"
    assert DeepSeekSettings.from_env().model == "deepseek-v4-flash"


@pytest.mark.parametrize("content", [None, "", "DEEPSEEK_API_KEY=\n", "DEEPSEEK_API_KEY\n"])
def test_missing_dotenv_key_never_falls_back_to_environment(tmp_path, monkeypatch, content):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(deepseek, "ENV_FILE", env_file)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "system-secret")
    if content is not None:
        env_file.write_text(content, encoding="utf-8")
    assert not DeepSeekSettings.from_env().ready


def test_dotenv_does_not_expand_environment_variables(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(deepseek, "ENV_FILE", env_file)
    monkeypatch.setenv("EXTERNAL_SECRET", "system-secret")
    env_file.write_text("DEEPSEEK_API_KEY=${EXTERNAL_SECRET}\n", encoding="utf-8")
    assert DeepSeekSettings.from_env().api_key.get_secret_value() == "${EXTERNAL_SECRET}"


def response_for(request, *, content=None):
    payload = json.loads(request.content)
    observation = json.loads(payload["messages"][1]["content"])
    marker = "本场固定配置 match_context（整场不变）：\n"
    static = json.loads(payload["messages"][0]["content"].split(marker, 1)[1])
    observation["rules"] = static["rules"]
    observation["tools"] = {"available": {}, "items": {"available": {}}}
    available = {}
    for tool in payload["tools"]:
        function = tool["function"]
        schema = deepcopy(function["parameters"])
        del schema["properties"]["decision_summary"]
        schema["required"].remove("decision_summary")
        available[function["name"]] = {
            "parameters": schema, "metadata": {},
        }
    observation["tools"]["available"] = available
    action = TestAgent().decide(observation)
    arguments = action.model_dump(exclude_none=True)
    skill = arguments.pop("skill")
    arguments["decision_summary"] = "根据当前距离和可用技能选择行动。"
    return httpx.Response(200, json={
        "model": "test-returned-model", "usage": {"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125},
        "choices": [{"message": content or {"role": "assistant", "reasoning_content": "MUST_NOT_BE_RECORDED",
            "tool_calls": [{"id": "call-1", "type": "function", "function": {
                "name": skill, "arguments": json.dumps(arguments),
            }}]}}],
    })


def run_decision(handler, **config):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await DeepSeekAgent(settings(**config), client).decide(Arena().observe("p1"))
    return asyncio.run(run())


def test_realistic_tool_request_returns_action_and_safe_metadata():
    def handler(request):
        body = json.loads(request.content)
        dynamic = json.loads(body["messages"][1]["content"])
        marker = "本场固定配置 match_context（整场不变）：\n"
        static = json.loads(body["messages"][0]["content"].split(marker, 1)[1])
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret-never-persist"
        assert body["model"] == "deepseek-v4-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["tool_choice"] == "required"
        assert "本场固定配置 match_context" in body["messages"][0]["content"]
        assert "rules" not in dynamic and "tools" not in dynamic
        assert "max_health" not in dynamic["self"]
        assert static["fighters"]["self"]["max_health"] == 100
        assert static["fighters"]["opponent"]["max_health"] == 100
        assert set(static["rules"]["skills"]) == {
            "jab", "heavy_punch", "kick", "guard", "dash", "move", "rest",
        }
        assert set(static["rules"]["opponent_skills"]) == set(static["rules"]["skills"])
        assert "test-secret-never-persist" not in json.dumps(body)
        return response_for(request)

    decision = run_decision(handler)
    assert decision.action.skill == "jab"
    assert decision.info.source == "llm"
    assert decision.info.total_tokens == 125
    assert decision.info.returned_model == "test-returned-model"
    assert decision.trace["input"][0]["messages"]
    assert decision.trace["output"][0]["body"]["choices"][0]["message"]["tool_calls"]
    assert "MUST_NOT_BE_RECORDED" not in decision.model_dump_json()
    assert "test-secret-never-persist" not in decision.model_dump_json()


def test_dynamic_tools_remove_cooling_skills_and_preserve_observation():
    arena = Arena()
    arena.fighters["p1"].cooldowns["heavy_punch"] = 2
    view = arena.observe("p1")
    before = deepcopy(view)
    tools = build_tools(view)
    assert "heavy_punch" not in [tool["function"]["name"] for tool in tools]
    assert all("decision_summary" in tool["function"]["parameters"]["required"] for tool in tools)
    assert all(tool["function"]["parameters"]["properties"]["decision_summary"]["maxLength"] == 60
               for tool in tools)
    assert all(" Rules: " not in tool["function"]["description"] for tool in tools)
    assert view == before


def test_invalid_output_is_repaired_once_and_usage_accumulates():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return response_for(request, content={"content": "I will attack."})
        assert len(calls[-1]["messages"]) == 3
        assert "Available tools:" in calls[-1]["messages"][-1]["content"]
        return response_for(request)

    decision = run_decision(handler)
    assert len(calls) == 2
    assert decision.info.source == "llm"
    assert decision.info.errors == ["exactly_one_tool_required"]
    assert decision.info.attempts == 2
    assert decision.info.total_tokens == 250


def test_retry_identifies_rejected_tool_call():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return response_for(request, content={"tool_calls": [{
                "type": "function", "function": {"name": "teleport", "arguments": "{}"},
            }]})
        instruction = calls[-1]["messages"][-1]["content"]
        assert '\"name\":\"teleport\"' in instruction
        assert '\"arguments\":\"{}\"' in instruction
        return response_for(request)

    decision = run_decision(handler)
    assert decision.info.source == "llm"
    assert decision.info.errors == ["tool_unavailable"]


def test_legal_action_is_kept_when_summary_is_missing_or_too_long():
    missing = run_decision(lambda request: response_for(request, content={
        "tool_calls": [{"type": "function", "function": {
            "name": "rest", "arguments": "{}",
        }}],
    }))
    assert missing.action.skill == "rest"
    assert missing.info.source == "llm"
    assert missing.info.summary == ""

    long_summary = "战" * 80
    truncated = run_decision(lambda request: response_for(request, content={
        "tool_calls": [{"type": "function", "function": {
            "name": "rest", "arguments": json.dumps({"decision_summary": long_summary}),
        }}],
    }))
    assert truncated.action.skill == "rest"
    assert truncated.info.summary == "战" * 60


@pytest.mark.parametrize("bad", [
    {"tool_calls": []}, {"tool_calls": [None]}, {"tool_calls": [{"type": "invalid"}]},
    {"tool_calls": [{"type": "function", "function": {"name": "teleport", "arguments": "{}"}}]},
    {"tool_calls": [{"type": "function", "function": {"name": "jab", "arguments": "not json"}}]},
    {"tool_calls": [{"type": "function", "function": {"name": "jab", "arguments": "[]"}}]},
    {"tool_calls": [{"type": "function", "function": {"name": "jab", "arguments": '{"target":"head","decision_summary":"test"}'}}]},
    {"tool_calls": [{"type": "function", "function": {"name": "rest", "arguments": '{"decision_summary":"test","damage":999}'}}]},
])
def test_persistent_invalid_output_falls_back_without_engine_mutation(bad):
    decision = run_decision(lambda request: response_for(request, content=bad))
    assert decision.action.skill == "rest"
    assert decision.info.source == "fallback"
    assert decision.info.attempts == 2
    assert len(decision.info.errors) == 2


def test_invalid_json_is_repaired_and_does_not_leak_provider_body():
    decision = run_decision(lambda _: httpx.Response(200, content=b"private upstream text"))
    assert decision.info.source == "fallback"
    assert "private" not in decision.model_dump_json()


def test_timeout_covers_both_attempts_in_one_budget():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.035 if calls == 1 else 0.1)
        return response_for(request, content={"content": "invalid"})

    decision = run_decision(handler, timeout_seconds=0.07)
    assert calls == 2
    assert decision.info.errors == ["exactly_one_tool_required", "request_timeout"]
    assert decision.info.source == "fallback"
    assert decision.info.latency_ms < 150


@pytest.mark.parametrize("status,code", [(400, "invalid_provider_request"), (401, "authentication_failed"),
    (402, "insufficient_balance"), (403, "authentication_failed"), (404, "model_unavailable")])
def test_fatal_provider_errors_are_clear_and_redacted(status, code):
    with pytest.raises(ProviderError) as captured:
        run_decision(lambda _: httpx.Response(status, json={"error": "test-secret-never-persist"}))
    assert captured.value.code == code
    assert "test-secret" not in str(captured.value)


@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (503, "provider_error")])
def test_transient_errors_use_one_fallback_attempt(status, code):
    decision = run_decision(lambda _: httpx.Response(status))
    assert decision.info.errors == [code]
    assert decision.info.attempts == 1


def test_connection_error_does_not_leak_request_details():
    def handler(request):
        raise httpx.ConnectError("test-secret-never-persist", request=request)
    decision = run_decision(handler)
    assert decision.info.errors == ["connection_error"]
    assert "test-secret" not in decision.model_dump_json()


def test_both_agents_are_concurrent_and_observe_same_world_time():
    async def run():
        barrier = asyncio.Event()
        snapshots = []

        async def handler(request):
            view = json.loads(json.loads(request.content)["messages"][1]["content"])
            snapshots.append(view)
            if len(snapshots) == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), 1)
            return response_for(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            recorded = await record_live_match(config=GameConfig(time_limit_ms=500), settings=settings(), client=client)
        assert len(snapshots) == 2
        assert all(view["simulation_time"] == 0 and view["opponent"]["action"] is None for view in snapshots)
        assert recorded.summary["result"]["simulation_time"] == 0.5
        return recorded
    assert asyncio.run(run()).format_version == 5


def test_model_match_completes_with_current_legal_tools():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response_for)) as client:
            return await record_live_match(settings=settings(), client=client)
    recorded = asyncio.run(run())
    assert all(action.skill in d.details[player].available_tools for d in recorded.decisions for player, action in d.actions.items())
    assert recorded.summary["event_counts"].get("fallback", 0) == 0
    assert recorded.agent_metadata["p1"]["prompt_version"] == "combat-tools-v16"
    assert recorded.agent_metadata["p1"]["system_prompt"]
    encoded = encode_recording(recorded)
    assert "test-secret" not in encoded
    assert "MUST_NOT_BE_RECORDED" not in encoded
    assert decode_recording(encoded) == recorded


def test_llm_io_and_payoff_matrices_are_only_written_to_turn_logs(tmp_path):
    store = ReplayStore(tmp_path)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response_for)) as client:
            return await record_live_match(
                config=GameConfig(time_limit_ms=500), settings=settings(), client=client,
                store=store,
            )

    recorded = asyncio.run(run())
    turn_log = store.turn_log_path(recorded.replay_id, 1).read_text(encoding="utf-8")
    assert "TURN 1" in turn_log
    assert "[P1] PAYOFF MATRIX" in turn_log
    assert "[P2] PAYOFF MATRIX" in turn_log
    assert "| Own / Opp | B1" in turn_log
    assert "Action legend:" in turn_log
    assert "[P1] FULL ENGINE EVALUATION" in turn_log
    assert "Risk weighted" in turn_log
    assert "Recommended score gap:" in turn_log
    assert "Recommended terminal status:" in turn_log
    assert "[P1] DECISION CONTEXT SENT TO LLM" in turn_log
    assert "Counterfactual analysis sent to model:" in turn_log
    assert "| Action" in turn_log and "Preference" in turn_log
    assert "Stable match context: stored in match header" in turn_log
    assert "Available actions:" in turn_log
    assert "Self: HP" in turn_log and "Opponent: HP" in turn_log
    assert "[P1] LLM DECISION" in turn_log
    assert "Chosen action:" in turn_log
    assert "Decision source: llm" in turn_log
    assert "Engine comparison:" in turn_log
    assert "Reason: 根据当前距离和可用技能选择行动。" in turn_log
    assert "Message 1 [SYSTEM]" not in turn_log
    assert "Parameters:" not in turn_log
    assert "MUST_NOT_BE_RECORDED" not in turn_log

    replay_payload = encode_recording(recorded)
    assert "payoff_matrices" not in replay_payload
    assert "llm_requests" not in replay_payload
    assert "llm_responses" not in replay_payload


def test_transient_fallback_is_recorded_separately_from_success():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
            return await record_live_match("counterfactual_deepseek", "test", GameConfig(time_limit_ms=500), settings=settings(), client=client)
    recorded = asyncio.run(run())
    assert recorded.decisions[0].details["p1"].source == "fallback"
    assert recorded.decisions[0].details["p2"].source == "script"
    assert recorded.summary["event_counts"]["fallback"] == 1


def test_persistent_outage_stops_instead_of_saving_fake_draw():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
            await record_live_match(settings=settings(max_consecutive_failures=2), client=client)
    with pytest.raises(ProviderError) as captured:
        asyncio.run(run())
    assert captured.value.code == "provider_unavailable"


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_old_formats_are_rejected(version):
    original = record_match(config=GameConfig(time_limit_ms=500))
    lines = [json.loads(line) for line in encode_recording(original).splitlines()]
    lines[0]["data"]["format_version"] = version
    with pytest.raises(ValueError):
        decode_recording("\n".join(json.dumps(line) for line in lines))


def configure_mock(monkeypatch, handler=response_for):
    original_client = httpx.AsyncClient
    monkeypatch.setattr(DeepSeekSettings, "from_env", classmethod(lambda cls: settings()))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))


def test_websocket_runs_and_saves_full_dual_model_match(tmp_path, monkeypatch):
    configure_mock(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        assert "test-secret" not in client.get("/api/agents").text
        with client.websocket_connect("/api/live") as socket:
            socket.send_json({"p1": "counterfactual_deepseek", "p2": "counterfactual_deepseek", "config": {"time_limit_ms": 1000}})
            started = socket.receive_json()
            assert started["type"] == "started"
            while True:
                message = socket.receive_json()
                if message["type"] == "cycle":
                    socket.send_json({"type": "next"})
                if message["type"] == "finished":
                    replay_id = message["replay"]["replay_id"]
                    break
        assert client.get(f"/api/replays/{replay_id}").status_code == 200
        assert len(client.get("/api/replays").json()) == 1


def test_websocket_cancellation_interrupts_inflight_requests(tmp_path, monkeypatch):
    cancelled = threading.Event()

    async def slow(request):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return response_for(request)

    configure_mock(monkeypatch, slow)
    with TestClient(create_app(tmp_path)) as client:
        with client.websocket_connect("/api/live") as socket:
            socket.send_json({"p1": "counterfactual_deepseek", "p2": "counterfactual_deepseek"})
            assert socket.receive_json()["type"] == "started"
            assert socket.receive_json()["type"] == "waiting"
            socket.send_json({"type": "cancel"})
            assert socket.receive_json()["type"] == "cancelled"
        assert cancelled.wait(1)
        assert client.get("/api/replays").json() == []


def test_websocket_auth_failure_is_reported_and_not_saved(tmp_path, monkeypatch):
    configure_mock(monkeypatch, lambda _: httpx.Response(401))
    with TestClient(create_app(tmp_path)) as client:
        with client.websocket_connect("/api/live") as socket:
            socket.send_json({"p1": "counterfactual_deepseek"})
            while True:
                message = socket.receive_json()
                if message["type"] == "error":
                    assert message["code"] == "authentication_failed"
                    break
        assert client.get("/api/replays").json() == []


def test_missing_key_disables_option_and_returns_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(DeepSeekSettings, "from_env", classmethod(lambda cls: DeepSeekSettings()))
    with TestClient(create_app(tmp_path)) as client:
        option = client.get("/api/agents").json()[-1]
        assert option["available"] is False
        assert client.post("/api/matches", json={"p1": "counterfactual_deepseek"}).status_code == 503
