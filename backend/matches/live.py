from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx

from backend.agents.deepseek import (
    SYSTEM_PROMPT, AgentDecision, DecisionInfo, DeepSeekAgent, DeepSeekSettings, ProviderError,
)
from backend.agents.scripted import AGENTS
from backend.agents.character_tools import available_bindings
from backend.agents.planning import CounterfactualRollout, CounterfactualRolloutAgent, action_key
from backend.game import Arena, GameConfig
from backend.game.models import ATTACKS
from backend.replay.recording import FORMAT_VERSION, Recording, RULES_VERSION, config_digest

if TYPE_CHECKING:
    from backend.replay.store import ReplayStore

MODEL_AGENT_ID = "counterfactual_deepseek"
MODEL_AGENT_IDS = ("deepseek", MODEL_AGENT_ID)
AGENT_IDS = (*AGENTS, *MODEL_AGENT_IDS)
RECENT_TURN_LIMIT = 4
Update = Callable[[dict], Awaitable[None]]


def _fighter_changes(before: dict, after: dict) -> dict:
    changes = {}
    for player in before:
        player_changes = {
            key: round(after[player][key] - before[player][key], 3)
            for key in ("position", "health", "stamina", "mana", "shield")
            if abs(after[player][key] - before[player][key]) > 1e-9
        }
        item_ids = set(before[player]["items"]) | set(after[player]["items"])
        item_changes = {
            item_id: after[player]["items"].get(item_id, 0) - before[player]["items"].get(item_id, 0)
            for item_id in item_ids
            if after[player]["items"].get(item_id, 0) != before[player]["items"].get(item_id, 0)
        }
        if item_changes:
            player_changes["items"] = item_changes
        if player_changes:
            changes[player] = player_changes
    return changes


def _empty_behavior_history() -> dict:
    return {
        "through_turn": 0,
        "players": {
            player: {
                "observed_decisions": 0,
                "action_counts": {},
                "direction_counts": {},
                "item_counts": {},
                "outcome_counts": {},
                "health_damage_dealt": 0,
            }
            for player in ("p1", "p2")
        },
    }


def _increment(values: dict, key: str, amount: float = 1) -> None:
    values[key] = values.get(key, 0) + amount


def _accumulate_behavior(history: dict, turn: dict) -> None:
    history["through_turn"] = turn["turn"]
    for player, action in turn["actions"].items():
        behavior = history["players"][player]
        behavior["observed_decisions"] += 1
        _increment(behavior["action_counts"], action["skill"])
        if "direction" in action:
            directions = behavior["direction_counts"].setdefault(action["skill"], {})
            _increment(directions, action["direction"])
        if "item_id" in action:
            _increment(behavior["item_counts"], action["item_id"])
    for event in turn["events"]:
        player = event.get("actor")
        if player not in history["players"]:
            continue
        behavior = history["players"][player]
        _increment(behavior["outcome_counts"], event["status"])
        if event.get("health_damage"):
            behavior["health_damage_dealt"] += event["health_damage"]


def _opponent_history(history: dict, player: str) -> dict | None:
    opponent = "p2" if player == "p1" else "p1"
    behavior = history["players"][opponent]
    if not behavior["observed_decisions"]:
        return None
    return {"through_turn": history["through_turn"], **behavior}


def _structured_payoff_matrix(analysis) -> dict:
    rows = {}
    for outcome in analysis.payoff_matrix:
        key = action_key(outcome.own_action)
        row = rows.setdefault(key, {
            "own_action": outcome.own_action.model_dump(mode="json", exclude_none=True),
            "opponent_responses": [],
        })
        row["opponent_responses"].append({
            "opponent_action": (outcome.opponent_action.model_dump(mode="json", exclude_none=True)
                                if outcome.opponent_action else {"skill": "continue"}),
            "score": outcome.score,
            "metrics": outcome.metrics.model_dump(mode="json"),
        })
    return {"player": analysis.player, "rows": list(rows.values())}


