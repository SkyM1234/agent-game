"""Human-readable rendering for per-turn diagnostic logs."""

from __future__ import annotations

import json
from typing import Any


def _action_label(action: dict | None) -> str:
    if not action:
        return "continue"
    skill = str(action.get("skill", "unknown"))
    details = [
        f"{key}={value:.3f}" if isinstance(value, float) else f"{key}={value}"
        for key, value in action.items()
        if key not in ("skill", "decision_summary") and value is not None
    ]
    return skill + (f"({', '.join(details)})" if details else "")


def _number(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    cells = [[_number(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    border = "+-" + "-+-".join("-" * width for width in widths) + "-+"

    def line(row: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)) + " |"

    return [border, line(headers), border, *(line(row) for row in cells), border]


def _decoded(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _render_matrix(matrix: dict) -> list[str]:
    rows = matrix.get("rows", [])
    responses = []
    for row in rows:
        for response in row.get("opponent_responses", []):
            label = _action_label(response.get("opponent_action"))
            if label not in responses:
                responses.append(label)
    own_labels = [_action_label(row.get("own_action")) for row in rows]
    scores = []
    for row_index, row in enumerate(rows, 1):
        by_response = {
            _action_label(item.get("opponent_action")): item.get("score", "-")
            for item in row.get("opponent_responses", [])
        }
        scores.append([
            f"A{row_index}",
            *(by_response.get(response, "-") for response in responses),
        ])
    if not scores:
        return ["(no payoff matrix for this player)"]
    lines = _table(["Own / Opp", *(f"B{index}" for index in range(1, len(responses) + 1))], scores)
    lines.extend(["", "Action legend:"])
    for index in range(max(len(own_labels), len(responses))):
        own_label = own_labels[index] if index < len(own_labels) else None
        opponent_label = responses[index] if index < len(responses) else None
        if own_label == opponent_label:
            lines.append(f"  A{index + 1} / B{index + 1} = {own_label}")
        else:
            own = f"A{index + 1} = {own_label}" if own_label is not None else ""
            opponent = f"B{index + 1} = {opponent_label}" if opponent_label is not None else ""
            lines.append(f"  {own:<38} {opponent}".rstrip())
    return lines


def _render_calculation(calculation: dict, selected_action: dict | None) -> list[str]:
    lines = [
        f"Risk aversion: {_number(calculation.get('risk_aversion', '-'))}",
        f"Recommended action: {_action_label(calculation.get('recommended_action'))}",
        "Recommended risk-weighted score: "
        f"{_number(calculation.get('recommended_risk_weighted_score', '-'))}",
        "Recommended score gap: "
        f"{_number(calculation.get('recommended_score_gap', '-'))}",
        "Recommended terminal status: "
        f"{calculation.get('recommended_terminal_status', '-')}",
        "",
        "Key candidates (engine top 3 plus final choice):",
    ]
    candidates = calculation.get("candidates", [])
    shown = candidates[:3]
    selected_label = _action_label(selected_action)
    selected = next(
        (candidate for candidate in candidates
         if _action_label(candidate.get("action")) == selected_label),
        None,
    )
    if selected and selected not in shown:
        shown = [*shown, selected]
    recommended_label = _action_label(calculation.get("recommended_action"))
    lines.extend(_table(
        ["Role", "Action", "Risk weighted", "Worst", "Mean", "Terminal", "Worst response"],
        [[
            "/".join(filter(None, (
                "engine-best" if _action_label(candidate.get("action")) == recommended_label else "",
                "final-choice" if _action_label(candidate.get("action")) == selected_label else "",
            ))) or "alternative",
            _action_label(candidate.get("action")),
            candidate.get("risk_weighted_score", "-"),
            candidate.get("worst_score", "-"),
            candidate.get("mean_score", "-"),
            candidate.get("terminal_status", "-"),
            _action_label(candidate.get("worst_response")),
        ] for candidate in shown],
    ) if candidates else ["(no candidates)"])
    return lines


def _fighter_summary(label: str, fighter: dict) -> list[str]:
    line = (
        f"{label}: HP {fighter.get('health', '-')} | stamina {fighter.get('stamina', '-')} | "
        f"mana {fighter.get('mana', '-')} | shield {fighter.get('shield', 0)} | "
        f"position {fighter.get('position', '-')}"
    )
    lines = [line]
    if fighter.get("action"):
        lines.append(f"  Current action: {_action_label(fighter['action'])}")
    if fighter.get("cooldowns"):
        cooldowns = ", ".join(f"{name}={turns}" for name, turns in fighter["cooldowns"].items())
        lines.append(f"  Cooldowns: {cooldowns}")
    if fighter.get("items"):
        items = ", ".join(f"{name}={count}" for name, count in fighter["items"].items())
        lines.append(f"  Items: {items}")
    return lines


def _render_model_analysis(analysis: dict) -> list[str]:
    candidates = analysis.get("candidates", [])
    lines = [
        "Counterfactual analysis sent to model:",
        f"  Risk aversion: {_number(analysis.get('risk_aversion', '-'))}",
    ]
    if candidates:
        lines.extend(_table(
            ["Action", "Preference", "Terminal", "Worst response"],
            [[
                _action_label(candidate.get("action")),
                candidate.get("preference", "-"),
                candidate.get("terminal_status", "-"),
                _action_label(candidate.get("worst_response")),
            ] for candidate in candidates],
        ))
    details = analysis.get("detailed_worst_cases", [])
    if details:
        lines.append("  Detailed worst cases:")
        for detail in details:
            metrics = json.dumps(
                detail.get("metrics", {}), ensure_ascii=False, separators=(",", ":"),
            )
            lines.append(f"    {_action_label(detail.get('action'))}: {metrics}")
    return lines


def _render_request(request: dict) -> list[str]:
    observation = None
    retry_instruction = None
    for message in request.get("messages", []):
        if message.get("role") != "user":
            continue
        content = _decoded(message.get("content", ""))
        if isinstance(content, dict) and "self" in content:
            observation = content
        elif isinstance(content, str):
            retry_instruction = content
    tools = [
        tool.get("function", {}).get("name", "unknown")
        for tool in request.get("tools", [])
    ]
    lines = [
        f"Model: {request.get('model', '-')}",
        f"Available actions: {', '.join(tools) if tools else '(none)'}",
        "Stable match context: stored in match header; not repeated per turn.",
    ]
    if observation:
        lines.extend([
            f"Time: {observation.get('simulation_time', '-')}s | "
            f"remaining {observation.get('time_remaining_ms', '-')}ms | "
            f"distance {observation.get('distance', '-')}",
            *_fighter_summary("Self", observation.get("self", {})),
            *_fighter_summary("Opponent", observation.get("opponent", {})),
        ])
        threats = observation.get("incoming_threats", [])
        if threats:
            lines.append("Incoming threats:")
            lines.extend(
                "  " + " | ".join((
                    _action_label({"skill": threat.get("skill", "unknown")}),
                    f"phase {threat.get('phase', '-')}",
                    f"damage {threat.get('damage', '-')}",
                    f"in path {threat.get('target_in_path', False)}",
                    f"impact ETA {threat.get('earliest_impact_ms', '-')}ms",
                ))
                for threat in threats
            )
        analysis = observation.get("counterfactual_analysis")
        if analysis:
            lines.extend(_render_model_analysis(analysis))
        history = observation.get("opponent_history")
        if history:
            actions = ", ".join(
                f"{name}={count}" for name, count in history.get("action_counts", {}).items()
            ) or "none"
            directions = ", ".join(
                f"{name}({', '.join(f'{direction}={count}' for direction, count in counts.items())})"
                for name, counts in history.get("direction_counts", {}).items()
            ) or "none"
            items = ", ".join(
                f"{name}={count}" for name, count in history.get("item_counts", {}).items()
            ) or "none"
            outcomes = ", ".join(
                f"{name}={count}" for name, count in history.get("outcome_counts", {}).items()
            ) or "none"
            lines.extend([
                f"Opponent history through turn {history.get('through_turn', '-')}: "
                f"{history.get('observed_decisions', 0)} decisions",
                f"  Actions: {actions}",
                f"  Directions: {directions}; items: {items}",
                f"  Outcomes: {outcomes}; damage dealt {history.get('health_damage_dealt', 0)}",
            ])
        recent = observation.get("recent_turns", [])
        if recent:
            lines.append("Recent turns:")
            for turn in recent:
                actions = ", ".join(
                    f"{player}={_action_label(action)}"
                    for player, action in turn.get("actions", {}).items()
                )
                distance = turn.get("distance", {})
                events = ", ".join(
                    f"{event.get('actor', '?')}:{event.get('status', '?')}"
                    for event in turn.get("events", [])
                ) or "none"
                lines.append(
                    f"  Turn {turn.get('turn', '?')}: distance "
                    f"{distance.get('before', '-')} -> {distance.get('after', '-')}; "
                    f"actions {actions}; events {events}"
                )
    if retry_instruction:
        lines.append(f"Retry instruction: {retry_instruction}")
    return lines


def _render_decision(decision: dict, calculation: dict) -> list[str]:
    selected_tool = decision.get("selected_tool")
    action = _action_label(decision.get("action"))
    chosen = f"{selected_tool} -> {action}" if selected_tool and selected_tool != action else action
    lines = [
        f"Chosen action: {chosen}",
        f"Decision source: {decision.get('source', '-')}",
    ]
    recommended = calculation.get("recommended_action")
    if recommended:
        aligned = _action_label(decision.get("action")) == _action_label(recommended)
        lines.append(
            "Engine comparison: followed recommendation"
            if aligned else f"Engine comparison: overrode {_action_label(recommended)}"
        )
    missing_reason = "(no reason supplied)" if decision.get("source") == "counterfactual" else "(model supplied no reason)"
    lines.append(f"Reason: {decision.get('summary') or missing_reason}")
    if decision.get("source") != "counterfactual":
        lines.append(
            f"Model: {decision.get('model', '-')} | attempts {decision.get('attempts', 0)} | "
            f"tokens {decision.get('total_tokens', 0)} | "
            f"latency {_number(decision.get('latency_ms', 0))}ms"
        )
    if decision.get("errors"):
        lines.append(f"Earlier attempt errors: {', '.join(decision['errors'])}")
    return lines


def render_turn_log(payload: dict) -> str:
    """Render the evidence and final choice for each decision in a turn."""
    divider = "=" * 88
    lines = [divider, f"TURN {payload['turn']}", divider]
    matrices = payload.get("payoff_matrices", {})
    calculations = payload.get("engine_evaluations", {})
    traces = payload.get("model_traces", {})
    decisions = payload.get("decisions", {})
    players = list(dict.fromkeys([*matrices, *calculations, *traces, *decisions]))
    if not players:
        lines.extend(["", "No payoff calculation or LLM call occurred in this turn."])
    for player in players:
        title = player.upper()
        trace = traces.get(player, {})
        requests = trace.get("input", [])
        if requests:
            lines.extend(["", f"[{title}] DECISION CONTEXT SENT TO LLM", "-" * 88])
            lines.extend(_render_request(requests[-1]))
        lines.extend(["", f"[{title}] PAYOFF MATRIX", "-" * 88])
        lines.extend(_render_matrix(matrices.get(player, {})))
        if player in calculations:
            lines.extend(["", f"[{title}] FULL ENGINE EVALUATION", "-" * 88])
            lines.extend(_render_calculation(
                calculations[player], decisions.get(player, {}).get("action"),
            ))
        if player in decisions:
            source = decisions[player].get("source")
            decision_title = "ENGINE DECISION" if source == "counterfactual" else "LLM DECISION"
            lines.extend(["", f"[{title}] {decision_title}", "-" * 88])
            lines.extend(_render_decision(decisions[player], calculations.get(player, {})))
    return "\n".join(lines) + "\n"
