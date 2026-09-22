import asyncio
from copy import deepcopy
import json
from statistics import fmean

import httpx
from fastapi.testclient import TestClient
import pytest

from backend.agents.character_tools import model_observation, tool_names
from backend.agents.deepseek import DeepSeekSettings
from backend.agents.planning import CounterfactualRollout, CounterfactualRolloutAgent
from backend.agents.scripted import TestAgent
from backend.api.app import create_app
from backend.game import Arena, GameConfig
from backend.game.characters import load_character, with_characters
from backend.game.items import with_items
from backend.game.models import Action, default_skills
from backend.game.skills import jab, move, rest
from backend.matches.live import record_live_match
from backend.matches.runner import run_match
from backend.replay.store import ReplayStore


def configured_skill(name, **updates):
    skills = default_skills()
    skills[name] = skills[name].model_copy(update=updates)
    return skills


def test_rollout_accounts_for_opponent_movement_into_and_out_of_attack_range():
    arena = Arena(GameConfig(starting_positions=(3, 4.6)))
    rollout = CounterfactualRollout()

    toward = rollout.evaluate_branch(arena, "p1", jab(), move("forward"))
    away = rollout.evaluate_branch(arena, "p1", jab(), move("backward"))

    assert toward.metrics.damage_dealt == 10
    assert away.metrics.damage_dealt == 0
    assert toward.metrics.distance_after < away.metrics.distance_after


def test_prompt_summary_reports_score_gap_and_actual_terminal_outcomes():
    arena = Arena(GameConfig(starting_positions=(3, 3.8)))
    arena.fighters["p2"].health = 2
    rollout = CounterfactualRollout()

    winning_branch = rollout.evaluate_branch(arena, "p1", jab(), rest())
    analysis = rollout.analyze(arena, "p1")
    summary = analysis.prompt_summary()
    jab_candidate = next(item for item in summary["candidates"]
                         if item["action"]["skill"] == "jab")

    assert summary["recommended_score_gap"] >= 0
    assert summary["recommended_terminal_status"] in {
        "forced_win", "forced_loss", "forced_draw", "mixed", "none",
    }
    assert winning_branch.terminal_result == "win"
    assert jab_candidate["terminal_status"] == "mixed"
    assert jab_candidate["terminal_outcomes"]["win"] > 0


def test_only_forced_knockout_outcomes_bypass_model_decisions():
    skills = default_skills()
    skills["jab"] = skills["jab"].model_copy(update={"reach": 12, "windup_ms": 0})
    knockout_arena = Arena(GameConfig(skills=skills))
    knockout_arena.fighters["p2"].health = 1
    knockout = CounterfactualRollout().analyze(knockout_arena, "p1")

    time_limit = CounterfactualRollout().analyze(
        Arena(GameConfig(time_limit_ms=50)), "p1",
    )

    assert knockout.selected_terminal_status == "forced_win"
    assert knockout.selected_is_forced_knockout is True
    assert time_limit.selected_terminal_status == "forced_draw"
    assert time_limit.selected_evaluation.terminal_reasons["time_limit"] > 0
    assert time_limit.selected_is_forced_knockout is False


def test_damage_weights_increase_as_the_relevant_fighter_loses_health():
    rollout = CounterfactualRollout()

    healthy_opponent = Arena(GameConfig(starting_positions=(3, 4)))
    wounded_opponent = Arena(GameConfig(starting_positions=(3, 4)))
    wounded_opponent.fighters["p2"].health = 50
    damage_against_healthy = rollout.evaluate_branch(
        healthy_opponent, "p1", jab(), rest(),
    )
    damage_against_wounded = rollout.evaluate_branch(
        wounded_opponent, "p1", jab(), rest(),
    )

    healthy_self = Arena(GameConfig(starting_positions=(3, 4)))
    wounded_self = Arena(GameConfig(starting_positions=(3, 4)))
    wounded_self.fighters["p1"].health = 50
    hit_while_healthy = rollout.evaluate_branch(
        healthy_self, "p1", rest(), jab(),
    )
    hit_while_wounded = rollout.evaluate_branch(
        wounded_self, "p1", rest(), jab(),
    )

    assert damage_against_wounded.metrics.damage_dealt == damage_against_healthy.metrics.damage_dealt
    assert damage_against_wounded.score > damage_against_healthy.score
    assert hit_while_wounded.metrics.damage_received == hit_while_healthy.metrics.damage_received
    assert hit_while_wounded.score < hit_while_healthy.score