async def record_live_match(
    p1: str = MODEL_AGENT_ID, p2: str = MODEL_AGENT_ID, config: GameConfig | None = None,
    *, settings: DeepSeekSettings | None = None,
    on_update: Update | None = None,
    wait_for_next: Callable[[], Awaitable[None]] | None = None,
    client: httpx.AsyncClient | None = None,
    store: ReplayStore | None = None,
    resume_id: str | None = None,
) -> Recording:
    if client is None:
        async with httpx.AsyncClient(follow_redirects=False) as owned_client:
            return await record_live_match(p1, p2, config, settings=settings,
                                           on_update=on_update, wait_for_next=wait_for_next,
                                           client=owned_client, store=store,
                                           resume_id=resume_id)
    if resume_id and store is None:
        raise ValueError("resume requires a replay store")

    persisted = await asyncio.to_thread(store.load_checkpoint, resume_id) if resume_id else None
    names = persisted["header"]["agents"] if persisted else {"p1": p1, "p2": p2}
    if any(name not in AGENT_IDS for name in names.values()):
        raise ValueError("unknown agent")
    settings = settings or (DeepSeekSettings.from_env()
                            if any(name in MODEL_AGENT_IDS for name in names.values()) else DeepSeekSettings())
    if any(name in MODEL_AGENT_IDS for name in names.values()) and not settings.ready:
        raise ProviderError("missing_api_key")
    agents = {
        player: (DeepSeekAgent(
            settings, client,
            system_prompt=(persisted["header"]["agent_metadata"][player]["system_prompt"]
                           if persisted else SYSTEM_PROMPT),
        ) if name in MODEL_AGENT_IDS else AGENTS[name]())
        for player, name in names.items()
    }
    if persisted:
        for player, state in persisted["checkpoint"].get("agent_states", {}).items():
            if player in agents and hasattr(agents[player], "restore_state"):
                agents[player].restore_state(state)
    counterfactual_rollout = CounterfactualRollout()
    if persisted:
        header = persisted["header"]
        arena = Arena.from_state(GameConfig.model_validate(header["config"]),
                                 persisted["checkpoint"]["arena"])
        frames = [persisted["initial_frame"]]
        decisions = []
        for turn in persisted["turns"]:
            frames.extend(turn["frames"])
            decisions.append(turn["decision"])
        recent_turns = persisted["checkpoint"]["recent_turns"]
        behavior_history = persisted["checkpoint"]["behavior_history"]
        calls = Counter(persisted["checkpoint"].get("calls", {}))
        failures = Counter(persisted["checkpoint"].get("failures", {}))
    else:
        arena = Arena(config)
        frames, decisions = [arena.playback_frame()], []
        recent_turns = []
        behavior_history = _empty_behavior_history()
        calls, failures = Counter(), Counter()
        metadata = {player: settings.public_metadata() if name in MODEL_AGENT_IDS else {"provider": "script", "strategy": name}
                    for player, name in names.items()}
        for player, name in names.items():
            if name in MODEL_AGENT_IDS:
                metadata[player]["system_prompt"] = SYSTEM_PROMPT
        for player, character in arena.config.characters.items():
            if character.agent:
                metadata[player].update(character_id=character.character_id,
                                        character_prompt_version=character.agent.version)
            weapon = arena.config.equipment.get(player)
            if weapon:
                metadata[player]["weapon_id"] = weapon.weapon_id
            if arena.config.items.get(player):
                metadata[player]["item_ids"] = [item.item_id for item in arena.config.items[player]]
        header = {
            "format_version": FORMAT_VERSION, "rules_version": RULES_VERSION,
            "replay_id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
            "config_hash": config_digest(arena.config), "config": arena.config.model_dump(mode="json"),
            "agents": names, "agent_metadata": metadata,
        }

    def checkpoint() -> dict:
        return {
            "arena": arena.export_state(), "recent_turns": recent_turns,
            "behavior_history": behavior_history,
            "calls": dict(calls), "failures": dict(failures),
            "agent_states": {
                player: agent.export_state() for player, agent in agents.items()
                if hasattr(agent, "export_state")
            },
        }

    if store and not persisted:
        await asyncio.to_thread(store.begin_match, header, frames[0], checkpoint())

    async def send(message):
        if on_update:
            await on_update(message)

    await send({"type": "started", "replay": {**header, "frames": frames.copy(),
                "decisions": decisions.copy(), "events": [asdict(event) for event in arena.events],
                "summary": {"result": None}}})
    while arena.result is None:
        before = arena.snapshot()
        observations = {player: arena.observe(player) for player in arena.fighters
                        if arena.can_decide(player)}
        analyses = {}
        planning_times = {}
        for player, observation in observations.items():
            if names[player] == MODEL_AGENT_ID:
                planning_started = time.perf_counter()
                analysis = counterfactual_rollout.analyze(arena, player)
                planning_times[player] = (time.perf_counter() - planning_started) * 1000
                analyses[player] = analysis
                observation["counterfactual_analysis"] = analysis.prompt_summary(model_facing=True)
            if names[player] in MODEL_AGENT_IDS:
                observation["recent_turns"] = [{**turn, "actions": {
                    actor: {key: value for key, value in action.items() if key != "summary"}
                    for actor, action in turn["actions"].items()}}
                    for turn in recent_turns[-RECENT_TURN_LIMIT:]]
                history = _opponent_history(behavior_history, player)
                if history:
                    observation["opponent_history"] = history
        await send({"type": "waiting", "players": [player for player in observations if names[player] in MODEL_AGENT_IDS],
                    "simulation_time": arena.simulation_time})

        async def decide(player, observation):
            agent = agents[player]
            if isinstance(agent, DeepSeekAgent):
                if names[player] == "deepseek":
                    return await agent.decide(observation)
                analysis = analyses[player]
                terminal = analysis.selected_terminal_status
                if analysis.selected_is_forced_knockout:
                    bindings = available_bindings(observation)
                    action = analysis.selected_action
                    selected_tool = next(
                        name for name, tool in bindings.items() if tool["action"] == action.skill
                    )
                    label = {"forced_win": "胜局", "forced_loss": "败局", "forced_draw": "平局"}[terminal]
                    return AgentDecision(action=action, info=DecisionInfo(
                        source="counterfactual",
                        summary=f"权威推演确认所有合法应对均为{label}，直接执行最高收益动作。",
                        available_tools=list(bindings), selected_tool=selected_tool,
                        rollout_recommendation=action,
                        risk_weighted_score=analysis.selected_evaluation.risk_weighted_score,
                    ))
                decision = await agent.decide(observation)
                info = decision.info.model_copy(update={
                    "rollout_recommendation": analysis.selected_action,
                    "risk_weighted_score": analysis.selected_evaluation.risk_weighted_score,
                })
                return decision.model_copy(update={"info": info})
            if isinstance(agent, CounterfactualRolloutAgent):
                action = await asyncio.to_thread(agent.decide_in_arena, arena, player)
                analysis = agent.last_analysis
                analyses[player] = analysis
                bindings = available_bindings(observation)
                selected_tool = next(name for name, tool in bindings.items()
                                     if tool["action"] == action.skill)
                return AgentDecision(action=action, info=DecisionInfo(
                    source="counterfactual", summary="按最坏情况与平均收益的加权结果选择动作。",
                    available_tools=list(bindings), selected_tool=selected_tool,
                    rollout_recommendation=action,
                    risk_weighted_score=analysis.selected_evaluation.risk_weighted_score,
                ))
            action = agent.decide(observation)
            return AgentDecision(action=action, info=DecisionInfo(
                source="script", available_tools=list(available_bindings(observation)),
            ))

        async def timed_decide(player, view):
            started = time.perf_counter()
            decision = await decide(player, view)
            info = decision.info.model_copy(update={
                "decision_latency_ms": ((time.perf_counter() - started) * 1000
                                        + planning_times.get(player, 0)),
            })
            return decision.model_copy(update={"info": info})

        tasks = [asyncio.create_task(timed_decide(player, view)) for player, view in observations.items()]
        try:
            selected = dict(zip(observations, await asyncio.gather(*tasks)))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for player, decision in selected.items():
            calls[player] += 1
            failures[player] = failures[player] + 1 if decision.info.source == "fallback" else 0
            if failures[player] >= settings.max_consecutive_failures:
                raise ProviderError("provider_unavailable", reason=decision.info.errors[-1], player=player)
        actions = {player: decision.action for player, decision in selected.items()}
        decision_details = {}
        for player, decision in selected.items():
            decision_details[player] = decision.info.model_dump(mode="json")
        recorded_decision = {
            "tick": arena.tick, "simulation_time": arena.simulation_time,
            "actions": {player: action.model_dump(exclude_none=True) for player, action in actions.items()},
            "details": decision_details,
        }
        decisions.append(recorded_decision)
        cycle_frames = []
        events = arena.advance(
            actions, on_frame=cycle_frames.append,
            fallback_reasons={player: decision.info.errors[-1] for player, decision in selected.items()
                              if decision.info.source == "fallback"},
        )
        action_ids = {event.actor: event.action_id for event in events if event.status == "started"}
        after = arena.snapshot()
        recent_turns.append({
            "turn": before["turn"],
            "distance": {
                "before": round(abs(before["fighters"]["p1"]["position"]
                                    - before["fighters"]["p2"]["position"]), 3),
                "after": round(abs(after["fighters"]["p1"]["position"]
                                   - after["fighters"]["p2"]["position"]), 3),
            },
            "changes": _fighter_changes(before["fighters"], after["fighters"]),
            "actions": {player: {**action.model_dump(exclude_none=True),
                                   "action_id": action_ids.get(player),
                                   **({"reach": arena.skills_for(arena.fighters[player])[action.skill].reach}
                                      if action.skill in ATTACKS else {}),
                                   "summary": selected[player].info.summary}
                        for player, action in actions.items()},
            "events": [{"actor": event.actor, "action_id": event.action_id, "status": event.status,
                        **({"reason": event.reason} if event.reason else {}),
                        **{key: event.effects[key] for key in ("skill", "item_id", "health_damage", "destination")
                           if key in event.effects}}
                       for event in events if event.status in ("hit", "missed", "rejected", "fallback", "teleported", "item_used")],
        })
        while len(recent_turns) > RECENT_TURN_LIMIT:
            _accumulate_behavior(behavior_history, recent_turns.pop(0))
        frames.extend(cycle_frames)
        turn_payload = {"frames": cycle_frames, "events": [asdict(event) for event in events],
                        "decision": recorded_decision}
        if store:
            turn_log = {
                "turn": before["turn"],
                "payoff_matrices": {
                    player: _structured_payoff_matrix(analysis)
                    for player, analysis in analyses.items()
                },
                "engine_evaluations": {
                    player: analysis.prompt_summary()
                    for player, analysis in analyses.items()
                },
                "model_traces": {
                    player: decision.trace for player, decision in selected.items()
                    if decision.trace
                },
                "decisions": {
                    player: {
                        "source": decision.info.source,
                        "action": decision.action.model_dump(mode="json", exclude_none=True),
                        "selected_tool": decision.info.selected_tool,
                        "summary": decision.info.summary,
                        "model": decision.info.returned_model or decision.info.requested_model,
                        "attempts": decision.info.attempts,
                        "total_tokens": decision.info.total_tokens,
                        "latency_ms": decision.info.latency_ms,
                        "errors": decision.info.errors,
                    }
                    for player, decision in selected.items() if player in analyses
                },
            }
            await asyncio.to_thread(
                store.save_turn_log, header["replay_id"], before["turn"], turn_log,
            )
            await asyncio.to_thread(
                store.append_turn, header["replay_id"], len(decisions), before["turn"],
                before["tick"], turn_payload, checkpoint(),
            )
        await send({"type": "cycle", **turn_payload})
        if arena.result is None and wait_for_next:
            await wait_for_next()
    summary = {
        "result": asdict(arena.result), "fighters": arena.snapshot()["fighters"],
        "decisions": {player: calls[player] for player in names},
        "event_counts": dict(Counter(event.status for event in arena.events)),
        "model_usage": {player: {
            field: sum(decision["details"].get(player, {}).get(field, 0) for decision in decisions)
            for field in ("attempts", "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms")
        } for player, name in names.items() if name in MODEL_AGENT_IDS},
    }
    recording = Recording(**header, frames=frames, decisions=decisions,
                          events=[asdict(event) for event in arena.events], summary=summary)
    if store:
        await asyncio.to_thread(store.save, recording)
    return recording
