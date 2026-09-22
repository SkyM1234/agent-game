"""Typed tool functions build commands; only the engine can execute them."""

from .models import Action, Direction


def jab() -> Action:
    return Action(skill="jab")


def heavy_punch() -> Action:
    return Action(skill="heavy_punch")


def kick() -> Action:
    return Action(skill="kick")


def guard() -> Action:
    return Action(skill="guard")


def dash(direction: Direction = "forward") -> Action:
    return Action(skill="dash", direction=direction)


def teleport(position: float) -> Action:
    return Action(skill="dash", position=position)


def move(direction: Direction = "forward") -> Action:
    return Action(skill="move", direction=direction)


def rest() -> Action:
    return Action(skill="rest")


def idle() -> Action:
    """Build the internal no-effect action used for rejected decisions."""
    return Action(skill="idle")