def test_full_health_uses_the_configured_base_damage_weights():
    rollout = CounterfactualRollout()

    assert rollout._health_scaled_weight(
        rollout.weights.damage_dealt, 100, 100,
    ) == pytest.approx(rollout.weights.damage_dealt)
    assert rollout._health_scaled_weight(
        rollout.weights.damage_received, 100, 100,
    ) == pytest.approx(rollout.weights.damage_received)
    assert rollout._health_scaled_weight(
        rollout.weights.damage_dealt, 50, 100,
    ) == pytest.approx(rollout.weights.damage_dealt * 1.5)
    assert rollout._health_scaled_weight(
        rollout.weights.damage_received, 50, 100,
    ) == pytest.approx(rollout.weights.damage_received * 1.5)


def test_rollout_uses_500ms_decision_boundaries_and_excludes_active_time_from_lock():
    short_lock = Arena(GameConfig(skills=configured_skill(
        "jab", windup_ms=50, active_ms=1000, recovery_ms=100,
    )))
    short = CounterfactualRollout().evaluate_branch(short_lock, "p1", jab(), jab())
    assert short.metrics.elapsed_ms == 500
    assert short.metrics.ready_players == ["p1", "p2"]
    assert short.metrics.ongoing_actions == {"p1": 1, "p2": 1}

    long_lock = Arena(GameConfig(skills=configured_skill(
        "jab", windup_ms=100, active_ms=1000, recovery_ms=500,
    )))
    long = CounterfactualRollout().evaluate_branch(long_lock, "p1", jab(), jab())
    assert long.metrics.elapsed_ms == 1000
    assert long.metrics.ready_players == ["p1", "p2"]
    assert long.metrics.ongoing_actions == {"p1": 1, "p2": 1}


def test_existing_projectile_continues_in_cloned_branch_without_mutating_arena():
    arena = Arena(GameConfig(
        starting_positions=(3, 4.5),
        skills=configured_skill(
            "jab", windup_ms=0, active_ms=2000, recovery_ms=0,
            projectile_speed=2, reach=3,
        ),
    ))
    arena.advance({"p1": jab(), "p2": rest()})
    before = deepcopy(arena.snapshot())
    event_count = len(arena.events)

    branch = CounterfactualRollout().evaluate_branch(arena, "p1", rest(), rest())

    assert branch.metrics.damage_dealt == 10
    assert branch.metrics.ongoing_actions["p1"] >= 1
    assert arena.snapshot() == before
    assert len(arena.events) == event_count
    assert arena.fighters["p2"].health == arena.config.max_health


def test_item_rollouts_score_consumption_and_preserve_unspent_inventory():
    config = with_items(GameConfig(), {
        "p1": ["healing_potion"], "p2": ["healing_potion"],
    })
    arena = Arena(config)
    arena.fighters["p1"].health = 50
    arena.fighters["p2"].health = 50
    rollout = CounterfactualRollout()

    use_own = rollout.evaluate_branch(
        arena, "p1", Action(skill="use_item", item_id="healing_potion"), rest(),
    )
    preserve = rollout.evaluate_branch(arena, "p1", rest(), rest())
    enemy_spends = rollout.evaluate_branch(
        arena, "p1", rest(), Action(skill="use_item", item_id="healing_potion"),
    )

    assert use_own.metrics.damage_received == -25
    assert use_own.metrics.items_spent == {"healing_potion": 1}
    assert use_own.score > preserve.score
    assert preserve.metrics.items_spent == {}
    assert enemy_spends.metrics.opponent_items_spent == {"healing_potion": 1}
    assert arena.fighters["p1"].items["healing_potion"] == 1
    assert arena.fighters["p2"].items["healing_potion"] == 1


def test_rollout_keeps_only_the_best_risk_weighted_teleport_position():
    arena = Arena(with_characters(GameConfig(), {
        "p1": "crimson_blade", "p2": "frost_bell",
    }))
    rollout = CounterfactualRollout()
    actions = rollout.candidate_actions(arena, "p2")
    teleports = [action for action in actions if action.position is not None]
    opponent_actions = rollout.candidate_actions(arena, "p1")
    assert len(teleports) == 5

    def score(action):
        scores = [rollout.evaluate_branch(arena, "p2", action, response).score
                  for response in opponent_actions]
        return rollout.weights.risk_aversion * min(scores) + (
            1 - rollout.weights.risk_aversion
        ) * fmean(scores)

    expected = max(teleports, key=lambda action: (score(action), action.position))
    analysis = rollout.analyze(arena, "p2")
    evaluated = [item for item in analysis.evaluations if item.action.position is not None]
    summarized = [item for item in analysis.prompt_summary()["candidates"]
                  if "position" in item["action"]]

    assert [item.action for item in evaluated] == [expected]
    assert [item["action"] for item in summarized] == [expected.model_dump(exclude_none=True)]


