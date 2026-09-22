import asyncio
from copy import deepcopy
import json
import shutil

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.agents.character_tools import available_bindings, model_observation, tool_names
from backend.agents.deepseek import DeepSeekAgent, DeepSeekSettings, ProviderError, build_tools, parse_tool_response
from backend.api.app import create_app
from backend.game import Arena, GameConfig
from backend.game import characters
from backend.game.characters import CharacterConfigError, load_character, with_characters
from backend.game.skills import guard, jab, rest
from backend.matches.live import record_live_match
from backend.replay.recording import decode_recording, encode_recording, record_match
from backend.replay.store import ReplayStore

CHOICES = {"p1": "crimson_blade", "p2": "frost_bell"}


def config(**changes):
    return with_characters(GameConfig(**changes), CHOICES)


def response(name, **args):
    return {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{
        "type": "function", "function": {"name": name,
        "arguments": json.dumps({"decision_summary": "保持合适距离。", **args})},
    }]}}]}


def test_rosters_have_distinct_callable_tools_and_independent_prompts():
    arena = Arena(config())
    blade = {tool["function"]["name"] for tool in build_tools(arena.observe("p1"))}
    frost = {tool["function"]["name"] for tool in build_tools(arena.observe("p2"))}
    assert blade.isdisjoint(frost)
    assert {"blade_flurry", "draw_slash", "blade_guard", "breath"} <= blade
    assert {"ice_bolt", "frost_burst", "ice_shield", "meditate"} <= frost
    assert not ({"jab", "heavy_punch", "kick"} & (blade | frost))
    assert "剑士" in arena.config.characters["p1"].agent.prompt
    assert "法师" in arena.config.characters["p2"].agent.prompt


@pytest.mark.parametrize("character_id", ["crimson_blade", "frost_bell"])
def test_prompt_variants_share_mechanics_and_preserve_snapshots(character_id):
    neutral = load_character(character_id)
    aggressive = load_character(character_id, prompt_variant="aggressive")
    assert neutral == load_character(character_id, prompt_variant="neutral")
    assert neutral.agent.prompt != aggressive.agent.prompt
    assert aggressive.agent.version == neutral.agent.version + "-aggressive-v1"
    assert aggressive.agent.tools == neutral.agent.tools
    assert aggressive.model_dump(exclude={"agent"}) == neutral.model_dump(exclude={"agent"})
    mixed = GameConfig(time_limit_ms=500, characters={"p1": neutral, "p2": aggressive})
    recording = record_match(config=mixed)
    assert decode_recording(encode_recording(recording)).config == mixed


def test_unknown_prompt_variant_is_rejected():
    with pytest.raises(ValueError, match="unknown prompt variant"):
        load_character("crimson_blade", prompt_variant="../prompt")


def test_prompt_overrides_reject_unselected_players_and_invalid_variants():
    with pytest.raises(ValueError, match="selected players"):
        with_characters(GameConfig(), CHOICES, prompt_variants={"p3": "aggressive"})
    with pytest.raises(ValueError, match="unknown prompt variant"):
        with_characters(GameConfig(), CHOICES, prompt_variants={"p1": "unknown"})


def test_parser_maps_own_tools_and_rejects_opponent_or_legacy_names():
    view = Arena(config()).observe("p2")
    action, summary = parse_tool_response(response("ice_bolt"), view)
    assert action.skill == "jab"
    assert action.model_dump(exclude_none=True) == {"skill": "jab"}
    assert summary
    for name in ("blade_flurry", "jab"):
        with pytest.raises(ValueError, match="tool_unavailable"):
            parse_tool_response(response(name, target="torso"), view)


def test_model_view_has_consistent_names_without_duplicating_character_prompts():
    arena = Arena(config())
    arena.advance({"p1": guard(), "p2": rest()})
    view = arena.observe("p1")
    before = deepcopy(view)
    model = model_observation(view)
    assert "characters" not in model["rules"]
    assert "blade_guard" in model["rules"]["skills"]
    assert "ice_bolt" in model["rules"]["opponent_skills"]
    assert "blade_guard" in model["self"]["cooldowns"]
    assert model["self"]["character"]["name"] == "绯刃"
    assert "events" not in model
    assert model["self"]["max_health"] == arena.config.resource_limit("p1", "health")
    assert model["opponent"]["max_mana"] == arena.config.resource_limit("p2", "mana")
    assert model["time_remaining_ms"] == arena.config.time_limit_ms - arena.time_ms
    assert model["rules"]["omitted_skill_values_are_zero"] is True
    assert "mana_cost" not in model["rules"]["skills"]["blade_flurry"]
    assert view == before


