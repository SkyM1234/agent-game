"""Load independent consumable item definitions and compose player inventories."""

from pathlib import Path

from .models import GameConfig, ItemSpec

ITEM_CONFIG_ROOT = Path(__file__).resolve().parents[2] / "configs/items"


class ItemConfigError(ValueError):
    def __init__(self, item_id: str):
        super().__init__(f"item configuration is invalid: {item_id}")


def load_item(item_id: str) -> ItemSpec:
    if not item_id or Path(item_id).name != item_id:
        raise ItemConfigError(item_id)
    path = ITEM_CONFIG_ROOT / f"{item_id}.json"
    try:
        item = ItemSpec.model_validate_json(path.read_text(encoding="utf-8"))
        if item.item_id != item_id:
            raise ValueError("item identity mismatch")
        return item
    except (OSError, ValueError, TypeError):
        raise ItemConfigError(item_id) from None


def item_catalog() -> dict[str, ItemSpec]:
    catalog = {}
    for path in sorted(ITEM_CONFIG_ROOT.glob("*.json")):
        item = load_item(path.stem)
        if item.item_id in catalog:
            raise ItemConfigError(item.item_id)
        catalog[item.item_id] = item
    return catalog


def with_items(config: GameConfig, choices: dict[str, list[str]]) -> GameConfig:
    return GameConfig.model_validate({**config.model_dump(), "items": {
        player: [load_item(item_id).model_dump() for item_id in item_ids]
        for player, item_ids in choices.items()
    }})
