import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

from backend.evaluation.analysis import analyze_report, recording_behavior
from backend.evaluation.runner import default_suite, run_evaluation
from backend.evaluation.store import EvaluationStore
from backend.game.models import Action


def recording(decisions, events=(), characters=None):
    return SimpleNamespace(
        decisions=[SimpleNamespace(actions={"p1": action}, details={"p1": SimpleNamespace(
            source=source, rollout_recommendation=recommendation)})
            for action, source, recommendation in decisions],
        events=[SimpleNamespace(actor=actor, status=status) for actor, status in events],
        config=SimpleNamespace(characters=characters or {}),
        frames=[SimpleNamespace(fighters={p: SimpleNamespace(health=50) for p in ("p1", "p2")})],
    )


def test_behavior_compares_parameters_and_excludes_non_model_decisions():
    forward = Action(skill="move", direction="forward")
    backward = Action(skill="move", direction="backward")
    teleport = Action(skill="dash", position=3)
    result = recording_behavior(recording([
        (backward, "llm", forward),
        (teleport, "llm", Action(skill="dash", position=4)),
        (forward, "llm", forward),
        (forward, "llm", None),
        (backward, "fallback", forward),
        (backward, "counterfactual", forward),
    ], [("p1", "hit"), ("p1", "missed"), ("p1", "projectile"), ("p2", "hit")]))["p1"]
    assert result["recommendation_comparisons"] == 3
    assert result["recommendation_deviations"] == 2
    assert result["actions"] == 6
    assert result["action_counts"] == {"move": 5, "dash": 1}
    assert result["direction_counts"] == {"backward": 3, "forward": 2}
    assert result["hits"] == result["misses"] == 1


def test_aggregation_weights_nulls_failures_and_character_identity():
    rest = Action(skill="rest")
    move = Action(skill="move", direction="forward")
    first = recording_behavior(recording([(move, "llm", rest)]))["p1"]
    second = recording_behavior(recording([(rest, "llm", rest)] * 3))["p1"]
    report = {"strategies": ["counterfactual_deepseek", "heuristic"], "suite": {"scenarios": [
        {"name": "one", "config": {"characters": {"p1": {"character_id": "mage", "name": "法师"}}}},
        {"name": "two", "config": {"characters": {"p2": {"character_id": "mage", "name": "法师"}}}},
    ]}, "matches": []}
    for scenario, player, behavior, duration in [("one", "p1", first, 10), ("two", "p2", second, 30)]:
        opponent = "p2" if player == "p1" else "p1"
        report["matches"].append({"scenario": scenario, "status": "completed",
            "agents": {player: "counterfactual_deepseek", opponent: "heuristic"},
            "result": {"winner": player if player == "p1" else None, "simulation_time": duration, "reason": "time_limit"},
            "metrics": {player: {"model_decisions": 1, "total_tokens": 100, "behavior": behavior},
                        opponent: {"model_decisions": 0, "total_tokens": 0}}})
    report["matches"].append({"scenario": "one", "status": "failed",
                              "agents": {"p1": "counterfactual_deepseek", "p2": "heuristic"}})
    analysis = analyze_report(report)
    row, missing = analysis["strategies"]
    assert row["deviation_rate"] == .25
    assert row["deviation_match_mean"] == .5
    assert row["duration_seconds"] == 20
    assert row["score_rate"] == .75
    assert row["failed"] == 1
    assert row["rest_rate"] == .75
    assert row["behavior_matches"] == 2
    assert row["time_limit_matches"] == 2
    assert missing["hits"] is missing["deviation_rate"] is None
    assert missing["behavior_matches"] == 0
    mage = next(row for row in analysis["roles"] if row.get("character_id") == "mage")
    assert mage["completed"] == 2  # Same character across both player slots.
    assert analysis["matchups"][0]["score_rate"] == .75


def test_old_reports_backfill_without_writes_and_missing_replays_are_visible(tmp_path):
    store = EvaluationStore(tmp_path)
    report = asyncio.run(run_evaluation(store, default_suite(500), ["heuristic", "counterfactual"]))
    legacy = deepcopy(report)
    del legacy["analysis"]
    for match in legacy["matches"]:
        for metric in match["metrics"].values():
            del metric["behavior"]
    store.save(legacy)
    path = store.run_directory(report["run_id"]) / "report.json"
    original = path.read_bytes()
    assert store.detail(report["run_id"]) == report
    assert path.read_bytes() == original
    replay = store.replay_path(report["run_id"], report["matches"][0]["replay_id"])
    replay.write_text('invalid replay', encoding="utf-8")
    partial = store.detail(report["run_id"])
    assert all(row["completed"] == 4 and row["behavior_matches"] == 3 for row in partial["analysis"]["strategies"])
    assert partial["analysis"]["strategies"][0]["deviation_rate"] is None
    assert json.loads(path.read_text(encoding="utf-8")) == legacy


def test_matchups_separate_both_characters_and_merge_player_slots_and_mirrors():
    sword = {"character_id": "sword", "name": "剑士"}
    mage = {"character_id": "mage", "name": "法师"}
    report = {"strategies": ["deepseek", "heuristic"], "suite": {"scenarios": [
        {"name": "normal", "config": {"characters": {"p1": sword, "p2": mage}}},
        {"name": "swapped", "config": {"characters": {"p1": mage, "p2": sword}}},
        {"name": "same", "config": {"characters": {"p1": sword, "p2": sword}}},
    ]}, "matches": []}
    for scenario, player, winner, mirrored in [
        ("normal", "p1", "p1", False),
        ("normal", "p1", "p1", True),
        ("swapped", "p2", "p1", False),
        ("normal", "p2", None, False),
        ("same", "p1", "p2", False),
    ]:
        opponent = "p2" if player == "p1" else "p1"
        report["matches"].append({"scenario": scenario, "status": "completed", "mirrored": mirrored,
            "agents": {player: "deepseek", opponent: "heuristic"},
            "result": {"winner": winner, "simulation_time": 10, "reason": "time_limit"},
            "metrics": {p: {"model_decisions": 0, "total_tokens": 0} for p in ("p1", "p2")}})
    rows = analyze_report(report)["matchups"]
    own = {(row["character_id"], row["opponent_character_id"]): row
           for row in rows if row["strategy"] == "deepseek"}
    assert set(own) == {("sword", "mage"), ("mage", "sword"), ("sword", "sword")}
    main = own["sword", "mage"]
    assert (main["wins"], main["draws"], main["losses"], main["completed"]) == (2, 0, 1, 3)
    assert (main["character_name"], main["opponent_character_name"]) == ("剑士", "法师")
    assert own["mage", "sword"]["draws"] == 1
    assert own["sword", "sword"]["losses"] == 1
    reverse = next(row for row in rows if row["strategy"] == "heuristic"
                   and row["character_id"] == "mage" and row["opponent_character_id"] == "sword")
    assert (reverse["wins"], reverse["losses"]) == (1, 2)