def test_model_view_exposes_resource_limits_and_separate_lock_and_effect_timing():
    arena = Arena(config())
    arena.advance({"p1": rest(), "p2": jab()})

    model = model_observation(arena.observe("p2"))
    action = model["self"]["action"]

    assert model["self"]["max_mana"] == arena.config.resource_limit("p2", "mana")
    assert model["opponent"]["max_health"] == arena.config.resource_limit("p1", "health")
    assert model["self"]["can_decide"] is True
    assert action["lock_remaining_ms"] == 0
    jab_spec = arena.config.characters["p2"].skills["jab"]
    assert action["effect_remaining_ms"] == jab_spec.duration_ms - arena.config.decision_ms
    assert "remaining_ms" not in action
    assert "elapsed_ms" not in action


def test_model_view_exposes_unresolved_incoming_attack_timing():
    arena = Arena(config(decision_ms=100, time_limit_ms=1000, starting_positions=(3, 5)))
    arena.advance({"p1": rest(), "p2": jab()})

    model = model_observation(arena.observe("p1"))
    threat = model["incoming_threats"][0]

    assert threat["skill"] == "ice_bolt"
    assert threat["phase"] == "windup"
    assert threat["target_in_path"] is True
    assert threat["earliest_impact_ms"] == 400
    assert threat["projectile"] is True


def test_next_decision_sees_recent_missed_attack_and_range_without_tool_filtering():
    requests = []

    def provider(request):
        body = json.loads(request.content)
        view = json.loads(body["messages"][1]["content"])
        requests.append((view, {tool["function"]["name"] for tool in body["tools"]}))
        if view["self"]["fighter_id"] == "p2":
            return httpx.Response(200, json=response("meditate"))
        name = "blade_flurry" if not view["recent_turns"] else "blade_walk"
        return httpx.Response(200, json=response(name, **({"direction": "forward"} if name == "blade_walk" else {})))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match(config=config(starting_positions=(3, 4.9), time_limit_ms=1000),
                settings=DeepSeekSettings(api_key="test-only"), client=client)

    recording = asyncio.run(run())
    blade_requests = [(view, tools) for view, tools in requests if view["self"]["fighter_id"] == "p1"]
    assert blade_requests[0][0]["recent_turns"] == []
    assert "blade_flurry" in blade_requests[1][1]
    previous = blade_requests[1][0]["recent_turns"][-1]
    attack = previous["actions"]["p1"]
    assert previous["distance"]["before"] == previous["distance"]["after"] == 1.9
    assert previous["changes"].get("p2", {}).get("health", 0) == 0
    assert attack["skill"] == "blade_flurry"
    assert attack["reach"] == recording.config.characters["p1"].skills["jab"].reach
    assert "summary" not in attack
    assert "summary" not in previous["actions"]["p2"]
    assert any(event["actor"] == "p1" and event["action_id"] == attack["action_id"]
               and event["status"] == "missed" and event["reason"] == "out_of_range"
               for event in previous["events"])
    assert recording.decisions[1].actions["p1"].skill == "move"


def test_recent_turn_context_is_bounded_per_match(tmp_path):
    lengths = []
    views = []
    store = ReplayStore(tmp_path)

    def provider(request):
        body = json.loads(request.content)
        view = json.loads(body["messages"][1]["content"])
        views.append(view)
        lengths.append(len(view["recent_turns"]))
        walk = "blade_walk" if view["self"]["fighter_id"] == "p1" else "frost_walk"
        return httpx.Response(200, json=response(walk, direction="forward"))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match(
                config=config(time_limit_ms=4000),
                settings=DeepSeekSettings(api_key="test-only"), client=client,
                store=store,
            )

    recording = asyncio.run(run())
    assert lengths[0:2] == [0, 0]
    assert lengths[-2:] == [4, 4]
    assert "opponent_history" not in views[0]
    blade_history = next(
        view["opponent_history"] for view in reversed(views)
        if view["self"]["fighter_id"] == "p1" and "opponent_history" in view
    )
    assert blade_history["through_turn"] >= 1
    assert blade_history["observed_decisions"] >= 1
    assert blade_history["action_counts"] == {
        "frost_walk": blade_history["observed_decisions"],
    }
    assert blade_history["direction_counts"] == {
        "frost_walk": {"forward": blade_history["observed_decisions"]},
    }
    last_log_path = max((store.log_directory / recording.replay_id).glob("turn-*.log"))
    last_log = last_log_path.read_text(encoding="utf-8")
    assert "Opponent history through turn" in last_log
    assert "frost_walk" in last_log


