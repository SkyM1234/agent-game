import asyncio
from copy import deepcopy
import json
import sys

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.agents.deepseek import DeepSeekSettings
from backend.agents.heuristic import HeuristicAgent
from backend.agents.planning import CounterfactualRollout
from backend.api.app import create_app
from backend.evaluation.__main__ import main
from backend.evaluation.runner import (
    Prices, Scenario, Suite, aggregate, default_suite, match_plan, percentile, player_metrics, run_evaluation,
)
from backend.evaluation.store import EvaluationStore
from backend.game import Arena, GameConfig
from backend.game.characters import load_character
from backend.matches.live import record_live_match


def test_schedule_balances_roles_and_sides_and_freezes_suite():
    suite = default_suite(500)
    before = suite.model_dump()
    plan = match_plan(suite, ["heuristic", "counterfactual"], repeats=2)
    assert len(plan) == 8
    for scenario in suite.scenarios:
        matches = [item for item in plan if item["scenario"] == scenario.name]
        for strategy in ("heuristic", "counterfactual"):
            for player in ("p1", "p2"):
                appearances = [item for item in matches if item["agents"][player] == strategy]
                assert len(appearances) == 4
                assert {item["mirrored"] for item in appearances} == {False, True}
    assert suite.model_dump() == before
    with pytest.raises(ValueError):
        match_plan(suite, ["heuristic", "heuristic"], 1)


@pytest.mark.parametrize("distance", [0.8, 2, 3.5, 6, 10, 11.2])
def test_single_distance_is_preserved_in_every_scheduled_match(distance):
    suite = default_suite(500, distance=distance)
    assert len(suite.scenarios) == 1
    plan = match_plan(suite, ["heuristic", "counterfactual"], repeats=2)
    assert len(plan) == 8
    for item in plan:
        config = GameConfig.model_validate(item["config"])
        left, right = config.starting_positions
        assert abs(right - left) == pytest.approx(distance)
        assert (left + right) / 2 == pytest.approx(config.arena_width / 2)


@pytest.mark.parametrize("arguments, distance", [([], 6), (["--distance", "3.5"], 3.5)])
@pytest.mark.parametrize("prompt_variant", [None, "neutral", "aggressive"])
def test_cli_records_only_the_requested_distance(tmp_path, monkeypatch, arguments, distance, prompt_variant):
    if prompt_variant is not None:
        arguments = [*arguments, "--prompt-variant", prompt_variant]
    monkeypatch.setattr(sys, "argv", ["evaluation", "--output", str(tmp_path),
                                      "--time-limit-ms", "500", *arguments])
    main()
    report = json.loads(next(tmp_path.glob("*/report.json")).read_text(encoding="utf-8"))
    assert len(report["suite"]["scenarios"]) == 1
    assert report["completed_matches"] == report["planned_matches"] == 4
    store = EvaluationStore(tmp_path)
    for match in report["matches"]:
        recording = store.replay(report["run_id"], match["replay_id"])
        left, right = recording.config.starting_positions
        assert abs(right - left) == pytest.approx(distance)
        expected = default_suite(500, distance=distance, prompt_variant=prompt_variant or "neutral")
        assert recording.config.characters == expected.scenarios[0].config.characters


@pytest.mark.parametrize("arguments,p1_variant,p2_variant", [
    (["--p1-prompt", "aggressive", "--p2-prompt", "neutral"], "aggressive", "neutral"),
    (["--p1-prompt", "neutral", "--p2-prompt", "aggressive"], "neutral", "aggressive"),
    (["--p1-prompt", "aggressive"], "aggressive", "neutral"),
    (["--p2-prompt", "aggressive"], "neutral", "aggressive"),
    (["--prompt-variant", "aggressive", "--p1-prompt", "neutral"], "neutral", "aggressive"),
    (["--prompt-variant", "aggressive", "--p2-prompt", "neutral"], "aggressive", "neutral"),
])
def test_cli_preserves_individual_prompts_in_reports_and_replays(
    tmp_path, monkeypatch, arguments, p1_variant, p2_variant,
):
    monkeypatch.setattr(sys, "argv", ["evaluation", "--output", str(tmp_path),
                                      "--time-limit-ms", "500", *arguments])
    main()
    report = json.loads(next(tmp_path.glob("*/report.json")).read_text(encoding="utf-8"))
    expected = {"p1": load_character("crimson_blade", prompt_variant=p1_variant),
                "p2": load_character("frost_bell", prompt_variant=p2_variant)}
    assert GameConfig.model_validate(report["suite"]["scenarios"][0]["config"]).characters == expected
    assert report["completed_matches"] == report["planned_matches"] == 4
    store = EvaluationStore(tmp_path)
    for match in report["matches"]:
        recording = store.replay(report["run_id"], match["replay_id"])
        assert recording.config.characters == expected


