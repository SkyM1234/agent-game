"""Expose character tool names while keeping engine actions and replay IDs stable."""

from copy import deepcopy


SKILL_FIELDS = (
    "cooldown_turns", "projectile_speed", "stamina_cost", "mana_cost",
    "windup_ms", "active_ms", "recovery_ms", "reach", "health_damage",
    "speed", "teleport", "stamina_restore", "mana_restore",
)


def _compact_skill(spec: dict) -> dict:
    return {key: spec[key] for key in SKILL_FIELDS
            if key in spec and spec[key] not in (0, 0.0, False, None)}


def _compact_item(item: dict) -> dict:
    return {
        "name": item["name"],
        "timing": {key: value for key, value in item["timing"].items() if value},
        "effects": {key: value for key, value in item["effects"].items() if value},
    }


def profile_for(observation: dict, player: str | None = None) -> dict | None:
    player = player or observation["self"]["fighter_id"]
    return observation["rules"].get("characters", {}).get(player, {}).get("agent")


def tool_names(observation: dict, player: str | None = None) -> dict[str, str]:
    profile = profile_for(observation, player)
    return {tool["action"]: tool["name"] for tool in profile["tools"]} if profile else {}


def available_bindings(observation: dict) -> dict[str, dict]:
    available = observation["tools"]["available"]
    profile = profile_for(observation)
    if not profile:
        bindings = {name: {**deepcopy(tool), "action": name, "description": "Game action."}
                    for name, tool in available.items()}
    else:
        bindings = {}
        for definition in profile["tools"]:
            action = definition["action"]
            if action not in available:
                continue
            tool = deepcopy(available[action])
            for key, values in definition["parameters"].items():
                if not isinstance(values, list):
                    continue
                tool["parameters"]["properties"][key]["enum"] = [
                    value for value in tool["parameters"]["properties"][key]["enum"] if value in values
                ]
            if any("enum" in schema and not schema["enum"] for schema in tool["parameters"]["properties"].values()):
                continue
            bindings[definition["name"]] = {**tool, "action": action, "description": definition["description"]}
    if "use_item" in available:
        tool = deepcopy(available["use_item"])
        bindings["use_item"] = {**tool, "action": "use_item",
            "description": "使用一件携带的主动消耗品。效果、剩余次数和当前可选 item_id 见观察与参数枚举。"}
    return bindings


def model_observation(observation: dict) -> dict:
    view = deepcopy(observation)
    own = observation["self"]["fighter_id"]
    enemy = observation["opponent"]["fighter_id"]
    names = {player: tool_names(observation, player) for player in (own, enemy)}
    raw_rules = view["rules"]
    characters = raw_rules.get("characters", {})
    equipment = raw_rules.get("equipment", {})
    item_specs = raw_rules.get("items", {})
    resource_limits = raw_rules.get("resource_limits", {})
    raw_skills = {
        own: raw_rules["skills"],
        enemy: raw_rules.get(
            "opponent_skills",
            characters.get(enemy, {}).get("skills", raw_rules["skills"]),
        ),
    }
    own_names = names[own]
    view["time_remaining_ms"] = max(
        0, raw_rules["time_limit_ms"] - round(view["simulation_time"] * 1000),
    )
    view.pop("events", None)
    view["rules"] = {
        "decision_ms": raw_rules["decision_ms"],
        "arena_width": raw_rules["arena_width"],
        "fighter_radius": raw_rules["fighter_radius"],
        "guard_damage_multiplier": raw_rules["guard_damage_multiplier"],
        "omitted_skill_values_are_zero": True,
        "skills_are_effective_after_weapon_modifiers": True,
        "skills": {own_names.get(key, key): _compact_skill(spec)
                   for key, spec in raw_skills[own].items()},
        "opponent_skills": {names[enemy].get(key, key): _compact_skill(spec)
                            for key, spec in raw_skills[enemy].items()},
        "items": {
            player: {item["item_id"]: _compact_item(item) for item in loadout}
            for player, loadout in item_specs.items() if loadout
        },
    }
    raw_tools = view["tools"]
    unavailable_items = raw_tools.get("items", {}).get("unavailable", {})
    view["tools"] = {
        "unavailable": {own_names.get(key, key): reason
                        for key, reason in raw_tools["unavailable"].items()},
        **({"unavailable_items": unavailable_items} if unavailable_items else {}),
    }

    def enrich_action(action: dict, player: str, *, current: bool) -> None:
        elapsed = action.pop("elapsed_ms", 0)
        effect_remaining = action.pop("remaining_ms", None)
        if effect_remaining is not None:
            action["effect_remaining_ms"] = max(0, effect_remaining)
        if not current:
            return
        if action["skill"] == "use_item":
            spec = next(item["timing"] for item in item_specs.get(player, [])
                        if item["item_id"] == action["item_id"])
        else:
            spec = raw_skills[player][action["skill"]]
        action["lock_remaining_ms"] = max(
            0, spec["windup_ms"] + spec["recovery_ms"] - elapsed,
        )

    for field, player in (("self", own), ("opponent", enemy)):
        fighter = view[field]
        character = characters.get(player, {})
        if character:
            fighter["character"] = {
                key: character[key]
                for key in ("character_id", "name", "title", "profession")
                if key in character
            }
        limits = resource_limits.get(player, {})
        for resource in ("health", "stamina", "mana"):
            if resource in limits:
                fighter[f"max_{resource}"] = limits[resource]
        weapon = equipment.get(player)
        if weapon:
            fighter["weapon"] = {key: weapon[key] for key in ("weapon_id", "name")}
        if fighter["action"]:
            enrich_action(fighter["action"], player, current=True)
            skill = fighter["action"]["skill"]
            fighter["action"]["skill"] = names[player].get(skill, skill)
            fighter["can_decide"] = fighter["action"]["lock_remaining_ms"] == 0
        else:
            fighter["can_decide"] = True
        for action in fighter.get("ongoing_actions", []):
            enrich_action(action, player, current=False)
            action["skill"] = names[player].get(action["skill"], action["skill"])
        fighter["cooldowns"] = {
            names[player].get(key, key): value
            for key, value in fighter.get("cooldowns", {}).items()
        }
    for turn in view.get("recent_turns", []):
        for player, action in turn["actions"].items():
            action.pop("summary", None)
            action["skill"] = names[player].get(action["skill"], action["skill"])
        for event in turn["events"]:
            mapping = names.get(event["actor"], {})
            if "skill" in event:
                event["skill"] = mapping.get(event["skill"], event["skill"])
    for threat in view.get("incoming_threats", []):
        threat["skill"] = names[enemy].get(threat["skill"], threat["skill"])
    history = view.get("opponent_history")
    if history:
        history["action_counts"] = {
            names[enemy].get(skill, skill): count
            for skill, count in history.get("action_counts", {}).items()
        }
        history["direction_counts"] = {
            names[enemy].get(skill, skill): counts
            for skill, counts in history.get("direction_counts", {}).items()
        }
    analysis = view.get("counterfactual_analysis")
    if analysis:
        recommended = analysis.get("recommended_action")
        if recommended:
            recommended["skill"] = own_names.get(recommended["skill"], recommended["skill"])
        for candidate in analysis["candidates"]:
            candidate["action"]["skill"] = own_names.get(
                candidate["action"]["skill"], candidate["action"]["skill"]
            )
            response = candidate["worst_response"]
            response["skill"] = names[enemy].get(response["skill"], response["skill"])
        for detail in analysis.get("detailed_worst_cases", []):
            detail["action"]["skill"] = own_names.get(
                detail["action"]["skill"], detail["action"]["skill"],
            )
    return view