def test_action_started_after_lock_expiry_is_visible_in_opponent_context():
    requests = []

    def provider(request):
        view = json.loads(json.loads(request.content)["messages"][1]["content"])
        requests.append(view)
        if view["self"]["fighter_id"] == "p1":
            return httpx.Response(200, json=response("breath"))
        if view["self"]["action"] and view["self"]["action"]["phase"] == "active":
            return httpx.Response(200, json=response("frost_walk", direction="backward"))
        return httpx.Response(200, json=response("ice_bolt"))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match(config=config(time_limit_ms=1500),
                settings=DeepSeekSettings(api_key="test-only"), client=client)

    asyncio.run(run())
    frost_active = next(view for view in requests
                        if view["self"]["fighter_id"] == "p2"
                        and view["self"]["action"]
                        and view["self"]["action"]["phase"] == "active")
    assert frost_active["recent_turns"][-1]["actions"]["p2"]["skill"] == "ice_bolt"
    blade_next_turn = next(view for view in requests
                           if view["self"]["fighter_id"] == "p1"
                           and view["simulation_time"] == 1.0)
    frost_action = blade_next_turn["recent_turns"][-1]["actions"]["p2"]
    assert frost_action["skill"] == "frost_walk"
    assert frost_action["action_id"] is not None
    assert [view["simulation_time"] for view in requests
            if view["self"]["fighter_id"] == "p2"] == [0.0, 0.5, 1.0]


def test_dynamic_tools_filter_cooldown_stamina_and_mana():
    arena = Arena(config())
    fighter = arena.fighters["p2"]
    skills = arena.config.characters["p2"].skills
    jab_spec = skills["jab"]
    fighter.stamina = jab_spec.stamina_cost
    fighter.mana = jab_spec.mana_cost
    arena.fighters["p2"].cooldowns["dash"] = 2
    view = arena.observe("p2")
    names = tool_names(view)
    tools = {t["function"]["name"]: t["function"] for t in build_tools(view)}
    expected_unavailable = {
        names[action] for action, spec in skills.items()
        if action == "dash" or spec.stamina_cost > fighter.stamina or spec.mana_cost > fighter.mana
    }
    assert expected_unavailable.isdisjoint(tools)
    jab_name = names["jab"]
    assert set(tools[jab_name]["parameters"]["properties"]) == {"decision_summary"}
    with pytest.raises(ValueError, match="invalid_arguments"):
        parse_tool_response(response(jab_name, target="torso"), view)
    assert jab_spec.mana_cost > 0
    fighter.mana = jab_spec.mana_cost / 2
    assert jab_name not in available_bindings(arena.observe("p2"))


def test_profile_files_reload_for_new_matches_and_snapshot_old_matches(tmp_path, monkeypatch):
    root = tmp_path / "characters"
    shutil.copytree(characters.CHARACTER_CONFIG_ROOT, root)
    monkeypatch.setattr(characters, "CHARACTER_CONFIG_ROOT", root)
    original = config(time_limit_ms=500)
    prompt = root / "frost_bell/prompt_neutral.md"
    prompt.write_text("新的冰系战术提示词。", encoding="utf-8")
    tools_path = root / "frost_bell/tools.json"
    data = json.loads(tools_path.read_text(encoding="utf-8"))
    data["tools"][0]["name"] = "new_ice_bolt"
    tools_path.write_text(json.dumps(data), encoding="utf-8")
    character_path = root / "frost_bell/character.json"
    character_data = json.loads(character_path.read_text(encoding="utf-8"))
    character_data["version"] = "frost-bell-test-version"
    character_path.write_text(json.dumps(character_data), encoding="utf-8")
    loaded = config()
    assert loaded.characters["p2"].agent.prompt == "新的冰系战术提示词。"
    assert loaded.characters["p2"].agent.version == "frost-bell-test-version"
    assert original.characters["p2"].agent.version == "frost-bell-v6"
    assert load_character("frost_bell", prompt_variant="aggressive").agent.version == "frost-bell-test-version-aggressive-v1"
    assert original.characters["p2"].agent.tools[0].name == "ice_bolt"
    assert "new_ice_bolt" in available_bindings(Arena(loaded).observe("p2"))
    recording = record_match(config=original)
    assert decode_recording(encode_recording(recording)).config == original


