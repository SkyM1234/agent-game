"""Descriptive evaluation statistics; never runs agents or the game engine."""
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from statistics import fmean

from backend.replay.recording import decode_recording

VERSION = 1
METHODOLOGY = [
    "角色统计按人物 ID 分组，不将 P1/P2 当作职业；交手结果按双方的策略与人物组合分别统计，合并镜像站位与重复场次。",
    "推荐偏离只统计 source=llm 且保存了推演推荐的决策，比较完整动作参数；自动终局和保底不计入。偏离不等于错误，同分动作也可能不同。",
    "加权偏离率 = 偏离次数 / 可比较决策数；逐场平均偏离率只对存在可比较决策的场次等权平均。",
    "动作占比以提交动作数为分母，后退包含方向为 backward 的移动和疾步；传送不推断为后退。命中/落空是回放事件数，不等于全部攻击的命中率。",
    "时长为模拟时间，非模型等待时间；击倒包括双方同时击倒。缺少或损坏的旧回放不补零，行为统计单独显示覆盖场次。",
]


def recording_behavior(recording) -> dict:
    result = {}
    for player in ("p1", "p2"):
        counts = Counter()
        directions = Counter()
        compared = deviated = 0
        for decision in recording.decisions:
            action = decision.actions.get(player)
            if action is None:
                continue
            counts[action.skill] += 1
            if action.direction:
                directions[action.direction] += 1
            detail = decision.details.get(player)
            if detail and detail.source == "llm" and detail.rollout_recommendation is not None:
                compared += 1
                deviated += action != detail.rollout_recommendation
        events = Counter(event.status for event in recording.events if event.actor == player)
        character = recording.config.characters.get(player)
        result[player] = {
            "version": VERSION,
            "character_id": character.character_id if character else "default",
            "character_name": character.name if character else "默认角色",
            "actions": sum(counts.values()), "action_counts": dict(counts),
            "direction_counts": dict(directions),
            "recommendation_comparisons": compared, "recommendation_deviations": deviated,
            "hits": events["hit"], "misses": events["missed"],
            "final_health": recording.frames[-1].fighters[player].health,
        }
    return result


@lru_cache(maxsize=256)
def _replay_behavior(path: str, modified: int, size: int) -> tuple:
    # Stat fields invalidate cached statistics when a replay is replaced.
    recording = decode_recording(Path(path).read_text(encoding="utf-8"))
    return recording.replay_id, recording.config_hash, recording.agents, recording_behavior(recording)


def backfill_behavior(report: dict, directory: Path) -> None:
    """Enrich the response, preserving the original report and replay files."""
    import re

    for match in report["matches"]:
        if match["status"] != "completed":
            continue
        if all(match["metrics"][p].get("behavior", {}).get("version") == VERSION for p in ("p1", "p2")):
            continue
        replay_id = match.get("replay_id", "")
        if not isinstance(replay_id, str) or not re.fullmatch(r"[a-f0-9]{32}", replay_id):
            continue
        path = directory / "replays" / f"{replay_id}.jsonl"
        try:
            stat = path.stat()
            identity, config_hash, agents, behavior = _replay_behavior(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
            if identity != replay_id or config_hash != match.get("config_hash") or agents != match["agents"]:
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for player in ("p1", "p2"):
            match["metrics"][player]["behavior"] = behavior[player]


def _summarize(appearances: list[tuple[dict, str]]) -> dict:
    completed = [(m, p) for m, p in appearances if m["status"] == "completed"]
    count = len(completed)
    wins = sum(m["result"]["winner"] == p for m, p in completed)
    draws = sum(m["result"]["winner"] is None for m, _ in completed)
    metrics = [m["metrics"][p] for m, p in completed]
    behavior = [item["behavior"] for item in metrics if item.get("behavior", {}).get("version") == VERSION]
    comparisons = sum(item["recommendation_comparisons"] for item in behavior)
    deviations = sum(item["recommendation_deviations"] for item in behavior)
    per_match = [item["recommendation_deviations"] / item["recommendation_comparisons"]
                 for item in behavior if item["recommendation_comparisons"]]
    actions = sum(item["actions"] for item in behavior)
    action_counts, direction_counts = Counter(), Counter()
    for item in behavior:
        action_counts.update(item["action_counts"])
        direction_counts.update(item["direction_counts"])
    duration = [m["result"]["simulation_time"] for m, _ in completed]
    return {
        "completed": count, "failed": len(appearances) - count,
        "wins": wins, "draws": draws, "losses": count - wins - draws,
        "win_rate": wins / count if count else None,
        "score_rate": (wins + draws * 0.5) / count if count else None,
        "duration_seconds": fmean(duration) if duration else None,
        "time_limit_matches": sum(m["result"]["reason"] == "time_limit" for m, _ in completed),
        "knockout_matches": sum(m["result"]["reason"] in ("knockout", "double_knockout") for m, _ in completed),
        "model_decisions": sum(item["model_decisions"] for item in metrics),
        "tokens_per_match": fmean(item["total_tokens"] for item in metrics) if metrics else None,
        "behavior_matches": len(behavior), "actions": actions,
        "action_counts": dict(action_counts), "direction_counts": dict(direction_counts),
        "backward_rate": direction_counts["backward"] / actions if actions else None,
        "rest_rate": action_counts["rest"] / actions if actions else None,
        "recommendation_comparisons": comparisons, "recommendation_deviations": deviations,
        "deviation_rate": deviations / comparisons if comparisons else None,
        "deviation_match_mean": fmean(per_match) if per_match else None,
        "deviation_matches": len(per_match),
        "hits": sum(item["hits"] for item in behavior) if behavior else None,
        "misses": sum(item["misses"] for item in behavior) if behavior else None,
    }


def analyze_report(report: dict) -> dict:
    strategies, roles, matchups = defaultdict(list), defaultdict(list), defaultdict(list)
    names = {}
    configs = {s["name"]: s["config"] for s in report["suite"]["scenarios"]}
    for match in report["matches"]:
        for player, strategy in match["agents"].items():
            character = configs.get(match["scenario"], {}).get("characters", {}).get(player, {})
            character_id = character.get("character_id", "default")
            names[character_id] = character.get("name", "默认角色")
            appearance = (match, player)
            strategies[strategy].append(appearance)
            roles[strategy, character_id].append(appearance)
            opponent_player = "p2" if player == "p1" else "p1"
            opponent = match["agents"][opponent_player]
            opponent_character = configs.get(match["scenario"], {}).get("characters", {}).get(opponent_player, {})
            opponent_character_id = opponent_character.get("character_id", "default")
            matchups[strategy, character_id, opponent, opponent_character_id].append(appearance)
    return {
        "version": VERSION, "methodology": METHODOLOGY,
        "strategies": [{"strategy": strategy, **_summarize(strategies[strategy])} for strategy in report["strategies"]],
        "roles": [{"strategy": strategy, "character_id": character, "character_name": names[character],
                   **_summarize(samples)} for (strategy, character), samples in roles.items()],
        "matchups": [{"strategy": strategy, "character_id": character, "character_name": names[character],
                      "opponent": opponent, "opponent_character_id": opponent_character,
                      "opponent_character_name": names[opponent_character], **_summarize(samples)}
                     for (strategy, character, opponent, opponent_character), samples in matchups.items()],
    }
