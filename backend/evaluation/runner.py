from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from itertools import combinations
import math
from pathlib import Path
import platform
import subprocess
import time
from uuid import uuid4

import httpx
from pydantic import Field, model_validator

from backend.agents.deepseek import DeepSeekSettings, ProviderError
from backend.game import Arena, GameConfig
from backend.game.characters import PromptVariant, with_characters
from backend.game.models import Schema
from backend.matches.live import MODEL_AGENT_IDS, record_live_match
from backend.replay.recording import RULES_VERSION, config_digest
from backend.replay.store import ReplayStore
from .store import EvaluationStore
from .analysis import analyze_report, recording_behavior

STRATEGIES = ("heuristic", "counterfactual", "deepseek", "counterfactual_deepseek")


class Scenario(Schema):
    name: str = Field(min_length=1, max_length=80)
    config: GameConfig


class Suite(Schema):
    name: str = Field(default="距离对照 v1", min_length=1, max_length=80)
    scenarios: list[Scenario] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def validate_recording_length(self):
        if any(scenario.config.time_limit_ms // scenario.config.step_ms > 2400 for scenario in self.scenarios):
            raise ValueError("evaluation recordings allow at most 2400 simulation steps")
        return self


class Prices(Schema):
    input_per_million: float = Field(ge=0, allow_inf_nan=False)
    output_per_million: float = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")


def default_suite(time_limit_ms: int = 10000, *, distance: float = 6,
                  prompt_variant: PromptVariant = "neutral",
                  prompt_variants: dict[str, PromptVariant] | None = None) -> Suite:
    base = with_characters(GameConfig(time_limit_ms=time_limit_ms),
                           {"p1": "crimson_blade", "p2": "frost_bell"},
                           prompt_variant=prompt_variant, prompt_variants=prompt_variants)
    minimum = 2 * base.fighter_radius
    maximum = base.arena_width - minimum
    if not math.isfinite(distance) or not minimum <= distance <= maximum:
        raise ValueError(f"distance must be finite and between {minimum:g} and {maximum:g}")
    positions = ((base.arena_width - distance) / 2, (base.arena_width + distance) / 2)
    return Suite(scenarios=[Scenario(name=f"距离 {distance:g}", config=GameConfig.model_validate({
        **base.model_dump(), "starting_positions": positions,
    }))])


def match_plan(suite: Suite, strategies: list[str], repeats: int) -> list[dict]:
    if len(strategies) < 2 or len(set(strategies)) != len(strategies) or any(name not in STRATEGIES for name in strategies):
        raise ValueError("select at least two distinct evaluation strategies")
    if not 1 <= repeats <= 100:
        raise ValueError("repeats must be between 1 and 100")
    plan = []
    for scenario in suite.scenarios:
        for first, second in combinations(strategies, 2):
            for repeat in range(1, repeats + 1):
                for mirrored in (False, True):
                    config = scenario.config.model_dump()
                    if mirrored:
                        config["starting_positions"] = [config["arena_width"] - x for x in config["starting_positions"]]
                    for p1, p2 in ((first, second), (second, first)):
                        plan.append({"scenario": scenario.name, "repeat": repeat, "mirrored": mirrored,
                                     "agents": {"p1": p1, "p2": p2}, "config": config})
    return plan


def percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def player_metrics(recording, player: str, prices: Prices | None) -> dict:
    details = [decision.details[player] for decision in recording.decisions if player in decision.details]
    model = [detail for detail in details if detail.attempts > 0]
    prompt_tokens = sum(detail.prompt_tokens for detail in details)
    completion_tokens = sum(detail.completion_tokens for detail in details)
    return {
        "decisions": len(details), "model_decisions": len(model),
        "first_valid": sum(detail.source == "llm" and detail.attempts == 1 and not detail.errors for detail in model),
        "repaired": sum(detail.source == "llm" and detail.attempts > 1 for detail in model),
        "fallbacks": sum(detail.source == "fallback" for detail in details),
        "attempts": sum(detail.attempts for detail in details),
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "total_tokens": sum(detail.total_tokens for detail in details),
        "decision_latencies_ms": [detail.decision_latency_ms for detail in details],
        "model_latencies_ms": [detail.latency_ms for detail in model],
        "damage_dealt": sum(event.effects.get("health_damage", 0) for event in recording.events if event.actor == player),
        "rejected_actions": sum(event.status == "rejected" and event.actor == player for event in recording.events),
        "estimated_cost": ((prompt_tokens * prices.input_per_million + completion_tokens * prices.output_per_million) / 1_000_000
                           if prices else (0 if not model else None)),
        "returned_models": sorted({detail.returned_model for detail in model if detail.returned_model}),
    }


def aggregate(matches: list[dict], strategies: list[str]) -> list[dict]:
    rows = []
    for strategy in strategies:
        appearances = [(match, player) for match in matches for player, name in match["agents"].items() if name == strategy]
        completed = [(match, player) for match, player in appearances if match["status"] == "completed"]
        metrics = [match["metrics"][player] for match, player in completed]
        count = len(completed)
        wins = sum(match["result"]["winner"] == player for match, player in completed)
        draws = sum(match["result"]["winner"] is None for match, _ in completed)
        model_count = sum(item["model_decisions"] for item in metrics)
        decision_latencies = [value for item in metrics for value in item["decision_latencies_ms"]]
        model_latencies = [value for item in metrics for value in item["model_latencies_ms"]]
        costs = [item["estimated_cost"] for item in metrics]
        rows.append({
            "strategy": strategy, "completed": count, "failed": len(appearances) - count,
            "wins": wins, "draws": draws, "losses": count - wins - draws,
            "win_rate": wins / count if count else None,
            "score_rate": (wins + draws * 0.5) / count if count else None,
            "completion_rate": count / len(appearances) if appearances else None,
            "model_decisions": model_count,
            "first_valid_rate": sum(item["first_valid"] for item in metrics) / model_count if model_count else None,
            "repair_rate": sum(item["repaired"] for item in metrics) / model_count if model_count else None,
            "fallback_rate": sum(item["fallbacks"] for item in metrics) / model_count if model_count else None,
            "decision_p50_ms": percentile(decision_latencies, 0.5),
            "decision_p95_ms": percentile(decision_latencies, 0.95),
            "model_p95_ms": percentile(model_latencies, 0.95),
            "tokens_per_match": sum(item["total_tokens"] for item in metrics) / count if count else None,
            "cost_per_match": sum(costs) / count if count and all(value is not None for value in costs) else None,
            "damage_per_match": sum(item["damage_dealt"] for item in metrics) / count if count else None,
            "rejected_actions": sum(item["rejected_actions"] for item in metrics),
        })
    return rows


def source_metadata() -> dict:
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted((root / "backend").rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"commit": commit, "backend_sha256": digest.hexdigest(), "python": platform.python_version(),
            "platform": platform.platform(), "rules_version": RULES_VERSION}


async def run_evaluation(store: EvaluationStore, suite: Suite, strategies: list[str], *, repeats: int = 1,
                         settings: DeepSeekSettings | None = None, prices: Prices | None = None,
                         client: httpx.AsyncClient | None = None, progress=None) -> dict:
    # Freeze defaults/character packs before scheduling, including custom suites.
    suite = Suite(name=suite.name, scenarios=[Scenario(name=item.name, config=Arena(item.config).config)
                                            for item in suite.scenarios])
    plan = match_plan(suite, strategies, repeats)
    use_model = any(name in MODEL_AGENT_IDS for name in strategies)
    settings = settings or (DeepSeekSettings.from_env() if use_model else DeepSeekSettings())
    if use_model and not settings.ready:
        raise ProviderError("missing_api_key")
    report = {
        "schema_version": 1, "run_id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "running", "strategies": strategies, "repeats": repeats,
        "planned_matches": len(plan), "completed_matches": 0, "failed_matches": 0,
        "suite": suite.model_dump(mode="json"), "source": source_metadata(),
        "model": settings.public_metadata() if use_model else None,
        "prices": prices.model_dump() if prices else None,
        "matches": [], "summary": [],
        "methodology": [
            "每个策略组合在每个局面交换角色归属，并镜像出生位置，共四场；所有策略使用相同合法动作约束。",
            "纯 LLM 与混合策略共享模型、提示词和历史窗口；纯 LLM 不接收推演结果，也不走必然击倒捷径。",
            "胜率 = 胜场 / 完成场次；得分率 = (胜场 + 0.5 × 平局) / 完成场次；失败单独报告。",
            "动作、延迟和用量指标仅统计完成场次；首次成功/修正/保底率以实际请求模型的决策次数为分母。",
            "决策延迟包含本策略推演和模型等待，不含动画、存储与对方预计算；并发和硬件影响耗时。",
            "费用按服务端返回的输入/输出 Token 和手填单价估算，未区分缓存折扣；失败请求费用可能未计入。",
            "固定局面支持复跑；LLM 输出不保证逐次一致。本地确定性策略重复运行不增加独立样本。",
            "结果仅描述当前局面集合；短局、角色搭配和小样本可能影响结论，不代表通用胜率。",
        ],
    }
    report["analysis"] = analyze_report(report)
    replay_store = ReplayStore(store.run_directory(report["run_id"]) / "replays")
    store.save(report)
    if progress:
        progress(report)
    try:
        async with httpx.AsyncClient(follow_redirects=False) as owned:
            for index, item in enumerate(plan):
                config = GameConfig.model_validate(item["config"])
                sample = {key: value for key, value in item.items() if key != "config"}
                sample.update(index=index + 1, config_hash=config_digest(config))
                started = time.perf_counter()
                try:
                    recording = await record_live_match(**item["agents"], config=config, settings=settings,
                                                        client=client or owned)
                    replay_store.save(recording)
                    behavior = recording_behavior(recording)
                    sample.update(status="completed", replay_id=recording.replay_id, result=recording.summary["result"],
                                  metrics={player: {**player_metrics(recording, player, prices), "behavior": behavior[player]}
                                           for player in ("p1", "p2")})
                    report["completed_matches"] += 1
                except ProviderError as error:
                    sample.update(status="failed", replay_id=None, error_code=error.code, error_reason=error.reason)
                    report["failed_matches"] += 1
                sample["wall_time_ms"] = (time.perf_counter() - started) * 1000
                report["matches"].append(sample)
                report["summary"] = aggregate(report["matches"], strategies)
                report["analysis"] = analyze_report(report)
                store.save(report)
                if progress:
                    progress(report)
                if sample.get("error_code") in ("authentication_failed", "insufficient_balance", "model_unavailable", "invalid_provider_request"):
                    report["status"] = "stopped"
                    break
            else:
                report["status"] = "completed"
    except BaseException:
        report["status"] = "interrupted"
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        store.save(report)
    return report
