"""Explicit, paid-provider smoke check using local DeepSeek configuration."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from backend.agents.deepseek import DeepSeekAgent, DeepSeekSettings, ProviderError
from backend.game import Arena, GameConfig
from backend.game.characters import with_characters
from backend.matches.live import record_live_match
from backend.replay.store import ReplayStore


async def check(args):
    settings = DeepSeekSettings.from_env()
    config = with_characters(GameConfig(time_limit_ms=args.match_ms or 60_000), {
        "p1": args.p1_character, "p2": args.p2_character,
    })
    if args.match_ms:

        async def update(message):
            if message["type"] == "cycle":
                details = message["decision"]["details"]
                print(json.dumps({"time": message["frames"][-1]["simulation_time"],
                                  "sources": {p: d["source"] for p, d in details.items()},
                                  "errors": {p: d["errors"] for p, d in details.items()}}, ensure_ascii=True), flush=True)

        recording = await record_live_match("deepseek", "deepseek", config, settings=settings, on_update=update)
        if args.save:
            ReplayStore(Path(__file__).resolve().parents[1] / "data").save(recording)
        print(json.dumps(recording.summary, ensure_ascii=True, indent=2))
        fallback = any(detail.source == "fallback" for decision in recording.decisions for detail in decision.details.values())
        return 1 if fallback else 0
    async with httpx.AsyncClient() as client:
        decision = await DeepSeekAgent(settings, client).decide(Arena(config).observe(args.player))
    print(decision.model_dump_json(indent=2).encode("ascii", "backslashreplace").decode())
    return 0 if decision.info.source == "llm" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--match-ms", type=int, default=0, help="Run a complete match of this game duration")
    parser.add_argument("--save", action="store_true", help="Save the finished match to the replay library")
    parser.add_argument("--p1-character", choices=("crimson_blade", "frost_bell"), default="crimson_blade")
    parser.add_argument("--p2-character", choices=("crimson_blade", "frost_bell"), default="frost_bell")
    parser.add_argument("--player", choices=("p1", "p2"), default="p1", help="Fighter for a single-decision check")
    try:
        code = asyncio.run(check(parser.parse_args()))
    except ProviderError as error:
        print(json.dumps({"error": error.code, "reason": error.reason, "player": error.player}, ensure_ascii=True))
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
