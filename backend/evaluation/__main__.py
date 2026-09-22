import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import get_args

from backend.agents.deepseek import ProviderError
from backend.game.characters import PromptVariant
from .runner import Prices, STRATEGIES, Suite, default_suite, run_evaluation
from .store import EvaluationStore


def main():
    parser = argparse.ArgumentParser(description="Run paired, headless strategy evaluations; no model calls by default.")
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES)
    parser.add_argument("--include-llm", action="store_true", help="Enable the two model strategies (incurs API usage)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--time-limit-ms", type=int, default=20000, help="Default suite match duration; ignored with --suite")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--distance", type=float, default=6,
                        help="Initial center-to-center distance, 0.8 to 11.2 (default: 6); spawns are centered")
    source.add_argument("--suite", type=Path, help="Single-scenario suite JSON or report.json to reuse its frozen configuration")
    parser.add_argument("--prompt-variant", choices=get_args(PromptVariant),
                        help="Default prompt for both fighters (default: neutral); overridden by --p1-prompt/--p2-prompt")
    parser.add_argument("--p1-prompt", choices=get_args(PromptVariant),
                        help="Prompt for p1 (crimson_blade); cannot combine with --suite")
    parser.add_argument("--p2-prompt", choices=get_args(PromptVariant),
                        help="Prompt for p2 (frost_bell); cannot combine with --suite")
    parser.add_argument("--output", type=Path, default=Path(os.environ.get("ARENA_DATA_DIR", Path(__file__).resolve().parents[2] / "data")) / "evaluations")
    parser.add_argument("--input-price", type=float, help="Price per million input tokens")
    parser.add_argument("--output-price", type=float, help="Price per million output tokens")
    parser.add_argument("--currency", default="CNY")
    args = parser.parse_args()
    if args.suite and any(value is not None for value in (args.prompt_variant, args.p1_prompt, args.p2_prompt)):
        parser.error("prompt options cannot combine with --suite; the suite already contains frozen prompts")
    strategies = args.strategies or (list(STRATEGIES) if args.include_llm else ["heuristic", "counterfactual"])
    if not args.include_llm and any(name in ("deepseek", "counterfactual_deepseek") for name in strategies):
        parser.error("model evaluations require --include-llm")
    if (args.input_price is None) != (args.output_price is None):
        parser.error("provide both --input-price and --output-price")
    try:
        prices = Prices(input_per_million=args.input_price, output_per_million=args.output_price,
                        currency=args.currency) if args.input_price is not None else None
        payload = json.loads(args.suite.read_text(encoding="utf-8-sig")) if args.suite else None
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("suite must be a JSON object")
        suite = (Suite.model_validate(payload.get("suite", payload)) if payload is not None
                 else default_suite(args.time_limit_ms, distance=args.distance,
                                    prompt_variant=args.prompt_variant or "neutral",
                                    prompt_variants={player: variant for player, variant in
                                                     (("p1", args.p1_prompt), ("p2", args.p2_prompt))
                                                     if variant is not None}))
        def progress(report):
            print(f"{report['run_id']}  {len(report['matches'])}/{report['planned_matches']}"
                  f"  completed={report['completed_matches']} failed={report['failed_matches']}", flush=True)
        report = asyncio.run(run_evaluation(EvaluationStore(args.output), suite, strategies,
                                            repeats=args.repeats, prices=prices, progress=progress))
    except (ValueError, OSError, ProviderError) as error:
        parser.error(str(error))
    print(args.output / report["run_id"] / "report.json")
    if report["status"] != "completed" or report["failed_matches"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