def test_counterfactual_agent_completes_match_without_rejected_or_fallback_actions():
    arena = Arena(GameConfig(time_limit_ms=1000))
    decisions = []
    summary = run_match(arena, {
        "p1": CounterfactualRolloutAgent(), "p2": TestAgent(),
    }, on_decision=decisions.append)

    assert summary["event_counts"].get("rejected", 0) == 0
    assert summary["event_counts"].get("fallback", 0) == 0
    assert summary["decisions"]["p1"] > 0
    detail = decisions[0]["details"]["p1"]
    assert detail["source"] == "counterfactual"
    assert detail["rollout_recommendation"] == decisions[0]["actions"]["p1"]
    assert isinstance(detail["risk_weighted_score"], float)


def test_deepseek_receives_engine_generated_counterfactual_analysis():
    observations = []

    def provider(request):
        body = json.loads(request.content)
        observations.append(json.loads(body["messages"][1]["content"]))
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "tool_calls",
            "message": {"tool_calls": [{"type": "function", "function": {
                "name": "rest",
                "arguments": json.dumps({"decision_summary": "保留资源并观察对手。"}),
            }}]},
        }]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match(
                "counterfactual_deepseek", "test", GameConfig(time_limit_ms=500),
                settings=DeepSeekSettings(api_key="test-only"), client=client,
            )

    recording = asyncio.run(run())
    analysis = observations[0]["counterfactual_analysis"]
    details = recording.decisions[0].details["p1"]

    assert analysis["generated_by"] == "engine_counterfactual_rollout"
    assert analysis["horizon"] == "next_decision_event"
    assert analysis["candidates"]
    assert "recommended_action" not in analysis
    assert "recommended_score_gap" not in analysis
    assert all(0 <= item["preference"] <= 100 for item in analysis["candidates"])
    labels = [item["action"]["skill"] + str(
        item["action"].get("direction", item["action"].get("position", item["action"].get("item_id", "")))
    ) for item in analysis["candidates"]]
    assert labels == sorted(labels)
    assert details.rollout_recommendation is not None
    assert details.risk_weighted_score is not None


def test_forced_knockout_is_executed_without_http_request(tmp_path):
    p1 = load_character("crimson_blade")
    p2 = load_character("frost_bell").model_copy(update={"max_health": 1})
    skills = dict(p1.skills)
    skills["jab"] = skills["jab"].model_copy(update={"reach": 12, "windup_ms": 0})
    p1 = p1.model_copy(update={"skills": skills})
    config = GameConfig(
        characters={"p1": p1, "p2": p2},
        starting_positions=(3, 9), time_limit_ms=500,
    )
    request_count = 0

    def provider(_request):
        nonlocal request_count
        request_count += 1
        return httpx.Response(500)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match(
                "counterfactual_deepseek", "test", config,
                settings=DeepSeekSettings(api_key="test-only"), client=client,
                store=ReplayStore(tmp_path),
            )

    recording = asyncio.run(run())
    detail = recording.decisions[0].details["p1"]

    assert request_count == 0
    assert recording.summary["result"]["winner"] == "p1"
    assert recording.summary["result"]["reason"] == "knockout"
    assert detail.source == "counterfactual"
    assert detail.attempts == 0
    assert detail.total_tokens == 0
    turn_log = ReplayStore(tmp_path).turn_log_path(
        recording.replay_id, 1,
    ).read_text(encoding="utf-8")
    assert "[P1] FULL ENGINE EVALUATION" in turn_log
    assert "[P1] ENGINE DECISION" in turn_log
    assert "Decision source: counterfactual" in turn_log
    assert "DECISION CONTEXT SENT TO LLM" not in turn_log
    assert "Model: - | attempts 0" not in turn_log


def test_counterfactual_summary_uses_character_specific_tool_names():
    config = with_characters(GameConfig(), {
        "p1": "crimson_blade", "p2": "frost_bell",
    })
    arena = Arena(config)
    observation = arena.observe("p1")
    observation["counterfactual_analysis"] = CounterfactualRollout().analyze(
        arena, "p1",
    ).prompt_summary()

    view = model_observation(observation)
    analysis = view["counterfactual_analysis"]
    own_names = set(tool_names(observation, "p1").values())
    enemy_names = set(tool_names(observation, "p2").values())

    assert analysis["recommended_action"]["skill"] in own_names
    assert all(item["action"]["skill"] in own_names for item in analysis["candidates"])
    assert all(item["worst_response"]["skill"] in enemy_names | {"continue"}
               for item in analysis["candidates"])


def test_counterfactual_websocket_streams_first_cycle_instead_of_full_match(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        with client.websocket_connect("/api/live") as socket:
            socket.send_json({
                "p1": "counterfactual", "p2": "test",
                "config": {"time_limit_ms": 500},
            })
            started = socket.receive_json()
            waiting = socket.receive_json()
            cycle = socket.receive_json()
            finished = socket.receive_json()

    assert started["type"] == "started"
    assert waiting == {"type": "waiting", "players": [], "simulation_time": 0.0}
    assert cycle["type"] == "cycle"
    assert cycle["decision"]["details"]["p1"]["source"] == "counterfactual"
    assert finished["type"] == "finished"
