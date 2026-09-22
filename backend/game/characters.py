"""Load character rules and agent profiles from their local asset packages."""

import json
from pathlib import Path
from typing import Literal, get_args

from .models import CharacterId, CharacterLoadout, GameConfig

CHARACTER_CONFIG_ROOT = Path(__file__).resolve().parents[2] / "configs/characters"
PromptVariant = Literal["neutral", "aggressive"]


class CharacterConfigError(ValueError):
    def __init__(self, character_id: str):
        super().__init__(f"人物 {character_id} 的 character.json、tools.json 或提示词配置无效")


def load_character(character_id: CharacterId, *, prompt_variant: PromptVariant = "neutral") -> CharacterLoadout:
    if prompt_variant not in get_args(PromptVariant):
        raise ValueError(f"unknown prompt variant: {prompt_variant}")
    if character_id not in get_args(CharacterId):
        raise CharacterConfigError(character_id)
    directory = CHARACTER_CONFIG_ROOT / character_id
    try:
        data = json.loads((directory / "character.json").read_text(encoding="utf-8"))
        tools = json.loads((directory / "tools.json").read_text(encoding="utf-8"))
        version = data.pop("version")
        if not isinstance(version, str) or not version:
            raise ValueError("character version must be a non-empty string")
        prompt_file = f"prompt_{prompt_variant}.md"
        prompt = (directory / prompt_file).read_text(encoding="utf-8").strip()
        if prompt_variant == "aggressive":
            version += "-aggressive-v1"
        if data["character_id"] != character_id:
            raise ValueError("character identity mismatch")
        if "profession" not in data:
            raise ValueError("character profession is required")
        character = CharacterLoadout.model_validate({**data, "agent": {**tools, "version": version, "prompt": prompt}})
        # Validate rules as a whole, including timing, resource costs and free fallback actions.
        GameConfig(characters={"p1": character, "p2": character})
        return character
    except (OSError, ValueError, TypeError, KeyError):
        raise CharacterConfigError(character_id) from None


def character_catalog() -> dict[str, CharacterLoadout]:
    return {character_id: load_character(character_id) for character_id in get_args(CharacterId)}


def with_characters(config: GameConfig, choices: dict[str, CharacterId], *,
                    prompt_variant: PromptVariant = "neutral",
                    prompt_variants: dict[str, PromptVariant] | None = None) -> GameConfig:
    variants = prompt_variants or {}
    if set(variants) - set(choices):
        raise ValueError("prompt variants must refer to selected players")
    return GameConfig.model_validate({**config.model_dump(), "characters": {
        player: load_character(character_id, prompt_variant=variants.get(player, prompt_variant)).model_dump()
        for player, character_id in choices.items()
    }})
