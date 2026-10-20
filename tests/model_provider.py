"""Read the newest observation from both initial prompts and Pi tool results."""

import json


def current_observation(payload):
    for message in reversed(payload["messages"]):
        if message["role"] not in ("user", "tool"):
            continue
        try:
            value = json.loads(message["content"])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and "self" in value:
            return value
    raise AssertionError("request has no current observation")
