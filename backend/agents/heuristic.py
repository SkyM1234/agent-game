"""Deterministic distance/resource baseline; no lookahead or model calls."""
from backend.game.models import Action, parameter_matches


class HeuristicAgent:
    def decide(self, observation: dict) -> Action:
        available = observation["tools"]["available"]
        skills = observation["rules"]["skills"]
        own = observation["self"]
        distance = observation["distance"]
        for item_id, item in observation["tools"].get("items", {}).get("available", {}).items():
            if any(item["effects"].get(f"{resource}_restore", 0) and
                   own[resource] < observation["rules"][f"max_{resource}"] * 0.45
                   for resource in ("health", "stamina", "mana")):
                return Action(skill="use_item", item_id=item_id)
        attacks = [name for name in ("jab", "heavy_punch", "kick")
                   if name in available and skills[name]["reach"] >= distance]
        if attacks:
            best = max(attacks, key=lambda name: skills[name]["health_damage"] /
                       max(1, skills[name]["windup_ms"] + skills[name]["recovery_ms"]))
            return Action(skill=best)
        if "rest" in available and (own["stamina"] < 20 or own["mana"] < 10):
            return Action(skill="rest")
        if "dash" in available and distance > 3:
            schema = available["dash"]["parameters"]["properties"]
            if "position" in schema:
                enemy = observation["opponent"]["position"]
                facing = 1 if enemy > own["position"] else -1
                position = enemy - facing * max(1, skills["jab"]["reach"] * 0.8)
                if parameter_matches(position, schema["position"]):
                    return Action(skill="dash", position=position)
            elif "forward" in schema["direction"]["enum"]:
                return Action(skill="dash", direction="forward")
        if "move" in available and "forward" in available["move"]["parameters"]["properties"]["direction"]["enum"]:
            return Action(skill="move", direction="forward")
        # Custom packs may put recovery on cooldown; always respect the tool schema.
        for name in ("rest", "guard", "jab", "heavy_punch", "kick", "move", "dash", "use_item"):
            if name not in available:
                continue
            arguments = {}
            for key, schema in available[name]["parameters"]["properties"].items():
                arguments[key] = schema["enum"][0] if "enum" in schema else own["position"]
            if all(parameter_matches(arguments[key], schema) for key, schema in
                   available[name]["parameters"]["properties"].items()):
                return Action(skill=name, **arguments)
        raise ValueError("no legal baseline action")
