from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path

from pydantic import ValidationError

from backend.agents.scripted import AGENTS
from backend.agents.deepseek import ProviderError
from backend.matches.live import AGENT_IDS, MODEL_AGENT_IDS, record_live_match
from backend.game import Arena, GameConfig
from .runner import run_match


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a headless AI fighting match.")
    parser.add_argument("--p1", choices=AGENT_IDS, default="test")
    parser.add_argument("--p2", choices=AGENT_IDS, default="test")
    parser.add_argument("--config", type=Path, help="JSON overrides for GameConfig")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--trace", action="store_true", help="Print combat events as JSON lines")
    output.add_argument("--json", action="store_true", help="Print the match summary as JSON")
    args = parser.parse_args()
    try:
        config = (GameConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
                  if args.config else GameConfig())
    except (OSError, ValidationError) as error:
        parser.error(str(error))
    arena = Arena(config)

    def print_events(events):
        for event in events:
            print(json.dumps(asdict(event), ensure_ascii=True))

    if any(name in MODEL_AGENT_IDS for name in (args.p1, args.p2)):
        async def progress(message):
            if args.trace and message["type"] == "cycle":
                for event in message["events"]:
                    print(json.dumps(event, ensure_ascii=True), flush=True)
        try:
            recording = asyncio.run(record_live_match(args.p1, args.p2, config, on_update=progress))
        except ProviderError as error:
            parser.error(str(error))
        summary = recording.summary
    else:
        summary = run_match(
            arena, {"p1": AGENTS[args.p1](), "p2": AGENTS[args.p2]()},
            on_events=print_events if args.trace else None,
        )
    if args.json or args.trace:
        print(json.dumps(summary, indent=None if args.trace else 2))
        return
    result = summary["result"]
    print(f"{args.p1} (p1) vs {args.p2} (p2)")
    print(f"Winner: {result['winner'] or 'draw'} | {result['reason']}"
          f" | game time: {result['simulation_time']:.2f}s")
    for fighter_id, fighter in summary["fighters"].items():
        print(f"{fighter_id}: health={fighter['health']:.1f}, stamina={fighter['stamina']:.1f}"
              f", mana={fighter['mana']:.1f}")
    counts = summary["event_counts"]
    print(f"Hits: {counts.get('hit', 0)}"
          f" | rejected: {counts.get('rejected', 0)} | fallbacks: {counts.get('fallback', 0)}")


if __name__ == "__main__":
    main()
