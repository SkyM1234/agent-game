"""Load independent weapon definitions and compose them with character choices."""

from pathlib import Path

from .models import GameConfig, WeaponSpec

WEAPON_CONFIG_ROOT = Path(__file__).resolve().parents[2] / "configs" / "weapons"


class WeaponConfigError(ValueError):
    def __init__(self, weapon_id: str):
        super().__init__(f"weapon configuration is invalid: {weapon_id}")


def load_weapon(weapon_id: str) -> WeaponSpec:
    if not weapon_id or Path(weapon_id).name != weapon_id:
        raise WeaponConfigError(weapon_id)
    path = WEAPON_CONFIG_ROOT / f"{weapon_id}.json"
    try:
        weapon = WeaponSpec.model_validate_json(path.read_text(encoding="utf-8"))
        if weapon.weapon_id != weapon_id:
            raise ValueError("weapon identity mismatch")
        return weapon
    except (OSError, ValueError, TypeError):
        raise WeaponConfigError(weapon_id) from None


def weapon_catalog() -> dict[str, WeaponSpec]:
    catalog = {}
    for path in sorted(WEAPON_CONFIG_ROOT.glob("*.json")):
        weapon = load_weapon(path.stem)
        if weapon.weapon_id in catalog:
            raise WeaponConfigError(weapon.weapon_id)
        catalog[weapon.weapon_id] = weapon
    return catalog


def with_weapons(config: GameConfig, choices: dict[str, str]) -> GameConfig:
    return GameConfig.model_validate({**config.model_dump(), "equipment": {
        player: load_weapon(weapon_id).model_dump() for player, weapon_id in choices.items()
    }})
