from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict

from backend.agents.scripted import Agent
from backend.agents.character_tools import available_bindings
from backend.game.engine import Arena
from backend.game.models import Event


def run_match(arena: Arena, agents: Mapping[str, Agent],
              on_events: Callable[[list[Event]], None] | None = None,
              *, on_frame: Callable[[dict], None] | None = None,
              on_decision: Callable[[dict], None] | None = None) -> dict:
    if set(agents) != set(arena.fighters):
        raise ValueError("exactly one agent is required for each fighter")
    decisions = Counter()
    while arena.result is None:
        # Freeze both observations before invoking either agent.
        observations = {
            fighter_id: arena.observe(fighter_id)
            for fighter_id in arena.fighters
            if arena.can_decide(fighter_id)
        }
        actions = {}
        details = {}
        for fighter_id, observation in observations.items():
            agent = agents[fighter_id]
            actions[fighter_id] = (agent.decide_in_arena(arena, fighter_id)
                                   if hasattr(agent, "decide_in_arena") else agent.decide(observation))
            analysis = getattr(agent, "last_analysis", None)
            if analysis is not None:
                bindings = available_bindings(observation)
                action = actions[fighter_id]
                selected_tool = next(name for name, tool in bindings.items()
                                     if tool["action"] == action.skill)
                details[fighter_id] = {
                    "source": "counterfactual",
                    "summary": "按最坏情况与平均收益的加权结果选择动作。",
                    "available_tools": list(bindings),
                    "selected_tool": selected_tool,
                    "rollout_recommendation": action.model_dump(exclude_none=True),
                    "risk_weighted_score": analysis.selected_evaluation.risk_weighted_score,
                }
            decisions[fighter_id] += 1
        if on_decision:
            decision = {
                "tick": arena.tick, "simulation_time": arena.simulation_time,
                "actions": {key: value.model_dump(exclude_none=True)
                            if hasattr(value, "model_dump") else value
                            for key, value in actions.items()},
            }
            if details:
                decision["details"] = details
            on_decision(decision)
        events = arena.advance(actions, on_frame=on_frame)
        if on_events:
            on_events(events)
    return {
        "result": asdict(arena.result),
        "fighters": arena.snapshot()["fighters"],
        "decisions": {fighter_id: decisions[fighter_id] for fighter_id in arena.fighters},
        "event_counts": dict(Counter(event.status for event in arena.events)),
    }