@pytest.mark.parametrize("option", ["--prompt-variant", "--p1-prompt", "--p2-prompt"])
def test_cli_rejects_prompt_override_for_frozen_suite(tmp_path, monkeypatch, capsys, option):
    monkeypatch.setattr(sys, "argv", ["evaluation", "--suite", str(tmp_path / "suite.json"),
                                      option, "aggressive", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "frozen prompts" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("distance", ["-1", "0", "0.79", "11.21", "nan", "inf", "abc"])
def test_cli_rejects_invalid_distance_before_writing_report(tmp_path, monkeypatch, capsys, distance):
    monkeypatch.setattr(sys, "argv", ["evaluation", "--output", str(tmp_path), "--distance", distance])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "distance" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("as_report", [False, True])
def test_cli_reuses_single_scenario_configuration(tmp_path, monkeypatch, as_report):
    suite = default_suite(500, distance=3.5)
    payload = suite.model_dump(mode="json")
    source = tmp_path / "suite.json"
    source.write_text(json.dumps({"suite": payload} if as_report else payload), encoding="utf-8")
    output = tmp_path / "evaluations"
    monkeypatch.setattr(sys, "argv", ["evaluation", "--output", str(output), "--suite", str(source)])
    main()
    report = json.loads(next(output.glob("*/report.json")).read_text(encoding="utf-8"))
    assert report["suite"] == payload
    assert report["completed_matches"] == report["planned_matches"] == 4


@pytest.mark.parametrize("extra, message", [([], "at most 1 item"), (["--distance", "3.5"], "not allowed")])
def test_cli_rejects_multiple_scenarios_and_conflicting_distance(tmp_path, monkeypatch, capsys, extra, message):
    payload = {"scenarios": [default_suite(500, distance=distance).scenarios[0].model_dump(mode="json")
                             for distance in (2, 6, 10)]}
    source = tmp_path / "suite.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "evaluations"
    monkeypatch.setattr(sys, "argv", ["evaluation", "--output", str(output), "--suite", str(source), *extra])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert message in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("scenario", [default_suite(10000, distance=distance).scenarios[0] for distance in (2, 6, 10)])
def test_baseline_plays_legal_actions_in_both_orientations(scenario):
    for positions in (scenario.config.starting_positions, tuple(reversed(scenario.config.starting_positions))):
        arena = Arena(scenario.config.model_copy(update={"starting_positions": positions}))
        agents = {player: HeuristicAgent() for player in ("p1", "p2")}
        while arena.result is None:
            events = arena.advance({player: agent.decide(arena.observe(player)) for player, agent in agents.items()
                                    if arena.can_decide(player)})
            assert not any(event.status in ("rejected", "fallback") for event in events)


def test_pure_llm_never_invokes_rollout_and_metrics_include_repair(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pure LLM must not inspect counterfactual branches")
    monkeypatch.setattr(CounterfactualRollout, "analyze", forbidden)
    requests = []
    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        assert "counterfactual_analysis" not in json.loads(body["messages"][1]["content"])
        name = "not_a_tool" if len(requests) == 1 else "rest"
        return httpx.Response(200, json={"model": "controlled-model", "usage": {
            "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
        }, "choices": [{"message": {"tool_calls": [{"type": "function", "function": {
            "name": name, "arguments": json.dumps({"decision_summary": "恢复资源。"}),
        }}]}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await record_live_match("deepseek", "heuristic", GameConfig(time_limit_ms=500),
                                           client=client, settings=DeepSeekSettings(api_key="test-only"))
    recording = asyncio.run(run())
    metrics = player_metrics(recording, "p1", Prices(input_per_million=2, output_per_million=4))
    assert len(requests) == 2
    assert metrics["first_valid"] == 0
    assert metrics["repaired"] == 1
    assert metrics["fallbacks"] == 0
    assert metrics["total_tokens"] == 240
    assert metrics["estimated_cost"] == pytest.approx(0.00056)
    assert metrics["decision_latencies_ms"][0] >= metrics["model_latencies_ms"][0]


def test_rates_distinguish_draws_failures_and_non_model_strategies():
    recording = asyncio.run(record_live_match("heuristic", "heuristic", GameConfig(time_limit_ms=500)))
    metrics = player_metrics(recording, "p1", None)
    matches = [{"agents": {"p1": "heuristic", "p2": "counterfactual"}, "status": "completed",
                "result": {"winner": winner}, "metrics": {"p1": deepcopy(metrics), "p2": deepcopy(metrics)}}
               for winner in ("p1", "p2", None)]
    matches.append({"agents": {"p1": "heuristic", "p2": "counterfactual"}, "status": "failed"})
    row = aggregate(matches, ["heuristic"])[0]
    assert (row["wins"], row["draws"], row["losses"], row["failed"]) == (1, 1, 1, 1)
    assert row["win_rate"] == pytest.approx(1 / 3)
    assert row["score_rate"] == 0.5
    assert row["completion_rate"] == 0.75
    assert row["first_valid_rate"] is None
    assert row["cost_per_match"] == 0
    assert percentile([10, 20, 30], 0.95) == 29
    assert percentile([], 0.95) is None


def test_completed_evaluation_roundtrip_and_read_only_api(tmp_path):
    store = EvaluationStore(tmp_path / "evaluations")
    suite = Suite(scenarios=[Scenario(name="短局", config=GameConfig(time_limit_ms=500))])
    report = asyncio.run(run_evaluation(store, suite, ["heuristic", "counterfactual"]))
    assert report["completed_matches"] == report["planned_matches"] == 4
    assert report["status"] == "completed"
    assert store.load(report["run_id"]) == report
    replay_id = report["matches"][0]["replay_id"]
    prefix = f"/api/evaluations/{report['run_id']}"
    with TestClient(create_app(tmp_path)) as client:
        assert len(client.get("/api/evaluations").json()) == 1
        assert client.get(prefix).json() == report
        assert client.get(prefix + "/export").json() == report
        replay = client.get(prefix + f"/replays/{replay_id}")
        assert replay.status_code == 200
        assert replay.json()["config_hash"] == report["matches"][0]["config_hash"]
        assert client.get(prefix + f"/replays/{replay_id}/export").status_code == 200
        assert client.get(prefix + "/replays/" + "0" * 32).status_code == 404
        assert client.get("/api/replays").json() == []
        assert client.get("/api/evaluations/not-an-id").status_code == 404
    repeated = asyncio.run(run_evaluation(store, Suite.model_validate(report["suite"]), report["strategies"]))
    assert [item["result"] for item in repeated["matches"]] == [item["result"] for item in report["matches"]]
    app = create_app(tmp_path)
    app.state.store.save(store.replay(report["run_id"], replay_id))
    with TestClient(app) as client:
        store.save({**report, "status": "running"})
        assert client.delete(prefix).status_code == 409
        assert store.replay_path(report["run_id"], replay_id).exists()
        store.save(report)
        assert client.delete(prefix).status_code == 204
        assert not store.run_directory(report["run_id"]).exists()
        assert client.get(prefix).status_code == 404
        assert client.get(prefix + f"/replays/{replay_id}").status_code == 404
        assert client.get(prefix + "/export").status_code == 404
        assert client.delete(prefix).status_code == 404
        assert client.delete("/api/evaluations/not-an-id").status_code == 404
        assert [item["run_id"] for item in client.get("/api/evaluations").json()] == [repeated["run_id"]]
        assert client.get(f"/api/replays/{replay_id}").status_code == 200
    with pytest.raises(FileNotFoundError):
        store.load("../outside")
    with pytest.raises(FileNotFoundError):
        store.delete("../outside")


def test_failed_provider_stops_report_without_invented_wins(tmp_path):
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(401)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await run_evaluation(EvaluationStore(tmp_path),
                Suite(scenarios=[Scenario(name="短局", config=GameConfig(time_limit_ms=500))]),
                ["heuristic", "deepseek"], settings=DeepSeekSettings(api_key="test-only"), client=client)
    report = asyncio.run(run())
    assert len(calls) == 1
    assert report["status"] == "stopped"
    assert report["failed_matches"] == 1
    assert report["completed_matches"] == 0
    assert all(row["win_rate"] is None for row in report["summary"])
    assert "test-only" not in json.dumps(report)