@pytest.mark.parametrize("prompt_variant", ["neutral", "aggressive"])
@pytest.mark.parametrize("version", [None, "", 123])
def test_missing_or_invalid_character_version_is_rejected(tmp_path, monkeypatch, prompt_variant, version):
    root = tmp_path / "characters"
    shutil.copytree(characters.CHARACTER_CONFIG_ROOT, root)
    monkeypatch.setattr(characters, "CHARACTER_CONFIG_ROOT", root)
    path = root / "frost_bell/character.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if version is None:
        data.pop("version")
    else:
        data["version"] = version
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CharacterConfigError):
        load_character("frost_bell", prompt_variant=prompt_variant)


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_action", "bad_parameter", "missing_file"])
def test_invalid_character_package_is_rejected(tmp_path, monkeypatch, mutation):
    root = tmp_path / "characters"
    shutil.copytree(characters.CHARACTER_CONFIG_ROOT, root)
    monkeypatch.setattr(characters, "CHARACTER_CONFIG_ROOT", root)
    path = root / "frost_bell/tools.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "duplicate":
        data["tools"][0]["name"] = data["tools"][1]["name"]
    elif mutation == "wrong_action":
        data["tools"][0]["action"] = "teleport"
    elif mutation == "bad_parameter":
        data["tools"][0]["parameters"]["target"] = ["head"]
    if mutation == "missing_file":
        path.unlink()
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CharacterConfigError):
        load_character("frost_bell")




def test_requests_load_own_prompt_and_record_selected_public_tool():
    bodies = []

    def provider(request):
        body = json.loads(request.content)
        bodies.append(body)
        tools = {tool["function"]["name"] for tool in body["tools"]}
        name = "blade_walk" if "blade_walk" in tools else "frost_walk"
        return httpx.Response(200, json=response(name, direction="forward"))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match(config=config(time_limit_ms=500),
                settings=DeepSeekSettings(api_key="test-only"), client=client)

    recording = asyncio.run(run())
    assert "剑士绯刃" in bodies[0]["messages"][0]["content"]
    assert "法师霜铃" in bodies[1]["messages"][0]["content"]
    assert "ice_bolt" not in {t["function"]["name"] for t in bodies[0]["tools"]}
    assert recording.decisions[0].details["p2"].selected_tool == "frost_walk"
    assert recording.agent_metadata["p2"]["character_prompt_version"] == "frost-bell-v6"
    assert decode_recording(encode_recording(recording)) == recording


def test_connection_failure_has_precise_redacted_fallback_and_stop_reason():
    def provider(request):
        raise httpx.ConnectError("private-secret", request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            settings = DeepSeekSettings(api_key="private-key", max_consecutive_failures=1)
            result = await DeepSeekAgent(settings, client).decide(Arena(config()).observe("p2"))
            assert "无法连接模型服务" in result.info.summary
            assert "冥想" in result.info.summary
            assert "private" not in result.model_dump_json()
            with pytest.raises(ProviderError) as error:
                await record_live_match(config=config(time_limit_ms=500), settings=settings, client=client)
            assert error.value.reason == "connection_error"
            assert error.value.player in ("p1", "p2")
            assert "无法连接" in str(error.value)
            assert "private" not in str(error.value)
    asyncio.run(run())


def test_truncated_tool_arguments_are_reported_as_truncated():
    payload = response("ice_bolt", target="torso")
    payload["choices"][0]["finish_reason"] = "length"
    with pytest.raises(ValueError, match="response_truncated"):
        parse_tool_response(payload, Arena(config()).observe("p2"))


def test_websocket_error_includes_actual_cause_and_player(tmp_path, monkeypatch):
    original = httpx.AsyncClient

    def provider(request):
        raise httpx.ConnectError("private-secret", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(provider), **kw))
    monkeypatch.setattr(DeepSeekSettings, "from_env", classmethod(lambda cls: DeepSeekSettings(api_key="private-key", max_consecutive_failures=1)))
    with TestClient(create_app(tmp_path)) as client:
        with client.websocket_connect("/api/live") as socket:
            socket.send_json({"p1":"counterfactual_deepseek", "p2":"test", "characters": CHOICES})
            assert socket.receive_json()["type"] == "started"
            assert socket.receive_json()["type"] == "waiting"
            error = socket.receive_json()
            assert error["type"] == "error"
            assert error["reason"] == "connection_error"
            assert error["player"] == "p1"
            assert "private" not in json.dumps(error)
        assert client.get("/api/replays").json() == []
